"""All provider operations are mocked; no credentials or network are used."""

from collections import deque
from datetime import UTC, datetime
from email.utils import format_datetime
from types import SimpleNamespace

import openai
import pytest

from ripple.agent import AgentController
from ripple.llm import LLMError, OpenAILLM, RetryPolicy


class HTTPFailure(Exception):
    def __init__(self, status, headers=None):
        super().__init__(f"mock HTTP {status}")
        self.status_code = status
        self.response = SimpleNamespace(headers=headers or {})


def response(raw='{"ok": true}'):
    return SimpleNamespace(
        output_text=raw,
        model="mock",
        usage=SimpleNamespace(input_tokens=3, output_tokens=2, total_tokens=5),
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw))],
    )


@pytest.fixture
def factory(monkeypatch):
    def make(responses=(), chat=(), *, replay=False, max_attempts=3, backoff=None):
        calls, sleeps, constructor = [], [], []
        queues = {"responses": deque(responses), "chat": deque(chat)}

        def invoke(endpoint, **kwargs):
            calls.append((endpoint, kwargs))
            item = queues[endpoint].popleft()
            if isinstance(item, Exception):
                raise item
            return item

        def client(**kwargs):
            constructor.append(kwargs)
            return SimpleNamespace(
                responses=SimpleNamespace(
                    create=lambda **kw: invoke("responses", **kw)
                ),
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=lambda **kw: invoke("chat", **kw)
                    )
                ),
            )

        monkeypatch.setattr(openai, "OpenAI", client)
        llm = OpenAILLM(
            api_key="fixture-not-real",
            model="mock",
            base_url=None,
            retry_policy=RetryPolicy(
                allow_request_replay=replay, max_attempts=max_attempts
            ),
            sleep_fn=sleeps.append,
            clock_fn=lambda: 1_700_000_000,
            backoff_fn=backoff,
        )
        assert constructor[0]["max_retries"] == 0 and constructor[0]["timeout"] == 90.0
        return llm, calls, sleeps

    return make


@pytest.mark.parametrize("status", [429, 503])
def test_default_refuses_request_replay_with_billing_diagnostic(factory, status):
    llm, calls, sleeps = factory(
        [HTTPFailure(status, {"Retry-After": "1"}), response()]
    )
    with pytest.raises(LLMError):
        llm.interpret("unchanged prompt")
    assert len(calls) == 1 and not sleeps
    assert llm.drain_diagnostics()[-1]["reason_code"] == "request_replay_not_authorized"
    assert llm.drain_diagnostics() == []


@pytest.mark.parametrize("status", [429, 503])
def test_explicit_replay_retries_transient_responses_without_changing_payload(
    factory, status
):
    llm, calls, sleeps = factory(
        [HTTPFailure(status, {"Retry-After": "2.5"}), response()], replay=True
    )
    assert llm.interpret("exact prompt").payload == {"ok": True}
    assert sleeps == [2.5] and len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][1]["input"] == "exact prompt" and calls[0][1]["store"] is False
    events = llm.drain_diagnostics()
    assert next(e for e in events if e["reason_code"] == "retry_scheduled")[
        "duplicate_billing_risk"
    ]
    assert all("fixture-not-real" not in str(e) for e in events)


def test_chat_fallback_and_retry_share_one_attempt_budget(factory):
    retry_date = format_datetime(
        datetime.fromtimestamp(1_700_000_002, UTC), usegmt=True
    )
    llm, calls, sleeps = factory(
        [HTTPFailure(404)],
        [HTTPFailure(429, {"Retry-After": retry_date}), response()],
        replay=True,
    )
    result = llm.interpret("original input")
    assert result.endpoint == "chat_completions_fallback" and sleeps == [2]
    assert [name for name, _ in calls] == ["responses", "chat", "chat"]
    assert calls[1] == calls[2]
    assert calls[1][1]["messages"][1]["content"] == "original input"


def test_backoff_is_injected_and_attempts_are_bounded(factory):
    llm, calls, sleeps = factory(
        [HTTPFailure(503)] * 5, replay=True, backoff=lambda attempt: attempt / 10
    )
    with pytest.raises(LLMError):
        llm.interpret("bounded")
    assert len(calls) == 3 and sleeps == [0.1, 0.2]
    assert llm.drain_diagnostics()[-1]["reason_code"] == "attempt_budget_exhausted"


def test_retry_after_beyond_budget_does_not_retry_earlier_than_requested(factory):
    llm, calls, sleeps = factory(
        [HTTPFailure(429, {"Retry-After": "120"}), response()], replay=True
    )
    with pytest.raises(LLMError):
        llm.interpret("honor delay")
    assert len(calls) == 1 and not sleeps
    assert (
        llm.drain_diagnostics()[-1]["reason_code"] == "retry_after_exceeds_delay_budget"
    )


