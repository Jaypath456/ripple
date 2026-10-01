import shutil
import subprocess
from pathlib import Path

from ripple.agent_models import (
    AffectedComponent,
    CandidateUpdate,
    ChangeImpactReport,
    ChangeKind,
    FeatureIntent,
    GroundedClaim,
    ReportDraft,
    Risk,
    RunStats,
)
from ripple.expand import expand_report
from ripple.ledger import CandidateLedger, EvidenceRecord
from ripple.render import render_markdown
from ripple.scanner import scan_repository
from ripple.tools import ToolSession
from ripple.validate import validate_report_draft


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _fixture(tmp_path: Path):
    shutil.copytree("tests/fixtures/soft_delete_app", tmp_path, dirs_exist_ok=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "RIPPLE Tests")
    _git(tmp_path, "config", "user.email", "ripple@example.test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    return scan_repository(tmp_path)


def _record(ledger: CandidateLedger, session: ToolSession, name: str, args: dict):
    result = session.invoke(name, args)
    data = result.data if isinstance(result.data, dict) else {}
    touched = set()
    for key in ("path", "id", "symbol_id", "target", "directory"):
        if data.get(key):
            touched.add(str(data[key]))
    for item in data.get("neighbors", []):
        touched.add(str(item["path"]))
    for item in data.get("references", []):
        touched.add(str(item["path"]))
    for item in data.get("tests", []):
        touched.add(str(item["test_path"]))
    for fact in data.get("facts", []):
        touched.update(
            str(fact[key])
            for key in ("path", "symbol_id", "directory", "handler")
            if fact.get(key)
        )
    ledger.add_evidence(
        EvidenceRecord(
            evidence_id=result.evidence_id,
            tool_name=name,
            arguments=args,
            result=result,
            touched_targets=frozenset(touched),
            strong=name != "search_code" and result.ok,
        )
    )
    return result


def _expanded(tmp_path: Path):
    index = _fixture(tmp_path)
    ledger = CandidateLedger(index)
    session = ToolSession(index)
    target = "app/models.py::User"
    inspected = _record(ledger, session, "inspect_symbol", {"target": target})
    ledger.seed(target, "model match", inspected.evidence_id)
    assert ledger.apply(
        CandidateUpdate(
            target=target,
            status="confirmed",
            reason="User is the persisted model",
            evidence_ids=(inspected.evidence_id,),
        ),
        inspected.evidence_id,
    )
    _record(ledger, session, "find_references", {"symbol_id": target})
    _record(ledger, session, "find_tests", {"target": target})
    _record(
        ledger,
        session,
        "get_dependencies",
        {"path": "app/models.py", "direction": "imported_by", "depth": 1},
    )
    for kind in ("models", "migrations", "routes", "settings"):
        _record(ledger, session, "repo_facts", {"kind": kind})
    intent = FeatureIntent(
        summary="Add soft delete support for users",
        change_kinds=(ChangeKind.DATA_MODEL, ChangeKind.MIGRATION),
        search_terms=("soft", "delete", "users"),
    )
    return index, ledger, expand_report(index, intent, ledger)


def test_expand_keeps_regression_separate_and_adds_migration_tests_order(
    tmp_path: Path,
) -> None:
    _, _, expanded = _expanded(tmp_path)
    assert expanded.components[0].change_type == "new_file"
    assert expanded.components[0].target == "app/migrations/<proposed migration>"
    assert "app/service.py" in {item.target for item in expanded.regression_areas}
    assert "app/service.py" not in {item.target for item in expanded.components}
    assert expanded.tests[0].test_path == "tests/user_checks.py"
    assert expanded.implementation_order.index(
        "app/models.py"
    ) < expanded.implementation_order.index("app/migrations/<proposed migration>")
    assert expanded.implementation_order[-1] == "tests/user_checks.py"


def test_validator_keeps_deterministic_migration_and_requires_risk_evidence(
    tmp_path: Path,
) -> None:
    index, ledger, expanded = _expanded(tmp_path)
    target = "app/models.py::User"
    evidence = ledger.candidates[target].evidence_ids[0]
    draft = ReportDraft(
        affected_components=(
            AffectedComponent(
                target=target,
                change_type="modify",
                change_kind=ChangeKind.DATA_MODEL,
                reason="User persistence changes",
                confidence="high",
                evidence=(evidence,),
            ),
            AffectedComponent(
                target="made/up/migration.py",
                change_type="new_file",
                change_kind=ChangeKind.MIGRATION,
                reason="unsupported location",
                confidence="high",
                evidence=(evidence,),
            ),
        ),
        risks=(
            Risk(
                description="Callers may assume all users are active.",
                severity="medium",
                related_targets=(target,),
                evidence=(evidence,),
            ),
            Risk(
                description="Unsupported risk.",
                severity="high",
                related_targets=(target,),
                evidence=("e999",),
            ),
        ),
    )
    result = validate_report_draft(draft, index, ledger, expanded)
    assert "app/migrations/<proposed migration>" in {
        item.target for item in result.components
    }
    assert len(result.risks) == 1
    assert any("invalid migration" in item for item in result.dropped_claims)
    assert any("risk without valid evidence" in item for item in result.dropped_claims)


def test_unsupported_fact_claim_is_dropped(tmp_path: Path) -> None:
    index, ledger, expanded = _expanded(tmp_path)
    draft = ReportDraft(
        affected_components=(),
        schema_changes=(
            GroundedClaim(
                description="Invented schema claim",
                targets=("app/models.py::User",),
                evidence=("e999",),
            ),
        ),
    )
    result = validate_report_draft(draft, index, ledger, expanded)
    assert all(
        item.description != "Invented schema claim" for item in result.schema_changes
    )
    assert any("unsupported schema_changes" in item for item in result.dropped_claims)


def test_markdown_is_stable_and_handles_empty_sections() -> None:
    report = ChangeImpactReport(
        report_id="run-1",
        request="Add a feature",
        commit="a" * 40,
        status="abstained",
        affected_components=(),
        run_stats=RunStats(
            model="scripted",
            tool_calls=0,
            duplicate_calls=0,
            llm_calls=0,
            runtime_seconds=1.0,
            stop_reason="no_progress",
            dirty=False,
        ),
    )
    rendered = render_markdown(report)
    assert rendered == render_markdown(report)
    assert rendered.startswith("# RIPPLE Change Impact Report")
    assert rendered.count("_None._") >= 8
    assert "Configuration: `full-report-v1`" in rendered
