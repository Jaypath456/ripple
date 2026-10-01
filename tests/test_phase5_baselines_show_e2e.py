import shutil
import subprocess
from pathlib import Path

import pytest

from ripple.agent import AgentController
from ripple.agent_models import FeatureRequest
from ripple.baselines import cochange_baseline
from ripple.llm import ScriptedLLM
from ripple.phase5_baselines import one_shot_baseline, react_baseline
from ripple.scanner import scan_repository
from ripple.show_run import RunNotFoundError, replay_run


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _soft_delete_repo(tmp_path: Path):
    shutil.copytree("tests/fixtures/soft_delete_app", tmp_path, dirs_exist_ok=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "RIPPLE Tests")
    _git(tmp_path, "config", "user.email", "ripple@example.test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    return scan_repository(tmp_path)


def test_b2_is_deterministic_and_excludes_tests(indexed_repo) -> None:
    first = cochange_baseline(indexed_repo, "refresh authentication token")
    second = cochange_baseline(indexed_repo, "refresh authentication token")
    assert first == second
    assert first.baseline == "B2"
    assert all("test" not in item.path.name for item in first.source_predictions)


def test_b3_uses_one_call_preserves_order_and_drops_hallucination(indexed_repo) -> None:
    llm = ScriptedLLM(
        rankings=[{"paths": ["src/service.py", "missing.py", "src/auth.py"]}]
    )
    run = one_shot_baseline(
        indexed_repo, FeatureRequest(text="Refresh authentication tokens"), llm
    )
    assert run.llm_calls == 1
    assert [item.path.as_posix() for item in run.prediction.source_predictions] == [
        "src/service.py",
        "src/auth.py",
    ]
    assert run.dropped_predictions == ("missing or non-source path: missing.py",)


def test_b4_has_no_ledger_expand_or_validator_and_basic_path_filter(
    indexed_repo,
) -> None:
    llm = ScriptedLLM(
        react_decisions=[
            {
                "tool_name": "search_code",
                "arguments": {"query": "refresh token", "limit": 3},
            },
            {
                "tool_name": "submit_report",
                "arguments": {},
                "predicted_paths": ["invented.py", "src/auth.py"],
            },
        ]
    )
    run = react_baseline(
        indexed_repo, FeatureRequest(text="Refresh authentication tokens"), llm
    )
    assert run.llm_calls == 2
    assert run.tool_calls == 1
    assert [item.path.as_posix() for item in run.prediction.source_predictions] == [
        "src/auth.py"
    ]
    assert run.dropped_predictions


def test_show_run_replays_without_model_call(indexed_repo, tmp_path: Path) -> None:
    llm = ScriptedLLM(
        interpretations=[
            {
                "summary": "Refresh auth token",
                "change_kinds": ["auth"],
                "search_terms": ["refresh", "auth", "token"],
            }
        ],
        decisions=[
            {
                "tool_name": "search_code",
                "arguments": {"query": "refresh auth token", "limit": 15},
                "reason": "repeat seed search",
                "ledger_updates": [],
            }
        ]
        * 4,
    )
    run = AgentController(indexed_repo, llm, output_root=tmp_path / ".ripple").run(
        FeatureRequest(text="Refresh authentication tokens")
    )
    output = replay_run(run.report.report_id, tmp_path)
    assert "request=Refresh authentication tokens" in output
    assert "config=full-report-v1" in output
    assert "Finished: status=abstained" in output
    with pytest.raises(RunNotFoundError, match="run not found"):
        replay_run("unknown", tmp_path)


def test_soft_delete_full_report_end_to_end(tmp_path: Path) -> None:
    index = _soft_delete_repo(tmp_path)
    target = "app/models.py::User"
    llm = ScriptedLLM(
        interpretations=[
            {
                "summary": "Add soft-delete support for users",
                "change_kinds": ["data_model", "migration", "business_logic"],
                "search_terms": ["User", "delete_user", "soft delete"],
            }
        ],
        decisions=[
            {
                "tool_name": "inspect_symbol",
                "arguments": {"target": target},
                "reason": "Inspect the persisted user model",
                "ledger_updates": [],
            },
            {
                "tool_name": "find_references",
                "arguments": {"symbol_id": target},
                "reason": "Find user model consumers",
                "ledger_updates": [
                    {
                        "target": target,
                        "status": "confirmed",
                        "reason": "Soft delete requires persisted User state",
                        "evidence_ids": ["e2"],
                    }
                ],
            },
            {
                "tool_name": "find_tests",
                "arguments": {"target": target},
                "reason": "Find mapped user tests",
                "ledger_updates": [],
            },
            {
                "tool_name": "submit_report",
                "arguments": {},
                "reason": "Evidence checks are complete",
                "ledger_updates": [],
            },
        ],
        reports=[
            {
                "affected_components": [
                    {
                        "target": target,
                        "change_type": "modify",
                        "change_kind": "data_model",
                        "reason": "Store soft-delete state on User",
                        "confidence": "high",
                        "evidence": ["e2"],
                    }
                ],
                "risks": [
                    {
                        "description": "Authentication may include deleted users.",
                        "severity": "high",
                        "related_targets": [target],
                        "evidence": ["e2"],
                    }
                ],
            }
        ],
    )
    run = AgentController(index, llm, output_root=tmp_path / "artifacts").run(
        FeatureRequest(text="Add soft-delete support for users")
    )
    assert run.report.status == "completed"
    assert "app/migrations/<proposed migration>" in {
        item.target for item in run.report.affected_components
    }
    assert "app/auth.py" in {item.target for item in run.report.regression_areas}
    assert run.report.suggested_tests[0].test_path == "tests/user_checks.py"
    assert run.report.implementation_order[-1] == "tests/user_checks.py"
    assert run.report.risks[0].evidence == ("e2",)
    assert run.markdown_path.is_file()
