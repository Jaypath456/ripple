import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ripple.agent import TraceWriter
from ripple.agent_models import (
    AffectedComponent,
    ChangeImpactReport,
    ChangeKind,
    RunStats,
)
from ripple.cli import main
from ripple.diff_models import DiffFinding
from ripple.diffing import parse_diff
from ripple.llm import ScriptedLLM
from ripple.scanner import scan_repository
from ripple.verification import (
    VerificationError,
    _incompatible_signature,
    _investigate,
    verify_repository,
)
from ripple.verify_tools import VerificationToolSession


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "RIPPLE Tests")
    _git(repo, "config", "user.email", "ripple@example.test")
    (repo / ".git/info/exclude").write_text(".ripple/\nout/\nverification-output/\n")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src/core.py").write_text("def target(value):\n    return value\n")
    (repo / "src/caller.py").write_text(
        "from core import target\n\ndef caller():\n    return target(1)\n"
    )
    (repo / "src/adjacent.py").write_text(
        "from core import target\n\ndef adjacent():\n    return target(2)\n"
    )
    (repo / "src/high.py").write_text("VALUE = 'high'\n")
    (repo / "src/medium.py").write_text("VALUE = 'medium'\n")
    (repo / "src/low.py").write_text("VALUE = 'low'\n")
    (repo / "src/z_unrelated.py").write_text("VALUE = 1\n")
    (repo / "tests/test_core.py").write_text(
        "from core import target\n\ndef test_target():\n    assert target(1) == 1\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")


def _report(repo: Path, base: str, *, latest: bool = False) -> Path:
    report = ChangeImpactReport(
        report_id="fixture-report",
        request="Change target behavior",
        commit=base,
        status="completed",
        affected_components=(
            AffectedComponent(
                target="src/core.py::target",
                change_type="modify",
                change_kind=ChangeKind.BUSINESS_LOGIC,
                reason="Primary implementation",
                confidence="high",
                evidence=("e1",),
            ),
            AffectedComponent(
                target="src/high.py",
                change_type="modify",
                change_kind=ChangeKind.BUSINESS_LOGIC,
                reason="High prediction",
                confidence="high",
                evidence=("e2",),
            ),
            AffectedComponent(
                target="src/medium.py",
                change_type="modify",
                change_kind=ChangeKind.BUSINESS_LOGIC,
                reason="Medium prediction",
                confidence="medium",
                evidence=("e3",),
            ),
            AffectedComponent(
                target="src/low.py",
                change_type="modify",
                change_kind=ChangeKind.BUSINESS_LOGIC,
                reason="Low prediction",
                confidence="low",
                evidence=("e4",),
            ),
        ),
        run_stats=RunStats(
            model="scripted",
            tool_calls=0,
            duplicate_calls=0,
            llm_calls=0,
            runtime_seconds=0,
            stop_reason="submitted",
            dirty=False,
        ),
    )
    reports = repo / ".ripple" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / ("latest-report.json" if latest else "fixture-report.json")
    path.write_text(report.model_dump_json(indent=2))
    return path


def _categories(run, category: str) -> set[str]:
    return {item.path for item in run.analysis.findings if item.category == category}


