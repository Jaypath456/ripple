"""Regenerate the Guided Replay fixtures with the real RIPPLE core.

Run from the repository root:  python demo/build_fixtures.py

A deterministic Git repository is built from ``fixtures/sample_app`` (fixed author and
dates, so commit SHAs are stable). For each scenario the real ``AgentController``
(Python-owned tools, candidate ledger, Expand, and report validator) runs Stage A, and
the real ``verify_repository`` runs Stage B on the scenario's implementation commit.

Only the model's decisions are scripted, through RIPPLE's own ``ScriptedLLM`` test
double, so replay is reproducible and needs no API key. Every tool result, ledger
transition, dropped claim, report, and Stage B category is produced by core code.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo.logic import capture_run
from ripple.agent import AgentController
from ripple.agent_models import FeatureRequest
from ripple.llm import ScriptedLLM
from ripple.render import render_markdown
from ripple.scanner import scan_repository
from ripple.verification import verify_repository
from ripple.verification_render import render_verification_markdown

DEMO = Path(__file__).resolve().parent
SAMPLE = DEMO / "fixtures" / "sample_app"
OUTPUT = DEMO / "fixtures" / "replay"
SCRIPTED_MODEL = "scripted-demo-policy"
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "RIPPLE Demo",
    "GIT_AUTHOR_EMAIL": "demo@ripple.invalid",
    "GIT_COMMITTER_NAME": "RIPPLE Demo",
    "GIT_COMMITTER_EMAIL": "demo@ripple.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(repo: Path, *args: str, date: str = "2026-01-01T12:00:00Z") -> str:
    env = {
        **os.environ,
        **_GIT_ENV,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout


def _overlay(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, dirs_exist_ok=True)


def _commit(repo: Path, message: str, day: int) -> str:
    _git(repo, "add", "-A")
    date = f"2026-01-{day:02d}T12:00:00Z"
    _git(repo, "commit", "-q", "-m", message, date=date)
    return _git(repo, "rev-parse", "HEAD").strip()


def build_sample_repo(destination: Path) -> dict[str, str]:
    """Create the sample repository with history and one branch per scenario."""

    destination.mkdir(parents=True)
    _git(destination, "init", "-q", "-b", "main")
    _git(destination, "config", "core.hooksPath", os.devnull)
    # Older versions first, so co_changed sees real co-change history.
    _overlay(SAMPLE / "base", destination)
    _overlay(SAMPLE / "history" / "v1", destination)
    _overlay(SAMPLE / "history" / "v2", destination)
    _commit(destination, "initial users and orders", 1)
    _overlay(SAMPLE / "base", destination)
    _overlay(SAMPLE / "history" / "v2", destination)
    _commit(destination, "add user names and ordered listing", 2)
    _overlay(SAMPLE / "base", destination)
    base = _commit(destination, "add order cancellation", 3)
    commits = {"base": base}
    for day, scenario in enumerate(sorted(SCENARIOS), start=10):
        _git(destination, "switch", "-q", "-c", f"impl/{scenario}", base)
        _overlay(SAMPLE / "scenarios" / scenario, destination)
        commits[scenario] = _commit(destination, f"implement {scenario}", day)
        _git(destination, "switch", "-q", "main")
    return commits


class RecordingLLM:
    """Pass-through wrapper that records the exact prompts RIPPLE sends."""

    def __init__(self, inner: ScriptedLLM) -> None:
        self.inner = inner
        self.model = inner.model
        self.prompts: list[dict[str, str]] = []

    def __getattr__(self, operation: str):
        method = getattr(self.inner, operation)

        def call(prompt: str):
            self.prompts.append({"operation": operation, "prompt": prompt})
            return method(prompt)

        return call


def _decision(tool: str, arguments: dict, reason: str, *updates: tuple) -> dict:
    return {
        "tool_name": tool,
        "arguments": arguments,
        "reason": reason,
        "ledger_updates": [
            {"target": target, "status": status, "reason": why, "evidence_ids": [ev]}
            for target, status, why, ev in updates
        ],
    }


USER = "users/models.py::User"
DELETE = "users/service.py::delete_user"
INVOICE = "billing/service.py::delete_invoice"

SCENARIOS: dict[str, dict[str, Any]] = {
    "soft_delete": {
        "title": "Add soft-delete support to users",
        "request": "Add soft-delete support to users so deleted accounts are hidden "
        "instead of removed.",
        "implementation": "Adds User.deleted_at, a new migration, soft-delete logic in "
        "the user service, and an updated user test.",
        "teaches": "Model + service impact, a deterministic migration proposal, mapped "
        "tests, a rejected look-alike candidate, and a dropped unsupported claim.",
        "interpretation": {
            "summary": "Hide deleted users by recording a deletion timestamp",
            "change_kinds": ["data_model", "migration", "business_logic"],
            "search_terms": ["soft delete", "delete_user", "User", "users", "deleted"],
        },
        "decisions": [
            _decision("inspect_symbol", {"target": USER}, "Inspect the user model."),
            _decision(
                "find_references",
                {"symbol_id": USER},
                "Find code that depends on the user model.",
                (USER, "confirmed", "Soft delete needs persisted state on User.", "e2"),
            ),
            _decision("find_tests", {"target": USER}, "Find tests for the user model."),
            _decision(
                "inspect_symbol", {"target": DELETE}, "Inspect the deletion function."
            ),
            _decision(
                "find_references",
                {"symbol_id": DELETE},
                "Find callers of delete_user.",
                (DELETE, "confirmed", "delete_user must stop removing rows.", "e5"),
            ),
            _decision("find_tests", {"target": DELETE}, "Find tests for delete_user."),
            _decision(
                "inspect_symbol",
                {"target": INVOICE},
                "Check whether invoice deletion is related.",
            ),
            _decision(
                "repo_facts",
                {"kind": "migrations"},
                "Check for an existing migration directory.",
                (
                    INVOICE,
                    "rejected",
                    "Voids invoices; unrelated to user accounts.",
                    "e8",
                ),
            ),
            _decision("submit_report", {}, "Confirmed targets are checked."),
        ],
        "report": {
            "affected_components": [
                {
                    "target": USER,
                    "change_type": "modify",
                    "change_kind": "data_model",
                    "reason": "Add a nullable deleted_at timestamp to User.",
                    "confidence": "high",
                    "evidence": ["e2"],
                },
                {
                    "target": DELETE,
                    "change_type": "modify",
                    "change_kind": "business_logic",
                    "reason": "Stamp deleted_at instead of deleting the row.",
                    "confidence": "high",
                    "evidence": ["e5"],
                },
                {
                    "target": "users/auth.py::login",
                    "change_type": "modify",
                    "change_kind": "auth",
                    "reason": "Deleted users must not log in.",
                    "confidence": "medium",
                    "evidence": ["e99"],
                },
            ],
            "risks": [
                {
                    "description": "Queries that list users may still return "
                    "soft-deleted accounts.",
                    "severity": "medium",
                    "related_targets": [DELETE],
                    "evidence": ["e5"],
                }
            ],
        },
    },
    "required_argument": {
        "title": "Add a required argument to delete_user",
        "request": "Add a required actor argument to delete_user so every deletion "
        "is audited.",
        "implementation": "Adds a required actor parameter, updates the API route, "
        "but leaves the admin purge job calling the old signature.",
        "teaches": "A signature change whose unchanged caller Stage B flags as "
        "stale_caller.",
        "interpretation": {
            "summary": "Require an actor when deleting users, for auditing",
            "change_kinds": ["api", "business_logic"],
            "search_terms": ["delete_user", "actor", "audit", "delete", "users"],
        },
        "decisions": [
            _decision(
                "inspect_symbol", {"target": DELETE}, "Inspect the current signature."
            ),
            _decision(
                "find_references",
                {"symbol_id": DELETE},
                "Every caller must pass the new argument.",
                (DELETE, "confirmed", "The signature gains a required actor.", "e2"),
            ),
            _decision("find_tests", {"target": DELETE}, "Find tests for delete_user."),
            _decision("submit_report", {}, "Confirmed target is checked."),
        ],
        "report": {
            "affected_components": [
                {
                    "target": DELETE,
                    "change_type": "modify",
                    "change_kind": "api",
                    "reason": "Add a required actor parameter and audit entry.",
                    "confidence": "high",
                    "evidence": ["e2"],
                }
            ],
            "risks": [
                {
                    "description": "Existing callers break until they pass actor.",
                    "severity": "high",
                    "related_targets": [DELETE],
                    "evidence": ["e3"],
                }
            ],
        },
    },
    "deletion_behavior": {
        "title": "Change user deletion behavior",
        "request": "Change user deletion so accounts are deactivated instead of "
        "removed.",
        "implementation": "Changes delete_user but leaves the mapped user test "
        "untouched, and also edits the unrelated orders service.",
        "teaches": "missing_test for unchanged mapped tests and an unexpected change "
        "with no relationship to the prediction.",
        "interpretation": {
            "summary": "Deactivate accounts on deletion instead of removing them",
            "change_kinds": ["business_logic"],
            "search_terms": ["delete_user", "deactivate", "delete", "users"],
        },
        "decisions": [
            _decision(
                "inspect_symbol", {"target": DELETE}, "Inspect the deletion function."
            ),
            _decision(
                "find_references",
                {"symbol_id": DELETE},
                "Find callers affected by the behavior change.",
                (DELETE, "confirmed", "Deletion behavior lives here.", "e2"),
            ),
            _decision("find_tests", {"target": DELETE}, "Find tests for delete_user."),
            _decision("submit_report", {}, "Confirmed target is checked."),
        ],
        "report": {
            "affected_components": [
                {
                    "target": DELETE,
                    "change_type": "modify",
                    "change_kind": "business_logic",
                    "reason": "Deactivate instead of deleting the row.",
                    "confidence": "high",
                    "evidence": ["e2"],
                }
            ],
        },
    },
}


def _relative(value: Any, root: Path) -> Any:
    """Replace temporary absolute paths so fixtures carry no machine paths."""

    text = json.dumps(value, default=str)
    return json.loads(text.replace(str(root), "<workspace>"))


def _run_scenario(repo: Path, commits: dict[str, str], key: str, work: Path) -> dict:
    spec = SCENARIOS[key]
    _git(repo, "switch", "-q", "--detach", commits["base"])
    index = scan_repository(repo)
    llm = RecordingLLM(
        ScriptedLLM(
            interpretations=[spec["interpretation"]],
            decisions=spec["decisions"],
            reports=[spec["report"]],
            model=SCRIPTED_MODEL,
        )
    )
    controller = AgentController(index, llm, output_root=work / key / "stage_a")
    run = controller.run(FeatureRequest(text=spec["request"]))
    verification = verify_repository(
        repo,
        report_value=str(run.report_path),
        requested_range=f"{commits['base']}..{commits[key]}",
        output_root=work / key / "stage_b",
    )
    diff = _git(repo, "diff", f"{commits['base']}..{commits[key]}")
    _git(repo, "switch", "-q", "main")
    payload = {
        "id": key,
        "title": spec["title"],
        "request": spec["request"],
        "implementation": spec["implementation"],
        "teaches": spec["teaches"],
        "generation": {
            "model": SCRIPTED_MODEL,
            "note": "Model decisions are scripted with RIPPLE's ScriptedLLM test "
            "double. Tool results, ledger transitions, validation, the report, and "
            "Stage B categories were produced by the real RIPPLE core.",
        },
        "base_commit": commits["base"],
        "head_commit": commits[key],
        "repository": {
            "files": [item.path.as_posix() for item in index.files],
            "tests": [item.path.as_posix() for item in index.files if item.is_test],
            "symbols": len(index.symbols),
            "imports": len(index.imports),
            "references": len(index.references),
        },
        "scripted_model_outputs": {
            "interpretation": spec["interpretation"],
            "decisions": spec["decisions"],
            "report_draft": spec["report"],
        },
        "prompts": llm.prompts,
        **capture_run(controller, run, verification),
        "report_markdown": render_markdown(run.report),
        "diff": diff,
        "verification_markdown": render_verification_markdown(verification.analysis),
    }
    return _relative(payload, repo.parent)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ripple-demo-") as temporary:
        root = Path(temporary)
        repo = root / "sample_app"
        commits = build_sample_repo(repo)
        for key in SCENARIOS:
            payload = _run_scenario(repo, commits, key, root / "work")
            path = OUTPUT / f"{key}.json"
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            report = payload["report"]
            categories = sorted(
                {item["category"] for item in payload["verification"]["findings"]}
            )
            print(
                f"{key}: stage_a={report['status']} "
                f"components={len(report['affected_components'])} "
                f"dropped={len(report['dropped_claims'])} stage_b={categories}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
