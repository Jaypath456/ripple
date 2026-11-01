"""Paid-API-free capture, admission, replay, rollout and hostile-input checks."""

import dataclasses
import hashlib
import json
import signal
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from ripple.llm import OpenAILLM, RetryPolicy
from ripple.pilot import (
    TASKS,
    CaptureClient,
    MockBody,
    ReplayClient,
    WireEndpoint,
    attach_gate,
    dry_run,
    execute_one,
    load_trajectory,
    mock_contract,
    offline,
    package_hash,
    request_deadline,
    verify_package,
    verify_runtime,
)
from ripple.pilot_admission import (
    Admission,
    Contract,
    Journal,
    Limits,
    PilotStop,
    canonical,
    digest,
    private_write,
)

PACKAGE = Path(__file__).parents[1] / "dev/p2b"


@pytest.fixture
def gate(tmp_path):
    journal = Journal(tmp_path / "capture")
    result = Admission(journal, mock_contract())
    result.start(TASKS[0], 110000)
    yield result
    journal.close()


def request(model="mock-p2b"):
    return {"model": model, "input": "fixed prompt"}


def body(**changes):
    values = {
        "model": "mock-p2b",
        "status": "completed",
        "output_text": "{}",
        "usage": SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
    }
    return MockBody(**{**values, **changes})


def test_complete_three_task_capture_and_pairing(tmp_path):
    with offline():
        result = dry_run(PACKAGE, tmp_path / "dry")
    assert result["live_inference_calls"] == 0
    assert result["mock_wire_attempts"] == 23
    assert result["mock_tokens"] == 3450
    for row, expected in zip(result["tasks"], [1, 2, 0]):
        assert row["same_recorded_responses"] and row["provenance_identical"]
        assert row["off_live_replay_identical"]
        assert row["additional_inference_for_replay"] == 0
        assert len(row["on_source_paths"]) == expected
        assert len(row["retained_targets"]) == expected
        assert row["on_source_paths"] == row["expected_source_paths"]
        artifact = load_trajectory(tmp_path / "dry" / row["task_id"])
        assert digest(artifact["records"]) == row["response_stream_sha256"]
        assert artifact["provenance"]["evidence"]
        assert not row["off"]["affected_components"]
    assert result["tasks"][2]["off"] == result["tasks"][2]["on"]


@pytest.mark.parametrize("unknown", [None, -1, True, 1.5, "100"])
def test_unknown_usage_latches_and_keeps_full_reservation(gate, unknown):
    gate.reserve("responses", request())
    reserved = gate.total_tokens
    with pytest.raises(PilotStop, match="usage_unknown"):
        gate.settle(1, {}, unknown, 50, 150)
    assert gate.total_tokens == reserved and gate.pending and gate.stopped
    with pytest.raises(PilotStop):
        gate.reserve("responses", request())
    assert gate.total_requests == 1


def test_authoritative_usage_reconciles_only_after_capture(gate):
    gate.reserve("responses", request())
    assert gate.total_tokens > 150
    gate.settle(1, {}, 100, 50, 150)
    assert gate.total_tokens == gate.task_tokens == 150
    assert gate.journal.events[-1]["kind"] == "wire_settled"


@pytest.mark.parametrize(
    "counts", [(100, 50, 149), (99999, 50, 100049), (1, 2001, 2002)]
)
def test_inconsistent_or_excess_usage_is_not_credited(gate, counts):
    gate.reserve("responses", request())
    charged = gate.total_tokens
    with pytest.raises(PilotStop):
        gate.settle(1, {}, *counts)
    assert gate.total_tokens == charged and gate.stopped


@pytest.mark.parametrize("delta,allowed", [(0, True), (-1, False)])
def test_exact_reservation_boundary(tmp_path, delta, allowed):
    j = Journal(tmp_path / "boundary")
    try:
        contract = mock_contract()
        amount = contract.input_bound(request()) + 2000
        gate = Admission(j, contract)
        gate.start(TASKS[0], amount + delta)
        if allowed:
            assert gate.reserve("responses", request()) == 1
            assert gate.total_tokens == amount
        else:
            with pytest.raises(PilotStop, match="token_limit"):
                gate.reserve("responses", request())
            assert gate.total_requests == 0
    finally:
        j.close()


