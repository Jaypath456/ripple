"""Deterministic Markdown presentation for Stage B verification."""

from ripple.diff_models import DiffAnalysis


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_verification_markdown(analysis: DiffAnalysis) -> str:
    categories = {
        name: [item for item in analysis.findings if item.category == name]
        for name in (
            "expected",
            "adjacent",
            "unexpected",
            "missing_predicted",
            "missing_test",
            "stale_caller",
        )
    }
    primary = {
        item.path: item
        for item in analysis.findings
        if item.category in {"expected", "adjacent", "unexpected"}
    }
    lines = [
        "# RIPPLE Verification Report",
        "",
        f"- Request: {_cell(analysis.request)}",
        f"- Original report: `{analysis.report_id}`",
        f"- Base: `{analysis.base}`",
        f"- Head: `{analysis.head}`",
        f"- Configuration: `{analysis.config_version}`",
    ]
    if analysis.base_warning:
        lines.append(f"- Base warning: {_cell(analysis.base_warning)}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Predicted changed: {len(categories['expected'])}",
            f"- Missing predicted: {len(categories['missing_predicted'])}",
            f"- Adjacent: {len(categories['adjacent'])}",
            f"- Unexpected: {len(categories['unexpected'])}",
            f"- Missing tests: {len(categories['missing_test'])}",
            f"- Stale callers: {len(categories['stale_caller'])}",
            "",
            "## Changed Files",
            "",
            "| Path | Status | Category | Cosmetic | Changed Symbols | Verdict |",
            "|---|---|---|---|---|---|",
        ]
    )
    for change in analysis.changes:
        finding = primary.get(change.path)
        lines.append(
            f"| `{_cell(change.path)}` | {change.status} | "
            f"{finding.category if finding else 'n/a'} | "
            f"{str(change.cosmetic_only).lower()} | "
            f"{_cell(', '.join(change.changed_symbols) or '—')} | "
            f"{finding.verdict if finding else 'n/a'} |"
        )

    sections = (
        ("Missing Predicted", "missing_predicted"),
        ("Missing Tests", "missing_test"),
        ("Stale Callers", "stale_caller"),
        ("Unexpected Changes", "unexpected"),
    )
    for title, category in sections:
        lines.extend(["", f"## {title}", ""])
        values = categories[category]
        if values:
            lines.extend(
                f"- `{item.path}` — **{item.verdict}**: {item.explanation} "
                f"Evidence: `{', '.join(item.evidence) or 'none'}`."
                for item in values
            )
        else:
            lines.append("_None._")
    lines.extend(["", "## Ignored Files", ""])
    if analysis.ignored_files:
        lines.extend(
            f"- `{item.path}` ({item.status})" for item in analysis.ignored_files
        )
    else:
        lines.append("_None._")
    lines.extend(
        [
            "",
            "## Original Prediction Metrics",
            "",
            f"- Precision: {analysis.file_precision:.6f}",
            f"- Recall: {analysis.file_recall:.6f}",
            "",
            "## Verification Statistics",
            "",
            f"- Changed files: {analysis.run_statistics.changed_files}",
            f"- Ignored files: {analysis.run_statistics.ignored_files}",
            f"- Findings: {analysis.run_statistics.findings}",
            f"- Investigated files: {analysis.run_statistics.investigated_files}",
            f"- Tool calls: {analysis.run_statistics.tool_calls}",
            f"- LLM calls: {analysis.run_statistics.llm_calls}",
            f"- Runtime: {analysis.run_statistics.runtime_seconds:.3f}s",
            "",
        ]
    )
    return "\n".join(lines)
