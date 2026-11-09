"""Synthetic offline accounting/response-stop regressions; live evidence is read-only."""

from pathlib import Path
from typing import ClassVar

import pytest

from ripple import pilot
from ripple.llm import LLMResponse
from ripple.pilot_admission import (
    Admission,
    Contract,
    Journal,
    PilotStop,
    inspect_journal,
)
from ripple.pilot_experimental import ExperimentalAdmission, ExperimentalSchemaGuard

# Sanitized numeric reconstruction; no campaign paths, prompts or responses.
NUMBERS = [
    (6449, 417, 431, 848),
    (14067, 1994, 1397, 3391),
    (12626, 1656, 901, 2557),
    (12970, 1749, 1151, 2900),
    (8902, 791, 457, 1248),
    (13582, 1903, 573, 2476),
    (13049, 1774, 1545, 3319),
    (13177, 0, 0, 0),
]
ROWS = [
    dict(zip(("input_estimate", "input_tokens", "output_tokens", "total_tokens"), row))
    for row in NUMBERS
]
PAID = ROWS[:7]
OPERATIONS = [
    "interpret",
    "choose_next_action",
    "choose_next_action",
    "choose_next_action",
    "decide_candidates",
    "choose_next_action",
    "choose_next_action",
]
VALID = {
    "interpret": {
        "summary": "Synthetic accounting investigation",
        "change_kinds": ["business_logic"],
        "search_terms": ["stock", "reserved", "threshold"],
    },
    "choose_next_action": {
        "tool_name": "submit_report",
        "arguments": {},
        "reason": "synthetic",
    },
    "decide_candidates": {"decisions": []},
    "draft_report": {"affected_components": []},
}


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


class SyntheticPolicy:
    model = "synthetic-accounting-model"
    context_limit = 18000
    output_limit = 2000
    mock = True
    requests_per_minute = 24
    tokens_per_minute = 110000
    output_parameters: ClassVar[dict[str, str]] = {
        "responses": "max_output_tokens",
        "chat_completions": "max_completion_tokens",
    }

    def input_bound(self, request):
        return request["estimate"]


def experiment():
    return {
        "profile": "EXPERIMENTAL_UB_COURSE",
        "provider_guarantees_verified": False,
        "reservation_floor": True,
        "ledger_sha256": "a" * 64,
        "approval_sha256": "b" * 64,
    }


@pytest.fixture
def factory(tmp_path):
    journals = []

    def make(*, experimental=True, policy=None):
        journal = Journal(tmp_path / ("journal-" + str(len(journals))))
        journals.append(journal)
        clock = Clock()
        gate = (ExperimentalAdmission if experimental else Admission)(
            journal,
            policy or SyntheticPolicy(),
            clock=clock,
            experiment=experiment() if experimental else None,
        )
        gate.start("SYNTHETIC-INVESTIGATION", 110000)
        return gate, clock

    yield make
    for journal in journals:
        journal.close()


def request(row):
    return {
        "model": SyntheticPolicy.model,
        "estimate": row["input_estimate"],
        "input": "synthetic",
    }


def replay_accounting(gate, clock, count=7):
    for i, row in enumerate(PAID[:count]):
        clock.value = float(i * 2)
        number = gate.reserve("responses", request(row))
        gate.settle(
            number, {}, row["input_tokens"], row["output_tokens"], row["total_tokens"]
        )


def test_seven_sanitized_costs_reconstruct_without_refund(factory):
    gate, clock = factory()
    replay_accounting(gate, clock)
    assert gate.total_requests == 7 and gate.total_tokens == gate.task_tokens == 95645
    assert sum(r["total_tokens"] for r in PAID) == 16739
    clock.value = 14.0
    with pytest.raises(PilotStop, match="token_limit"):
        gate.reserve("responses", request(ROWS[7]))
    assert (
        gate.total_requests == 7 and gate.total_tokens == 95645 and gate.pending is None
    )
    gate.journal.close()
    row = inspect_journal(gate.journal.root)["tasks"][0]
    assert (
        row["status"] == "CENSORED"
        and not row["unknown_cost"]
        and not row["evaluation_eligible"]
    )


def test_waiting_a_minute_does_not_recover_task_allocation(factory):
    gate, clock = factory()
    replay_accounting(gate, clock)
    clock.value = 80.0
    with pytest.raises(PilotStop, match="token_limit"):
        gate.reserve("responses", request(ROWS[7]))
    assert gate.total_requests == 7 and gate.total_tokens == 95645


