"""Offline fixture demo: scripted proposals, real deterministic evidence and Git."""

import json
import subprocess
import tempfile
from pathlib import Path

from ripple.agent import AgentController
from ripple.agent_models import FeatureRequest
from ripple.cache import scan_repository_cached
from ripple.llm import ScriptedLLM
from ripple.verification import verify_repository

REQUEST = "Round invoice subtotal to two decimal places before checkout displays it"
TARGET = "billing.py::subtotal"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "demo Git operation failed")
    return result.stdout.strip()


def positive_script() -> ScriptedLLM:
    return ScriptedLLM(
        model="scripted-offline-fixture",
        interpretations=[
            {
                "summary": REQUEST,
                "change_kinds": ["business_logic"],
                "search_terms": ["subtotal", "invoice", "billing"],
            }
        ],
        decisions=[
            {
                "tool_name": "inspect_symbol",
                "arguments": {"target": TARGET},
                "reason": "Inspect invoice subtotal implementation",
            },
            {
                "tool_name": "find_references",
                "arguments": {"symbol_id": TARGET},
                "reason": "Check checkout callers",
                "ledger_updates": [
                    {
                        "target": TARGET,
                        "status": "confirmed",
                        "reason": "Rounding belongs in the subtotal calculation",
                        "evidence_ids": ["e2"],
                    }
                ],
            },
            {
                "tool_name": "find_tests",
                "arguments": {"target": TARGET},
                "reason": "Find invoice tests",
            },
            {
                "tool_name": "submit_report",
                "arguments": {},
                "reason": "Caller and test checks completed",
            },
        ],
        reports=[
            {
                "affected_components": [
                    {
                        "target": TARGET,
                        "change_type": "modify",
                        "change_kind": "business_logic",
                        "reason": "Round the sum before returning the invoice subtotal",
                        "confidence": "high",
                        "evidence": ["e2"],
                    }
                ]
            }
        ],
    )


def run_demo(output_parent: Path) -> dict:
    output_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="invoice-", dir=output_parent)).resolve()
    repo = root / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / ".gitignore").write_text(".ripple/\n")
    (repo / "billing.py").write_text(
        'def subtotal(prices):\n    """Compute the invoice subtotal."""\n    return sum(prices)\n'
    )
    (repo / "checkout.py").write_text(
        "from billing import subtotal\n\ndef display_total(prices):\n    return subtotal(prices)\n"
    )
    (repo / "tests/test_billing.py").write_text(
        "from billing import subtotal\n\ndef test_subtotal():\n    assert subtotal([1, 2]) == 3\n"
    )
    _git(repo, "init", "-q")
    # Only this fresh demo-owned fixture gets commits. The user's checkout is untouched.
    _git(repo, "config", "user.name", "RIPPLE Demo")
    _git(repo, "config", "user.email", "demo@example.test")
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "demo base")
    base = _git(repo, "rev-parse", "HEAD")
    scanned = scan_repository_cached(repo)
    positive = AgentController(scanned.index, positive_script()).run(
        FeatureRequest(text=REQUEST)
    )
    negative = AgentController(
        scanned.index,
        ScriptedLLM(
            model="scripted-offline-fixture",
            interpretations=[
                {
                    "summary": "Predict tomorrow's weather",
                    "change_kinds": ["business_logic"],
                    "search_terms": ["meteorology", "precipitation", "forecast"],
                }
            ],
            decisions=[
                {
                    "tool_name": "submit_report",
                    "arguments": {},
                    "reason": "No supporting repository evidence",
                }
            ]
            * 4,
        ),
        output_root=root / "negative",
    ).run(FeatureRequest(text="Predict tomorrow's weather"))
    (repo / "billing.py").write_text(
        'def subtotal(prices):\n    """Compute the invoice subtotal rounded to cents."""\n    return round(sum(prices), 2)\n'
    )
    (repo / "tests/test_billing.py").write_text(
        "from billing import subtotal\n\ndef test_subtotal():\n    assert subtotal([1.111, 2.222]) == 3.33\n"
    )
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "round invoice subtotal and update test",
    )
    control_head = _git(repo, "rev-parse", "HEAD")
    control = verify_repository(
        repo,
        report_value=str(positive.report_path),
        requested_range=f"{base}..{control_head}",
        llm=None,
    )
    # Plant a separate incompatible signature change, leaving checkout.py unchanged.
    (repo / "billing.py").write_text(
        'def subtotal(prices, currency):\n    """Compute the invoice subtotal rounded to cents."""\n    return round(sum(prices), 2)\n'
    )
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "plant unchanged caller anomaly",
    )
    anomaly_head = _git(repo, "rev-parse", "HEAD")
    anomaly = verify_repository(
        repo,
        report_value=str(positive.report_path),
        requested_range=f"{base}..{anomaly_head}",
        llm=None,
    )
    result = {
        "mode": "OFFLINE SCRIPTED FIXTURE; no live model inference",
        "model_calls_paid": 0,
        "directory": str(root),
        "repo": str(repo),
        "base": base,
        "request": REQUEST,
        "positive_status": positive.report.status,
        "predictions": [c.target for c in positive.report.affected_components],
        "report": str(positive.report_path),
        "markdown": str(positive.markdown_path),
        "trace": str(positive.trace_path),
        "negative_status": negative.report.status,
        "negative_report": str(negative.report_path),
        "control_range": f"{base}..{control_head}",
        "control_verification": str(control.json_path),
        "control_categories": [f.category for f in control.analysis.findings],
        "anomaly_range": f"{base}..{anomaly_head}",
        "anomaly_verification": str(anomaly.json_path),
        "anomaly_categories": [f.category for f in anomaly.analysis.findings],
    }
    if (
        positive.report.status != "completed"
        or negative.report.status != "abstained"
        or "stale_caller" not in result["anomaly_categories"]
    ):
        raise ValueError(f"demo acceptance failed; inspect artifacts in {root}")
    (root / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
