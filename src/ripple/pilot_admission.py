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
        sync_directory(path.parent)
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
    sync_directory(path.parent)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


MAX_OBSERVED_TOKENS = 2**63 - 1
OPERATIONS = {"interpret", "choose_next_action", "decide_candidates", "draft_report"}
STOP_REASONS = {
    "added_component_provenance_failed",
    "clock_invalid",
    "clock_reset_requires_review",
    "context_or_output_limit",
    "fixture_source_changed",
    "logical_capture_failed",
    "mock_expected_output_mismatch",
    "model_mismatch",
    "new_confirmation_created",
    "off_live_replay_mismatch",
    "ordinary_validation_failed",
    "original_component_changed",
    "original_rejections_changed",
    "provider_rate_limit",
    "replay_provenance_mismatch",
    "request_identity_changed",
    "request_limit",
    "response_size",
    "runtime_or_refs_changed",
    "terminal_summary_write_failed",
    "time_boundary",
    "token_limit",
    "unexpected_source_prediction",
    "usage_exceeds_reservation",
    "usage_observation_mismatch",
    "usage_unknown_or_inconsistent",
    "execution_interrupted",
    "request_deadline",
    "provider_failure_or_interruption",
    "capture_or_execution_failed",
    "response_incomplete",
    "response_model_mismatch",
    "capture_write_failed",
}
EVENT_FIELDS = {
    "task_started": ({"task", "allocation"}, set()),
    "wire_reserved": (
        {"attempt", "task", "endpoint", "input_bound", "reservation", "at", "request"},
        set(),
    ),
    "wire_observed": ({"attempt", "response"}, set()),
    "wire_metadata": (
        {
            "attempt",
            "requested_model",
            "returned_model",
            "fallback_derived_model",
            "model_origin",
            "usage_present",
        },
        {
            "invalid_returned_model",
            "completion_status",
            "invalid_completion_status",
            "incomplete_details",
            "choice_count",
            "finish_reasons",
            "usage",
            "invalid_usage_fields",
        },
    ),
    "wire_settled": (
        {"attempt", "response", "input_tokens", "output_tokens", "total_tokens"},
        {"accounted_tokens", "unknown_cost", "complete", "overrun"},
    ),
    "usage_unknown": ({"attempt"}, set()),
    "stop": ({"reason"}, {"task"}),
    "task_finished": ({"task", "seconds"}, {"summary_protocol", "summary_sha256"}),
    "logical_request": ({"operation", "prompt", "schema"}, set()),
    "logical_response": (
        {"operation", "prompt", "schema", "request_sha256", "response", "diagnostics"},
        set(),
    ),
}
SUMMARY_FIELDS = (
    "task",
    "allocation",
    "status",
    "reason",
    "attempts",
    "reserved_tokens",
    "accounted_tokens",
    "observed_usage",
    "observed_attempts",
    "unknown_cost",
    "unresolved_attempts",
    "evaluation_eligible",
)


class JournalError(PilotStop):
    """Only fixed reason codes and recognized event names reach diagnostics."""

    def __init__(self, code, sequence=None, kind=None):
        super().__init__(code)
        self.sequence = sequence
        self.kind = kind if isinstance(kind, str) and kind in EVENT_FIELDS else None


def integer(value, lower=0, upper=MAX_OBSERVED_TOKENS):
    return type(value) is int and lower <= value <= upper


def finite_number(value, upper=None):
    return (
        type(value) in {int, float}
        and 0 <= value <= MAX_OBSERVED_TOKENS
        and math.isfinite(value)
        and (upper is None or value <= upper)
    )


def identifier(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 160
        and all(
            char.isascii() and (char.isalnum() or char in "._-:/+") for char in value
        )
    )


