"""Deterministic validation and claim dropping for agent reports."""

from dataclasses import dataclass

from ripple.agent_models import AffectedComponent, ReportDraft, SuggestedTest
from ripple.ledger import CandidateLedger
from ripple.models import RepositoryIndex


@dataclass(frozen=True)
class ValidatedDraft:
    components: tuple[AffectedComponent, ...]
    tests: tuple[SuggestedTest, ...]
    dropped_claims: tuple[str, ...]


def validate_report_draft(
    draft: ReportDraft,
    index: RepositoryIndex,
    ledger: CandidateLedger,
) -> ValidatedDraft:
    """Keep only repository-grounded, confirmed, adequately investigated claims."""

    paths = {item.path.as_posix() for item in index.files}
    confirmed = {item.target: item for item in ledger.confirmed()}
    kept: list[AffectedComponent] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for component in draft.affected_components:
        if component.target in seen:
            dropped.append(f"duplicate component dropped: {component.target}")
            continue
        seen.add(component.target)
        candidate = confirmed.get(component.target)
        if candidate is None:
            dropped.append(f"unconfirmed component dropped: {component.target}")
            continue
        if component.change_type == "new_file":
            dropped.append(f"invented new-file component dropped: {component.target}")
            continue
        if component.target.partition("::")[0] not in paths:
            dropped.append(f"missing component dropped: {component.target}")
            continue
        valid_evidence = tuple(
            evidence_id
            for evidence_id in component.evidence
            if evidence_id in ledger.evidence
            and component.target in ledger.evidence[evidence_id].touched_targets
        )
        if not valid_evidence:
            dropped.append(f"unsupported component dropped: {component.target}")
            continue
        if len(valid_evidence) != len(component.evidence):
            dropped.append(f"invalid evidence removed: {component.target}")
        confidence = component.confidence
        if confidence == "high" and not any(
            ledger.evidence[evidence_id].strong for evidence_id in valid_evidence
        ):
            confidence = "medium"
            dropped.append(
                f"confidence capped for lexical-only support: {component.target}"
            )
        kept.append(
            component.model_copy(
                update={"evidence": valid_evidence, "confidence": confidence}
            )
        )

    tests: list[SuggestedTest] = []
    for test in draft.suggested_tests:
        valid_evidence = tuple(
            evidence_id
            for evidence_id in test.evidence
            if evidence_id in ledger.evidence
            and ledger.evidence[evidence_id].tool_name == "find_tests"
        )
        if not valid_evidence:
            dropped.append(
                f"test suggestion without find_tests evidence dropped: {test.test_path}"
            )
            continue
        if len(valid_evidence) != len(test.evidence):
            dropped.append(f"invalid test evidence removed: {test.test_path}")
        if test.action == "update" and test.test_path not in paths:
            dropped.append(f"missing test update dropped: {test.test_path}")
            continue
        tests.append(test.model_copy(update={"evidence": valid_evidence}))

    for field_name in (
        "schema_changes",
        "api_changes",
        "config_changes",
        "regression_areas",
        "implementation_order",
        "risks",
    ):
        if getattr(draft, field_name):
            dropped.append(f"unsupported Phase 5 narrative dropped: {field_name}")
    return ValidatedDraft(
        components=tuple(kept),
        tests=tuple(tests),
        dropped_claims=tuple(dropped),
    )
