"""Isolated P2B capture/admission/replay harness; default command is offline.

The production controller, prompts, validators and provider defaults are untouched.
Live execution requires a reviewed UB deployment contract and a separate approval.
"""

import argparse
import copy
import dataclasses
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from ripple.agent import RIPPLE_V2, AgentController
from ripple.agent_models import (
    AgentDecision,
    CandidateDecisionSet,
    FeatureIntent,
    FeatureRequest,
    ReportDraft,
)
from ripple.llm import LLMResponse, OpenAILLM, RetryPolicy
from ripple.pilot_admission import (
    Admission,
    Contract,
    Journal,
    PilotStop,
    canonical,
    digest,
    inspect_journal,
    private_write,
)
from ripple.scanner import scan_repository
from ripple.validate import validate_report_draft

BASE = "0c1d96371451033f7f317ef030070740229f4255"
MODEL = "openai/gpt-oss-20b"
URL = "https://gateway.bullsai.buffalo.edu/v1"
TASKS = (
    "P2B-DEV-001-STOCK-LOW-WATERMARK",
    "P2B-DEV-002-RESERVATION-CANCEL-NOTIFY",
    "P2B-DEV-003-OUT-OF-REPO-IAM",
)
SCHEMAS = {
    "interpret": FeatureIntent,
    "choose_next_action": AgentDecision,
    "decide_candidates": CandidateDecisionSet,
    "draft_report": ReportDraft,
}


@contextmanager
def offline():
    """Pilot replays cannot connect sockets, even if a wrong client is supplied."""

    def refused(*args, **kwargs):
        raise PilotStop("offline_socket_blocked")

    methods = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.create_connection,
    )
    credentials = {
        key: value
        for key, value in os.environ.items()
        if any(
            part in key.upper() for part in ("API_KEY", "ACCESS_TOKEN", "AUTH_TOKEN")
        )
    }
    for key in credentials:
        del os.environ[key]
    socket.socket.connect = refused
    socket.socket.connect_ex = refused
    socket.create_connection = refused
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = (
            methods
        )
        os.environ.update(credentials)


