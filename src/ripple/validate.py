"""Deterministic validation and claim dropping for agent reports."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ripple.agent_models import (
    AffectedComponent,
    BlindSpot,
    GroundedClaim,
    RegressionArea,
    ReportDraft,
    Risk,
    SuggestedTest,
)
from ripple.expand import ExpansionResult
from ripple.ledger import CandidateLedger
from ripple.models import RepositoryIndex


@dataclass(frozen=True)
class ValidatedDraft:
    components: tuple[AffectedComponent, ...]
    schema_changes: tuple[GroundedClaim, ...]
    api_changes: tuple[GroundedClaim, ...]
    config_changes: tuple[GroundedClaim, ...]
    regression_areas: tuple[RegressionArea, ...]
    tests: tuple[SuggestedTest, ...]
    implementation_order: tuple[str, ...]
    risks: tuple[Risk, ...]
    blind_spots: tuple[BlindSpot, ...]
    dropped_claims: tuple[str, ...]


def _dedupe(items: tuple[Any, ...], key: Callable[[Any], object]) -> tuple[Any, ...]:
    values: dict[object, Any] = {}
    for item in items:
        values.setdefault(key(item), item)
    return tuple(values.values())


def validate_report_draft(
    draft: ReportDraft,
    index: RepositoryIndex,
    ledger: CandidateLedger,
    expansion: ExpansionResult | None = None,
) -> ValidatedDraft:
    """Keep only grounded claims and merge immutable deterministic expansion."""

    expansion = expansion or ExpansionResult((), (), (), (), (), (), (), ())
    paths = {item.path.as_posix() for item in index.files}
    confirmed = {item.target: item for item in ledger.confirmed()}
    migration_components = {item.target: item for item in expansion.components}
    kept: list[AffectedComponent] = []
    dropped: list[str] = []
    seen: set[str] = set()
    legacy_unsupported: set[str] = set()
    for name in (
        "schema_changes",
        "api_changes",
        "config_changes",
        "regression_areas",
        "risks",
    ):
        if any(isinstance(item, str) for item in getattr(draft, name)):
            legacy_unsupported.add(name)
            dropped.append(f"unsupported Phase 5 narrative dropped: {name}")
    if draft.implementation_order and not expansion.implementation_order:
        legacy_unsupported.add("implementation_order")
        dropped.append("unsupported Phase 5 narrative dropped: implementation_order")
    for component in draft.affected_components:
        if component.target in seen:
            dropped.append(f"duplicate component dropped: {component.target}")
            continue
        seen.add(component.target)
        if component.change_type == "new_file":
            deterministic = migration_components.get(component.target)
            if deterministic is None:
                dropped.append(
                    f"invalid migration new-file dropped: {component.target}"
                )
            else:
                kept.append(deterministic)
            continue
        if component.target not in confirmed:
            dropped.append(f"unconfirmed component dropped: {component.target}")
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

    for component in expansion.components:
        if component.target not in {item.target for item in kept}:
            kept.append(component)

    deterministic_claims = {
        "schema_changes": expansion.schema_changes,
        "api_changes": expansion.api_changes,
        "config_changes": expansion.config_changes,
    }
    validated_claims: dict[str, tuple[GroundedClaim, ...]] = {}
    for name, required in deterministic_claims.items():
        accepted = list(required)
        for claim in getattr(draft, name):
            if isinstance(claim, str):
                continue
            if claim in required:
                continue
            valid_evidence = all(item in ledger.evidence for item in claim.evidence)
            touched = all(
                any(
                    target in ledger.evidence[item].touched_targets
                    for target in claim.targets
                )
                for item in claim.evidence
                if item in ledger.evidence
            )
            valid_targets = all(
                target.partition("::")[0] in paths or target in migration_components
                for target in claim.targets
            )
            if valid_evidence and touched and valid_targets:
                accepted.append(claim)
            else:
                dropped.append(f"unsupported {name} claim dropped: {claim.description}")
        validated_claims[name] = _dedupe(
            tuple(accepted), lambda item: (item.description, item.targets)
        )

    tests = list(expansion.tests)
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
        if test.action == "update" and test.test_path not in paths:
            dropped.append(f"missing test update dropped: {test.test_path}")
            continue
        tests.append(test.model_copy(update={"evidence": valid_evidence}))

    risks: list[Risk] = []
    symbols = {item.id for item in index.symbols}
    allowed_targets = (
        paths
        | symbols
        | set(migration_components)
        | {item.target for item in expansion.regression_areas}
    )
    for risk in draft.risks:
        if isinstance(risk, str):
            continue
        if not all(item in ledger.evidence for item in risk.evidence):
            dropped.append(f"risk without valid evidence dropped: {risk.description}")
            continue
        if not all(target in allowed_targets for target in risk.related_targets):
            dropped.append(f"risk with invalid target dropped: {risk.description}")
            continue
        if not any(
            target in ledger.evidence[evidence].touched_targets
            for evidence in risk.evidence
            for target in risk.related_targets
        ):
            dropped.append(f"unsupported risk dropped: {risk.description}")
            continue
        risks.append(risk)

    if (
        draft.regression_areas
        and "regression_areas" not in legacy_unsupported
        and draft.regression_areas != expansion.regression_areas
    ):
        dropped.append(
            "LLM regression ordering/content replaced by deterministic Expand"
        )
    if (
        draft.implementation_order
        and "implementation_order" not in legacy_unsupported
        and draft.implementation_order != expansion.implementation_order
    ):
        dropped.append("LLM implementation order replaced by deterministic graph order")
    if draft.blind_spots and draft.blind_spots != expansion.blind_spots:
        dropped.append("unsupported LLM blind spots replaced by observed conditions")

    return ValidatedDraft(
        components=tuple(kept),
        schema_changes=validated_claims["schema_changes"],
        api_changes=validated_claims["api_changes"],
        config_changes=validated_claims["config_changes"],
        regression_areas=expansion.regression_areas,
        tests=_dedupe(tuple(tests), lambda item: (item.action, item.test_path)),
        implementation_order=expansion.implementation_order,
        risks=tuple(risks),
        blind_spots=expansion.blind_spots,
        dropped_claims=tuple(dropped),
    )
