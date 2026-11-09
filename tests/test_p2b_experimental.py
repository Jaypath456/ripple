"""Opt-in UB profile checks, entirely offline with synthetic in-memory transports."""

import copy
import dataclasses
import json
import signal
import subprocess
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

import pytest

from ripple import pilot
from ripple import pilot_experimental as experimental
from ripple.pilot_admission import (
    Admission,
    Contract,
    Journal,
    JournalError,
    Limits,
    PilotStop,
    accounting_state,
    canonical,
    digest,
    inspect_journal,
    private_write,
)

PACKAGE = Path(__file__).parents[1] / "dev/p2b"


def metadata(**changes):
    return {
        "profile": experimental.PROFILE,
        "provider_guarantees_verified": False,
        "reservation_floor": True,
        "ledger_sha256": "a" * 64,
        "approval_sha256": "b" * 64,
        **changes,
    }


def request(**changes):
    return {"model": pilot.MODEL, "input": "synthetic", **changes}


def response(**changes):
    raw = {
        "model": pilot.MODEL,
        "status": "completed",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "output_tokens_details": {"reasoning_tokens": 30},
        },
        **changes,
    }
    return SimpleNamespace(model_dump=lambda **kw: copy.deepcopy(raw))


@pytest.fixture
def gate(tmp_path):
    journal = Journal(tmp_path / "journal")
    value = experimental.ExperimentalAdmission(
        journal, experimental.ExperimentalPolicy(), experiment=metadata()
    )
    value.start(pilot.TASKS[0], 110000)
    yield value
    journal.close()


def approved(tmp_path):
    output = tmp_path / "experiment"
    value = experimental.approval_template(PACKAGE, output, pilot.TASKS[0])
    value.update(
        approved=True,
        acknowledges_unverified_provider=True,
        operator="offline-test",
        approval_id="synthetic-approval-1",
    )
    return output, value


@pytest.fixture
def simulated_identity(monkeypatch):
    actual = experimental.repository_identity(require_clean=False)
    monkeypatch.setattr(experimental, "repository_identity", lambda **kw: actual)
    return actual


def call(output, approval_path, task=pilot.TASKS[0], **kwargs):
    return experimental.execute_one(
        PACKAGE,
        output,
        task,
        approval_path,
        profile=experimental.PROFILE,
        acknowledged_task=task,
        **kwargs,
    )


def test_strict_loader_and_default_cli_unchanged():
    for name in ("dev/p2b/provider_contract.json",):
        with pytest.raises(PilotStop, match="provider_contract_unverified"):
            Contract.load(PACKAGE.parents[1] / name)
    # Exact implementation, not only behavioral samples, preserves strict evidence rules.
    import ast

    root = PACKAGE.parents[1]
    old = subprocess.check_output(
        [
            "git",
            "-C",
            str(root),
            "show",
            f"{experimental.BASE}:src/ripple/pilot_admission.py",
        ]
    ).decode()
    now = (root / "src/ripple/pilot_admission.py").read_text()
    contract = lambda source: next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.ClassDef) and n.name == "Contract"
    )
    assert ast.dump(contract(old)) == ast.dump(contract(now))
    assert (
        subprocess.check_output(
            ["git", "-C", str(root), "show", f"{experimental.BASE}:src/ripple/pilot.py"]
        )
        == (root / "src/ripple/pilot.py").read_bytes()
    )


def test_experimental_policy_never_loads_as_verified(tmp_path):
    path = tmp_path / "experimental.json"
    path.write_text(json.dumps(experimental.specification()))
    with pytest.raises(PilotStop, match="provider_contract_unverified"):
        Contract.load(path)
    assert not isinstance(experimental.ExperimentalPolicy(), Contract)
    assert experimental.specification()["provider_guarantees_verified"] is False


def test_policy_and_bound_include_entire_request():
    policy = experimental.ExperimentalPolicy()
    value = request(
        instructions="synthetic system",
        tools=[{"type": "function", "name": "synthetic"}],
        text={"format": {"schema": {"type": "object"}}},
    )
    assert policy.input_bound(value) == len(canonical(value)) + 4096
    assert policy.input_bound(value) > policy.input_bound(request())
    assert experimental.specification()["limits"] == dataclasses.asdict(Limits())
    assert not policy.mock and policy.requests_per_minute == 24