def primitive(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return primitive(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): primitive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [primitive(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(primitive(v) for v in value)
    if isinstance(value, Path):
        return str(value)
    return value


def provenance(controller):
    return primitive(
        {
            "index": controller.index,
            "candidates": controller.ledger.candidates,
            "evidence": controller.ledger.evidence,
            "presented": controller.presented,
            "cache": controller.cache,
            "offered_complete": controller.offered_complete,
        }
    )


def semantic(report):
    data = report.model_dump(mode="json")
    del data["report_id"]
    del data["run_stats"]["runtime_seconds"]
    return data


@contextmanager
def execution_interrupts():
    """Graceful termination records a terminal outcome; SIGKILL remains inspectable."""
    previous = signal.getsignal(signal.SIGTERM)

    def interrupted(*args):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextmanager
def request_deadline(seconds):
    """An absolute deadline, in addition to SDK per-I/O timeouts (main thread)."""
    if signal.getitimer(signal.ITIMER_REAL)[0]:
        raise PilotStop("existing_deadline_requires_review")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(*args):
        raise TimeoutError("pilot_deadline")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def protected_roots():
    """All registered repositories and their shared Git storage are protected."""
    root = Path(__file__).resolve().parents[2]
    common = Path(
        subprocess.check_output(
            [
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ]
        )
        .decode()
        .strip()
    )
    entries = (
        subprocess.check_output(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"]
        )
        .decode()
        .splitlines()
    )
    return [root, common, common.parent] + [
        Path(line[len("worktree ") :])
        for line in entries
        if line.startswith("worktree ")
    ]


def validate_output_destination(output, package, manifest):
    """Check disjoint canonical paths before creating any output artifact."""
    path = output.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise PilotStop("output_symlink")
    destination = path.resolve()
    protected = [*protected_roots(), package]
    protected.extend(package / row["fixture"] for row in manifest["tasks"])
    for entry in protected:
        entry = entry.resolve()
        if (
            destination == entry
            or destination in entry.parents
            or entry in destination.parents
        ):
            raise PilotStop("output_overlaps_protected_path")
    return destination


def verify_evaluation_entries(root, files):
    """Frozen trees reject added files, directories and symlinks as well as edits."""
    expected = set()
    for name in files:
        path = Path(name)
        expected.add(path.as_posix())
        expected.update(p.as_posix() for p in path.parents if p != Path("."))
    actual = {p.relative_to(root).as_posix() for p in (root / "evaluation").rglob("*")}
    actual.add("evaluation")
    if actual != expected or any((root / name).is_symlink() for name in actual):
        raise PilotStop("frozen_evaluation_entries_changed")


def response_metadata(endpoint, requested_model, raw):
    """Allowlisted provider metadata; missing fields are never supplied defaults."""
    returned = raw.get("model")
    invalid_model = returned is not None and (
        not isinstance(returned, str)
        or re.fullmatch(r"[A-Za-z0-9._:/+-]{1,160}", returned) is None
    )
    if not isinstance(returned, str) or invalid_model:
        returned = None
    result = {
        "requested_model": requested_model,
        "returned_model": returned,
        "fallback_derived_model": requested_model if returned is None else None,
        "model_origin": "provider" if returned is not None else "configured_fallback",
        "usage_present": "usage" in raw,
    }
    if invalid_model:
        result["invalid_returned_model"] = True
    if endpoint == "responses":
        if raw.get("status") in {
            "completed",
            "incomplete",
            "failed",
            "cancelled",
            "in_progress",
            "queued",
        }:
            result["completion_status"] = raw["status"]
        elif "status" in raw:
            result["invalid_completion_status"] = True
        details = raw.get("incomplete_details")
        if isinstance(details, dict) and details.get("reason") in {
            "max_output_tokens",
            "content_filter",
        }:
            result["incomplete_details"] = {"reason": details["reason"]}
    else:
        result["choice_count"] = len(raw.get("choices", []))
        result["finish_reasons"] = [
            choice["finish_reason"]
            for choice in raw.get("choices", [])
            if isinstance(choice, dict)
            and choice.get("finish_reason")
            in {"stop", "length", "tool_calls", "content_filter", "function_call", None}
            and "finish_reason" in choice
        ]
    usage = raw.get("usage")
    allowed = {
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
    }
    detail_names = {
        "input_tokens_details",
        "output_tokens_details",
        "prompt_tokens_details",
        "completion_tokens_details",
    }
    invalid = []

    def filtered(data, prefix="usage"):
        output = {}
        for key, value in data.items():
            if key in allowed:
                if value is None or (type(value) is int and value >= 0):
                    output[key] = value
                else:
                    invalid.append(prefix + "." + key)
            elif key in detail_names:
                if isinstance(value, dict):
                    output[key] = filtered(value, prefix + "." + key)
                elif value is None:
                    output[key] = None
                else:
                    invalid.append(prefix + "." + key)
        return output

    if isinstance(usage, dict):
        result["usage"] = filtered(usage)
    elif "usage" in raw:
        result["usage"] = None
    if invalid:
        result["invalid_usage_fields"] = invalid
    return result


class WireEndpoint:
    """Cap and account every SDK wire create, including endpoint fallback.

    HTTP exceptions never expose their text and never trigger another wire call.
    Even a 404 latches closed until UB's billing semantics are established.
    """

    def __init__(self, gate: Admission, endpoint: str, send):
        self.gate, self.endpoint, self.send = gate, endpoint, send

    def create(self, **kwargs):
        gate = self.gate
        if gate.stopped:
            raise PilotStop("admission_closed")
        check = getattr(gate, "preflight", None)
        if check is not None:
            check()
        field = gate.contract.output_parameters[self.endpoint]
        kwargs = copy.deepcopy(kwargs)
        kwargs[field] = gate.limits.output_tokens
        attempt = gate.reserve(self.endpoint, kwargs)
        started = gate.clock()
        try:
            with request_deadline(gate.limits.request_seconds):
                response = self.send(**kwargs)
            raw = response.model_dump(mode="json", exclude_unset=True)
            # Persist safe usage before a body-size or capture failure can reject it.
            metadata = response_metadata(self.endpoint, kwargs["model"], raw)
            gate.append({"kind": "wire_metadata", "attempt": attempt, **metadata})
            if len(canonical(raw)) > 2 * 1024 * 1024:
                gate.stop("response_size")
            gate.append({"kind": "wire_observed", "attempt": attempt, "response": raw})
            if self.endpoint == "responses":
                complete = raw.get("status") == "completed"
            else:
                complete = (
                    len(raw.get("choices", [])) == 1
                    and raw["choices"][0].get("finish_reason") == "stop"
                )
            reason = "response_incomplete"
            if metadata["returned_model"] != gate.contract.model:
                complete, reason = False, "response_model_mismatch"
            if gate.clock() - started >= gate.limits.request_seconds:
                complete, reason = False, "request_deadline"
            usage = raw.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            gate.settle(
                attempt,
                raw,
                usage.get("input_tokens", usage.get("prompt_tokens")),
                usage.get("output_tokens", usage.get("completion_tokens")),
                usage.get("total_tokens"),
                complete=complete,
                stop_reason=reason,
            )
            return response
        except PilotStop:
            raise
        except BaseException as error:  # noqa: BLE001 - cancellations latch too
            reason = (
                "execution_interrupted"
                if isinstance(error, (KeyboardInterrupt, SystemExit))
                else "request_deadline"
                if isinstance(error, TimeoutError)
                else "provider_failure_or_interruption"
            )
            gate.stop(reason)


def attach_gate(client: OpenAILLM, gate: Admission):
    if client.retry_policy.allow_request_replay:
        raise PilotStop("transient_retry_enabled")
    raw = client._client
    if getattr(raw, "max_retries", 0) != 0:
        raise PilotStop("sdk_retry_enabled")
    if not gate.contract.mock:
        http = getattr(raw, "_client", None)
        if http is None or not hasattr(http, "follow_redirects"):
            raise PilotStop("http_transport_unverified")
        http.follow_redirects = False
        if http.follow_redirects is not False:
            raise PilotStop("http_redirects_enabled")
    client._client = SimpleNamespace(
        responses=WireEndpoint(gate, "responses", raw.responses.create),
        chat=SimpleNamespace(
            completions=WireEndpoint(
                gate, "chat_completions", raw.chat.completions.create
            )
        ),
    )
    return client


class CaptureClient:
    def __init__(self, client, gate: Admission):
        self.client, self.gate, self.model = client, gate, client.model
        self.records = []
        self.diagnostics = []

    def __getattr__(self, operation):
        if operation not in SCHEMAS:
            raise AttributeError(operation)

        def call(prompt):
            request = {
                "operation": operation,
                "prompt": prompt,
                "schema": SCHEMAS[operation].model_json_schema(),
            }
            self.gate.append({"kind": "logical_request", **request})
            record = {**request, "request_sha256": digest(request)}
            try:
                response = getattr(self.client, operation)(prompt)
                record["response"] = primitive(response)
                self.diagnostics = self.client.drain_diagnostics()
                record["diagnostics"] = self.diagnostics
                self.gate.append({"kind": "logical_response", **record})
                self.records.append(copy.deepcopy(record))
                return response
            except BaseException:  # noqa: BLE001 - latch admission on cancellation too
                if not self.gate.stopped:
                    self.gate.stop("logical_capture_failed")
                raise PilotStop("capture_or_provider_stopped") from None

        return call

    def drain_diagnostics(self):
        result, self.diagnostics = self.diagnostics, []
        return result


class ReplayClient:
    def __init__(self, records, model):
        self.records, self.model = copy.deepcopy(records), model
        self.position = 0
        self.failed = False
        self.diagnostics = []

    def __getattr__(self, operation):
        if operation not in SCHEMAS:
            raise AttributeError(operation)

        def call(prompt):
            request = {
                "operation": operation,
                "prompt": prompt,
                "schema": SCHEMAS[operation].model_json_schema(),
            }
            if self.failed or self.position >= len(self.records):
                self.failed = True
                raise PilotStop("replay_exhausted")
            record = self.records[self.position]
            if digest(request) != record["request_sha256"] or any(
                request[k] != record[k] for k in request
            ):
                self.failed = True
                raise PilotStop("replay_request_mismatch")
            self.position += 1
            self.diagnostics = copy.deepcopy(record["diagnostics"])
            return LLMResponse(**copy.deepcopy(record["response"]))

        return call

    def drain_diagnostics(self):
        result, self.diagnostics = self.diagnostics, []
        return result

    def verify_complete(self):
        if self.failed or self.position != len(self.records):
            raise PilotStop("replay_not_identical")


def package_file(package, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PilotStop("package_path")
    file = package.absolute() / path
    if (
        any(p.is_symlink() for p in (file, *file.parents))
        or not file.is_file()
        or file.stat().st_size > 2 * 1024 * 1024
    ):
        raise PilotStop("package_file_path_or_size")
    return file


def verify_package(package: Path):
    if any(p.is_symlink() for p in (package.absolute(), *package.absolute().parents)):
        raise PilotStop("package_symlink")
    manifest = json.loads(package_file(package, "tasks.json").read_text())
    rows = manifest["tasks"]
    if (
        tuple(row["task_id"] for row in rows) != TASKS
        or [row["token_allocation"] for row in rows] != [110000, 130000, 110000]
        or manifest["p2a_commit"] != BASE
        or manifest["planning_reserve_tokens"] != 350000
        or manifest["maximum_wire_requests"] != 72
    ):
        raise PilotStop("task_contract_mismatch")
    for row in rows:
        for field in ("fixture", "source_manifest", "oracle_file"):
            path = Path(row[field])
            if path.is_absolute() or ".." in path.parts:
                raise PilotStop("package_path")
        sums = package_file(package, row["source_manifest"])
        if (
            hashlib.sha256(sums.read_bytes()).hexdigest()
            != row["source_manifest_sha256"]
        ):
            raise PilotStop("source_manifest_hash")
        source = package / row["fixture"]
        names = set()
        for line in sums.read_text().splitlines():
            expected, name = line.split("  ", 1)
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or name in names:
                raise PilotStop("source_manifest_path")
            names.add(name)
            file = package_file(package, Path(row["fixture"]) / path)
            if any(p.is_symlink() for p in (file, *file.parents)):
                raise PilotStop("source_symlink")
            if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                raise PilotStop("source_file_hash")
        if names != {
            p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()
        }:
            raise PilotStop("unlisted_source")
        oracle = json.loads(package_file(package, row["oracle_file"]).read_text())
        if (
            oracle["task_id"] != row["task_id"]
            or oracle["excluded_from_model_context"] is not True
        ):
            raise PilotStop("oracle_contract")
        if any(
            not (source / p).is_file() or p.startswith("tests/")
            for p in oracle["expected_existing_source_paths"]
        ):
            raise PilotStop("oracle_source")
    return manifest


def fixture_index(package, task, root):
    destination = root / "source"
    shutil.copytree(package / task["fixture"], destination)
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "/usr/bin/false",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
    }
    for args in (
        ("init", "-q"),
        ("add", "."),
        ("commit", "-qm", "P2B synthetic input"),
    ):
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=RIPPLE Fixture",
                "-c",
                "user.email=ripple@example.test",
                "-C",
                str(destination),
                *args,
            ],
            env=env,
            check=True,
            capture_output=True,
        )
    index = scan_repository(destination)
    if index.dirty:
        raise PilotStop("fixture_dirty")
    return index


def run_pair(index, task, capture, root, *, variant=RIPPLE_V2):
    request = FeatureRequest(text=task["request"])
    identity = source_identity(index)

    def controller(client, enabled, label):
        return AgentController(
            index,
            client,
            variant=variant,
            retain_confirmed_report=enabled,
            max_tokens=task["token_allocation"],
            output_root=root / label,
        )

    live = controller(capture, False, "captured-off")
    live_run = live.run(request)
    if capture.gate.stopped:
        raise PilotStop("captured_trajectory_failed")
    if source_identity(index) != identity:
        capture.gate.stop("fixture_source_changed")
    state = provenance(live)
    trajectory = {
        "model": capture.model,
        "records": capture.records,
        "source_commit": index.commit,
        "source_index": primitive(index),
        "provenance": state,
        "report": semantic(live_run.report),
    }
    private_write(
        root / "trajectory.json",
        trajectory,
    )
    private_write(root / "trajectory.sha256.json", {"sha256": digest(trajectory)})
    outputs = []
    for enabled in (False, True):
        if source_identity(index) != identity:
            capture.gate.stop("fixture_source_changed")
        sealed = load_trajectory(root)
        replay = ReplayClient(sealed["records"], sealed["model"])
        engine = controller(replay, enabled, "replay-on" if enabled else "replay-off")
        with offline():
            run = engine.run(request)
        replay.verify_complete()
        if provenance(engine) != state or source_identity(index) != identity:
            capture.gate.stop("replay_provenance_mismatch")
        outputs.append(run.report)
    off, on = outputs
    if semantic(off) != semantic(live_run.report):
        capture.gate.stop("off_live_replay_mismatch")
    original = {c.target: c for c in off.affected_components}
    after = {c.target: c for c in on.affected_components}
    if any(after.get(k) != value for k, value in original.items()):
        capture.gate.stop("original_component_changed")
    if tuple(on.dropped_claims[: len(off.dropped_claims)]) != off.dropped_claims:
        capture.gate.stop("original_rejections_changed")
    added = set(after) - set(original)
    if not added <= {c.target for c in live.ledger.confirmed()}:
        capture.gate.stop("new_confirmation_created")
    checked = validate_report_draft(
        ReportDraft(affected_components=on.affected_components), index, live.ledger
    )
    if checked.components != on.affected_components:
        capture.gate.stop("ordinary_validation_failed")
    for target in added:
        for evidence_id in after[target].evidence:
            evidence = live.ledger.evidence.get(evidence_id)
            if (
                not evidence
                or not evidence.result.ok
                or evidence.evidence_id != evidence.result.evidence_id
                or target not in evidence.touched_targets
                or evidence_id not in live.presented.get(target, ())
            ):
                capture.gate.stop("added_component_provenance_failed")
    return {
        "task_id": task["task_id"],
        "off": semantic(off),
        "on": semantic(on),
        "same_recorded_responses": True,
        "off_live_replay_identical": True,
        "provenance_identical": True,
        "source_commit": index.commit,
        "response_stream_sha256": digest(capture.records),
        "provenance_sha256": digest(state),
        "retained_targets": sorted(added),
        "wire_requests": capture.gate.task_requests,
        "tokens": capture.gate.task_tokens,
        "additional_inference_for_replay": 0,
    }


def load_trajectory(root):
    path = root / "trajectory.json"
    if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
        raise PilotStop("trajectory_path_or_size")
    data = json.loads(path.read_text())
    if (
        digest(data)
        != json.loads((root / "trajectory.sha256.json").read_text())["sha256"]
    ):
        raise PilotStop("trajectory_integrity")
    return data


def package_hash(package):
    return digest(
        {
            p.relative_to(package).as_posix(): hashlib.sha256(
                package_file(package, p.relative_to(package)).read_bytes()
            ).hexdigest()
            for p in sorted(package.rglob("*"))
            if p.is_file()
        }
    )


def source_identity(index):
    def git(*args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                str(index.repo_root),
                *args,
            ]
        )

    if git("rev-parse", "HEAD").decode().strip() != index.commit or git(
        "status", "--porcelain=v1", "-uall"
    ):
        raise PilotStop("fixture_dirty_or_commit_changed")
    files = {}
    for name in git("ls-files", "-z").decode().split("\0")[:-1]:
        path = index.repo_root / name
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise PilotStop("fixture_symlink")
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest(files)