@pytest.mark.parametrize("value", ["nan", "inf", "-2", "not a date"])
def test_invalid_retry_after_uses_bounded_backoff(factory, value):
    llm, calls, sleeps = factory(
        [HTTPFailure(429, {"Retry-After": value}), response()], replay=True
    )
    llm.interpret("retry")
    assert len(calls) == 2 and sleeps == [5]


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("ambiguous completion"),
        HTTPFailure(401, {"Retry-After": "1"}),
        HTTPFailure(500, {"Retry-After": "1"}),
        HTTPFailure(503, {"x-should-retry": "false"}),
    ],
)
def test_ambiguous_or_vetoed_failures_never_replay(factory, error):
    llm, calls, sleeps = factory([error, response()], replay=True)
    with pytest.raises(LLMError):
        llm.interpret("unchanged")
    assert len(calls) == 1 and not sleeps


def test_retry_budget_cannot_multiply_through_endpoint_fallback(factory):
    llm, calls, sleeps = factory(
        [HTTPFailure(503), HTTPFailure(503), HTTPFailure(404)],
        [response()],
        replay=True,
    )
    with pytest.raises(LLMError, match="attempt budget"):
        llm.interpret("bounded across endpoints")
    assert len(calls) == 3 and all(name == "responses" for name, _ in calls)
    assert sleeps == [5, 10]


def test_malformed_success_is_not_replayed(factory):
    llm, calls, sleeps = factory([response("not JSON")], replay=True)
    assert llm.interpret("schema validation is separate").payload == "not JSON"
    assert len(calls) == 1 and not sleeps


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"max_attempts": 4},
        {"max_delay_seconds": -1},
        {"max_delay_seconds": float("inf")},
        {"allow_request_replay": "yes"},
    ],
)
def test_retry_policy_bounds_are_validated(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_retry_opt_in_environment_is_explicit(factory, monkeypatch):
    factory([response()])
    monkeypatch.setenv("RIPPLE_LLM_API_KEY", "fixture-not-real")
    monkeypatch.setenv("RIPPLE_LLM_MODEL", "mock")
    monkeypatch.delenv("RIPPLE_LLM_ALLOW_REQUEST_REPLAY", raising=False)
    assert not OpenAILLM.from_env().retry_policy.allow_request_replay
    monkeypatch.setenv("RIPPLE_LLM_ALLOW_REQUEST_REPLAY", "1")
    assert OpenAILLM.from_env().retry_policy.allow_request_replay


def test_provider_diagnostics_reach_trace_without_response_schema_changes(
    factory, indexed_repo, tmp_path
):
    import json
    from dataclasses import asdict

    from ripple.llm import LLMResponse

    llm, calls, sleeps = factory([HTTPFailure(429), response()], replay=True)
    controller = AgentController(indexed_repo, llm, output_root=tmp_path / "out")
    result = controller._llm("interpret", "exact prompt")
    assert set(asdict(result)) == set(asdict(LLMResponse({}, "mock")))
    events = [
        json.loads(line) for line in controller.trace.path.read_text().splitlines()
    ]
    assert any(
        e["event"] == "provider_diagnostic" and e["reason_code"] == "retry_scheduled"
        for e in events
    )
    assert llm.drain_diagnostics() == []
    assert len(calls) == 2 and sleeps == [5]


def test_shared_client_keeps_concurrent_request_budgets_and_diagnostics_separate(
    monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    barrier, lock = Barrier(2), Lock()
    counts = {}

    def create(**kwargs):
        prompt = kwargs["input"]
        with lock:
            counts[prompt] = counts.get(prompt, 0) + 1
            attempt = counts[prompt]
        if attempt == 1:
            barrier.wait(timeout=10)
            raise HTTPFailure(503)
        return response()

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    llm = OpenAILLM(
        api_key="fixture-not-real",
        model="mock",
        base_url=None,
        retry_policy=RetryPolicy(allow_request_replay=True),
        sleep_fn=lambda delay: None,
    )

    def run(prompt):
        result = llm.interpret(prompt)
        diagnostics = llm.drain_diagnostics()
        return result.payload, [
            event["attempt"]
            for event in diagnostics
            if event["reason_code"] == "request_attempt"
        ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(run, ["left prompt", "right prompt"]))
    assert outputs == [({"ok": True}, [1, 2]), ({"ok": True}, [1, 2])]
    assert counts == {"left prompt": 2, "right prompt": 2}
    assert llm.drain_diagnostics() == []