def test_strict_known_usage_accounting_is_a_distinct_contract(factory):
    gate, clock = factory(experimental=False)
    replay_accounting(gate, clock)
    assert gate.total_tokens == 16739
    clock.value = 14.0
    gate.reserve("responses", request(ROWS[7]))
    assert gate.total_requests == 8 and gate.total_tokens == 31916
    # Synthetic unit construction is not permission to load an unverified contract.
    with pytest.raises(PilotStop, match="provider_contract_unverified"):
        Contract.load(
            Path(pilot.__file__).parents[2] / "dev/p2b/provider_contract.json"
        )


def test_rate_only_limit_is_distinct_from_task_limit(factory):
    policy = SyntheticPolicy()
    policy.tokens_per_minute = 100000  # hypothetical stricter ceiling
    gate, clock = factory(policy=policy)
    replay_accounting(gate, clock)
    assert gate.total_tokens + 8000 < 110000
    with pytest.raises(PilotStop, match="local_reserved_token_rate_limit"):
        gate.reserve("responses", {"model": policy.model, "estimate": 6000})


@pytest.mark.parametrize(
    "accounted,next_reservation,rate_ceiling",
    [
        (0, 8000, 110000),
        (90000, 8000, 110000),
        (95645, 15177, 110000),
        (95645, 8000, 100000),
        (110000, 8000, 110000),
    ],
)
def test_budget_first_reason_never_admits_a_previously_denied_request(
    factory, accounted, next_reservation, rate_ceiling
):
    policy = SyntheticPolicy()
    policy.tokens_per_minute = rate_ceiling
    gate, _ = factory(policy=policy)
    remaining = accounted
    while remaining:
        cost = min(17000, remaining)
        number = gate.reserve(
            "responses", {"model": policy.model, "estimate": cost - 2000}
        )
        gate.settle(number, {}, 1, 1, 2)
        remaining -= cost
    budget_blocked = accounted + next_reservation > 110000
    rate_blocked = accounted + next_reservation > rate_ceiling
    old_reason = (
        "provider_rate_limit"
        if rate_blocked
        else "token_limit"
        if budget_blocked
        else None
    )
    new_reason = (
        "token_limit"
        if budget_blocked
        else "local_reserved_token_rate_limit"
        if rate_blocked
        else None
    )
    assert (old_reason is None) == (new_reason is None)
    if new_reason is None:
        gate.reserve(
            "responses", {"model": policy.model, "estimate": next_reservation - 2000}
        )
        assert not gate.stopped
    else:
        with pytest.raises(PilotStop, match=new_reason):
            gate.reserve(
                "responses",
                {"model": policy.model, "estimate": next_reservation - 2000},
            )
    if accounted == 95645 and next_reservation == 15177:
        assert old_reason == "provider_rate_limit" and new_reason == "token_limit"


class PaidClient:
    model = SyntheticPolicy.model

    def __init__(self, gate, rows, payloads, usage=None, complete=True):
        self.gate, self.rows, self.payloads, self.usage, self.complete = (
            gate,
            rows,
            payloads,
            usage,
            complete,
        )
        self.calls = 0

    def drain_diagnostics(self):
        return []

    def __getattr__(self, operation):
        if operation not in pilot.SCHEMAS:
            raise AttributeError(operation)

        def call(prompt):
            row = self.rows[self.calls]
            number = self.gate.reserve("responses", request(row))
            self.calls += 1
            counts = (
                self.usage
                if self.usage is not None
                else (row["input_tokens"], row["output_tokens"], row["total_tokens"])
            )
            self.gate.settle(number, {}, *counts, complete=self.complete)
            return LLMResponse(
                payload=self.payloads[self.calls - 1],
                model=self.model,
                input_tokens=counts[0],
                output_tokens=counts[1],
                total_tokens=counts[2],
            )

        return call