def verify_runtime():
    """Every inherited runtime/evaluation byte must match the P2A commit."""
    root = Path(__file__).resolve().parents[2]

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])

    if subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", BASE, "HEAD"],
        check=False,
        capture_output=True,
    ).returncode:
        raise PilotStop("runtime_base_mismatch")
    evaluation_files = (
        git("ls-tree", "-r", "--name-only", BASE, "evaluation").decode().splitlines()
    )
    verify_evaluation_entries(root, evaluation_files)
    for entry in git("ls-tree", "-r", "-z", BASE, "src/ripple", "evaluation").split(
        b"\0"
    ):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        path = root / name.decode()
        body = path.read_bytes()
        oid = hashlib.sha1(
            b"blob " + str(len(body)).encode() + b"\0" + body
        ).hexdigest()
        if path.is_symlink() or oid != meta.split()[2].decode():
            raise PilotStop("inherited_runtime_or_frozen_modified")
    refs = {
        line.split()[1]: line.split()[0]
        for line in git("show-ref").decode().splitlines()
        if not line.split()[1].startswith("refs/codex/")
    }
    return {"refs_sha256": digest(refs), "base": BASE}


class MockBody(SimpleNamespace):
    def model_dump(self, **kwargs):
        return {
            "model": self.model,
            "output_text": self.output_text,
            "status": self.status,
            "usage": vars(self.usage) if self.usage else None,
        }