def test_approval_template_is_offline_unapproved_and_does_not_write(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        experimental, "OpenAILLM", lambda **kw: pytest.fail("SDK construction")
    )
    with pilot.offline():
        output, value = approved(tmp_path)
    assert not output.exists()
    # approved() explicitly flips the template, so inspect the actual template too.
    template = experimental.approval_template(PACKAGE, output, pilot.TASKS[0])
    assert (
        template["approved"] is False
        and template["acknowledges_unverified_provider"] is False
    )
    assert (
        template["binding"]["experimental_policy"]["status"]
        == "EXPERIMENTAL_UNVERIFIED"
    )
    assert len(value["binding"]["git_commit"]) == 40


@pytest.mark.parametrize(
    "field",
    [
        "git_commit",
        "harness_sha256",
        "input_package_sha256",
        "output_directory",
        "experimental_policy",
        "refs_sha256",
        "base",
    ],
)
def test_all_approval_binding_fields_must_match(
    tmp_path, field, simulated_identity, monkeypatch
):
    output, value = approved(tmp_path)
    value["binding"][field] = "mismatch"
    path = tmp_path / "approval.json"
    private_write(path, value)
    monkeypatch.setattr(
        experimental, "OpenAILLM", lambda **kw: pytest.fail("SDK construction")
    )
    with pytest.raises(PilotStop, match="experimental_task_not_approved"):
        call(output, path)
    assert not output.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("approved", False),
        ("approved", 1),
        ("acknowledges_unverified_provider", False),
        ("acknowledges_unverified_provider", 1),
        ("operator", "REPLACE_WITH_OPERATOR"),
        ("approval_id", "REPLACE_WITH_UNIQUE_ID"),
        ("task_id", pilot.TASKS[1]),
        ("operator", ""),
        ("approval_id", None),
    ],
)
def test_explicit_operator_fields_required(tmp_path, simulated_identity, field, value):
    output, approval = approved(tmp_path)
    approval[field] = value
    with pytest.raises(PilotStop):
        experimental.validate_approval(
            approval,
            experimental.binding(PACKAGE, output, require_clean=True),
            pilot.TASKS[0],
            pilot.TASKS[0],
        )


@pytest.mark.parametrize("ack", [None, pilot.TASKS[1]])
def test_task_specific_cli_acknowledgement_required(tmp_path, simulated_identity, ack):
    output, value = approved(tmp_path)
    with pytest.raises(PilotStop):
        experimental.validate_approval(value, value["binding"], pilot.TASKS[0], ack)
    assert not output.exists()


def test_dirty_checkout_cannot_execute(tmp_path, monkeypatch):
    original = subprocess.check_output

    def dirty_status(command, *a, **kw):
        if "status" in command:
            return b"?? synthetic-unreviewed-file\n"
        return original(command, *a, **kw)

    monkeypatch.setattr(subprocess, "check_output", dirty_status)
    monkeypatch.setattr(
        experimental, "OpenAILLM", lambda **kw: pytest.fail("SDK construction")
    )
    with pytest.raises(PilotStop, match="reviewed_clean_commit_required"):
        call(tmp_path / "output", tmp_path / "approval.json")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "profile", [None, "STRICT", "EXPERIMENTAL", "VERIFIED_UB_DEPLOYMENT"]
)
def test_profile_requires_explicit_opt_in(tmp_path, profile):
    with pytest.raises(PilotStop, match="profile_opt_in"):
        experimental.execute_one(
            PACKAGE,
            tmp_path / "output",
            pilot.TASKS[0],
            tmp_path / "approval",
            profile=profile,
            acknowledged_task=pilot.TASKS[0],
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "kind", ["public", "symlink", "duplicate", "invalid", "oversized"]
)
def test_approval_file_is_private_safe_json(tmp_path, kind):
    path = tmp_path / "approval.json"
    private_write(path, {"approved": False})
    if kind == "public":
        path.chmod(0o644)
    elif kind == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    elif kind == "duplicate":
        path.write_bytes(b'{"approved":true,"approved":false}')
    elif kind == "invalid":
        path.write_bytes(b"{")
    else:
        path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(PilotStop, match="approval_invalid"):
        experimental.load_approval(path)