def test_25th_and_73rd_wire_attempts_are_denied(tmp_path):
    j = Journal(tmp_path / "limits")
    try:
        gate = Admission(j, dataclasses.replace(mock_contract(), framing_tokens=1))
        for task in TASKS:
            gate.start(task, 110000)
            for _ in range(24):
                attempt = gate.reserve("responses", request())
                gate.settle(attempt, {}, 1, 1, 2)
            gate.finish()
        assert gate.total_requests == 72
        gate.start("extra", 110000)
        with pytest.raises(PilotStop, match="request_limit"):
            gate.reserve("responses", request())
        assert gate.total_requests == 72
    finally:
        j.close()
    j = Journal(tmp_path / "per-task")
    try:
        gate = Admission(j, dataclasses.replace(mock_contract(), framing_tokens=1))
        gate.start(TASKS[0], 110000)
        for _ in range(24):
            attempt = gate.reserve("responses", request())
            gate.settle(attempt, {}, 1, 1, 2)
        with pytest.raises(PilotStop, match="request_limit"):
            gate.reserve("responses", request())
        assert gate.total_requests == 24
    finally:
        j.close()


def test_aggregate_ceiling_applies_even_with_task_balance(tmp_path):
    j = Journal(tmp_path / "aggregate")
    try:
        gate = Admission(j, mock_contract(), limits=Limits(aggregate_tokens=7000))
        gate.start(TASKS[0], 110000)
        gate.reserve("responses", request())
        gate.settle(1, {}, 4000, 1000, 5000)
        with pytest.raises(PilotStop, match="token_limit"):
            gate.reserve("responses", request())
        assert gate.total_requests == 1
    finally:
        j.close()


@pytest.mark.parametrize(
    "field", ["input_tokens", "output_tokens", "task_requests", "aggregate_tokens"]
)
def test_limits_cannot_be_weakened(field):
    with pytest.raises(ValueError):
        Limits(**{field: True})
    with pytest.raises(ValueError):
        Limits(**{field: 999999})


@pytest.mark.parametrize("status", [404, 429, 503, 401, 500, None])
def test_http_failures_never_retry_or_fallback(gate, status):
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        error = RuntimeError("private provider text must not enter capture")
        error.status_code = status
        raise error

    client = object.__new__(OpenAILLM)
    from contextvars import ContextVar

    client.model = gate.contract.model
    client.retry_policy = RetryPolicy()
    client._request_state = ContextVar("test_pilot", default=None)
    client._client = SimpleNamespace(
        responses=SimpleNamespace(create=send),
        chat=SimpleNamespace(completions=SimpleNamespace(create=send)),
    )
    capture = CaptureClient(attach_gate(client, gate), gate)
    with pytest.raises(PilotStop):
        capture.interpret("fixed prompt")
    with pytest.raises(PilotStop):
        capture.interpret("another prompt")
    assert len(calls) == gate.total_requests == 1
    assert gate.stopped and gate.pending
    assert (
        "private provider text" not in (gate.journal.root / "journal.jsonl").read_text()
    )


@pytest.mark.parametrize("error", [TimeoutError(), KeyboardInterrupt()])
def test_timeout_or_interruption_stops_unknown_billing(gate, error):
    def send(**kwargs):
        raise error

    with pytest.raises(PilotStop):
        WireEndpoint(gate, "responses", send).create(**request())
    assert gate.stopped and gate.pending and gate.total_requests == 1


def test_output_cap_is_on_exact_wire_request(gate):
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        return body()

    endpoint = WireEndpoint(gate, "responses", send)
    endpoint.create(**request(), max_output_tokens=999999)
    assert calls[0]["max_output_tokens"] == 2000
    assert gate.journal.events[1]["request"] == calls[0]
    assert gate.total_tokens == 150


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"input": "x" * 16000}, "context_or_output"),
        ({"model": "other"}, "model_mismatch"),
    ],
)
def test_bad_request_rejected_before_send(gate, change, reason):
    called = []
    with pytest.raises(PilotStop, match=reason):
        WireEndpoint(gate, "responses", lambda **kw: called.append(kw)).create(
            **{**request(), **change}
        )
    assert not called and gate.total_requests == 0