class MockProvider:
    """Explicit synthetic response policy, never used by execute-one."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.position = 0
        self.calls = 0

    def create(self, **request):
        self.calls += 1
        prompt = request["input"]
        schema = request["text"]["format"]["name"]
        match = re.search(
            r"<repository_data>\n(.*)\n</repository_data>", prompt, re.DOTALL
        )
        data = json.loads(match.group(1)) if match and schema != "featureintent" else {}
        if schema == "featureintent":
            payload = {
                "summary": "Synthetic development diagnostic",
                "change_kinds": ["business_logic"],
                "search_terms": self.scenario["search_terms"],
            }
        elif schema == "agentdecision":
            plan = self.scenario["steps"]
            if self.position >= len(plan) and data.get("submission_ready"):
                payload = {
                    "tool_name": "submit_report",
                    "arguments": {},
                    "reason": "mock complete",
                }
            else:
                tool, arguments = plan[min(self.position, len(plan) - 1)]
                self.position += 1
                payload = {
                    "tool_name": tool,
                    "arguments": arguments,
                    "reason": "mock investigation",
                    "ledger_updates": [],
                }
        elif schema == "candidatedecisionset":
            decisions = []
            for item in data["candidates"]:
                support = [e["evidence_id"] for e in item["evidence"] if e["strong"]]
                relevant = item["target"] in self.scenario["confirmed_targets"]
                decisions.append(
                    {
                        "target": item["target"],
                        "decision": "confirm" if relevant and support else "keep",
                        "evidence_ids": support[:1] if relevant else [],
                        "reason": "mock policy",
                    }
                )
            payload = {"decisions": decisions}
        else:
            payload = {"affected_components": []}
        return MockBody(
            model=request["model"],
            output_text=json.dumps(payload),
            status="completed",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
        )


def mock_client(gate, scenario):
    # No SDK initialization, credential lookup or network-capable transport.
    provider = MockProvider(scenario)
    client = object.__new__(OpenAILLM)
    from contextvars import ContextVar

    client.model = gate.contract.model
    client.retry_policy = RetryPolicy(allow_request_replay=False)
    client._request_state = ContextVar("pilot_mock", default=None)
    client._client = SimpleNamespace(
        responses=provider, chat=SimpleNamespace(completions=provider)
    )
    return CaptureClient(attach_gate(client, gate), gate), provider


def mock_contract():
    return Contract(
        "mock-p2b",
        18000,
        2000,
        4096,
        {"responses": "max_output_tokens", "chat_completions": "max_completion_tokens"},
        mock=True,
    )


def expected_comparison(package, task, result):
    """Read sealed labels only after capture/replay, never into a model prompt."""
    oracle = json.loads((package / task["oracle_file"]).read_text())
    expected = set(oracle["expected_existing_source_paths"])
    scores = {}
    for arm in ("off", "on"):
        actual = {
            c["target"].partition("::")[0] for c in result[arm]["affected_components"]
        }
        scores[arm] = {
            "source_paths": sorted(actual),
            "true_positive": len(actual & expected),
            "false_positive": len(actual - expected),
            "false_negative": len(expected - actual),
            "exact_source_set": actual == expected,
        }
    return {"expected_source_paths": sorted(expected), "comparisons": scores}


@execution_interrupts()
def dry_run(package, output):
    manifest = verify_package(package)
    output = validate_output_destination(output, package, manifest)
    journal = Journal(output)
    try:
        gate = Admission(journal, mock_contract())
        scenarios = json.loads((package / "mock_scenarios.json").read_text())
        results = []
        with offline():
            for task in manifest["tasks"]:
                folder = output / task["task_id"]
                folder.mkdir(mode=0o700)
                index = fixture_index(package, task, folder)
                gate.start(task["task_id"], task["token_allocation"])
                capture, provider = mock_client(gate, scenarios[task["task_id"]])
                result = run_pair(index, task, capture, folder)
                oracle = json.loads((package / task["oracle_file"]).read_text())
                expected = set(oracle["expected_existing_source_paths"])
                actual = {
                    c["target"].partition("::")[0]
                    for c in result["on"]["affected_components"]
                }
                if actual != expected or provider.calls != result["wire_requests"]:
                    gate.stop("mock_expected_output_mismatch")
                result.update(
                    {
                        "expected_source_paths": sorted(expected),
                        "on_source_paths": sorted(actual),
                        "mock_expectations_passed": True,
                        "live_inference_calls": 0,
                    }
                )
                private_write(folder / "result.json", result)
                gate.finish()
                results.append(result)
        summary = {
            "mode": "MOCK_ONLY",
            "live_inference_calls": 0,
            "mock_wire_attempts": gate.total_requests,
            "mock_tokens": gate.total_tokens,
            "tasks": results,
            "live_admission": "BLOCKED_UNVERIFIED_CONTRACT",
        }
        private_write(output / "dry_run_results.json", summary)
        return summary
    except BaseException as error:
        if "gate" in locals() and gate.current and not gate.stopped:
            reason = (
                "execution_interrupted"
                if isinstance(error, (KeyboardInterrupt, SystemExit))
                else "capture_or_execution_failed"
            )
            try:
                gate.stop(reason)
            except PilotStop:
                pass
        raise
    finally:
        if "gate" in locals():
            gate.persist_summary()
        journal.close()


@execution_interrupts()
def execute_one(package, output, task_id, contract_path, approval_path):
    manifest = verify_package(package)
    output = validate_output_destination(output, package, manifest)
    contract = Contract.load(
        contract_path
    )  # Fail before credentials or SDK initialization.
    approval = json.loads(approval_path.read_text())
    if approval.get("approved") is not True or approval.get(
        "package_sha256"
    ) != package_hash(package):
        raise PilotStop("execution_not_approved")
    if (
        approval.get("contract_sha256")
        != hashlib.sha256(contract_path.read_bytes()).hexdigest()
    ):
        raise PilotStop("contract_not_approved")
    if approval.get("harness_sha256") != harness_hash():
        raise PilotStop("harness_not_approved")
    if approval.get("output_directory") != str(output.absolute()):
        raise PilotStop("pilot_ledger_directory_not_approved")
    runtime = verify_runtime()
    if approval.get("refs_sha256") != runtime["refs_sha256"]:
        raise PilotStop("refs_not_approved")
    journal = Journal(output)
    try:
        gate = Admission(journal, contract)
        phase = approval.get("phase")
        if phase == "first":
            if (
                task_id != TASKS[0]
                or gate.finished
                or approval.get("tasks") != [TASKS[0]]
            ):
                raise PilotStop("first_task_only")
        elif phase == "remaining":
            first = output / TASKS[0] / "result.json"
            if (
                task_id not in TASKS[1:]
                or TASKS[0] not in gate.finished
                or approval.get("tasks") != list(TASKS[1:])
                or hashlib.sha256(first.read_bytes()).hexdigest()
                != approval.get("reviewed_first_result_sha256")
                or (task_id == TASKS[2] and TASKS[1] not in gate.finished)
            ):
                raise PilotStop("first_result_review_required")
        else:
            raise PilotStop("execution_phase_unapproved")
        task = next(row for row in manifest["tasks"] if row["task_id"] == task_id)
        folder = output / task_id
        folder.mkdir(mode=0o700)
        index = fixture_index(package, task, folder)
        gate.start(task_id, task["token_allocation"])
        original_fixture = source_identity(index)
        original_package = package_hash(package)

        def preflight():
            if (
                verify_runtime() != runtime
                or harness_hash() != approval["harness_sha256"]
                or source_identity(index) != original_fixture
                or package_hash(package) != original_package
            ):
                gate.stop("request_identity_changed")

        gate.preflight = preflight
        verify_environment()
        client = OpenAILLM(
            api_key=os.environ.get("RIPPLE_LLM_API_KEY", ""),
            model=MODEL,
            base_url=URL,
            retry_policy=RetryPolicy(allow_request_replay=False),
        )
        capture = CaptureClient(attach_gate(client, gate), gate)
        result = run_pair(index, task, capture, folder)
        result.update(expected_comparison(package, task, result))
        private_write(folder / "result.json", result)
        if result["comparisons"]["on"]["false_positive"]:
            gate.stop("unexpected_source_prediction")
        if verify_runtime() != runtime:
            gate.stop("runtime_or_refs_changed")
        gate.finish()
        return result
    except BaseException as error:
        if "gate" in locals() and gate.current and not gate.stopped:
            reason = (
                "execution_interrupted"
                if isinstance(error, (KeyboardInterrupt, SystemExit))
                else "capture_or_execution_failed"
            )
            try:
                gate.stop(reason)
            except PilotStop:
                pass
        raise
    finally:
        if "gate" in locals():
            gate.persist_summary()
        journal.close()


def verify_environment():
    """Refuse live SDK construction outside the reviewed locked environment."""
    import platform
    from importlib.metadata import PackageNotFoundError, version

    root = Path(__file__).resolve().parents[2]
    specification = json.loads(
        (root / "requirements/p2b-hardening-environment.json").read_text()
    )
    if (
        platform.python_version() != specification["python"]
        or platform.system() != "Darwin"
        or platform.machine() != "arm64"
    ):
        raise PilotStop("runtime_environment_unverified")
    for package in specification["packages"]:
        try:
            installed = version(package["name"])
        except PackageNotFoundError:
            raise PilotStop("runtime_environment_unverified") from None
        if installed != package["version"]:
            raise PilotStop("runtime_environment_unverified")
    return digest(specification)


def harness_hash():
    root = Path(__file__).resolve().parents[2]
    return digest(
        {
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                Path(__file__),
                Path(__file__).with_name("pilot_admission.py"),
                root / "requirements/p2b-hardening.lock",
                root / "requirements/p2b-hardening-environment.json",
            )
        }
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("dry-run", "execute-one"):
        sub = commands.add_parser(command)
        sub.add_argument("--package", type=Path, default=Path("dev/p2b"))
        sub.add_argument("--output", type=Path, required=True)
        if command == "execute-one":
            sub.add_argument("--task", required=True, choices=TASKS)
            sub.add_argument("--contract", type=Path, required=True)
            sub.add_argument("--approval", type=Path, required=True)
    inspector = commands.add_parser("inspect", help="Read an existing journal offline")
    inspector.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            print(json.dumps(inspect_journal(args.output), indent=2))
        elif args.command == "dry-run":
            result = dry_run(args.package, args.output)
            print(
                json.dumps({k: v for k, v in result.items() if k != "tasks"}, indent=2)
            )
        else:
            result = execute_one(
                args.package, args.output, args.task, args.contract, args.approval
            )
            print(
                json.dumps(
                    {
                        "task": result["task_id"],
                        "wire_requests": result["wire_requests"],
                    }
                )
            )
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - private CLI boundary
        # Do not print provider text, credentials or private filesystem details.
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": str(error)
                    if isinstance(error, PilotStop)
                    else type(error).__name__,
                }
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