@pytest.mark.parametrize(
    "path", ["repository", "package", "fixture", "git", "evaluation", "symlink"]
)
def test_experimental_output_isolation_before_approval(tmp_path, path):
    root = PACKAGE.parents[1]
    locations = {
        "repository": root / "out",
        "package": PACKAGE / "out",
        "fixture": PACKAGE / "fixtures/out",
        "git": root / ".git/out",
        "evaluation": root / "evaluation/out",
    }
    if path == "symlink":
        link = tmp_path / "link"
        link.symlink_to(root, target_is_directory=True)
        locations[path] = link / "out"
    with pytest.raises(PilotStop, match="output_"):
        experimental.binding(PACKAGE, locations[path], require_clean=False)


def test_reservation_floor_is_durable_and_labels_summaries(gate):
    gate.reserve("responses", request())
    reservation = gate.total_tokens
    gate.settle(1, {}, 100, 50, 150)
    assert gate.total_tokens == reservation > 150
    assert gate.task_tokens == reservation
    gate.finish()
    root = gate.journal.root
    gate.journal.close()
    inspected = inspect_journal(root)
    row = inspected["tasks"][0]
    assert row["evaluation_eligible"] and not row["unknown_cost"]
    assert row["experiment"] == metadata()
    assert row["accounted_tokens"] == reservation
    assert row["observed_usage"]["total_tokens"] == 150
    data = json.loads(next(root.glob("terminal-*.json")).read_text())
    assert data["experiment"] == metadata()
    journal = Journal(root)
    try:
        restored = experimental.ExperimentalAdmission(
            journal,
            experimental.ExperimentalPolicy(),
            experiment=metadata(approval_sha256="c" * 64),
        )
        assert restored.total_tokens == reservation and restored.finished == [
            pilot.TASKS[0]
        ]
        with pytest.raises(PilotStop, match="profile_changed"):
            Admission(journal, experimental.ExperimentalPolicy())
    finally:
        journal.close()


def test_strict_settlement_still_uses_valid_usage(tmp_path):
    journal = Journal(tmp_path / "strict")
    try:
        value = Admission(journal, pilot.mock_contract())
        value.start("STRICT", 110000)
        value.reserve("responses", {"model": "mock-p2b", "input": "synthetic"})
        value.settle(1, {}, 100, 50, 150)
        assert value.total_tokens == 150
        assert "experiment" not in value.journal.events[0]
        value.finish()
    finally:
        journal.close()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"usage": None}, "usage_unknown"),
        ({"status": "incomplete"}, "response_incomplete"),
        ({"model": "other-model"}, "response_model_mismatch"),
        ({"model": None}, "response_model_mismatch"),
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
        (
            {
                "usage": {
                    "input_tokens": 1000000,
                    "output_tokens": 50,
                    "total_tokens": 1000050,
                }
            },
            "usage_exceeds_reservation",
        ),
        (
            {"usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 151}},
            "usage_unknown",
        ),
        (
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                    "output_tokens_details": {"reasoning_tokens": 51},
                }
            },
            "usage_unknown",
        ),
        (
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                    "output_tokens_details": {"reasoning_tokens": -1},
                }
            },
            "usage_unknown",
        ),
    ],
)
def test_unexpected_provider_behavior_stops_and_preserves_evidence(
    gate, changes, reason
):
    sends = []

    def send(**kw):
        sends.append(kw)
        return response(**changes)

    endpoint = pilot.WireEndpoint(gate, "responses", send)
    with pytest.raises(PilotStop, match=reason):
        endpoint.create(**request(max_output_tokens=99999))
    assert len(sends) == 1 and sends[0]["max_output_tokens"] == 2000
    assert gate.stopped
    with pytest.raises(PilotStop, match="admission_closed"):
        endpoint.create(**request())
    assert len(sends) == 1
    gate.journal.close()
    row = inspect_journal(gate.journal.root)["tasks"][0]
    assert row["experiment"] == metadata() and not row["evaluation_eligible"]
    assert row["accounted_tokens"] >= row["reserved_tokens"]


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("sensitive-exception"),
        TimeoutError("sensitive-exception"),
        KeyboardInterrupt(),
    ],
)
def test_failures_and_interruption_keep_private_reservations(gate, error):
    def fail(**kw):
        raise error

    with pytest.raises(PilotStop) as caught:
        pilot.WireEndpoint(gate, "responses", fail).create(**request())
    assert "sensitive" not in str(caught.value)
    gate.journal.close()
    row = inspect_journal(gate.journal.root)["tasks"][0]
    assert row["unknown_cost"] and not row["evaluation_eligible"]
    assert row["accounted_tokens"] == row["reserved_tokens"]