@pytest.mark.parametrize("operation", list(VALID))
def test_valid_payloads_pass_without_changing_production_or_accounting(
    factory, operation
):
    gate, _clock = factory()
    paid = PaidClient(gate, [PAID[0]], [VALID[operation]])
    guarded = ExperimentalSchemaGuard(paid, gate)
    result = getattr(guarded, operation)("synthetic")
    assert result.payload is VALID[operation]
    assert not gate.stopped and gate.total_tokens == 8449 and gate.total_requests == 1


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("interpret", "not an object"),
        ("choose_next_action", '{"tool_name":"submit_report"'),
        ("decide_candidates", []),
        ("draft_report", '{"affected_components":[]}'),
    ],
)
def test_invalid_payload_stops_before_logical_acceptance(factory, operation, payload):
    gate, _clock = factory()
    paid = PaidClient(gate, [PAID[0]], [payload])
    guarded = ExperimentalSchemaGuard(paid, gate)
    capture = pilot.CaptureClient(guarded, gate)
    with pytest.raises(PilotStop):
        getattr(capture, operation)("synthetic")
    assert gate.stopped and gate.total_tokens == 8449 and paid.calls == 1
    assert any(
        e["kind"] == "stop" and e["reason"] == "response_schema_invalid"
        for e in gate.journal.events
    )
    assert not any(e["kind"] == "logical_response" for e in gate.journal.events)
    with pytest.raises(PilotStop, match="admission_closed"):
        getattr(guarded, operation)("second synthetic")
    assert paid.calls == 1 and gate.total_requests == 1
    gate.journal.close()
    row = inspect_journal(gate.journal.root)["tasks"][0]
    assert (
        row["status"] == "CENSORED"
        and not row["unknown_cost"]
        and not row["evaluation_eligible"]
    )


def test_schema_boundary_stops_the_sanitized_cost_sequence_at_six(factory):
    gate, _clock = factory()
    payloads = [VALID[op] for op in OPERATIONS]
    payloads[5] = '{"synthetic":"unterminated'
    paid = PaidClient(gate, PAID, payloads)
    guarded = ExperimentalSchemaGuard(paid, gate)
    capture = pilot.CaptureClient(guarded, gate)
    for operation in OPERATIONS[:5]:
        getattr(capture, operation)("synthetic")
    with pytest.raises(PilotStop):
        capture.choose_next_action("synthetic invalid six")
    assert paid.calls == gate.total_requests == 6 and gate.total_tokens == 80596
    assert sum(e["kind"] == "logical_response" for e in gate.journal.events) == 5
    with pytest.raises(PilotStop):
        guarded.choose_next_action("forbidden seven")
    assert paid.calls == 6


@pytest.mark.parametrize(
    "usage,complete,reason",
    [
        (None, False, "response_incomplete"),
        ((None, 1, 1), True, "usage_unknown_or_inconsistent"),
        ((1, 1, 3), True, "usage_unknown_or_inconsistent"),
        ((200000, 2001, 202001), True, "usage_exceeds_reservation"),
    ],
)
def test_existing_usage_and_incomplete_gates_take_precedence(
    factory, usage, complete, reason
):
    gate, _clock = factory()
    paid = PaidClient(
        gate, [PAID[0]], [VALID["choose_next_action"]], usage=usage, complete=complete
    )
    guarded = ExperimentalSchemaGuard(paid, gate)
    with pytest.raises(PilotStop, match=reason):
        guarded.choose_next_action("synthetic")
    assert paid.calls == 1 and gate.stopped
    assert any(
        e["kind"] == "stop" and e["reason"] == reason for e in gate.journal.events
    )
    assert not any(
        e["kind"] == "stop" and e["reason"] == "response_schema_invalid"
        for e in gate.journal.events
    )
    if usage == (200000, 2001, 202001):
        assert gate.total_tokens == 202001


@pytest.mark.parametrize(
    "cap,exceedances", [(256, 7), (1024, 3), (1536, 1), (1600, 0), (2000, 0)]
)
def test_reducing_output_cap_is_not_a_lossless_accounting_fix(cap, exceedances):
    assert sum(row["output_tokens"] > cap for row in PAID) == exceedances


def test_local_request_pacing_is_not_a_provider_response(factory):
    policy = SyntheticPolicy()
    policy.requests_per_minute = 1
    gate, clock = factory(policy=policy)
    replay_accounting(gate, clock, count=1)
    with pytest.raises(PilotStop, match="local_request_rate_limit"):
        gate.reserve("responses", request(PAID[0]))
    assert gate.total_requests == 1
    gate.journal.close()
    row = inspect_journal(gate.journal.root)["tasks"][0]
    assert row["status"] == "CENSORED" and not row["evaluation_eligible"]


def test_aggregate_budget_precedes_pacing(factory):
    from ripple.pilot_admission import Limits

    gate, clock = factory()
    replay_accounting(gate, clock)
    gate.limits = Limits(aggregate_tokens=100000)
    # Fits the task, exceeds aggregate and (hypothetical) local token pacing.
    gate.contract.tokens_per_minute = 100000
    with pytest.raises(PilotStop, match="token_limit"):
        gate.reserve("responses", {"model": gate.contract.model, "estimate": 6000})
    assert gate.total_tokens == 95645 and gate.total_requests == 7


