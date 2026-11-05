"""Offline adversarial checks for accounting, terminal evidence and isolation."""

import copy
import dataclasses
import json
from types import SimpleNamespace

import pytest

from ripple import pilot
from ripple.pilot import WireEndpoint, mock_contract, response_metadata
from ripple.pilot_admission import (
    Admission,
    Journal,
    Limits,
    PilotStop,
    accounting_state,
    inspect_journal,
)


@pytest.fixture
def gate(tmp_path):
    journal = Journal(tmp_path / "journal")
    gate = Admission(journal, mock_contract())
    gate.start("SYNTHETIC-HARDENING", 110000)
    yield gate
    journal.close()


def request():
    return {"model": "mock-p2b", "input": "synthetic"}


def raw_response(**changes):
    data = {
        "model": "mock-p2b",
        "status": "completed",
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    }
    data.update(changes)
    return SimpleNamespace(model_dump=lambda **kwargs: copy.deepcopy(data))


def terminal(gate):
    paths = list(gate.journal.root.glob("terminal-*.json"))
    assert len(paths) == 1
    assert paths[0].stat().st_mode & 0o777 == 0o600
    return json.loads(paths[0].read_text())


@pytest.mark.parametrize("counts", [(400000, 1000, 401000), (100, 3000, 3100)])
def test_overrun_is_visible_conservative_and_reconstructable(gate, counts):
    gate.reserve("responses", request())
    reservation = gate.total_tokens
    with pytest.raises(PilotStop, match="usage_exceeds_reservation"):
        gate.settle(1, {}, *counts)
    expected = max(reservation, counts[2])
    assert gate.total_tokens == expected
    row = terminal(gate)
    assert row["accounted_tokens"] == expected
    assert row["observed_usage"]["total_tokens"] == counts[2]
    assert row["unknown_cost"] and not row["evaluation_eligible"]
    if counts[2] > 350000:
        assert row["accounted_tokens"] > 350000
    root = gate.journal.root
    gate.journal.close()
    assert inspect_journal(root)["accounted_tokens"] == expected
    journal = Journal(root)
    try:
        with pytest.raises(PilotStop, match="previous_run"):
            Admission(journal, mock_contract())
    finally:
        journal.close()


@pytest.mark.parametrize(
    "counts", [(None, 1, 1), (1, 1, 3), (-1, 1, 0), (True, 1, 2), (1.5, 1, 2.5)]
)
def test_unknown_usage_is_explicit_in_durable_summary(gate, counts):
    gate.reserve("responses", request())
    reservation = gate.total_tokens
    with pytest.raises(PilotStop, match="usage_unknown"):
        gate.settle(1, {}, *counts)
    row = terminal(gate)
    assert row["unknown_cost"] and row["observed_attempts"] == 0
    assert row["accounted_tokens"] == reservation
    assert row["unresolved_attempts"] == [1]
    assert any(e["kind"] == "usage_unknown" for e in gate.journal.events)