def test_chat_fallback_rejected_before_attempt(gate):
    sent = []
    with pytest.raises(PilotStop, match="endpoint_changed"):
        pilot.WireEndpoint(
            gate, "chat_completions", lambda **kw: sent.append(kw)
        ).create(**request())
    assert not sent and gate.total_requests == 0


def test_single_pending_request_and_journal_lock(gate):
    gate.reserve("responses", request())
    with pytest.raises(PilotStop, match="admission_closed"):
        gate.reserve("responses", request())
    with pytest.raises(BlockingIOError):
        Journal(gate.journal.root)


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile", "STRICT"),
        ("provider_guarantees_verified", True),
        ("reservation_floor", False),
        ("ledger_sha256", "bad"),
        ("approval_sha256", "bad"),
    ],
)
def test_experimental_journal_schema_rejects_false_claims(tmp_path, field, value):
    journal = Journal(tmp_path / "journal")
    try:
        with pytest.raises(JournalError):
            Admission(
                journal,
                experimental.ExperimentalPolicy(),
                experiment=metadata(**{field: value}),
            )
    finally:
        journal.close()


def test_reconstruction_rejects_refunding_experimental_reservation(gate):
    gate.reserve("responses", request())
    events = copy.deepcopy(gate.journal.events)
    events.append(
        {
            "kind": "wire_settled",
            "attempt": 1,
            "response": {},
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "accounted_tokens": 150,
            "unknown_cost": False,
            "complete": True,
            "overrun": False,
        }
    )
    with pytest.raises(JournalError, match="accounting_invalid"):
        accounting_state(events)


def test_budget_exhaustion_retains_uncensored_observation_and_censored_summary(gate):
    gate.limits = dataclasses.replace(gate.limits, aggregate_tokens=6500)
    gate.reserve("responses", request())
    gate.settle(1, {}, 100, 50, 150)
    with pytest.raises(PilotStop, match="token_limit"):
        gate.reserve("responses", request())
    gate.journal.close()
    row = inspect_journal(gate.journal.root)["tasks"][0]
    assert row["status"] == "CENSORED" and row["attempts"] == 1
    assert row["observed_usage"]["total_tokens"] == 150
    assert not row["evaluation_eligible"]


def test_stage_requires_exact_previous_review_and_sequence(tmp_path):
    value = SimpleNamespace(finished=[])
    approval = {"reviewed_previous_result_sha256": None}
    experimental.validate_stage(value, tmp_path, pilot.TASKS[0], approval)
    with pytest.raises(PilotStop, match="prior_result"):
        experimental.validate_stage(value, tmp_path, pilot.TASKS[1], approval)
    value.finished = [pilot.TASKS[0]]
    folder = tmp_path / pilot.TASKS[0]
    folder.mkdir()
    result = {
        "experiment": {"provider_guarantees_verified": False},
        "comparisons": {"on": {"false_positive": 0}},
    }
    private_write(folder / "result.json", result)
    with pytest.raises(PilotStop, match="prior_result"):
        experimental.validate_stage(value, tmp_path, pilot.TASKS[1], approval)
    approval["reviewed_previous_result_sha256"] = digest(result)
    experimental.validate_stage(value, tmp_path, pilot.TASKS[1], approval)


def test_cli_error_never_echoes_sensitive_exception(tmp_path, monkeypatch, capsys):
    def fail(*a, **kw):
        raise RuntimeError("sensitive-exception-detail")

    monkeypatch.setattr(experimental, "approval_template", fail)
    assert (
        experimental.main(
            [
                "prepare-approval",
                "--profile",
                experimental.PROFILE,
                "--output",
                str(tmp_path / "out"),
                "--task",
                pilot.TASKS[0],
            ]
        )
        == 2
    )
    output = capsys.readouterr().out
    assert "sensitive" not in output and "UNVERIFIED" not in output
    assert json.loads(output)["provider_guarantees_verified"] is False