def checksum(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def validate_event(event, sequence):
    """Validate trusted ledger fields; opaque provider bodies remain untrusted data."""
    kind = event.get("kind") if isinstance(event, dict) else None

    def require(condition):
        if not condition:
            raise JournalError("journal_event_schema", sequence, kind)

    require(isinstance(event, dict) and isinstance(kind, str) and kind in EVENT_FIELDS)
    required, optional = EVENT_FIELDS[kind]
    require(required <= event.keys() and event.keys() <= required | optional | {"kind"})
    if "task" in event:
        require((event["task"] is None and kind == "stop") or identifier(event["task"]))
    if "attempt" in event:
        require(integer(event["attempt"], 1, 72))
    if kind == "task_started":
        require(integer(event["allocation"], 1, 130000))
    elif kind == "wire_reserved":
        require(
            event["endpoint"] in {"responses", "chat_completions"}
            if isinstance(event["endpoint"], str)
            else False
        )
        require(
            integer(event["input_bound"], 1, 16000)
            and integer(event["reservation"], 1, 18000)
        )
        require(1 <= event["reservation"] - event["input_bound"] <= 2000)
        require(finite_number(event["at"]) and isinstance(event["request"], dict))
        require(identifier(event["request"].get("model")))
    elif kind in {"wire_observed", "wire_settled"}:
        require(isinstance(event["response"], dict))
        if kind == "wire_settled":
            require(
                all(
                    integer(event[k])
                    for k in ("input_tokens", "output_tokens", "total_tokens")
                )
            )
            require(
                event["input_tokens"] + event["output_tokens"] == event["total_tokens"]
            )
            if "accounted_tokens" in event:
                require(
                    integer(event["accounted_tokens"])
                    and event["accounted_tokens"] >= event["total_tokens"]
                )
            for key in ("unknown_cost", "complete", "overrun"):
                if key in event:
                    require(type(event[key]) is bool)
    elif kind == "wire_metadata":
        require(identifier(event["requested_model"]))
        require(
            all(
                event[key] is None or identifier(event[key])
                for key in ("returned_model", "fallback_derived_model")
            )
        )
        require(
            event["model_origin"] in {"provider", "configured_fallback"}
            if isinstance(event["model_origin"], str)
            else False
        )
        require(type(event["usage_present"]) is bool)
        for key in ("invalid_returned_model", "invalid_completion_status"):
            if key in event:
                require(type(event[key]) is bool)
        if "completion_status" in event:
            require(
                isinstance(event["completion_status"], str)
                and event["completion_status"]
                in {
                    "completed",
                    "incomplete",
                    "failed",
                    "cancelled",
                    "in_progress",
                    "queued",
                }
            )
        if "choice_count" in event:
            require(integer(event["choice_count"], 0, 10000))
        if "finish_reasons" in event:
            require(isinstance(event["finish_reasons"], list))
            require(
                all(
                    value is None
                    or isinstance(value, str)
                    and value
                    in {
                        "stop",
                        "length",
                        "tool_calls",
                        "content_filter",
                        "function_call",
                    }
                    for value in event["finish_reasons"]
                )
            )
        if "usage" in event:
            require(event["usage"] is None or isinstance(event["usage"], dict))

            def valid_usage(data, depth=0):
                require(depth <= 1)
                for key, value in data.items():
                    require(
                        isinstance(key, str)
                        and key
                        in {
                            "input_tokens",
                            "output_tokens",
                            "total_tokens",
                            "prompt_tokens",
                            "completion_tokens",
                            "cached_tokens",
                            "reasoning_tokens",
                            "audio_tokens",
                            "accepted_prediction_tokens",
                            "rejected_prediction_tokens",
                            "input_tokens_details",
                            "output_tokens_details",
                            "prompt_tokens_details",
                            "completion_tokens_details",
                        }
                    )
                    if isinstance(value, dict):
                        require(key.endswith("_details"))
                        valid_usage(value, depth + 1)
                    else:
                        require(value is None or integer(value))

            if isinstance(event["usage"], dict):
                valid_usage(event["usage"])
        if "invalid_usage_fields" in event:
            require(
                isinstance(event["invalid_usage_fields"], list)
                and all(
                    isinstance(v, str) and v.startswith("usage.") and len(v) < 120
                    for v in event["invalid_usage_fields"]
                )
            )
        if "incomplete_details" in event:
            require(
                isinstance(event["incomplete_details"], dict)
                and set(event["incomplete_details"]) == {"reason"}
            )
            require(
                event["incomplete_details"]["reason"]
                in {"max_output_tokens", "content_filter"}
                if isinstance(event["incomplete_details"]["reason"], str)
                else False
            )
    elif kind == "stop":
        require(isinstance(event["reason"], str) and event["reason"] in STOP_REASONS)
    elif kind == "task_finished":
        require(finite_number(event["seconds"], 2400))
        if "summary_protocol" in event or "summary_sha256" in event:
            require(
                type(event.get("summary_protocol")) is int
                and event["summary_protocol"] == 2
                and checksum(event.get("summary_sha256"))
            )
    elif kind in {"logical_request", "logical_response"}:
        require(
            isinstance(event["operation"], str) and event["operation"] in OPERATIONS
        )
        require(isinstance(event["prompt"], str) and isinstance(event["schema"], dict))
        if kind == "logical_response":
            require(
                checksum(event["request_sha256"])
                and isinstance(event["response"], dict)
                and isinstance(event["diagnostics"], list)
            )


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def decode_journal(data):
    events, previous = [], "0" * 64
    for sequence, line in enumerate(data.splitlines(keepends=True)):
        if not line.endswith(b"\n") or sequence >= 1024 or len(line) > 2 * 1024 * 1024:
            raise JournalError("journal_torn_or_oversized", sequence)
        kind = None
        try:
            record = json.loads(line, object_pairs_hook=unique_object)
            if not isinstance(record, dict) or set(record) != {
                "sequence",
                "previous",
                "event",
                "sha256",
            }:
                raise ValueError("envelope")
            event = record["event"]
            kind = event.get("kind") if isinstance(event, dict) else None
            claimed = record.pop("sha256")
            if (
                type(record["sequence"]) is not int
                or record["sequence"] != sequence
                or not checksum(claimed)
                or record["previous"] != previous
                or digest(record) != claimed
            ):
                raise ValueError("chain")
            validate_event(event, sequence)
            previous = claimed
            events.append(event)
        except JournalError:
            raise
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OverflowError,
            RecursionError,
        ):
            raise JournalError("journal_integrity", sequence, kind) from None
    return events, previous