@pytest.mark.parametrize(
    "change", [{"usage": None}, {"status": "incomplete"}, {"model": "other"}]
)
def test_bad_response_preserved_but_never_admits_more(gate, change):
    with pytest.raises(PilotStop):
        WireEndpoint(gate, "responses", lambda **kw: body(**change)).create(**request())
    assert any(e["kind"] == "wire_observed" for e in gate.journal.events)
    assert gate.pending and gate.stopped


def test_capture_write_failure_prevents_send(gate, monkeypatch):
    called = []
    monkeypatch.setattr(
        gate.journal,
        "append",
        lambda event: (_ for _ in ()).throw(OSError("fixture disk full")),
    )
    with pytest.raises(PilotStop, match="capture_write_failed"):
        WireEndpoint(gate, "responses", lambda **kw: called.append(kw)).create(
            **request()
        )
    assert not called and gate.stopped


def test_write_failure_after_send_keeps_pending_reservation(gate, monkeypatch):
    original = gate.journal.append

    def append(event):
        if event["kind"] == "wire_observed":
            raise OSError("fixture disk full")
        original(event)

    monkeypatch.setattr(gate.journal, "append", append)
    with pytest.raises(PilotStop):
        WireEndpoint(gate, "responses", lambda **kw: body()).create(**request())
    assert gate.stopped and gate.pending and gate.total_tokens > 150


def test_incomplete_run_refuses_restart_and_concurrent_open(tmp_path):
    path = tmp_path / "journal"
    j = Journal(path)
    try:
        with pytest.raises(BlockingIOError):
            Journal(path)
        gate = Admission(j, mock_contract())
        gate.start(TASKS[0], 110000)
        gate.reserve("responses", request())
    finally:
        j.close()
    j = Journal(path)
    try:
        with pytest.raises(PilotStop, match="previous_run_incomplete"):
            Admission(j, mock_contract())
    finally:
        j.close()


@pytest.mark.parametrize("tamper", [b'{"torn":', b"{}\n"])
def test_torn_or_replaced_journal_is_rejected(tmp_path, tamper):
    root = tmp_path / "journal"
    j = Journal(root)
    j.append({"kind": "fixture"})
    j.close()
    with (root / "journal.jsonl").open("ab") as stream:
        stream.write(tamper)
    with pytest.raises((PilotStop, KeyError)):
        Journal(root)


def test_symlinks_and_artifact_overwrite_are_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(outside)
    with pytest.raises(PilotStop, match="symlink"):
        Journal(linked)
    p = outside / "artifact.json"
    private_write(p, {"fixed": True})
    with pytest.raises(FileExistsError):
        private_write(p, {"fixed": False})
    assert json.loads(p.read_text()) == {"fixed": True}
    assert p.stat().st_mode & 0o777 == 0o600


def test_replay_checks_full_prompt_schema_and_exhaustion():
    from ripple.agent_models import FeatureIntent

    item = {
        "operation": "interpret",
        "prompt": "original",
        "schema": FeatureIntent.model_json_schema(),
    }
    record = {
        **item,
        "request_sha256": digest(item),
        "response": {"payload": {}, "model": "mock"},
        "diagnostics": [],
    }
    client = ReplayClient([record], "mock")
    with pytest.raises(PilotStop, match="mismatch"):
        client.interpret("altered")
    with pytest.raises(PilotStop):
        client.verify_complete()
    client = ReplayClient([record], "mock")
    assert client.interpret("original").payload == {}
    client.verify_complete()
    with pytest.raises(PilotStop, match="exhausted"):
        client.interpret("original")


def test_sealed_trajectory_rejects_response_tampering(tmp_path):
    data = {"records": [{"response": "original"}]}
    private_write(tmp_path / "trajectory.json", data)
    private_write(tmp_path / "trajectory.sha256.json", {"sha256": digest(data)})
    assert load_trajectory(tmp_path) == data
    (tmp_path / "trajectory.json").write_bytes(canonical({"records": []}))
    with pytest.raises(PilotStop, match="trajectory_integrity"):
        load_trajectory(tmp_path)


