"""Offline reproductions from the independent final correctness review."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from ripple import pilot_admission as admission
from ripple.pilot import mock_contract
from ripple.pilot_admission import (
    Admission,
    Journal,
    PilotStop,
    accounting_state,
    canonical,
    digest,
    inspect_journal,
)

TASK = "FINAL-CORRECTNESS-SYNTHETIC"


def request():
    return {"model": "mock-p2b", "input": "synthetic"}


@pytest.fixture
def gate(tmp_path):
    journal = Journal(tmp_path / "journal")
    value = Admission(journal, mock_contract())
    value.start(TASK, 110000)
    value.reserve("responses", request())
    yield value
    journal.close()


def settle(gate):
    gate.settle(1, {}, 100, 50, 150)


def summary_path(root):
    return root / f"terminal-{digest(TASK)[:16]}.json"


def inspect_unchanged(root):
    path = root / "journal.jsonl"
    before = path.read_bytes(), path.stat().st_mtime_ns
    result = inspect_journal(root)
    assert before == (path.read_bytes(), path.stat().st_mtime_ns)
    return result


def write_events(root, events):
    root.mkdir(mode=0o700)
    previous = "0" * 64
    rows = []
    for sequence, event in enumerate(events):
        record = {"sequence": sequence, "previous": previous, "event": event}
        previous = digest(record)
        rows.append(canonical({**record, "sha256": previous}) + b"\n")
    path = root / "journal.jsonl"
    path.write_bytes(b"".join(rows))
    path.chmod(0o600)
    return root


def prefix():
    return [
        {"kind": "task_started", "task": TASK, "allocation": 110000},
        {
            "kind": "wire_reserved",
            "attempt": 1,
            "task": TASK,
            "endpoint": "responses",
            "input_bound": 1000,
            "reservation": 3000,
            "at": 1.0,
            "request": request(),
        },
    ]


def observation(counts):
    return {
        "kind": "wire_observed",
        "attempt": 1,
        "response": {
            "usage": dict(
                zip(("input_tokens", "output_tokens", "total_tokens"), counts)
            )
        },
    }


def settlement(counts=(100, 50, 150)):
    return {
        "kind": "wire_settled",
        "attempt": 1,
        "response": {},
        **dict(zip(("input_tokens", "output_tokens", "total_tokens"), counts)),
    }


def finished():
    return {"kind": "task_finished", "task": TASK, "seconds": 1.0}


@pytest.mark.parametrize("failure_event_fails", [False, True])
def test_settled_task_summary_failure_never_eligible(
    gate, monkeypatch, failure_event_fails
):
    settle(gate)
    original = gate.journal.append

    def append(event):
        if failure_event_fails and event["kind"] == "stop":
            raise OSError("synthetic failure-event fault")
        original(event)

    monkeypatch.setattr(gate.journal, "append", append)
    monkeypatch.setattr(
        admission,
        "private_write",
        lambda *a: (_ for _ in ()).throw(OSError("synthetic summary fault")),
    )
    with pytest.raises(PilotStop):
        gate.finish()
    root = gate.journal.root
    gate.journal.close()
    row = inspect_unchanged(root)["tasks"][0]
    assert row["task"] == TASK and not row["evaluation_eligible"]


def test_summary_failure_after_task_finished_preserves_identity(gate, monkeypatch):
    settle(gate)
    gate.append(finished())
    monkeypatch.setattr(
        admission,
        "private_write",
        lambda *a: (_ for _ in ()).throw(OSError("synthetic summary fault")),
    )
    with pytest.raises(PilotStop):
        gate.stop("terminal_summary_write_failed")
    root = gate.journal.root
    gate.journal.close()
    row = inspect_unchanged(root)["tasks"][0]
    assert row["status"] != "COMPLETED" and not row["evaluation_eligible"]


@pytest.mark.parametrize("change", ["missing", "corrupt", "mismatch"])
def test_terminal_evidence_must_exist_and_match(gate, change):
    settle(gate)
    gate.finish()
    root = gate.journal.root
    gate.journal.close()
    path = summary_path(root)
    if change == "missing":
        path.unlink()
    elif change == "corrupt":
        path.write_bytes(b'{"partial":')
    else:
        data = json.loads(path.read_text())
        data["observed_usage"]["total_tokens"] += 1
        path.write_bytes(canonical(data) + b"\n")
    assert not inspect_unchanged(root)["tasks"][0]["evaluation_eligible"]


def test_completion_requires_journal_and_valid_terminal_evidence(gate):
    settle(gate)
    gate.finish()
    root = gate.journal.root
    gate.journal.close()
    row = inspect_unchanged(root)["tasks"][0]
    assert row["status"] == "COMPLETED" and row["evaluation_eligible"]
    assert summary_path(root).is_file()
    journal = Journal(root)
    try:
        assert Admission(journal, mock_contract()).finished == [TASK]
    finally:
        journal.close()


@pytest.mark.parametrize("cut", [1, 2, 3, 4])
def test_conflicting_observations_preserve_maximum_after_interruption(tmp_path, cut):
    events = (
        prefix()
        + [
            observation((400000, 1, 400001)),
            observation((100, 50, 150)),
            settlement(),
            finished(),
        ][:cut]
    )
    root = write_events(tmp_path / "journal", events)
    row = inspect_unchanged(root)["tasks"][0]
    assert row["accounted_tokens"] >= 400001
    assert row["unknown_cost"] and not row["evaluation_eligible"]
    if cut >= 2:
        assert row.get("conflicted") is True
    reconstructed = accounting_state(events)["tasks"][TASK]
    assert reconstructed["accounted_tokens"] == row["accounted_tokens"]
    assert reconstructed["unknown_cost"]


@pytest.mark.parametrize("later", [(101, 49, 150), (100, 51, 151), (101, 50, 151)])
def test_conflicting_components_cannot_be_resolved_by_settlement(tmp_path, later):
    events = prefix() + [
        observation((100, 50, 150)),
        observation(later),
        settlement(later),
        finished(),
    ]
    row = inspect_unchanged(write_events(tmp_path / "journal", events))["tasks"][0]
    assert row.get("conflicted") is True and row["unknown_cost"]
    assert row["accounted_tokens"] >= 3000 and not row["evaluation_eligible"]


def test_consistent_observations_still_settle(gate):
    event = observation((100, 50, 150))
    gate.append(event)
    gate.append(event)
    settle(gate)
    gate.finish()
    root = gate.journal.root
    gate.journal.close()
    row = inspect_unchanged(root)["tasks"][0]
    assert row["accounted_tokens"] == 150 and not row["unknown_cost"]
    assert row["evaluation_eligible"]


def test_missing_observation_cannot_be_silently_corrected(tmp_path):
    events = prefix() + [
        {"kind": "wire_observed", "attempt": 1, "response": {"usage": None}},
        settlement(),
        finished(),
    ]
    row = inspect_unchanged(write_events(tmp_path / "journal", events))["tasks"][0]
    assert row["unknown_cost"] and not row["evaluation_eligible"]
    assert row["accounted_tokens"] >= 3000


BAD_EVENTS = [
    ({"kind": "wire_reserved"}, "missing-reservation-fields"),
    ({"kind": "task_started", "task": [], "allocation": 110000}, "task-type"),
    ({"kind": "task_started", "task": TASK, "allocation": -1}, "negative-allocation"),
    ({"kind": "task_started", "task": TASK, "allocation": True}, "boolean-allocation"),
    ({"kind": "task_started", "task": TASK, "allocation": 130001}, "excess-allocation"),
    (
        {"kind": "task_started", "task": TASK, "allocation": 1.5},
        "fractional-allocation",
    ),
    ({"kind": "unrecognized", "sensitive": "synthetic-do-not-echo"}, "unknown-kind"),
    ([], "event-list"),
    (None, "event-null"),
    ({"kind": 123}, "kind-type"),
    ({"kind": "stop", "reason": {"arbitrary": "synthetic-do-not-echo"}}, "reason-type"),
]


@pytest.mark.parametrize("event,label", BAD_EVENTS, ids=[x[1] for x in BAD_EVENTS])
def test_hash_valid_malformed_event_is_structured_corrupt(tmp_path, event, label):
    result = inspect_unchanged(write_events(tmp_path / "journal", [event]))
    assert result["status"] == "CORRUPT"
    assert result["unknown_cost"] and not result["evaluation_eligible"]
    assert result["accounted_tokens"] is None
    assert "synthetic-do-not-echo" not in json.dumps(result)
    assert result.get("event_sequence") == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempt", True),
        ("attempt", 0),
        ("attempt", 73),
        ("input_bound", -1),
        ("input_bound", 16001),
        ("reservation", "3000"),
        ("reservation", -1),
        ("reservation", 999999),
        ("at", -1),
        ("at", "clock"),
        ("request", []),
        ("endpoint", "unverified"),
    ],
)
def test_invalid_reservation_fields_are_corrupt(tmp_path, field, value):
    events = prefix()
    events[1][field] = value
    result = inspect_unchanged(write_events(tmp_path / "journal", events))
    assert result["status"] == "CORRUPT" and result["unknown_cost"]
    assert not result["evaluation_eligible"] and result.get("event_sequence") == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", -1),
        ("output_tokens", True),
        ("total_tokens", 149),
        ("total_tokens", 2**80),
        ("accounted_tokens", 1),
        ("unknown_cost", "false"),
        ("complete", 1),
        ("overrun", "no"),
    ],
)
def test_invalid_settlement_fields_are_corrupt(tmp_path, field, value):
    event = settlement()
    event[field] = value
    result = inspect_unchanged(write_events(tmp_path / "journal", prefix() + [event]))
    assert result["status"] == "CORRUPT" and result["unknown_cost"]
    assert not result["evaluation_eligible"]


@pytest.mark.parametrize(
    "tail",
    [
        [settlement()],
        [observation((100, 50, 150))],
        [finished()],
        prefix()[:1] + prefix()[:1],
        prefix() + [settlement(), settlement()],
        prefix() + [settlement(), finished(), prefix()[1]],
    ],
)
def test_impossible_ordering_is_corrupt(tmp_path, tail):
    result = inspect_unchanged(write_events(tmp_path / "journal", tail))
    assert result["status"] == "CORRUPT" and not result["evaluation_eligible"]


@pytest.mark.parametrize("fault", ["truncated", "hash"])
def test_invalid_bytes_are_corrupt_without_repair(tmp_path, fault):
    root = write_events(tmp_path / "journal", prefix())
    path = root / "journal.jsonl"
    data = path.read_bytes()
    path.write_bytes(
        data[:-1]
        if fault == "truncated"
        else data.replace(b'"allocation":110000', b'"allocation":110001')
    )
    result = inspect_unchanged(root)
    assert result["status"] == "CORRUPT" and result["unknown_cost"]


@pytest.mark.parametrize("fault", ["missing", "modified"])
def test_runner_checks_fixture_hashes_before_pytest(tmp_path, monkeypatch, fault):
    from ripple import pilot

    script = Path(__file__).parents[1] / "dev/p2b/check_offline.py"
    spec = importlib.util.spec_from_file_location(
        "p2b_check_offline_under_test", script
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    fake = tmp_path / "root"
    metadata = []
    names = [
        "sympy/sine.py",
        "rich/Textualize__rich-2301/tests/_card_render.py",
        "rich/Textualize__rich-901/tests/_card_render.py",
    ]
    for name in names:
        data = b"synthetic-fixture\n"
        relative = ".ripple/hardening-fixtures/" + name
        path = fake / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        metadata.append(
            {
                "path": relative,
                "source": "synthetic",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    record = fake / "docs/evidence/p2b-hardening/fixture_sources.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps(metadata))
    damaged = fake / metadata[0]["path"]
    if fault == "missing":
        damaged.unlink()
    else:
        damaged.write_bytes(b"changed")
    monkeypatch.setattr(runner, "__file__", str(fake / "dev/p2b/check_offline.py"))
    monkeypatch.setattr(pilot, "verify_environment", lambda: None)
    monkeypatch.setattr(sys, "argv", ["check_offline.py"])
    monkeypatch.setattr(sys, "path", sys.path[:])
    monkeypatch.setenv("RIPPLE_P0_SYMPY_SINE_SOURCE", str(damaged))
    monkeypatch.setenv(
        "RIPPLE_P0_RICH_FIXTURES", str(fake / ".ripple/hardening-fixtures/rich")
    )
    monkeypatch.setattr(
        pytest, "main", lambda *a, **kw: pytest.fail("pytest must not start")
    )
    with pytest.raises(SystemExit, match="fixture"):
        runner.main()


@pytest.mark.parametrize("later", [(100, 50, 151), (101, 50, 150), (100, 51, 150)])
def test_inconsistent_components_remain_conflicted_and_uncertain(tmp_path, later):
    events = prefix() + [
        observation((100, 50, 150)),
        observation(later),
        settlement(),
        finished(),
    ]
    row = inspect_unchanged(write_events(tmp_path / "journal", events))["tasks"][0]
    assert row["conflicted"] and row["unknown_cost"]
    assert row["accounted_tokens"] >= 3000 and not row["evaluation_eligible"]


def test_interruption_after_prepared_summary_is_ineligible(gate, monkeypatch):
    settle(gate)
    original = gate.journal.append

    def append(event):
        if event["kind"] == "task_finished":
            raise KeyboardInterrupt()
        original(event)

    monkeypatch.setattr(gate.journal, "append", append)
    with pytest.raises(PilotStop):
        gate.finish()
    root = gate.journal.root
    gate.journal.close()
    prepared = json.loads(summary_path(root).read_text())
    assert (
        prepared["status"] == "COMPLETION_PREPARED"
        and not prepared["evaluation_eligible"]
    )
    assert not inspect_unchanged(root)["tasks"][0]["evaluation_eligible"]
    journal = Journal(root)
    try:
        with pytest.raises(PilotStop):
            Admission(journal, mock_contract())
    finally:
        journal.close()


def test_immutable_terminal_artifact_is_never_replaced(gate):
    settle(gate)
    path = summary_path(gate.journal.root)
    path.write_bytes(b'{"synthetic_existing_artifact":true}\n')
    path.chmod(0o600)
    before = path.read_bytes(), path.stat().st_mtime_ns
    with pytest.raises(PilotStop):
        gate.finish()
    assert before == (path.read_bytes(), path.stat().st_mtime_ns)
    root = gate.journal.root
    gate.journal.close()
    assert not inspect_unchanged(root)["tasks"][0]["evaluation_eligible"]


def test_valid_legacy_completion_remains_compatible(tmp_path):
    events = prefix() + [settlement(), finished()]
    root = write_events(tmp_path / "journal", events)
    legacy = {
        "task": TASK,
        "allocation": 110000,
        "status": "COMPLETED",
        "reason": "task_finished",
        "attempts": 1,
        "reserved_tokens": 3000,
        "accounted_tokens": 150,
        "observed_usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        },
        "observed_attempts": 1,
        "unknown_cost": False,
        "unresolved_attempts": [],
        "evaluation_eligible": True,
    }
    path = summary_path(root)
    path.write_bytes(canonical(legacy) + b"\n")
    path.chmod(0o600)
    assert inspect_unchanged(root)["tasks"][0]["evaluation_eligible"]
    state = accounting_state(admission.read_journal(root), root=root)
    assert state["tasks"][TASK] == inspect_unchanged(root)["tasks"][0]


@pytest.mark.parametrize(
    "kind",
    sorted(
        admission_kind
        for admission_kind in [
            "task_started",
            "wire_reserved",
            "wire_observed",
            "wire_metadata",
            "wire_settled",
            "usage_unknown",
            "stop",
            "task_finished",
            "logical_request",
            "logical_response",
        ]
    ),
)
def test_every_event_kind_requires_its_schema(tmp_path, kind):
    result = inspect_unchanged(write_events(tmp_path / "journal", [{"kind": kind}]))
    assert result["status"] == "CORRUPT" and result["unknown_cost"]
    assert result["event_sequence"] == 0 and result["event_type"] == kind
    assert not result["evaluation_eligible"]


@pytest.mark.parametrize("event", [{"kind": "unknown"}, settlement()])
def test_writer_rejects_invalid_events_without_changing_bytes(tmp_path, event):
    journal = Journal(tmp_path / "journal")
    path = journal.root / "journal.jsonl"
    before = path.read_bytes(), path.stat().st_mtime_ns
    try:
        with pytest.raises(PilotStop):
            journal.append(event)
        assert before == (path.read_bytes(), path.stat().st_mtime_ns)
    finally:
        journal.close()


def test_unexpected_reconstruction_error_is_structured_and_private(
    tmp_path, monkeypatch
):
    root = write_events(tmp_path / "journal", prefix())
    monkeypatch.setattr(
        admission,
        "accounting_state",
        lambda *a, **kw: (_ for _ in ()).throw(KeyError("synthetic-do-not-echo")),
    )
    result = inspect_unchanged(root)
    assert result["status"] == "CORRUPT" and result["unknown_cost"]
    assert not result[
        "evaluation_eligible"
    ] and "synthetic-do-not-echo" not in json.dumps(result)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_event_numbers_are_rejected(value):
    events = prefix()
    events[1]["at"] = value
    with pytest.raises(PilotStop):
        accounting_state(events)


def test_logical_response_requires_a_wire_attempt(tmp_path):
    logical = {"operation": "interpret", "prompt": "synthetic", "schema": {}}
    events = [
        prefix()[0],
        {"kind": "logical_request", **logical},
        {
            "kind": "logical_response",
            **logical,
            "request_sha256": digest(logical),
            "response": {},
            "diagnostics": [],
        },
    ]
    result = inspect_unchanged(write_events(tmp_path / "journal", events))
    assert result["status"] == "CORRUPT" and not result["evaluation_eligible"]


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_committed_terminal_evidence_is_not_automatically_repaired(gate, fault):
    settle(gate)
    gate.finish()
    path = summary_path(gate.journal.root)
    if fault == "missing":
        path.unlink()
        before = None
    else:
        path.write_bytes(b'{"corrupt":')
        before = path.read_bytes()
    gate.persist_summary()
    assert gate.summary_error
    assert (path.read_bytes() if path.exists() else None) == before
    root = gate.journal.root
    gate.journal.close()
    assert not inspect_unchanged(root)["tasks"][0]["evaluation_eligible"]


def test_failure_event_write_failure_has_an_independent_latch(gate, monkeypatch):
    settle(gate)
    gate.finish()
    before = summary_path(gate.journal.root).read_bytes()
    monkeypatch.setattr(
        gate.journal,
        "append",
        lambda event: (_ for _ in ()).throw(OSError("synthetic failure-event fault")),
    )
    with pytest.raises(PilotStop):
        gate.stop("terminal_summary_write_failed")
    root = gate.journal.root
    gate.journal.close()
    assert before == summary_path(root).read_bytes()
    assert not inspect_unchanged(root)["tasks"][0]["evaluation_eligible"]


def test_summary_directory_fsync_failure_never_commits_completion(gate, monkeypatch):
    settle(gate)
    monkeypatch.setattr(
        admission,
        "sync_directory",
        lambda *a: (_ for _ in ()).throw(OSError("synthetic fsync fault")),
    )
    with pytest.raises(PilotStop):
        gate.finish()
    assert not any(e["kind"] == "task_finished" for e in gate.journal.events)
    root = gate.journal.root
    gate.journal.close()
    assert not inspect_unchanged(root)["tasks"][0]["evaluation_eligible"]


def test_completion_append_error_keeps_failure_visible(gate, monkeypatch):
    settle(gate)
    original = gate.journal.append

    def append(event):
        if event["kind"] == "stop":
            raise OSError("synthetic stop fault")
        original(event)
        if event["kind"] == "task_finished":
            raise OSError("synthetic post-write fault")

    monkeypatch.setattr(gate.journal, "append", append)
    with pytest.raises(PilotStop):
        gate.finish()
    root = gate.journal.root
    gate.journal.close()
    row = inspect_unchanged(root)["tasks"][0]
    assert not row["evaluation_eligible"] and row["task"] == TASK


def test_reader_does_not_treat_a_short_read_as_the_whole_journal(tmp_path, monkeypatch):
    root = write_events(tmp_path / "journal", prefix())
    first_line_size = len(
        (root / "journal.jsonl").read_bytes().splitlines(keepends=True)[0]
    )
    original = admission.os.read
    monkeypatch.setattr(
        admission.os, "read", lambda fd, size: original(fd, min(size, first_line_size))
    )
    result = inspect_unchanged(root)
    assert result["attempts"] == 1 and result["accounted_tokens"] == 3000
    assert result["unknown_cost"]


def test_reader_eof_before_stat_size_is_corrupt(tmp_path, monkeypatch):
    root = write_events(tmp_path / "journal", prefix())
    first_line_size = len(
        (root / "journal.jsonl").read_bytes().splitlines(keepends=True)[0]
    )
    original = admission.os.read
    reads = []

    def read(fd, size):
        reads.append(size)
        return original(fd, min(size, first_line_size)) if len(reads) == 1 else b""

    monkeypatch.setattr(admission.os, "read", read)
    result = inspect_unchanged(root)
    assert result["status"] == "CORRUPT" and result["unknown_cost"]
    assert result["accounted_tokens"] is None and not result["evaluation_eligible"]