def read_exact(fd, size):
    """A short read is not EOF; never validate only a prefix of durable evidence."""
    parts, remaining = [], size
    while remaining:
        part = os.read(fd, remaining)
        if not part or len(part) > remaining:
            raise JournalError("journal_truncated_read")
        parts.append(part)
        remaining -= len(part)
    return b"".join(parts)


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
            data = read_exact(self.fd, info.st_size)
            self.events, self.previous = decode_journal(data)
        except BaseException:
            os.close(self.fd)
            raise

    def append(self, event) -> None:
        validate_event(event, len(self.events))
        accounting_state([*self.events, event])
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


def read_journal(root: Path):
    """Read and lock an existing journal without creating or repairing anything."""
    root = safe_directory(root)
    fd = os.open(root / "journal.jsonl", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1
            or info.st_size > 64 * 1024 * 1024
        ):
            raise PilotStop("journal_permissions_or_size")
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        events, _ = decode_journal(read_exact(fd, info.st_size))
        return events
    finally:
        os.close(fd)


def observed_components(response):
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None
    counts = (
        usage.get("input_tokens", usage.get("prompt_tokens")),
        usage.get("output_tokens", usage.get("completion_tokens")),
        usage.get("total_tokens"),
    )
    return counts if all(integer(value) for value in counts) else None


def observed_counts(response):
    """Only consistent nonnegative integer counts constitute observed usage."""
    counts = observed_components(response)
    return counts if counts is not None and counts[0] + counts[1] == counts[2] else None