def test_sigterm_scope_covers_execution_preflight(tmp_path, monkeypatch):
    previous = signal.getsignal(signal.SIGTERM)

    def interrupt(*a, **kw):
        signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(experimental, "binding", interrupt)
    with pytest.raises(KeyboardInterrupt):
        call(tmp_path / "out", tmp_path / "approval")
    assert signal.getsignal(signal.SIGTERM) == previous


def test_first_task_full_capture_identical_replay_with_synthetic_provider(
    tmp_path, monkeypatch, simulated_identity
):
    scenarios = json.loads((PACKAGE / "mock_scenarios.json").read_text())
    providers = []
    http_clients = []

    def initialize(client, **kwargs):
        provider = pilot.MockProvider(scenarios[pilot.TASKS[0]])
        providers.append(provider)
        client.model = kwargs["model"]
        client.retry_policy = kwargs["retry_policy"]
        client._request_state = ContextVar("test_experimental", default=None)
        http = SimpleNamespace(follow_redirects=True)
        http_clients.append(http)
        client._client = SimpleNamespace(
            responses=provider,
            chat=SimpleNamespace(completions=provider),
            _client=http,
            max_retries=0,
        )

    monkeypatch.setattr(experimental.OpenAILLM, "__init__", initialize)
    output, approval = approved(tmp_path)
    path = tmp_path / "approval.json"
    private_write(path, approval)
    with pilot.offline():
        result = call(output, path)
    assert result["comparisons"]["on"]["exact_source_set"]
    assert result["additional_inference_for_replay"] == 0
    assert result["experiment"]["provider_guarantees_verified"] is False
    assert result["experiment"]["policy"] == experimental.specification()
    assert len(providers) == 1 and providers[0].calls == result["wire_requests"]
    assert http_clients[0].follow_redirects is False
    inspected = inspect_journal(output)
    assert inspected["evaluation_eligible"]
    assert inspected["tasks"][0]["accounted_tokens"] == result["tokens"]
    assert result["tokens"] > inspected["tasks"][0]["observed_usage"]["total_tokens"]
    before = providers[0].calls
    with pytest.raises(PilotStop, match="prior_result"):
        call(output, path)
    assert len(providers) == 1 and providers[0].calls == before


@pytest.mark.parametrize("changes", [{"ledger_sha256": "c" * 64}, None])
def test_cannot_change_profile_or_ledger_on_restart(gate, changes):
    gate.reserve("responses", request())
    gate.settle(1, {}, 100, 50, 150)
    gate.finish()
    with pytest.raises(PilotStop, match="profile_changed"):
        Admission(
            gate.journal,
            experimental.ExperimentalPolicy(),
            experiment=metadata(**changes) if changes else None,
        )


def test_aggregate_floor_survives_multiple_tasks_and_reconstruction(tmp_path):
    journal = Journal(tmp_path / "campaign")
    limits = dataclasses.replace(Limits(), aggregate_tokens=13000)
    try:
        gate = experimental.ExperimentalAdmission(
            journal,
            experimental.ExperimentalPolicy(),
            limits=limits,
            experiment=metadata(),
        )
        for task in pilot.TASKS[:2]:
            gate.start(task, 110000)
            gate.reserve("responses", request())
            gate.settle(gate.total_requests, {}, 100, 50, 150)
            gate.finish()
        assert gate.total_tokens > 12000
        restored = experimental.ExperimentalAdmission(
            journal,
            experimental.ExperimentalPolicy(),
            limits=limits,
            experiment=metadata(approval_sha256="d" * 64),
        )
        restored.start(pilot.TASKS[2], 110000)
        with pytest.raises(PilotStop, match="token_limit"):
            restored.reserve("responses", request())
        assert restored.total_requests == 2
    finally:
        journal.close()
    result = inspect_journal(journal.root)
    assert result["tasks"][2]["status"] == "CENSORED"
    assert all(
        row["experiment"]["provider_guarantees_verified"] is False
        for row in result["tasks"]
    )