def test_strict_budget_pacing_priority_remains_historical(factory):
    gate, _ = factory(experimental=False)
    for row in PAID:
        number = gate.reserve("responses", request(row))
        gate.settle(
            number, {}, row["input_estimate"], 2000, row["input_estimate"] + 2000
        )
    assert gate.total_tokens == 95645
    with pytest.raises(PilotStop, match="provider_rate_limit"):
        gate.reserve("responses", request(ROWS[7]))


def test_historical_local_rate_reason_is_preserved(factory):
    import hashlib

    gate, clock = factory()
    replay_accounting(gate, clock, count=1)
    with pytest.raises(PilotStop, match="provider_rate_limit"):
        gate.stop("provider_rate_limit")
    gate.journal.close()
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in gate.journal.root.iterdir()
        if p.is_file()
    }
    row = inspect_journal(gate.journal.root)["tasks"][0]
    assert row["reason"] == "provider_rate_limit" and row["status"] == "CENSORED"
    after = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in gate.journal.root.iterdir()
        if p.is_file()
    }
    assert before == after


@pytest.fixture
def sdk_factory(factory):
    """Actual SDK and capture stack; HTTP terminates in an in-memory transport."""
    from contextvars import ContextVar

    import httpx
    from openai import OpenAI

    from ripple.llm import OpenAILLM, RetryPolicy
    from ripple.pilot_experimental import ExperimentalPolicy
    from ripple.pilot_formatting import FormattingClient

    clients = []

    def make(texts, *, status=200, changes=None):
        gate, _ = factory(policy=ExperimentalPolicy())
        dispatched = []

        def send(request):
            dispatched.append(request.url.path)
            if status != 200:
                return httpx.Response(
                    status,
                    json={
                        "error": {"message": "synthetic", "type": "rate_limit_error"}
                    },
                )
            raw = {
                "id": "resp_synthetic",
                "object": "response",
                "created_at": 0,
                "model": gate.contract.model,
                "status": "completed",
                "output": [
                    {
                        "id": "msg_synthetic",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": texts[len(dispatched) - 1],
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                    "output_tokens_details": {"reasoning_tokens": 20},
                },
            }
            raw.update(changes or {})
            return httpx.Response(200, json=raw)

        sdk = OpenAI(
            api_key="offline-synthetic",
            base_url="https://synthetic.invalid/v1",
            max_retries=0,
            timeout=90,
            http_client=httpx.Client(transport=httpx.MockTransport(send)),
        )
        clients.append(sdk)
        client = object.__new__(OpenAILLM)
        client.model, client.retry_policy = (
            gate.contract.model,
            RetryPolicy(allow_request_replay=False),
        )
        client._request_state = ContextVar("synthetic_schema_guard", default=None)
        client._client = sdk
        guarded = ExperimentalSchemaGuard(
            FormattingClient(pilot.attach_gate(client, gate)), gate
        )
        return gate, guarded, pilot.CaptureClient(guarded, gate), dispatched

    yield make
    for client in clients:
        client.close()


@pytest.mark.parametrize("operation", list(VALID))
@pytest.mark.parametrize(
    "text", ['{"unterminated":', "{}", "[]", '"raw synthetic text"']
)
def test_sdk_schema_failure_settles_once_and_stops_before_capture(
    sdk_factory, operation, text
):
    gate, guarded, capture, dispatched = sdk_factory([text])
    with pytest.raises(PilotStop):
        getattr(capture, operation)("synthetic")
    kinds = [event["kind"] for event in gate.journal.events]
    assert (
        kinds.index("wire_metadata") < kinds.index("wire_settled") < kinds.index("stop")
    )
    assert "logical_response" not in kinds
    assert gate.last_reason == "response_schema_invalid"
    assert dispatched == ["/v1/responses"] and gate.pending is None
    for other in VALID:
        with pytest.raises(PilotStop, match="admission_closed"):
            getattr(guarded, other)("must not dispatch")
    assert len(dispatched) == gate.total_requests == 1
    root = gate.journal.root
    gate.journal.close()
    row = inspect_journal(root)["tasks"][0]
    assert row["status"] == "CENSORED" and row["reason"] == "response_schema_invalid"
    assert row["observed_usage"] == {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
    }
    assert row["reserved_tokens"] == row["accounted_tokens"] > 150
    assert not row["unknown_cost"] and not row["evaluation_eligible"]
    restarted = Journal(root)
    try:
        with pytest.raises(PilotStop, match="previous_run_incomplete_or_stopped"):
            ExperimentalAdmission(restarted, gate.contract, experiment=experiment())
    finally:
        restarted.close()


def test_valid_sdk_operations_continue_without_transforming_payloads(sdk_factory):
    import json

    gate, _, capture, dispatched = sdk_factory(
        [json.dumps(value) for value in VALID.values()]
    )
    for operation, expected in VALID.items():
        assert getattr(capture, operation)("synthetic").payload == expected
    assert not gate.stopped and gate.total_requests == len(dispatched) == 4
    assert len(capture.records) == 4
    assert all(
        record["response"]["payload"] == expected
        for record, expected in zip(capture.records, VALID.values())
    )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"status": "incomplete"}, "response_incomplete"),
        ({"usage": None}, "usage_unknown_or_inconsistent"),
        (
            {"usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 151}},
            "usage_unknown_or_inconsistent",
        ),
        (
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 2001,
                    "total_tokens": 2101,
                }
            },
            "usage_exceeds_reservation",
        ),
    ],
)
def test_sdk_usage_and_completion_failures_precede_schema_validation(
    sdk_factory, changes, reason
):
    gate, guarded, capture, dispatched = sdk_factory(['{"malformed":'], changes=changes)
    with pytest.raises(PilotStop):
        capture.choose_next_action("synthetic")
    assert gate.last_reason == reason
    assert len(dispatched) == 1
    with pytest.raises(PilotStop, match="admission_closed"):
        guarded.choose_next_action("no additional request")
    assert len(dispatched) == 1