def completion_summary(row):
    """Prepared evidence never claims standalone evaluation eligibility."""
    result = {key: row[key] for key in SUMMARY_FIELDS}
    result.update(
        status="COMPLETION_PREPARED",
        reason="completion_prepared",
        evaluation_eligible=False,
        conflicted=row["conflicted"],
        completion_protocol=2,
    )
    return result


def read_private_artifact(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1
            or info.st_size > 2 * 1024 * 1024
        ):
            raise PilotStop("terminal_evidence_invalid")
        return read_exact(fd, info.st_size)
    finally:
        os.close(fd)


def verify_terminal_evidence(root, row, finish_event):
    """Bind the immutable artifact to the completion claim, including legacy files."""
    if root is None:
        return "unverified"
    failure = root / f"failure-{digest(row['task'])[:16]}.json"
    if failure.exists() or failure.is_symlink():
        return "failure_recorded"
    path = root / f"terminal-{digest(row['task'])[:16]}.json"
    try:
        data = read_private_artifact(path)
        if finish_event.get("summary_protocol") == 2:
            expected = canonical(completion_summary(row)) + b"\n"
            if (
                hashlib.sha256(data).hexdigest() != finish_event["summary_sha256"]
                or data != expected
            ):
                return "inconsistent"
        else:
            # Legacy summaries had no journal hash binding. Require every original
            # accounting field to match; missing legacy evidence is not grandfathered.
            expected = {key: row[key] for key in SUMMARY_FIELDS}
            expected["evaluation_eligible"] = True
            if data != canonical(expected) + b"\n":
                return "inconsistent"
    except FileNotFoundError:
        return "missing"
    except (OSError, PilotStop, ValueError, TypeError):
        return "invalid"
    return "verified"


