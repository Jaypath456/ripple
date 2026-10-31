"""Read-only deterministic trace replay."""

import json
from pathlib import Path
from typing import Any


class RunNotFoundError(ValueError):
    """Raised when a saved trace cannot be located."""


def locate_trace(identifier: str, repo: Path) -> Path:
    candidates = (
        repo / ".ripple" / "runs" / f"{identifier}.jsonl",
        repo / "runs" / f"{identifier}.jsonl",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RunNotFoundError(f"run not found: {identifier}")


def _summary(record: dict[str, Any]) -> str | None:
    event = record.get("event")
    if event == "run_started":
        return (
            f"Run {record.get('run_id')} | request={record.get('request', '[not recorded]')} "
            f"| commit={record.get('commit')} | model={record.get('model')} "
            f"| config={record.get('config_version')}"
        )
    if event == "intent_created":
        return f"Intent: {json.dumps(record.get('intent'), sort_keys=True)}"
    if event == "decision":
        decision = record.get("decision", {})
        return f"Decision: {decision.get('tool_name')} — {decision.get('reason')}"
    if event == "tool_result":
        duplicate = " duplicate" if record.get("duplicate") else ""
        return (
            f"Tool result: {record.get('tool')} {record.get('evidence_id')} "
            f"ok={record.get('ok')}{duplicate}"
        )
    if event == "ledger_update":
        return (
            f"Ledger: accepted={record.get('accepted')} {record.get('update', record)}"
        )
    if event == "expand_completed":
        return (
            f"Expand: regression={record.get('regression_areas')} "
            f"tests={record.get('tests')} order={record.get('implementation_order')}"
        )
    if event == "report_validated":
        return (
            f"Validation: components={record.get('components')} "
            f"dropped={record.get('dropped')}"
        )
    if event == "run_finished":
        return (
            f"Finished: status={record.get('status')} "
            f"stop_reason={record.get('stop_reason')} | tools={record.get('tool_calls')} "
            f"duplicates={record.get('duplicate_calls')} | llm={record.get('llm_calls')} "
            f"tokens={record.get('total_tokens')} | runtime={record.get('runtime_seconds')}s"
        )
    return None


def replay_run(identifier: str, repo: Path) -> str:
    path = locate_trace(identifier, repo.resolve())
    lines = [f"Trace: {path}"]
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                lines.append(f"Malformed trace line {line_number}: {error}")
                continue
            summary = _summary(record)
            if summary:
                lines.append(summary)
    return "\n".join(lines)
