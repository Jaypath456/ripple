"""Deterministic Phase 5 expansion of an investigated candidate ledger."""

from dataclasses import dataclass
from pathlib import Path

from ripple.agent_models import (
    AffectedComponent,
    BlindSpot,
    ChangeKind,
    FeatureIntent,
    GroundedClaim,
    RegressionArea,
    SuggestedTest,
)
from ripple.graph import implementation_order
from ripple.ledger import CandidateLedger, EvidenceRecord
from ripple.models import RepositoryIndex


@dataclass(frozen=True)
class ExpansionResult:
    components: tuple[AffectedComponent, ...]
    schema_changes: tuple[GroundedClaim, ...]
    api_changes: tuple[GroundedClaim, ...]
    config_changes: tuple[GroundedClaim, ...]
    regression_areas: tuple[RegressionArea, ...]
    tests: tuple[SuggestedTest, ...]
    implementation_order: tuple[str, ...]
    blind_spots: tuple[BlindSpot, ...]


def _records(ledger: CandidateLedger, name: str) -> tuple[EvidenceRecord, ...]:
    return tuple(
        record for record in ledger.evidence.values() if record.tool_name == name
    )


def _fact_claims(
    ledger: CandidateLedger, kind: str, confirmed: set[str]
) -> tuple[GroundedClaim, ...]:
    claims: list[GroundedClaim] = []
    for record in _records(ledger, "repo_facts"):
        data = record.result.data if record.result.ok else None
        if not isinstance(data, dict) or data.get("kind") != kind:
            continue
        for fact in data.get("facts", []):
            if not isinstance(fact, dict):
                continue
            targets = tuple(
                sorted(
                    target
                    for target in confirmed
                    if target.partition("::")[0] == fact.get("path")
                    or target == fact.get("symbol_id")
                    or target == fact.get("handler")
                )
            )
            if not targets:
                continue
            if kind == "models":
                description = (
                    f"{fact.get('framework')} model/schema {fact.get('symbol_id')} "
                    f"has {len(fact.get('fields', []))} statically visible fields."
                )
            elif kind == "routes":
                methods = ", ".join(fact.get("methods", [])) or "unspecified method"
                description = (
                    f"{fact.get('framework')} route {methods} "
                    f"{fact.get('route') or '[dynamic]'} uses {fact.get('handler')}."
                )
            else:
                description = (
                    f"Configuration key {fact.get('key')} is read via "
                    f"{fact.get('access_style')} in {fact.get('path')}."
                )
            claims.append(
                GroundedClaim(
                    description=description,
                    targets=targets,
                    evidence=(record.evidence_id,),
                )
            )
    return tuple(claims)