def accounting_state(events, *, root=None):
    """Validate events, retain every usage observation and verify completion evidence."""
    tasks, attempts, finish_events = {}, {}, {}
    current, durable_stop, elapsed, logical = None, False, 0.0, None
    logical_attempt_start = 0

    def observe(attempt, counts, components=None):
        components = counts if components is None else components
        if components is not None:
            if (
                attempt["component_observations"]
                and components not in attempt["component_observations"]
            ):
                attempt["conflicted"] = True
            if components not in attempt["component_observations"]:
                attempt["component_observations"].append(components)
            attempt["maximum_observed"] = max(
                attempt["maximum_observed"], components[2]
            )
            attempt["accounted_tokens"] = max(
                attempt["accounted_tokens"], components[2]
            )
        if counts is None:
            attempt["usage_uncertain"] = True
            return
        if attempt["observed"] is None:
            attempt["observed"] = counts
        elif attempt["observed"] != counts:
            attempt["conflicted"] = True
        if counts not in attempt["observations"]:
            attempt["observations"].append(counts)
        attempt["maximum_observed"] = max(attempt["maximum_observed"], counts[2])
        attempt["accounted_tokens"] = max(attempt["accounted_tokens"], counts[2])

    for sequence, event in enumerate(events):
        validate_event(event, sequence)
        kind = event["kind"]

        def require(condition, reason, sequence=sequence, kind=kind):
            if not condition:
                raise JournalError(reason, sequence, kind)

        require(not durable_stop or kind == "stop", "event_after_stop")
        if kind == "task_started":
            task = event["task"]
            require(
                current is None and task not in tasks and logical is None,
                "unfinished_or_duplicate_task",
            )
            require(
                not any(
                    a["conflicted"] or a["unknown_cost"] for a in attempts.values()
                ),
                "task_after_uncertain_usage",
            )
            current = task
            tasks[task] = {
                "task": task,
                "allocation": event["allocation"],
                "status": "UNFINISHED",
                "reason": "execution_unfinished",
            }
        elif kind == "wire_reserved":
            number = event["attempt"]
            require(
                current is not None
                and event["task"] == current
                and number == len(attempts) + 1
                and not any(not a["settled"] for a in attempts.values()),
                "unfinished_or_duplicate_attempt",
            )
            existing = [a for a in attempts.values() if a["task"] == current]
            require(
                len(existing) < 24
                and not any(
                    a["conflicted"] or a["unknown_cost"] for a in attempts.values()
                ),
                "request_after_uncertain_usage",
            )
            require(
                sum(a["accounted_tokens"] for a in existing) + event["reservation"]
                <= tasks[current]["allocation"]
                and sum(a["accounted_tokens"] for a in attempts.values())
                + event["reservation"]
                <= 350000,
                "reservation_budget_invalid",
            )
            require(
                not attempts or event["at"] >= next(reversed(attempts.values()))["at"],
                "reservation_clock_invalid",
            )
            attempts[number] = {
                **event,
                "accounted_tokens": event["reservation"],
                "observed": None,
                "observations": [],
                "component_observations": [],
                "maximum_observed": 0,
                "conflicted": False,
                "usage_uncertain": False,
                "settled": False,
                "unknown_cost": True,
            }
        elif kind in {
            "wire_observed",
            "wire_metadata",
            "wire_settled",
            "usage_unknown",
        }:
            attempt = attempts.get(event["attempt"])
            require(
                attempt is not None
                and not attempt["settled"]
                and attempt["task"] == current,
                "settlement_without_attempt"
                if kind == "wire_settled"
                else "observation_without_attempt",
            )
            if kind in {"wire_observed", "wire_metadata"}:
                response = event["response"] if kind == "wire_observed" else event
                observe(
                    attempt, observed_counts(response), observed_components(response)
                )
            elif kind == "usage_unknown":
                attempt["usage_uncertain"] = True
            else:
                counts = observed_counts({"usage": event})
                require(counts is not None, "settlement_usage_invalid")
                overrun = (
                    counts[0] > attempt["input_bound"]
                    or counts[1] > attempt["reservation"] - attempt["input_bound"]
                )
                complete = event.get("complete", True)
                require(
                    "overrun" not in event or event["overrun"] == overrun,
                    "settlement_overrun_invalid",
                )
                require(
                    not (overrun or not complete) or event.get("unknown_cost", True),
                    "settlement_uncertainty_invalid",
                )
                declared_unknown = event.get("unknown_cost", overrun or not complete)
                expected = (
                    max(attempt["reservation"], counts[2])
                    if declared_unknown
                    else counts[2]
                )
                require(
                    event.get("accounted_tokens", expected) == expected,
                    "settlement_accounting_invalid",
                )
                observe(attempt, counts)
                uncertain = (
                    declared_unknown
                    or attempt["conflicted"]
                    or attempt["usage_uncertain"]
                )
                attempt.update(
                    settled=True,
                    unknown_cost=uncertain,
                    accounted_tokens=max(
                        expected, attempt["reservation"], attempt["maximum_observed"]
                    )
                    if uncertain
                    else expected,
                )
        elif kind == "logical_request":
            require(
                current is not None
                and logical is None
                and not any(not a["settled"] for a in attempts.values()),
                "logical_request_order",
            )
            logical = {key: event[key] for key in ("operation", "prompt", "schema")}
            logical_attempt_start = len(attempts)
        elif kind == "logical_response":
            require(
                len(attempts) == logical_attempt_start + 1,
                "logical_response_without_wire_attempt",
            )
            require(
                logical is not None
                and not any(not a["settled"] for a in attempts.values()),
                "logical_response_order",
            )
            require(
                all(event[key] == value for key, value in logical.items())
                and digest(logical) == event["request_sha256"],
                "logical_response_identity",
            )
            logical = None
        elif kind == "stop":
            task = (
                event.get("task")
                or current
                or (next(reversed(tasks)) if tasks else None)
            )
            require(task in tasks, "stop_without_task")
            durable_stop = True
            reason = event["reason"]
            tasks[task].update(
                status="CENSORED"
                if reason
                in {
                    "token_limit",
                    "request_limit",
                    "time_boundary",
                    "context_or_output_limit",
                    "provider_rate_limit",
                }
                else "INTERRUPTED"
                if reason == "execution_interrupted"
                else "FAILED",
                reason=reason,
            )
        elif kind == "task_finished":
            task = event["task"]
            require(
                task == current
                and logical is None
                and not any(not a["settled"] for a in attempts.values()),
                "unfinished_task_cannot_finish",
            )
            require(elapsed + event["seconds"] <= 7200, "completion_time_invalid")
            finish_events[task] = event
            tasks[task].update(status="COMPLETED", reason="task_finished")
            elapsed += event["seconds"]
            current = None
    finished = []
    blocked = durable_stop
    for task, row in tasks.items():
        rows = [a for a in attempts.values() if a["task"] == task]
        counts = [
            max(a["observations"], key=lambda c: c[2])
            for a in rows
            if a["observations"]
        ]
        conflicted = any(a["conflicted"] for a in rows)
        uncertain = any(
            a["unknown_cost"] or a["usage_uncertain"] or a["conflicted"] for a in rows
        )
        row.update(
            attempts=len(rows),
            reserved_tokens=sum(a["reservation"] for a in rows),
            accounted_tokens=sum(a["accounted_tokens"] for a in rows),
            observed_usage={
                key: sum(c[i] for c in counts)
                for i, key in enumerate(
                    ("input_tokens", "output_tokens", "total_tokens")
                )
            },
            observed_attempts=len(counts),
            unknown_cost=uncertain,
            conflicted=conflicted,
            unresolved_attempts=[a["attempt"] for a in rows if not a["settled"]],
            evaluation_eligible=False,
        )
        if conflicted:
            row.update(status="FAILED", reason="usage_observation_mismatch")
            blocked = True
        elif row["status"] == "COMPLETED" and uncertain:
            row.update(status="FAILED", reason="usage_unknown_or_inconsistent")
            blocked = True
        if row["status"] == "COMPLETED":
            proof = verify_terminal_evidence(root, row, finish_events[task])
            row["terminal_evidence"] = proof
            row["evaluation_eligible"] = proof == "verified"
            if proof == "verified":
                finished.append(task)
            elif root is not None:
                row.update(status="FAILED", reason="terminal_evidence_" + proof)
                blocked = True
        elif root is not None:
            marker = root / f"failure-{digest(task)[:16]}.json"
            if marker.exists() or marker.is_symlink():
                row.update(
                    status="FAILED",
                    reason="terminal_failure_recorded",
                    evaluation_eligible=False,
                )
                blocked = True
    return {
        "tasks": tasks,
        "attempts": attempts,
        "current": current,
        "stopped": blocked,
        "finished": finished,
        "elapsed": elapsed,
    }