def test_all_primary_classifications_and_planted_anomalies(tmp_path: Path) -> None:
    _init(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD")
    report = _report(tmp_path, base)
    (tmp_path / "src/core.py").write_text(
        "def target(value, required):\n    return value + required\n"
    )
    (tmp_path / "src/adjacent.py").write_text(
        "from core import target\n\ndef adjacent():\n    return target(2, 1)\n"
    )
    (tmp_path / "src/z_unrelated.py").write_text("VALUE = 99\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "implementation with anomalies")
    head = _git(tmp_path, "rev-parse", "HEAD")

    run = verify_repository(
        tmp_path,
        report_value=str(report),
        requested_range=f"{base}..{head}",
        output_root=tmp_path / "verification-output",
    )
    assert _categories(run, "expected") == {"src/core.py"}
    assert "src/adjacent.py" in _categories(run, "adjacent")
    assert "src/z_unrelated.py" in _categories(run, "unexpected")
    assert _categories(run, "missing_predicted") == {
        "src/high.py",
        "src/medium.py",
    }
    assert "src/core.py" in _categories(run, "missing_test")
    assert "src/caller.py" in _categories(run, "stale_caller")
    assert run.analysis.file_precision == 0.25
    assert run.analysis.file_recall == pytest.approx(1 / 3)
    assert run.json_path.is_file() and run.markdown_path.is_file()
    assert run.trace_path.is_file()
    assert "unexpected" in run.markdown_path.read_text()
    events = [
        json.loads(line)["event"] for line in run.trace_path.read_text().splitlines()
    ]
    assert events[0] == "verification_started"
    assert events[-1] == "verification_finished"


def test_control_has_no_unexpected_missing_test_or_stale_caller(tmp_path: Path) -> None:
    _init(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD")
    report = _report(tmp_path, base)
    (tmp_path / "src/core.py").write_text("def target(value):\n    return value + 1\n")
    (tmp_path / "tests/test_core.py").write_text(
        "from core import target\n\ndef test_target():\n    assert target(1) == 2\n"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "correct implementation")
    head = _git(tmp_path, "rev-parse", "HEAD")
    run = verify_repository(
        tmp_path,
        report_value=str(report),
        requested_range=f"{base}..{head}",
        output_root=tmp_path / "out",
    )
    categories = {item.category for item in run.analysis.findings}
    assert "unexpected" not in categories
    assert "missing_test" not in categories
    assert "stale_caller" not in categories


def test_changed_caller_avoids_stale_warning(tmp_path: Path) -> None:
    _init(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD")
    report = _report(tmp_path, base)
    (tmp_path / "src/core.py").write_text(
        "def target(value, required):\n    return value + required\n"
    )
    (tmp_path / "src/caller.py").write_text(
        "from core import target\n\ndef caller():\n    return target(1, 2)\n"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "update caller")
    head = _git(tmp_path, "rev-parse", "HEAD")
    run = verify_repository(
        tmp_path,
        report_value=str(report),
        requested_range=f"{base}..{head}",
        output_root=tmp_path / "out",
    )
    assert "src/caller.py" not in _categories(run, "stale_caller")


def test_predicted_rename_matches_without_missing_or_unexpected(tmp_path: Path) -> None:
    _init(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD")
    report = _report(tmp_path, base)
    _git(tmp_path, "mv", "src/core.py", "src/renamed_core.py")
    _git(tmp_path, "commit", "-qm", "rename predicted file")
    head = _git(tmp_path, "rev-parse", "HEAD")
    run = verify_repository(
        tmp_path,
        report_value=str(report),
        requested_range=f"{base}..{head}",
        output_root=tmp_path / "out",
    )
    assert "src/renamed_core.py" in _categories(run, "expected")
    assert "src/core.py" not in _categories(run, "missing_predicted")
    assert "src/renamed_core.py" not in _categories(run, "unexpected")


def test_adjacent_via_cochange_without_import_edge(tmp_path: Path) -> None:
    _init(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD")
    report = _report(tmp_path, base)
    (tmp_path / "src/core.py").write_text("def target(value):\n    return value + 1\n")
    (tmp_path / "src/z_unrelated.py").write_text("VALUE = 2\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "cochange pair")
    base2 = _git(tmp_path, "rev-parse", "HEAD")
    report = _report(tmp_path, base2)
    (tmp_path / "src/z_unrelated.py").write_text("VALUE = 3\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "implementation")
    head = _git(tmp_path, "rev-parse", "HEAD")
    run = verify_repository(
        tmp_path,
        report_value=str(report),
        requested_range=f"{base2}..{head}",
        output_root=tmp_path / "out",
    )
    adjacent = [
        item for item in run.analysis.findings if item.path == "src/z_unrelated.py"
    ]
    assert adjacent[0].category == "adjacent"
    assert adjacent[0].evidence[0].startswith("cochange:")


def test_required_keyword_only_and_unchanged_signature_detection() -> None:
    old = __import__("ast").parse("def f(value): pass").body[0]
    keyword = __import__("ast").parse("def f(value, *, required): pass").body[0]
    same = __import__("ast").parse("def f(value):\n    pass\n").body[0]
    assert "keyword-only" in _incompatible_signature(old, keyword)
    assert _incompatible_signature(old, same) is None


def test_investigation_is_bounded_and_rejects_unsupported_or_empty_evidence(
    tmp_path: Path,
) -> None:
    _init(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD")
    index = scan_repository(tmp_path)
    (tmp_path / "src/z_unrelated.py").write_text("VALUE = 2\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "unexpected")
    head = _git(tmp_path, "rev-parse", "HEAD")
    snapshot = parse_diff(tmp_path, base, head)
    finding = DiffFinding(
        path="src/z_unrelated.py",
        category="unexpected",
        verdict="unexplained",
        explanation="No relationship",
        evidence=("diff:M",),
    )
    repeat = {
        "tool_name": "file_diff",
        "arguments": {"path": "src/z_unrelated.py"},
        "explanation": "Inspect again",
    }
    _, calls, _ = _investigate(
        finding,
        "Change target behavior",
        ScriptedLLM(investigations=[repeat] * 7),
        VerificationToolSession(index, snapshot),
        TraceWriter(tmp_path / "bounded.jsonl"),
    )
    assert calls == 8

    unsupported, _, _ = _investigate(
        finding,
        "Change target behavior",
        ScriptedLLM(
            investigations=[
                {
                    "tool_name": "submit_verdict",
                    "verdict": "banana",
                    "explanation": "Unsupported verdict",
                    "evidence": ["v1"],
                }
            ]
        ),
        VerificationToolSession(index, snapshot),
        TraceWriter(tmp_path / "unsupported.jsonl"),
    )
    assert unsupported.verdict == "unexplained"

    unsupported, _, _ = _investigate(
        finding,
        "Change target behavior",
        ScriptedLLM(
            investigations=[
                {
                    "tool_name": "submit_verdict",
                    "verdict": "justified",
                    "explanation": "No actual evidence",
                    "evidence": [],
                }
            ]
        ),
        VerificationToolSession(index, snapshot),
        TraceWriter(tmp_path / "empty.jsonl"),
    )
    assert unsupported.verdict == "unexplained"
    assert "lacked valid" in unsupported.explanation

    justified, _, _ = _investigate(
        finding,
        "Change target behavior",
        ScriptedLLM(
            investigations=[
                {
                    "tool_name": "submit_verdict",
                    "verdict": "justified",
                    "explanation": "The file diff directly implements the request.",
                    "evidence": ["v1"],
                }
            ]
        ),
        VerificationToolSession(index, snapshot),
        TraceWriter(tmp_path / "valid.jsonl"),
    )
    assert justified.verdict == "justified"
    assert justified.evidence == ("v1",)


def test_cli_verify_explicit_latest_unknown_and_invalid_range(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD")
    report = _report(tmp_path, base, latest=True)
    (tmp_path / "src/core.py").write_text("def target(value):\n    return value + 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "implementation")
    head = _git(tmp_path, "rev-parse", "HEAD")
    assert (
        main(
            [
                "verify",
                str(tmp_path),
                "--report",
                str(report),
                "--range",
                f"{base}..{head}",
            ]
        )
        == 0
    )
    assert "Verification:" in capsys.readouterr().out
    assert (
        main(
            [
                "verify",
                str(tmp_path),
                "--report",
                "latest",
                "--range",
                f"{base}..{head}",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "verify",
                str(tmp_path),
                "--report",
                "unknown",
                "--range",
                f"{base}..{head}",
            ]
        )
        == 1
    )
    assert main(["verify", str(tmp_path), "--report", "latest", "--range", "bad"]) == 1


def test_unknown_report_direct_error(tmp_path: Path) -> None:
    _init(tmp_path)
    with pytest.raises(VerificationError, match="report not found"):
        verify_repository(
            tmp_path,
            report_value="unknown",
            requested_range="HEAD~1..HEAD",
        )


def test_ignored_file_stays_visible_without_normal_judgment(tmp_path: Path) -> None:
    _init(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD")
    report = _report(tmp_path, base)
    (tmp_path / "README.md").write_text("Documentation only.\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-qm", "docs")
    head = _git(tmp_path, "rev-parse", "HEAD")
    run = verify_repository(
        tmp_path,
        report_value=str(report),
        requested_range=f"{base}..{head}",
        output_root=tmp_path / "out",
    )
    assert [item.path for item in run.analysis.ignored_files] == ["README.md"]
    assert "README.md" not in {item.path for item in run.analysis.findings}


def test_mismatched_requested_base_is_persisted_as_warning(tmp_path: Path) -> None:
    _init(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD")
    report = _report(tmp_path, base)
    (tmp_path / "src/core.py").write_text("def target(value):\n    return value + 1\n")
    _git(tmp_path, "add", "src/core.py")
    _git(tmp_path, "commit", "-qm", "middle")
    middle = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "src/core.py").write_text("def target(value):\n    return value + 2\n")
    _git(tmp_path, "add", "src/core.py")
    _git(tmp_path, "commit", "-qm", "head")
    head = _git(tmp_path, "rev-parse", "HEAD")
    run = verify_repository(
        tmp_path,
        report_value=str(report),
        requested_range=f"{middle}..{head}",
        output_root=tmp_path / "out",
    )
    assert run.analysis.base == base
    assert run.analysis.base_warning
    assert middle in run.analysis.base_warning


def test_soft_delete_stage_b_control_demo(tmp_path: Path) -> None:
    shutil.copytree("tests/fixtures/soft_delete_app", tmp_path, dirs_exist_ok=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "RIPPLE Tests")
    _git(tmp_path, "config", "user.email", "ripple@example.test")
    (tmp_path / ".git/info/exclude").write_text(".ripple/\nout/\n")
    fixture_test = tmp_path / "tests/user_checks.py"
    fixture_test.write_text(
        "from app.models import User\nfrom app.routes import remove_user\n"
        "from app.service import delete_user\n\n"
        "def test_delete_user() -> None:\n    delete_user(User())\n"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "pre-change soft delete fixture")
    base = _git(tmp_path, "rev-parse", "HEAD")
    report = ChangeImpactReport(
        report_id="soft-delete-report",
        request="Add soft-delete support for users",
        commit=base,
        status="completed",
        affected_components=(
            AffectedComponent(
                target="app/models.py::User",
                change_type="modify",
                change_kind=ChangeKind.DATA_MODEL,
                reason="Persist deletion state",
                confidence="high",
                evidence=("e1",),
            ),
            AffectedComponent(
                target="app/service.py::delete_user",
                change_type="modify",
                change_kind=ChangeKind.BUSINESS_LOGIC,
                reason="Change deletion behavior",
                confidence="high",
                evidence=("e2",),
            ),
            AffectedComponent(
                target="app/migrations/<proposed migration>",
                change_type="new_file",
                change_kind=ChangeKind.MIGRATION,
                reason="Add deletion-state column",
                confidence="high",
                evidence=("e1", "e3"),
            ),
        ),
        run_stats=RunStats(
            model="scripted",
            tool_calls=0,
            duplicate_calls=0,
            llm_calls=0,
            runtime_seconds=0,
            stop_reason="submitted",
            dirty=False,
        ),
    )
    reports = tmp_path / ".ripple/reports"
    reports.mkdir(parents=True)
    report_path = reports / "soft-delete-report.json"
    report_path.write_text(report.model_dump_json(indent=2))

    models = tmp_path / "app/models.py"
    models.write_text(
        models.read_text()
        + "    deleted: Mapped[bool] = mapped_column(default=False)\n"
    )
    (tmp_path / "app/service.py").write_text(
        "from app.models import User\n\n\ndef delete_user(user: User) -> None:\n"
        "    user.deleted = True\n"
    )
    (tmp_path / "app/routes.py").write_text(
        "from fastapi import APIRouter\n\nfrom app.service import delete_user\n\n"
        "router = APIRouter()\n\n@router.delete('/users/{user_id}')\n"
        "def remove_user(user_id: int) -> None:\n    delete_user(user_id)\n"
    )
    (tmp_path / "app/migrations/0002_soft_delete.py").write_text(
        '"""Add User.deleted."""\nrevision = "0002"\n'
    )
    (tmp_path / "tests/user_checks.py").write_text(
        "from app.models import User\nfrom app.service import delete_user\n\n"
        "def test_soft_delete_user() -> None:\n"
        "    user = User()\n    delete_user(user)\n    assert user.deleted\n"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "implement soft delete")
    head = _git(tmp_path, "rev-parse", "HEAD")
    run = verify_repository(
        tmp_path,
        report_value=str(report_path),
        requested_range=f"{base}..{head}",
        output_root=tmp_path / "out",
    )
    assert _categories(run, "expected") >= {
        "app/models.py",
        "app/service.py",
        "app/migrations/0002_soft_delete.py",
    }
    assert "app/routes.py" in _categories(run, "adjacent")
    assert "missing_test" not in {item.category for item in run.analysis.findings}
    assert "stale_caller" not in {item.category for item in run.analysis.findings}
