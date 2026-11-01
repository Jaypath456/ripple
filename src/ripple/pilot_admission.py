"""Private, append-only pilot accounting. No provider or credential access."""

import fcntl
import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from ripple.llm import LLMError


class PilotStop(LLMError):
    """A latched refusal; its reason code contains no provider error text."""


def canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def positive(value) -> bool:
    return type(value) is int and value > 0


def safe_directory(path: Path, *, create=False) -> Path:
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise PilotStop("symlink_directory")
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PilotStop("directory_owner")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PilotStop("directory_permissions")
    return path


def private_write(path: Path, value) -> None:
    """Never overwrite an existing artifact or follow its symlink."""
    data = canonical(value) + b"\n"
    if len(data) > 8 * 1024 * 1024:
        raise PilotStop("artifact_size")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


class Journal:
    """Hash chained records plus a process lock; torn/pending runs fail closed."""

    def __init__(self, root: Path):
        self.root = safe_directory(root, create=not root.exists())
        self.fd = os.open(
            self.root / "journal.jsonl",
            os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            info = os.fstat(self.fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_nlink != 1
                or info.st_size > 64 * 1024 * 1024
            ):
                raise PilotStop("journal_permissions_or_size")
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            data = os.read(self.fd, info.st_size)
            self.events = []
            previous = "0" * 64
            for line in data.splitlines(keepends=True):
                if not line.endswith(b"\n") or len(self.events) >= 1024:
                    raise PilotStop("journal_torn_or_oversized")
                record = json.loads(line)
                claimed = record.pop("sha256")
                if (
                    record["previous"] != previous
                    or record["sequence"] != len(self.events)
                    or digest(record) != claimed
                ):
                    raise PilotStop("journal_integrity")
                previous = claimed
                self.events.append(record["event"])
            self.previous = previous
        except BaseException:
            os.close(self.fd)
            raise

    def append(self, event) -> None:
        record = {
            "sequence": len(self.events),
            "previous": self.previous,
            "event": event,
        }
        checksum = digest(record)
        data = canonical({**record, "sha256": checksum}) + b"\n"
        if (
            len(data) > 2 * 1024 * 1024
            or len(self.events) >= 1024
            or os.fstat(self.fd).st_size + len(data) > 64 * 1024 * 1024
        ):
            raise PilotStop("journal_size")
        view = memoryview(data)
        while view:
            written = os.write(self.fd, view)
            if written <= 0:
                raise PilotStop("journal_write")
            view = view[written:]
        os.fsync(self.fd)
        self.events.append(json.loads(canonical(event)))
        self.previous = checksum

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


@dataclass(frozen=True)
class Limits:
    input_tokens: int = 16000
    output_tokens: int = 2000
    task_requests: int = 24
    aggregate_requests: int = 72
    aggregate_tokens: int = 350000
    request_seconds: int = 90
    task_seconds: int = 2400
    aggregate_seconds: int = 7200

    def __post_init__(self):
        if not all(positive(value) for value in vars(self).values()):
            raise ValueError("limits must be positive integers")
        ceiling = (16000, 2000, 24, 72, 350000, 90, 2400, 7200)
        if any(value > cap for value, cap in zip(vars(self).values(), ceiling)):
            raise ValueError("limits cannot exceed the preregistered maxima")


@dataclass(frozen=True)
class Contract:
    model: str
    context_limit: int
    output_limit: int
    framing_tokens: int
    output_parameters: dict[str, str]
    mock: bool = False
    requests_per_minute: int = 72
    tokens_per_minute: int = 350000

    @classmethod
    def load(cls, path: Path):
        """A reviewed deployment contract needs pinned local primary evidence.

        This is operator-reviewed evidence, not an automatic attestation service.
        Byte bounds are allowed only with a verified tokenizer/framing contract.
        The shipped profile intentionally cannot authorize a live request.
        """
        data = json.loads(path.read_text())
        if (
            data.get("status") != "VERIFIED_UB_DEPLOYMENT"
            or data.get("model") != "openai/gpt-oss-20b"
            or data.get("base_url") != "https://gateway.bullsai.buffalo.edu/v1"
            or data.get("output_includes_reasoning") is not True
            or data.get("usage_is_authoritative") is not True
            or data.get("input_bound_method") != "verified_utf8_plus_framing"
            or not positive(data.get("context_limit"))
            or not positive(data.get("output_limit"))
            or not positive(data.get("framing_token_upper_bound"))
            or not positive(data.get("requests_per_minute"))
            or not positive(data.get("tokens_per_minute"))
        ):
            raise PilotStop("provider_contract_unverified")
        for key in ("context", "output", "counting", "usage", "rate", "retry_billing"):
            item = data.get("evidence", {}).get(key, {})
            relative = Path(item.get("path", ""))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise PilotStop("contract_evidence_path")
            source = path.parent / relative
            if (
                any(p.is_symlink() for p in (source, *source.parents))
                or not source.is_file()
            ):
                raise PilotStop("contract_evidence_missing")
            if hashlib.sha256(source.read_bytes()).hexdigest() != item.get("sha256"):
                raise PilotStop("contract_evidence_hash")
        params = data.get("output_parameters", {})
        if (
            set(params) != {"responses", "chat_completions"}
            or params.get("responses") != "max_output_tokens"
            or params.get("chat_completions")
            not in {"max_completion_tokens", "max_tokens"}
        ):
            raise PilotStop("output_parameter_unverified")
        return cls(
            data["model"],
            data["context_limit"],
            data["output_limit"],
            data["framing_token_upper_bound"],
            params,
            requests_per_minute=data["requests_per_minute"],
            tokens_per_minute=data["tokens_per_minute"],
        )

    def input_bound(self, request) -> int:
        # Whole serialized request, not just user text. No truncation.
        return len(canonical(request)) + self.framing_tokens


class Admission:
    """One durable aggregate budget, shared by sequential, separately approved tasks."""

    def __init__(
        self, journal: Journal, contract: Contract, *, limits=None, clock=monotonic
    ):
        self.journal, self.contract, self.limits, self.clock = (
            journal,
            contract,
            limits or Limits(),
            clock,
        )
        self.stopped = False
        self.current = None
        self.pending = None
        self.total_requests = 0
        self.total_tokens = 0
        self.elapsed = 0.0
        self.finished = []
        self.recent = []
        for event in journal.events:
            kind = event["kind"]
            if kind == "task_started":
                if self.current:
                    raise PilotStop("unfinished_task")
                self.current = event["task"]
            elif kind == "wire_reserved":
                if self.pending:
                    raise PilotStop("unfinished_attempt")
                self.pending = event
                self.total_requests += 1
                self.total_tokens += event["reservation"]
                self.recent.append((event["at"], event["reservation"]))
            elif kind == "wire_settled":
                if not self.pending or self.pending["attempt"] != event["attempt"]:
                    raise PilotStop("settlement_without_attempt")
                self.total_tokens += event["total_tokens"] - self.pending["reservation"]
                self.recent[-1] = (self.recent[-1][0], event["total_tokens"])
                self.pending = None
            elif kind == "stop":
                self.stopped = True
            elif kind == "task_finished":
                self.finished.append(event["task"])
                self.elapsed += event["seconds"]
                self.current = None
        if self.stopped or self.pending or self.current:
            raise PilotStop("previous_run_incomplete_or_stopped")

    def stop(self, reason):
        self.stopped = True
        try:
            self.journal.append({"kind": "stop", "reason": reason})
        finally:
            raise PilotStop(reason)

    def append(self, event):
        try:
            self.journal.append(event)
        except BaseException:  # noqa: BLE001 - persistent stop on capture interruption
            self.stopped = True
            raise PilotStop("capture_write_failed") from None

    def start(self, task: str, allocation: int):
        if self.stopped or self.current or task in self.finished:
            raise PilotStop("task_not_admissible")
        if not positive(allocation) or allocation > 130000:
            raise PilotStop("task_allocation")
        self.append({"kind": "task_started", "task": task, "allocation": allocation})
        self.current, self.allocation = task, allocation
        self.started = self.clock()
        self.task_requests, self.task_tokens = 0, 0

    def reserve(self, endpoint: str, request):
        if self.stopped or not self.current or self.pending:
            raise PilotStop("admission_closed")
        if request.get("model") != self.contract.model:
            self.stop("model_mismatch")
        bound = self.contract.input_bound(request)
        elapsed = self.clock() - self.started
        limits = self.limits
        if not math.isfinite(elapsed) or elapsed < 0:
            self.stop("clock_invalid")
        if (
            elapsed + limits.request_seconds > limits.task_seconds
            or self.elapsed + elapsed + limits.request_seconds
            > limits.aggregate_seconds
        ):
            self.stop("time_boundary")
        if (
            bound > limits.input_tokens
            or bound + limits.output_tokens > self.contract.context_limit
            or limits.output_tokens > self.contract.output_limit
        ):
            self.stop("context_or_output_limit")
        if (
            self.task_requests >= limits.task_requests
            or self.total_requests >= limits.aggregate_requests
        ):
            self.stop("request_limit")
        reservation = bound + limits.output_tokens
        now = self.clock()
        if any(at > now for at, _ in self.recent):
            self.stop("clock_reset_requires_review")
        recent = [(at, cost) for at, cost in self.recent if now - at < 60]
        if (
            len(recent) >= self.contract.requests_per_minute
            or sum(cost for _, cost in recent) + reservation
            > self.contract.tokens_per_minute
        ):
            self.stop("provider_rate_limit")
        if (
            self.task_tokens + reservation > self.allocation
            or self.total_tokens + reservation > limits.aggregate_tokens
        ):
            self.stop("token_limit")
        event = {
            "kind": "wire_reserved",
            "attempt": self.total_requests + 1,
            "task": self.current,
            "endpoint": endpoint,
            "input_bound": bound,
            "reservation": reservation,
            "at": now,
            "request": request,
        }
        self.append(event)
        self.pending = event
        self.recent.append((now, reservation))
        self.total_requests += 1
        self.task_requests += 1
        self.total_tokens += reservation
        self.task_tokens += reservation
        return event["attempt"]

    def settle(self, attempt, response, input_tokens, output_tokens, total_tokens):
        if self.stopped or not self.pending or self.pending["attempt"] != attempt:
            raise PilotStop("settlement_not_admissible")
        values = (input_tokens, output_tokens, total_tokens)
        if (
            any(type(v) is not int or v < 0 for v in values)
            or total_tokens != input_tokens + output_tokens
        ):
            self.stop("usage_unknown_or_inconsistent")
        if (
            input_tokens > self.pending["input_bound"]
            or output_tokens > self.limits.output_tokens
        ):
            self.stop("usage_exceeds_reservation")
        self.append(
            {
                "kind": "wire_settled",
                "attempt": attempt,
                "response": response,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }
        )
        adjustment = total_tokens - self.pending["reservation"]
        self.task_tokens += adjustment
        self.total_tokens += adjustment
        self.recent[-1] = (self.recent[-1][0], total_tokens)
        self.pending = None

    def finish(self):
        if self.stopped or self.pending:
            raise PilotStop("cannot_finish_stopped_task")
        self.append(
            {
                "kind": "task_finished",
                "task": self.current,
                "seconds": self.clock() - self.started,
            }
        )
        self.elapsed += self.clock() - self.started
        self.finished.append(self.current)
        self.current = None