def test_input_cap_rejects_complete_serialized_request_before_dispatch(gate):
    sent = []
    with pytest.raises(PilotStop, match="context_or_output_limit"):
        pilot.WireEndpoint(gate, "responses", lambda **kw: sent.append(kw)).create(
            **request(instructions="x" * 16000)
        )
    assert not sent and gate.total_requests == 0


@pytest.mark.parametrize(
    "fault", ["sdk_retry", "app_retry", "redirect_read_only", "http_missing"]
)
def test_experimental_transport_guards_fail_closed(gate, fault):
    class ReadOnlyHTTP:
        @property
        def follow_redirects(self):
            return True

        @follow_redirects.setter
        def follow_redirects(self, value):
            pass

    client = SimpleNamespace(
        retry_policy=pilot.RetryPolicy(allow_request_replay=fault == "app_retry"),
        _client=SimpleNamespace(
            max_retries=1 if fault == "sdk_retry" else 0,
            _client=None if fault == "http_missing" else ReadOnlyHTTP(),
        ),
    )
    with pytest.raises(PilotStop):
        pilot.attach_gate(client, gate)
    assert gate.total_requests == 0


@pytest.mark.parametrize(
    "failure", ["capture", "interrupt", "identity", "approval_changed"]
)
def test_execute_failure_has_durable_experimental_terminal_summary(
    tmp_path, monkeypatch, simulated_identity, failure
):
    # Initialization is synthetic. For faults before actual wire dispatch no client
    # operation is needed; all socket access remains blocked by the offline runner.
    monkeypatch.setattr(
        experimental, "OpenAILLM", lambda **kw: SimpleNamespace(model=pilot.MODEL)
    )
    monkeypatch.setattr(pilot, "attach_gate", lambda client, gate: client)
    output, approval = approved(tmp_path)
    path = tmp_path / "approval.json"
    private_write(path, approval)

    def fail(index, task, capture, folder, **kwargs):
        if failure == "interrupt":
            raise KeyboardInterrupt()
        if failure == "identity":
            monkeypatch.setattr(experimental, "binding", lambda *a, **kw: {})
            capture.gate.preflight()
        if failure == "approval_changed":
            path.write_text("{}")
            capture.gate.preflight()
        raise RuntimeError("sensitive-exception-detail")

    monkeypatch.setattr(pilot, "run_pair", fail)
    with pytest.raises((PilotStop, KeyboardInterrupt)):
        call(output, path)
    row = inspect_journal(output)["tasks"][0]
    assert row["status"] in {"FAILED", "INTERRUPTED"}
    assert (
        not row["evaluation_eligible"]
        and row["experiment"]["profile"] == experimental.PROFILE
    )
    summary = json.loads(next(output.glob("terminal-*.json")).read_text())
    assert summary["experiment"] == row["experiment"]
    assert "sensitive" not in json.dumps(summary)


@pytest.mark.parametrize("limit", dataclasses.asdict(Limits()))
def test_every_limit_is_part_of_operator_binding(tmp_path, simulated_identity, limit):
    output, value = approved(tmp_path)
    expected = copy.deepcopy(value["binding"])
    value["binding"]["experimental_policy"]["limits"][limit] += 1
    with pytest.raises(PilotStop, match="task_not_approved"):
        experimental.validate_approval(value, expected, pilot.TASKS[0], pilot.TASKS[0])
    assert not output.exists()


def test_specification_matches_effective_policy():
    spec = experimental.specification()
    policy = experimental.ExperimentalPolicy()
    assumptions = spec["local_assumptions"]
    assert policy.model == spec["model"] == pilot.MODEL
    assert policy.framing_tokens == assumptions["framing_allowance"]
    assert policy.context_limit == assumptions["context_planning_ceiling"]
    assert policy.requests_per_minute == assumptions["requests_per_minute_ceiling"]
    assert policy.tokens_per_minute == assumptions["tokens_per_minute_ceiling"]
    assert policy.output_limit == spec["limits"]["output_tokens"]
    assert policy.output_parameters[spec["endpoint"]] == spec["output_parameter"]