def test_actual_provider_429_is_not_a_local_pacing_stop(sdk_factory):
    gate, guarded, capture, dispatched = sdk_factory([], status=429)
    with pytest.raises(PilotStop):
        capture.choose_next_action("synthetic")
    assert gate.last_reason == "provider_failure_or_interruption"
    assert len(dispatched) == 1 and gate.pending is not None
    with pytest.raises(PilotStop, match="admission_closed"):
        guarded.choose_next_action("no retry")
    assert len(dispatched) == 1
    gate.journal.close()
    row = inspect_journal(gate.journal.root)["tasks"][0]
    assert row["unknown_cost"] and not row["evaluation_eligible"]


@pytest.mark.parametrize("operation", list(VALID))
def test_execute_one_schema_stop_is_terminal_and_unscored(
    tmp_path, monkeypatch, operation
):
    import json

    from test_p2b_request_formatting import approved, synthetic_sdk

    from ripple import pilot_experimental as experimental
    from ripple.pilot_admission import read_journal

    identity = experimental.repository_identity(require_clean=False)
    monkeypatch.setattr(experimental, "repository_identity", lambda **kw: identity)
    original = pilot.MockProvider.create
    invalid_attempts = []

    def create(provider, **request):
        response = original(provider, **request)
        if (
            request["text"]["format"]["name"]
            == pilot.SCHEMAS[operation].__name__.lower()
        ):
            invalid_attempts.append(provider.calls)
            raw = response.model_dump()
            raw["output_text"] = response.output_text = '{"synthetic":'
            response.model_dump = lambda **_: raw
        return response

    monkeypatch.setattr(pilot.MockProvider, "create", create)
    providers = synthetic_sdk(monkeypatch)
    output, _, approval_path = approved(tmp_path)
    with pilot.offline(), pytest.raises(PilotStop):
        experimental.execute_one(
            Path(pilot.__file__).parents[2] / "dev/p2b",
            output,
            pilot.TASKS[0],
            approval_path,
            profile=experimental.PROFILE,
            acknowledged_task=pilot.TASKS[0],
        )
    assert invalid_attempts == [providers[0].calls]
    events = read_journal(output)
    assert (
        events[-1]["kind"] == "stop"
        and events[-1]["reason"] == "response_schema_invalid"
    )
    assert events[-2]["kind"] == "wire_settled"
    assert not any(event["kind"] == "task_finished" for event in events)
    report = inspect_journal(output)
    row = report["tasks"][0]
    assert row["status"] == "CENSORED" and not row["evaluation_eligible"]
    assert not report["evaluation_eligible"] and not row["unknown_cost"]
    assert row["accounted_tokens"] == row["reserved_tokens"]
    summaries = list(output.glob("terminal-*.json"))
    assert len(summaries) == 1
    assert json.loads(summaries[0].read_text())["reason"] == "response_schema_invalid"
    result = output / pilot.TASKS[0] / "result.json"
    if result.exists():
        value = json.loads(result.read_text())
        assert not value.get("accuracy_evaluable", False) and "comparisons" not in value