def expand_report(
    index: RepositoryIndex, intent: FeatureIntent, ledger: CandidateLedger
) -> ExpansionResult:
    """Build only deterministic additions; never promote regression areas."""

    confirmed_candidates = ledger.confirmed()
    confirmed = {item.target for item in confirmed_candidates}
    confirmed_paths = {item.target.partition("::")[0] for item in confirmed_candidates}
    components: list[AffectedComponent] = []

    schema = list(_fact_claims(ledger, "models", confirmed))
    api = list(_fact_claims(ledger, "routes", confirmed))
    config = list(_fact_claims(ledger, "settings", confirmed))

    migration_record: EvidenceRecord | None = None
    migration_directory: str | None = None
    for record in _records(ledger, "repo_facts"):
        data = record.result.data if record.result.ok else None
        if isinstance(data, dict) and data.get("kind") == "migrations":
            facts = data.get("facts", [])
            if facts:
                migration_record = record
                migration_directory = str(facts[0].get("directory"))
                break
    model_targets = {target for claim in schema for target in claim.targets}
    if (
        migration_record
        and migration_directory
        and model_targets
        and any(
            kind in intent.change_kinds
            for kind in (ChangeKind.DATA_MODEL, ChangeKind.MIGRATION)
        )
    ):
        model_evidence = next(
            evidence for claim in schema for evidence in claim.evidence if claim.targets
        )
        target = f"{migration_directory}/<proposed migration>"
        components.append(
            AffectedComponent(
                target=target,
                change_type="new_file",
                change_kind=ChangeKind.MIGRATION,
                reason="A confirmed model/schema change requires a migration under the existing migration directory.",
                confidence="high",
                evidence=(model_evidence, migration_record.evidence_id),
            )
        )
        schema.append(
            GroundedClaim(
                description=f"Add a generated migration under {migration_directory} for the confirmed model change.",
                targets=(target, *tuple(sorted(model_targets))),
                evidence=(model_evidence, migration_record.evidence_id),
            )
        )

    regression: dict[str, RegressionArea] = {}
    tests: dict[str, SuggestedTest] = {}
    for record in ledger.evidence.values():
        data = record.result.data if record.result.ok else None
        if not isinstance(data, dict):
            continue
        if (
            record.tool_name == "get_dependencies"
            and data.get("direction") == "imported_by"
        ):
            owner = str(data.get("path", ""))
            if owner not in confirmed_paths:
                continue
            for item in data.get("neighbors", []):
                target = str(item.get("path", ""))
                if target and target not in confirmed_paths:
                    regression[target] = RegressionArea(
                        target=target,
                        reason=f"Direct reverse dependency of confirmed component {owner}.",
                        evidence=(record.evidence_id,),
                        score=20,
                    )
        elif record.tool_name == "find_references":
            source = str(data.get("symbol_id", ""))
            if source not in confirmed:
                continue
            counts: dict[str, int] = {}
            for item in data.get("references", []):
                path = str(item.get("path", ""))
                if path and path not in confirmed_paths:
                    counts[path] = counts.get(path, 0) + 1
            for target, count in counts.items():
                prior = regression.get(target)
                score = 10 + count
                if prior and prior.score >= score:
                    continue
                regression[target] = RegressionArea(
                    target=target,
                    reason=f"Contains {count} static reference(s) to {source}.",
                    evidence=(record.evidence_id,),
                    score=score,
                )
        elif record.tool_name == "find_tests":
            owner = str(data.get("target", "")).partition("::")[0]
            if owner not in confirmed_paths:
                continue
            for item in data.get("tests", []):
                test_path = str(item.get("test_path", ""))
                if not test_path:
                    continue
                tests[test_path] = SuggestedTest(
                    action="update",
                    test_path=test_path,
                    covers=(owner,),
                    rationale=f"Static test mapping connects this test to {owner}.",
                    evidence=(record.evidence_id,),
                )
                regression[test_path] = RegressionArea(
                    target=test_path,
                    reason=f"Mapped test coverage for confirmed component {owner}.",
                    evidence=(record.evidence_id,),
                    score=5,
                )

    ordered_sources = implementation_order(
        index.dependency_graph, tuple(Path(path) for path in sorted(confirmed_paths))
    )
    migration_targets = [item.target for item in components]
    test_paths = sorted(tests)
    order = [path.as_posix() for path in ordered_sources]
    if migration_targets:
        model_files = {target.partition("::")[0] for target in model_targets}
        position = max(
            (order.index(path) + 1 for path in model_files if path in order), default=0
        )
        order[position:position] = migration_targets
    order.extend(path for path in test_paths if path not in order)

    blind_spots: list[BlindSpot] = []
    parse_errors = tuple(
        file.path.as_posix() for file in index.files if file.parse_error
    )
    if parse_errors:
        blind_spots.append(
            BlindSpot(
                description=f"Static parsing failed for: {', '.join(parse_errors[:10])}."
            )
        )
    if any(item.module == "*" or "*" in item.names for item in index.imports):
        blind_spots.append(
            BlindSpot(description="Star imports make static name resolution ambiguous.")
        )
    if any(item.target_path is None for item in index.imports):
        blind_spots.append(
            BlindSpot(
                description="External or dynamic imports are not resolved into the repository graph."
            )
        )
    if tests:
        blind_spots.append(
            BlindSpot(
                description="Static test mappings do not establish runtime coverage."
            )
        )
    if any(
        record.tool_name == "co_changed" and not record.result.ok
        for record in ledger.evidence.values()
    ):
        blind_spots.append(
            BlindSpot(
                description="Reachable Git history did not provide usable co-change evidence."
            )
        )

    return ExpansionResult(
        components=tuple(components),
        schema_changes=tuple(schema),
        api_changes=tuple(api),
        config_changes=tuple(config),
        regression_areas=tuple(
            sorted(regression.values(), key=lambda item: (-item.score, item.target))
        ),
        tests=tuple(tests[path] for path in sorted(tests)),
        implementation_order=tuple(order),
        blind_spots=tuple(blind_spots),
    )