def test_deadline_boundaries_and_provider_rate_are_checked(tmp_path):
    now = [0.0]
    j = Journal(tmp_path / "clock")
    try:
        gate = Admission(j, mock_contract(), clock=lambda: now[0])
        gate.start(TASKS[0], 110000)
        now[0] = 2400 - 90 + 0.01
        with pytest.raises(PilotStop, match="time_boundary"):
            gate.reserve("responses", request())
        assert gate.total_requests == 0
    finally:
        j.close()
    j = Journal(tmp_path / "rate")
    try:
        gate = Admission(j, dataclasses.replace(mock_contract(), requests_per_minute=1))
        gate.start(TASKS[0], 110000)
        gate.reserve("responses", request())
        gate.settle(1, {}, 100, 50, 150)
        with pytest.raises(PilotStop, match="provider_rate_limit"):
            gate.reserve("responses", request())
    finally:
        j.close()


def test_real_deadline_handler_and_offline_guard_restore():
    previous = signal.getsignal(signal.SIGALRM)
    with pytest.raises(TimeoutError), request_deadline(1):
        signal.raise_signal(signal.SIGALRM)
    assert signal.getsignal(signal.SIGALRM) == previous
    with offline(), pytest.raises(PilotStop, match="offline_socket"):
        socket.create_connection(("example.invalid", 443))


def test_retry_opt_in_cannot_escape_pilot(gate):
    with pytest.raises(PilotStop, match="transient_retry_enabled"):
        attach_gate(
            SimpleNamespace(retry_policy=RetryPolicy(allow_request_replay=True)), gate
        )
    with pytest.raises(PilotStop, match="sdk_retry_enabled"):
        attach_gate(
            SimpleNamespace(
                retry_policy=RetryPolicy(), _client=SimpleNamespace(max_retries=2)
            ),
            gate,
        )


def test_shipped_contract_blocks_before_credentials_or_client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        OpenAILLM,
        "__init__",
        lambda *a, **kw: pytest.fail("SDK must not be initialized"),
    )
    monkeypatch.setenv("RIPPLE_LLM_API_KEY", "fixture-never-used")
    with pytest.raises(PilotStop, match="provider_contract_unverified"):
        execute_one(
            PACKAGE,
            tmp_path / "live",
            TASKS[0],
            PACKAGE / "provider_contract.json",
            tmp_path / "missing-approval.json",
        )
    assert not (tmp_path / "live").exists()


def test_all_fixture_and_oracle_hashes_and_runtime_preserved():
    assert len(verify_package(PACKAGE)["tasks"]) == 3
    assert len(package_hash(PACKAGE)) == 64
    assert verify_runtime()["base"].startswith("112092")


def test_fixture_tampering_stops_before_execution(tmp_path):
    import shutil

    copy_path = tmp_path / "package"
    shutil.copytree(PACKAGE, copy_path)
    path = copy_path / "fixtures/stock/warehouse/status.py"
    path.write_text(path.read_text() + "\n# changed\n")
    with pytest.raises(PilotStop, match="source_file_hash"):
        verify_package(copy_path)


@pytest.mark.parametrize("task", TASKS[1:])
def test_first_approval_cannot_run_remaining_tasks(tmp_path, monkeypatch, task):
    from ripple import pilot

    monkeypatch.setattr(Contract, "load", lambda path: mock_contract())
    monkeypatch.setattr(
        pilot, "verify_runtime", lambda: {"refs_sha256": "fixture-refs"}
    )
    contract = tmp_path / "contract.json"
    contract.write_text("{}")
    approval = {
        "approved": True,
        "phase": "first",
        "output_directory": str(tmp_path / "run"),
        "tasks": [TASKS[0]],
        "package_sha256": package_hash(PACKAGE),
        "harness_sha256": pilot.harness_hash(),
        "refs_sha256": "fixture-refs",
        "contract_sha256": hashlib.sha256(contract.read_bytes()).hexdigest(),
    }
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval))
    with pytest.raises(PilotStop, match="first_task_only"):
        execute_one(PACKAGE, tmp_path / "run", task, contract, path)