def inspect_journal(root: Path):
    """No malformed evidence can become a successful inspection or a zero charge."""
    try:
        state = accounting_state(read_journal(root), root=root)
    except FileNotFoundError:
        raise
    except Exception as error:  # noqa: BLE001 - explicit fail-closed inspection boundary
        return {
            "mode": "OFFLINE_INSPECTION",
            "live_admission": "DISABLED",
            "status": "CORRUPT",
            "reason": str(error)
            if isinstance(error, JournalError)
            else "journal_reconstruction_error",
            "event_sequence": error.sequence
            if isinstance(error, JournalError)
            else None,
            "event_type": error.kind if isinstance(error, JournalError) else None,
            "attempts": None,
            "accounted_tokens": None,
            "unknown_cost": True,
            "evaluation_eligible": False,
        }
    rows = list(state["tasks"].values())
    return {
        "mode": "OFFLINE_INSPECTION",
        "live_admission": "DISABLED",
        "tasks": rows,
        "attempts": len(state["attempts"]),
        "accounted_tokens": sum(r["accounted_tokens"] for r in rows),
        "unknown_cost": any(r["unknown_cost"] for r in rows),
        "unfinished": state["current"] is not None,
        "stopped": state["stopped"],
        "evaluation_eligible": bool(rows)
        and all(r["evaluation_eligible"] for r in rows),
    }