@pytest.mark.parametrize("endpoint", ["responses", "chat_completions"])
def test_incomplete_response_accounts_usage_but_never_returns(gate, endpoint):
    change = (
        {"status": "incomplete"}
        if endpoint == "responses"
        else {
            "choices": [{"finish_reason": "length"}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
    )
    with pytest.raises(PilotStop, match="response_incomplete"):
        WireEndpoint(gate, endpoint, lambda **kw: raw_response(**change)).create(
            **request()
        )
    row = terminal(gate)
    assert row["observed_usage"]["total_tokens"] == 150
    assert row["accounted_tokens"] == row["reserved_tokens"]
    assert row["unknown_cost"] and row["status"] == "FAILED"
    assert not row["evaluation_eligible"]
    assert gate.pending is None


def test_observed_overrun_survives_failure_before_settlement(gate, monkeypatch):
    original = gate.journal.append

    def append(event):
        if event["kind"] == "wire_settled":
            raise OSError("synthetic disk failure")
        original(event)

    monkeypatch.setattr(gate.journal, "append", append)
    response = raw_response(
        usage={"input_tokens": 400000, "output_tokens": 1, "total_tokens": 400001}
    )
    with pytest.raises(PilotStop):
        WireEndpoint(gate, "responses", lambda **kw: response).create(**request())
    assert gate.total_tokens == 400001
    root = gate.journal.root
    gate.journal.close()
    row = inspect_journal(root)["tasks"][0]
    assert row["accounted_tokens"] == row["observed_usage"]["total_tokens"] == 400001
    assert row["unknown_cost"] and not row["evaluation_eligible"]


@pytest.mark.parametrize(
    "error",
    [RuntimeError("sensitive exception fixture"), KeyboardInterrupt(), TimeoutError()],
)
def test_failures_have_private_terminal_summaries(gate, error):
    def send(**kwargs):
        raise error

    with pytest.raises(PilotStop):
        WireEndpoint(gate, "responses", send).create(**request())
    row = terminal(gate)
    assert row["status"] in {"FAILED", "INTERRUPTED"}
    assert row["attempts"] == 1 and row["unknown_cost"]
    assert row["observed_attempts"] == 0
    assert "sensitive exception fixture" not in json.dumps(row)


@pytest.mark.parametrize("limit", ["tokens", "requests", "time"])
def test_budget_exhaustion_is_censored_before_dispatch(tmp_path, limit):
    journal = Journal(tmp_path / "journal")
    clock = [0]
    gate = Admission(
        journal,
        mock_contract(),
        limits=Limits(aggregate_tokens=1) if limit == "tokens" else None,
        clock=lambda: clock[0],
    )
    gate.start("budget", 110000)
    if limit == "requests":
        gate.limits = dataclasses.replace(gate.limits, task_requests=1)
        gate.reserve("responses", request())
        gate.settle(1, {}, 100, 50, 150)
    elif limit == "time":
        clock[0] = 2400
    try:
        with pytest.raises(PilotStop):
            gate.reserve("responses", request())
        row = terminal(gate)
        assert row["status"] == "CENSORED" and not row["evaluation_eligible"]
        assert row["attempts"] == int(limit == "requests")
    finally:
        journal.close()


def test_inspector_reads_unfinished_without_writes_or_resumption(
    gate, monkeypatch, capsys
):
    gate.reserve("responses", request())
    root = gate.journal.root
    gate.journal.close()
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    monkeypatch.setattr(
        pilot.OpenAILLM, "__init__", lambda *a, **kw: pytest.fail("SDK construction")
    )
    assert pilot.main(["inspect", "--output", str(root)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["unfinished"] and result["live_admission"] == "DISABLED"
    assert result["tasks"][0]["status"] == "UNFINISHED"
    assert not result["tasks"][0]["evaluation_eligible"]
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


def test_inspector_never_creates_missing_journal(tmp_path):
    missing = tmp_path / "absent"
    with pytest.raises(OSError):
        inspect_journal(missing)
    assert not missing.exists()


def test_capture_failure_summary_and_summary_disk_failure_are_safe(gate, monkeypatch):
    gate.reserve("responses", request())
    monkeypatch.setattr(
        gate.journal, "append", lambda event: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(PilotStop, match="capture_write_failed"):
        gate.append({"kind": "synthetic"})
    row = terminal(gate)
    assert row["reason"] == "capture_write_failed" and row["journal_write_uncertain"]
    assert row["unknown_cost"] and gate.stopped


def test_stop_remains_closed_when_summary_cannot_be_written(gate, monkeypatch):
    import ripple.pilot_admission as module

    monkeypatch.setattr(
        module, "private_write", lambda *a: (_ for _ in ()).throw(OSError("disk"))
    )
    gate.reserve("responses", request())
    with pytest.raises(PilotStop):
        gate.stop("execution_interrupted")
    assert gate.stopped and gate.summary_error
    root = gate.journal.root
    gate.journal.close()
    assert inspect_journal(root)["tasks"][0]["status"] == "INTERRUPTED"


@pytest.mark.parametrize(
    "destination", ["repository", "git", "evaluation", "package", "fixture", "ancestor"]
)
def test_output_overlap_rejected_before_creation(tmp_path, monkeypatch, destination):
    protected = tmp_path / "protected"
    package = tmp_path / "inputs"
    source = package / "fixtures/one"
    protected.mkdir()
    source.mkdir(parents=True)
    paths = {
        "repository": protected / "output",
        "git": protected / ".git/output",
        "evaluation": protected / "evaluation/output",
        "package": package / "output",
        "fixture": source / "output",
        "ancestor": tmp_path,
    }
    monkeypatch.setattr(pilot, "protected_roots", lambda: [protected])
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    with pytest.raises(PilotStop, match="output_overlaps"):
        pilot.validate_output_destination(
            paths[destination], package, {"tasks": [{"fixture": "fixtures/one"}]}
        )
    assert before == sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))


def test_output_symlink_and_traversal_aliases_are_rejected(tmp_path, monkeypatch):
    protected = tmp_path / "protected"
    protected.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(protected, target_is_directory=True)
    monkeypatch.setattr(pilot, "protected_roots", lambda: [protected])
    manifest = {"tasks": []}
    with pytest.raises(PilotStop, match="output_symlink"):
        pilot.validate_output_destination(
            link / "output", tmp_path / "inputs", manifest
        )
    with pytest.raises(PilotStop, match="output_overlaps"):
        pilot.validate_output_destination(
            tmp_path / "other/../protected/output", tmp_path / "inputs", manifest
        )


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_frozen_evaluation_additions_rejected(tmp_path, kind):
    folder = tmp_path / "evaluation/data"
    folder.mkdir(parents=True)
    (folder / "frozen.json").write_text("{}")
    files = ["evaluation/data/frozen.json"]
    pilot.verify_evaluation_entries(tmp_path, files)
    added = folder / "unexpected"
    if kind == "file":
        added.write_text("synthetic")
    elif kind == "directory":
        added.mkdir()
    else:
        added.symlink_to(folder / "frozen.json")
    with pytest.raises(PilotStop, match="frozen_evaluation_entries"):
        pilot.verify_evaluation_entries(tmp_path, files)


@pytest.mark.parametrize(
    "endpoint,details",
    [
        ("responses", "output_tokens_details"),
        ("chat_completions", "completion_tokens_details"),
    ],
)
def test_metadata_retains_only_explicit_allowlisted_details(endpoint, details):
    raw = {
        "model": "mock-p2b",
        "usage": {
            "total_tokens": 150,
            details: {"reasoning_tokens": 25, "private_extra": "never-export"},
            "private_extra": "never-export",
        },
    }
    metadata = response_metadata(endpoint, "mock-p2b", raw)
    assert metadata["requested_model"] == metadata["returned_model"] == "mock-p2b"
    assert metadata["fallback_derived_model"] is None
    assert metadata["usage"][details] == {"reasoning_tokens": 25}
    assert "never-export" not in json.dumps(metadata)
    assert "completion_status" not in metadata
    if endpoint == "chat_completions":
        assert metadata["finish_reasons"] == []


def test_missing_metadata_not_invented_and_fallback_is_explicit():
    metadata = response_metadata("responses", "requested", {})
    assert metadata["returned_model"] is None
    assert metadata["fallback_derived_model"] == "requested"
    assert metadata["model_origin"] == "configured_fallback"
    assert "completion_status" not in metadata and "usage" not in metadata
    invalid = response_metadata(
        "responses", "requested", {"usage": {"total_tokens": "private-value"}}
    )
    assert invalid["usage"] == {}
    assert invalid["invalid_usage_fields"] == ["usage.total_tokens"]
    assert "private-value" not in json.dumps(invalid)


def test_missing_returned_model_is_recorded_but_not_accepted(gate):
    raw = {
        "status": "completed",
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    }
    with pytest.raises(PilotStop, match="response_model_mismatch"):
        WireEndpoint(
            gate, "responses", lambda **kw: SimpleNamespace(model_dump=lambda **kw: raw)
        ).create(**request())
    metadata = next(e for e in gate.journal.events if e["kind"] == "wire_metadata")
    assert metadata["returned_model"] is None
    assert metadata["fallback_derived_model"] == "mock-p2b"
    assert terminal(gate)["observed_usage"]["total_tokens"] == 150


def test_restart_reducer_rejects_duplicate_settlement(gate):
    gate.reserve("responses", request())
    gate.settle(1, {}, 100, 50, 150)
    event = gate.journal.events[-1]
    with pytest.raises(PilotStop, match="settlement_without_attempt"):
        accounting_state([*gate.journal.events, event])


def test_locked_environment_is_part_of_approval_and_checked(monkeypatch):
    from importlib import metadata

    assert pilot.verify_environment()
    original = metadata.version
    monkeypatch.setattr(
        metadata,
        "version",
        lambda name: "unreviewed" if name.lower() == "openai" else original(name),
    )
    with pytest.raises(PilotStop, match="runtime_environment_unverified"):
        pilot.verify_environment()
    assert len(pilot.harness_hash()) == 64


def test_inspector_reports_torn_journal_without_repair(gate):
    gate.reserve("responses", request())
    root = gate.journal.root
    gate.journal.close()
    path = root / "journal.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"torn":')
    before = path.read_bytes()
    result = inspect_journal(root)
    assert result["status"] == "CORRUPT" and result["unknown_cost"]
    assert result["accounted_tokens"] is None and not result["evaluation_eligible"]
    assert before == path.read_bytes()


def test_wire_metadata_uses_explicit_fields_only(gate):
    seen = []

    def dump(**kwargs):
        seen.append(kwargs)
        return {
            "model": "mock-p2b",
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        }

    with pytest.raises(PilotStop, match="response_incomplete"):
        WireEndpoint(
            gate, "responses", lambda **kw: SimpleNamespace(model_dump=dump)
        ).create(**request())
    assert seen == [{"mode": "json", "exclude_unset": True}]
    metadata = next(e for e in gate.journal.events if e["kind"] == "wire_metadata")
    assert "completion_status" not in metadata


def test_dry_capture_artifact_failure_gets_terminal_summary(tmp_path, monkeypatch):
    original = pilot.private_write

    def write(path, value):
        if path.name == "trajectory.json":
            raise OSError("synthetic capture artifact failure")
        return original(path, value)

    monkeypatch.setattr(pilot, "private_write", write)
    root = tmp_path / "run"
    with pilot.offline(), pytest.raises(OSError):
        pilot.dry_run(pilot.Path(__file__).parents[1] / "dev/p2b", root)
    result = inspect_journal(root)
    assert result["tasks"][0]["status"] == "FAILED"
    assert result["tasks"][0]["reason"] == "capture_or_execution_failed"
    assert list(root.glob("terminal-*.json"))
    assert not (root / "dry_run_results.json").exists()


def test_settlement_cannot_reduce_previously_observed_usage(gate):
    gate.reserve("responses", request())
    gate.append(
        {
            "kind": "wire_observed",
            "attempt": 1,
            "response": {
                "usage": {
                    "input_tokens": 400000,
                    "output_tokens": 1,
                    "total_tokens": 400001,
                }
            },
        }
    )
    with pytest.raises(PilotStop, match="usage_observation_mismatch"):
        gate.settle(1, {}, 100, 50, 150)
    row = terminal(gate)
    assert row["observed_usage"]["total_tokens"] == row["accounted_tokens"] == 400001
    assert row["unknown_cost"] and not row["evaluation_eligible"]


def test_sigterm_is_recorded_and_original_handler_restored(gate):
    import signal

    previous = signal.getsignal(signal.SIGTERM)

    def send(**kwargs):
        signal.raise_signal(signal.SIGTERM)

    with pilot.execution_interrupts(), pytest.raises(PilotStop):
        WireEndpoint(gate, "responses", send).create(**request())
    assert signal.getsignal(signal.SIGTERM) == previous
    row = terminal(gate)
    assert row["status"] == "INTERRUPTED" and row["unknown_cost"]
    assert row["reason"] == "execution_interrupted"


def test_oversized_response_retains_usage_without_storing_oversized_body(gate):
    response = raw_response(
        usage={"input_tokens": 400000, "output_tokens": 1, "total_tokens": 400001},
        oversized_body="x" * (2 * 1024 * 1024),
    )
    with pytest.raises(PilotStop, match="response_size"):
        WireEndpoint(gate, "responses", lambda **kw: response).create(**request())
    row = terminal(gate)
    assert row["accounted_tokens"] == row["observed_usage"]["total_tokens"] == 400001
    assert row["unknown_cost"] and not row["evaluation_eligible"]
    assert not any(e["kind"] == "wire_observed" for e in gate.journal.events)
    root = gate.journal.root
    gate.journal.close()
    assert inspect_journal(root)["accounted_tokens"] == 400001