def synthetic_verified_contract(tmp_path):
    """Synthetic primary evidence exercises loading, not UB deployment attestation."""
    path = tmp_path / "mock-contract.json"
    note = tmp_path / "mock-document.md"
    note.write_text("MOCK documentation for tests only; not a BullsAI claim.\n")
    data = {
        "status": "VERIFIED_UB_DEPLOYMENT",
        "model": "openai/gpt-oss-20b",
        "base_url": "https://gateway.bullsai.buffalo.edu/v1",
        "context_limit": 18000,
        "output_limit": 2000,
        "output_includes_reasoning": True,
        "usage_is_authoritative": True,
        "input_bound_method": "verified_utf8_plus_framing",
        "framing_token_upper_bound": 4096,
        "requests_per_minute": 72,
        "tokens_per_minute": 350000,
        "output_parameters": {
            "responses": "max_output_tokens",
            "chat_completions": "max_completion_tokens",
        },
        "evidence": {
            key: {
                "path": note.name,
                "sha256": hashlib.sha256(note.read_bytes()).hexdigest(),
            }
            for key in (
                "context",
                "output",
                "counting",
                "usage",
                "rate",
                "retry_billing",
            )
        },
    }
    path.write_text(json.dumps(data))
    return path


def test_full_staged_rollout_with_mock_transport_only(tmp_path, monkeypatch):
    from contextvars import ContextVar

    from ripple import pilot

    scenarios = json.loads((PACKAGE / "mock_scenarios.json").read_text())
    providers = []

    def initialize(client, **kwargs):
        provider = pilot.MockProvider(scenarios[TASKS[len(providers)]])
        providers.append(provider)
        client.model = kwargs["model"]
        client.retry_policy = kwargs["retry_policy"]
        client._request_state = ContextVar("test_rollout", default=None)
        client._client = SimpleNamespace(
            responses=provider,
            chat=SimpleNamespace(completions=provider),
            _client=SimpleNamespace(follow_redirects=True),
            max_retries=0,
        )

    monkeypatch.setattr(OpenAILLM, "__init__", initialize)
    contract = synthetic_verified_contract(tmp_path)
    approval = {
        "approved": True,
        "phase": "first",
        "output_directory": str(tmp_path / "rollout"),
        "tasks": [TASKS[0]],
        "package_sha256": package_hash(PACKAGE),
        "harness_sha256": pilot.harness_hash(),
        "refs_sha256": verify_runtime()["refs_sha256"],
        "contract_sha256": hashlib.sha256(contract.read_bytes()).hexdigest(),
    }
    first_approval = tmp_path / "first-approval.json"
    first_approval.write_text(json.dumps(approval))
    output = tmp_path / "rollout"
    with offline():
        first = execute_one(PACKAGE, output, TASKS[0], contract, first_approval)
        assert len(providers) == 1 and first["comparisons"]["on"]["exact_source_set"]
        with pytest.raises(PilotStop, match="first_task_only"):
            execute_one(PACKAGE, output, TASKS[1], contract, first_approval)
        assert len(providers) == 1
        approval.update(
            {
                "phase": "remaining",
                "tasks": list(TASKS[1:]),
                "reviewed_first_result_sha256": hashlib.sha256(
                    (output / TASKS[0] / "result.json").read_bytes()
                ).hexdigest(),
            }
        )
        reviewed = tmp_path / "remaining-approval.json"
        reviewed.write_text(json.dumps(approval))
        for task in TASKS[1:]:
            assert execute_one(PACKAGE, output, task, contract, reviewed)[
                "comparisons"
            ]["on"]["exact_source_set"]
    assert sum(p.calls for p in providers) == 23
    journal = Journal(output)
    try:
        gate = Admission(journal, Contract.load(contract))
        assert gate.finished == list(TASKS) and gate.total_tokens == 3450
    finally:
        journal.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("context_limit", None),
        ("output_includes_reasoning", False),
        ("usage_is_authoritative", False),
        ("input_bound_method", "guessed"),
        ("tokens_per_minute", None),
    ],
)
def test_provider_evidence_fields_are_mandatory(tmp_path, field, value):
    p = synthetic_verified_contract(tmp_path)
    data = json.loads(p.read_text())
    data[field] = value
    p.write_text(json.dumps(data))
    with pytest.raises(PilotStop, match="provider_contract_unverified"):
        Contract.load(p)