class Admission:
    """Durable accounting; a stopped or unfinished journal never admits resumption."""

    def __init__(
        self, journal: Journal, contract: Contract, *, limits=None, clock=monotonic
    ):
        self.journal, self.contract = journal, contract
        self.limits, self.clock = limits or Limits(), clock
        self.last_reason = None
        self.last_task = None
        self.summary_error = False
        self.journal_write_uncertain = False
        self._restore()
        if self.stopped or self.pending or self.current:
            raise PilotStop("previous_run_incomplete_or_stopped")

    def _restore(self):
        state = accounting_state(self.journal.events, root=self.journal.root)
        self.last_task = next(reversed(state["tasks"]), None)
        self.current = state["current"]
        self.stopped = state["stopped"]
        self.finished = state["finished"]
        self.elapsed = state["elapsed"]
        attempts = list(state["attempts"].values())
        self.pending = next((a for a in attempts if not a["settled"]), None)
        self.total_requests = len(attempts)
        self.total_tokens = sum(a["accounted_tokens"] for a in attempts)
        self.recent = [(a["at"], a["accounted_tokens"]) for a in attempts]
        row = state["tasks"].get(self.current, {})
        self.task_requests = row.get("attempts", 0)
        self.task_tokens = row.get("accounted_tokens", 0)
        self.allocation = row.get("allocation", 0)

    def persist_summary(self, task=None, *, completion=False):
        """Write immutable prepared completion evidence or an ineligible outcome."""
        state = accounting_state(self.journal.events)
        task = task or self.current or self.last_task
        if task not in state["tasks"]:
            return None
        row = dict(state["tasks"][task])
        committed_v2 = any(
            event["kind"] == "task_finished"
            and event["task"] == task
            and event.get("summary_protocol") == 2
            for event in self.journal.events
        )
        if completion or row["status"] == "COMPLETED" and committed_v2:
            row = completion_summary(row)
        else:
            if self.last_reason and row["status"] in {"UNFINISHED", "COMPLETED"}:
                row.update(status="FAILED", reason=self.last_reason)
            if self.journal_write_uncertain:
                row.update(unknown_cost=True, journal_write_uncertain=True)
            row["evaluation_eligible"] = False
        data = canonical(row) + b"\n"
        path = self.journal.root / f"terminal-{digest(task)[:16]}.json"
        try:
            if (
                row["status"] == "COMPLETION_PREPARED"
                and committed_v2
                and not completion
            ):
                # A committed artifact may be checked, never silently reconstructed.
                if read_private_artifact(path) != data:
                    raise PilotStop("terminal_evidence_inconsistent")
            else:
                try:
                    private_write(path, row)
                except FileExistsError:
                    if read_private_artifact(path) != data:
                        raise PilotStop("terminal_evidence_inconsistent") from None
        except (OSError, PilotStop, ValueError, TypeError):
            self.summary_error = True
        if self.summary_error or self.journal_write_uncertain:
            self._failure_marker(task)
        return None if self.summary_error else hashlib.sha256(data).hexdigest()

    def _failure_marker(self, task):
        """An independent latch covers failure to append a journal failure event."""
        if task is None:
            return
        marker = self.journal.root / f"failure-{digest(task)[:16]}.json"
        try:
            private_write(
                marker,
                {
                    "task": task,
                    "reason": self.last_reason or "terminal_summary_write_failed",
                    "journal_write_uncertain": self.journal_write_uncertain,
                },
            )
        except FileExistsError:
            pass  # Existing failure evidence is never replaced or cleared.
        except (OSError, PilotStop):
            self.summary_error = True

    def stop(self, reason, *, task=None):
        task = task or self.current or self.last_task
        self.stopped, self.last_reason = True, reason
        try:
            self.journal.append({"kind": "stop", "reason": reason, "task": task})
        except BaseException:  # noqa: BLE001 - preserve failure when journal I/O fails
            self.journal_write_uncertain = True
        finally:
            self.persist_summary(task)
        raise PilotStop(reason) from None

    def append(self, event):
        try:
            self.journal.append(event)
        except BaseException:  # noqa: BLE001 - durable cancellation boundary
            self.stopped = True
            self.last_reason = "capture_write_failed"
            self.journal_write_uncertain = True
            self.persist_summary()
            raise PilotStop("capture_write_failed") from None
        self._restore()
        if any(
            row["conflicted"]
            for row in accounting_state(self.journal.events)["tasks"].values()
        ):
            self.stop("usage_observation_mismatch")

    def start(self, task: str, allocation: int):
        if self.stopped or self.current or task in self.finished:
            raise PilotStop("task_not_admissible")
        if not positive(allocation) or allocation > 130000:
            raise PilotStop("task_allocation")
        self.append({"kind": "task_started", "task": task, "allocation": allocation})
        self.started = self.clock()

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
        return event["attempt"]

    def settle(
        self,
        attempt,
        response,
        input_tokens,
        output_tokens,
        total_tokens,
        *,
        complete=True,
        stop_reason="response_incomplete",
    ):
        if self.stopped or not self.pending or self.pending["attempt"] != attempt:
            raise PilotStop("settlement_not_admissible")
        counts = observed_counts(
            {
                "usage": dict(
                    zip(
                        ("input_tokens", "output_tokens", "total_tokens"),
                        (input_tokens, output_tokens, total_tokens),
                    )
                )
            }
        )
        if counts is None:
            self.append({"kind": "usage_unknown", "attempt": attempt})
            self.stop("usage_unknown_or_inconsistent")
        if self.pending["observed"] is not None and self.pending["observed"] != counts:
            self.append(
                {
                    "kind": "wire_observed",
                    "attempt": attempt,
                    "response": {
                        "usage": dict(
                            zip(
                                ("input_tokens", "output_tokens", "total_tokens"),
                                counts,
                            )
                        )
                    },
                }
            )
            self.stop("usage_observation_mismatch")
        overrun = (
            input_tokens > self.pending["input_bound"]
            or output_tokens > self.limits.output_tokens
        )
        usage_uncertain = self.pending["usage_uncertain"]
        uncertain = overrun or not complete or usage_uncertain
        accounted = (
            max(self.pending["reservation"], total_tokens)
            if uncertain
            else total_tokens
        )
        self.append(
            {
                "kind": "wire_settled",
                "attempt": attempt,
                "response": response,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "accounted_tokens": accounted,
                "unknown_cost": uncertain,
                "complete": complete,
                "overrun": overrun,
            }
        )
        if usage_uncertain:
            self.stop("usage_unknown_or_inconsistent")
        if overrun:
            self.stop("usage_exceeds_reservation")
        if not complete:
            self.stop(stop_reason)

    def finish(self):
        if self.stopped or self.pending or self.current is None:
            raise PilotStop("cannot_finish_stopped_task")
        task = self.current
        row = accounting_state(self.journal.events)["tasks"][task]
        if row["unknown_cost"] or row["conflicted"]:
            self.stop("usage_unknown_or_inconsistent", task=task)
        summary_sha256 = self.persist_summary(task, completion=True)
        if summary_sha256 is None:
            self.stop("terminal_summary_write_failed", task=task)
        self.append(
            {
                "kind": "task_finished",
                "task": task,
                "seconds": self.clock() - self.started,
                "summary_protocol": 2,
                "summary_sha256": summary_sha256,
            }
        )
        if task not in self.finished:
            self.stop("terminal_summary_write_failed", task=task)
