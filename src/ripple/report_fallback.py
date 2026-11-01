"""Opt-in, deterministic retention of independently supported V2 confirmations."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from ripple.agent_models import AffectedComponent, BlindSpot, ChangeKind, ReportDraft
from ripple.ledger import Candidate, CandidateLedger, EvidenceRecord
from ripple.models import RepositoryIndex
from ripple.validate import ValidatedDraft, validate_report_draft


@dataclass(frozen=True)
class RetentionResult:
    report: ValidatedDraft
    retained: tuple[str, ...]
    refused: tuple[tuple[str, str], ...]


def _strong_confirmation(record: EvidenceRecord) -> bool:
    if not record.strong or record.tool_name not in {
        "inspect_symbol",
        "find_tests",
        "get_dependencies",
        "repo_facts",
        "co_changed",
        "find_references",
    }:
        return False
    if record.tool_name != "find_references":
        return True
    references = (
        record.result.data.get("references")
        if isinstance(record.result.data, dict)
        else None
    )
    return isinstance(references, (list, tuple)) and any(
        isinstance(reference, dict) and reference.get("confidence") == "high"
        for reference in references
    )


def _confirmation_evidence(
    candidate: Candidate,
    ledger: CandidateLedger,
    presented: Collection[str],
) -> tuple[tuple[str, ...], str | None]:
    confirmation = next(
        (
            entry
            for entry in reversed(candidate.history)
            if entry.get("status") == "confirmed"
        ),
        None,
    )
    if confirmation is None or confirmation.get("via") != "checkpoint":
        return (), "missing_checkpoint_confirmation"
    cited = confirmation.get("evidence_ids", ())
    if not isinstance(cited, (list, tuple)) or any(
        not isinstance(item, str) for item in cited
    ):
        return (), "invalid_confirmation_evidence"
    ids = tuple(dict.fromkeys(cited))
    if not ids:
        return (), "missing_confirmation_evidence"
    if any(evidence_id not in candidate.evidence_ids for evidence_id in ids):
        return (), "confirmation_not_in_ledger_citations"
    if any(evidence_id not in presented for evidence_id in ids):
        return (), "evidence_not_presented"
    records = [ledger.evidence.get(evidence_id) for evidence_id in ids]
    if any(record is None for record in records):
        return (), "unknown_evidence"
    if any(
        record.evidence_id != evidence_id or record.result.evidence_id != evidence_id
        for evidence_id, record in zip(ids, records)
        if record
    ):
        return (), "evidence_id_mismatch"
    if any(not record.result.ok for record in records if record):
        return (), "failed_evidence"
    if any(
        candidate.target not in record.touched_targets for record in records if record
    ):
        return (), "untouched_target"
    if not any(_strong_confirmation(record) for record in records if record):
        return (), "strong_evidence_required"
    return ids, None


def _checks_recorded(candidate: Candidate, ledger: CandidateLedger) -> bool:
    path = candidate.target.partition("::")[0]
    checked = set()
    for evidence_id, record in ledger.evidence.items():
        if (
            record.evidence_id != evidence_id
            or record.result.evidence_id != evidence_id
        ):
            continue
        if not record.result.ok or record.tool_name not in {
            "find_references",
            "find_tests",
        }:
            continue
        target = next(
            (
                value
                for key, value in record.arguments.items()
                if key in {"symbol_id", "target"}
            ),
            "",
        )
        if str(target).partition("::")[0] == path:
            checked.add(record.tool_name)
    return checked == {"find_references", "find_tests"}


def retain_confirmed_components(
    report: ValidatedDraft,
    index: RepositoryIndex,
    ledger: CandidateLedger,
    presented: Mapping[str, Collection[str]],
) -> RetentionResult:
    """Append only missing, checked confirmations; never repair a rejected claim.

    Model claims already passed the ordinary validator and stay unchanged.
    Retained components are independent proposals from accepted checkpoint history,
    validated again by that same validator. No inference, new targets, tools,
    ledger mutations, migration proposals or fabricated citations are introduced.
    """

    covered = {component.target for component in report.components}
    files = {file.path.as_posix(): file for file in index.files}
    retained: list[AffectedComponent] = []
    refused: list[tuple[str, str]] = []
    for candidate in ledger.confirmed():
        target = candidate.target
        if target in covered:
            continue
        path = Path(target.partition("::")[0])
        file = files.get(path.as_posix())
        problem = None
        if path.is_absolute() or ".." in path.parts or not ledger.valid_target(target):
            problem = "target_not_indexed"
        elif file is None or file.is_test:
            problem = "target_not_source"
        elif file.parse_error is not None:
            problem = "source_parse_error"
        elif (
            not candidate.checked_refs
            or not candidate.checked_tests
            or not _checks_recorded(candidate, ledger)
        ):
            problem = "checks_incomplete"
        ids, provenance_problem = _confirmation_evidence(
            candidate, ledger, presented.get(target, ())
        )
        problem = problem or provenance_problem
        if problem:
            refused.append((target, problem))
            continue
        proposal = AffectedComponent(
            target=target,
            change_type="modify",
            change_kind=ChangeKind.BUSINESS_LOGIC,
            reason="Retained from accepted checkpoint evidence after report coverage was lost.",
            confidence="low",
            evidence=ids,
        )
        checked = validate_report_draft(
            ReportDraft(affected_components=(proposal,)), index, ledger
        )
        if len(checked.components) != 1 or checked.components[0].target != target:
            refused.append((target, "validator_rejected"))
            continue
        retained.append(checked.components[0])
        covered.add(target)
    notes = tuple(
        note
        for target, code in refused
        if (note := f"Confirmed report retention refused [{code}]: {target}")
        not in report.dropped_claims
    )
    if not retained:
        result = (
            replace(report, dropped_claims=report.dropped_claims + notes)
            if notes
            else report
        )
        return RetentionResult(result, (), tuple(refused))
    ids = tuple(
        dict.fromkeys(item for component in retained for item in component.evidence)
    )
    result = replace(
        report,
        components=report.components + tuple(retained),
        dropped_claims=report.dropped_claims + notes,
        blind_spots=report.blind_spots
        + (
            BlindSpot(
                description=f"Deterministic fallback retained {len(retained)} checkpoint-confirmed target(s); review these low-confidence components.",
                evidence=ids,
            ),
        ),
    )
    return RetentionResult(
        result, tuple(component.target for component in retained), tuple(refused)
    )