def test_provider_evidence_hash_and_output_parameter_must_match(tmp_path):
    path = synthetic_verified_contract(tmp_path)
    (tmp_path / "mock-document.md").write_text("changed")
    with pytest.raises(PilotStop, match="contract_evidence_hash"):
        Contract.load(path)
    path = synthetic_verified_contract(tmp_path)
    data = json.loads(path.read_text())
    data["output_parameters"]["responses"] = "max_tokens"
    path.write_text(json.dumps(data))
    with pytest.raises(PilotStop, match="output_parameter_unverified"):
        Contract.load(path)


def test_mutated_provenance_in_treatment_is_refused(tmp_path, monkeypatch):
    from ripple import agent

    original = agent.retain_confirmed_components

    def tamper(report, index, ledger, presented):
        next(iter(ledger.candidates.values())).reason = "mutated provenance"
        return original(report, index, ledger, presented)

    monkeypatch.setattr(agent, "retain_confirmed_components", tamper)
    with pytest.raises(PilotStop, match="replay_provenance_mismatch"):
        dry_run(PACKAGE, tmp_path / "mutated")


def test_token_budget_counts_prompt_schema_and_unicode(gate):
    first = gate.contract.input_bound(request())
    assert (
        gate.contract.input_bound({**request(), "schema": {"description": "x" * 1000}})
        > first + 1000
    )
    assert gate.contract.input_bound({**request(), "input": "\u03bb" * 1000}) > first


def test_capture_failure_is_not_a_successful_negative_control(tmp_path, monkeypatch):
    from ripple import pilot

    monkeypatch.setattr(pilot.MockProvider, "create", lambda *a, **kw: body(usage=None))
    with pytest.raises(PilotStop, match="captured_trajectory_failed"):
        dry_run(PACKAGE, tmp_path / "missing-usage")
    assert not (tmp_path / "missing-usage/dry_run_results.json").exists()


def test_live_transport_redirects_disabled_before_send(gate):
    gate.contract = dataclasses.replace(mock_contract(), mock=False)
    raw = SimpleNamespace(
        max_retries=0,
        _client=SimpleNamespace(follow_redirects=True),
        responses=SimpleNamespace(create=lambda **kw: body()),
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: body())),
    )
    client = SimpleNamespace(retry_policy=RetryPolicy(), _client=raw)
    attach_gate(client, gate)
    assert raw._client.follow_redirects is False
    assert gate.total_requests == 0


def test_unknown_live_http_transport_is_refused(gate):
    gate.contract = dataclasses.replace(mock_contract(), mock=False)
    client = SimpleNamespace(
        retry_policy=RetryPolicy(), _client=SimpleNamespace(max_retries=0)
    )
    with pytest.raises(PilotStop, match="http_transport_unverified"):
        attach_gate(client, gate)
    assert gate.total_requests == 0


@pytest.mark.parametrize("status", [200, 307, 429, 503])
def test_real_sdk_with_in_memory_http_transport(gate, status):
    from contextvars import ContextVar

    import httpx
    from openai import OpenAI

    gate.contract = dataclasses.replace(mock_contract(), mock=False)
    calls = []

    def send(request):
        calls.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(
                status,
                headers={"location": "https://redirect.invalid/v1/responses"},
                json={"error": {"message": "fixture transient error"}},
            )
        return httpx.Response(
            200,
            json={
                "id": "resp_fixture",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "mock-p2b",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_fixture",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "{}", "annotations": []}
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                },
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(send), follow_redirects=True)
    raw = OpenAI(
        api_key="fixture-mocked-transport",
        base_url="https://fixture.invalid/v1",
        max_retries=0,
        http_client=http,
    )
    client = object.__new__(OpenAILLM)
    client.model, client.retry_policy = "mock-p2b", RetryPolicy()
    client._request_state = ContextVar("real_sdk_mock", default=None)
    client._client = raw
    capture = CaptureClient(attach_gate(client, gate), gate)
    try:
        with offline():
            if status == 200:
                assert capture.interpret("fixed prompt").payload == {}
            else:
                with pytest.raises(PilotStop):
                    capture.interpret("fixed prompt")
        assert len(calls) == 1 and calls[0]["max_output_tokens"] == 2000
        assert not http.follow_redirects
        assert (
            "fixture-mocked-transport"
            not in (gate.journal.root / "journal.jsonl").read_text()
        )
    finally:
        raw.close()
