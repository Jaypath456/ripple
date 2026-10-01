"""Stable deterministic Markdown rendering for canonical JSON reports."""

from ripple.agent_models import ChangeImpactReport, GroundedClaim


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _claims(title: str, claims: tuple[GroundedClaim, ...]) -> list[str]:
    lines = [f"## {title}", ""]
    if not claims:
        return [*lines, "_None._", ""]
    for claim in claims:
        lines.append(
            f"- {claim.description} Targets: `{', '.join(claim.targets)}`. "
            f"Evidence: `{', '.join(claim.evidence)}`."
        )
    return [*lines, ""]


def render_markdown(report: ChangeImpactReport) -> str:
    """Render a report without network or model access."""

    lines = [
        "# RIPPLE Change Impact Report",
        "",
        f"- Request: {_cell(report.request)}",
        f"- Commit: `{report.commit}`",
        f"- Status: `{report.status}`",
        f"- Configuration: `{report.run_stats.config_version}`",
        "",
        "## Affected Components",
        "",
    ]
    if report.affected_components:
        lines.extend(
            [
                "| Target | Change Type | Kind | Confidence | Reason | Evidence |",
                "|---|---|---|---|---|---|",
            ]
        )
        for item in report.affected_components:
            lines.append(
                f"| `{_cell(item.target)}` | {item.change_type} | "
                f"{item.change_kind.value} | {item.confidence} | {_cell(item.reason)} | "
                f"`{', '.join(item.evidence)}` |"
            )
    else:
        lines.append("_None._")
    lines.append("")
    lines.extend(_claims("Schema Changes", report.schema_changes))
    lines.extend(_claims("API Changes", report.api_changes))
    lines.extend(_claims("Configuration Changes", report.config_changes))

    lines.extend(["## Regression Areas", ""])
    if report.regression_areas:
        for item in report.regression_areas:
            lines.append(
                f"- `{item.target}` (score {item.score}): {item.reason} "
                f"Evidence: `{', '.join(item.evidence)}`."
            )
    else:
        lines.append("_None._")
    lines.extend(["", "## Suggested Tests", ""])
    if report.suggested_tests:
        for item in report.suggested_tests:
            lines.append(
                f"- **{item.action}** `{item.test_path}` — {item.rationale} "
                f"Covers: `{', '.join(item.covers)}`."
            )
    else:
        lines.append("_None._")
    lines.extend(["", "## Implementation Order", ""])
    if report.implementation_order:
        lines.extend(
            f"{position}. `{path}`"
            for position, path in enumerate(report.implementation_order, 1)
        )
    else:
        lines.append("_None._")
    lines.extend(["", "## Risks", ""])
    if report.risks:
        for item in report.risks:
            lines.append(
                f"- **{item.severity}**: {item.description} Targets: "
                f"`{', '.join(item.related_targets)}`. Evidence: "
                f"`{', '.join(item.evidence)}`."
            )
    else:
        lines.append("_None._")
    lines.extend(["", "## Blind Spots", ""])
    lines.extend(
        (f"- {item.description}" for item in report.blind_spots)
        if report.blind_spots
        else ["_None._"]
    )
    lines.extend(["", "## Dropped Claims", ""])
    lines.extend(
        (f"- {item}" for item in report.dropped_claims)
        if report.dropped_claims
        else ["_None._"]
    )
    stats = report.run_stats
    lines.extend(
        [
            "",
            "## Run Statistics",
            "",
            f"- Model: `{stats.model}`",
            f"- Tool calls: {stats.tool_calls}",
            f"- Duplicate calls: {stats.duplicate_calls}",
            f"- LLM calls: {stats.llm_calls}",
            f"- Tokens: {stats.total_tokens if stats.total_tokens is not None else 'unknown'}",
            f"- Runtime: {stats.runtime_seconds:.3f}s",
            f"- Stop reason: `{stats.stop_reason}`",
            f"- Dirty checkout: `{str(stats.dirty).lower()}`",
            "",
        ]
    )
    return "\n".join(lines)
