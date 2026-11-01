"""Python-owned candidate and evidence ledgers."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ripple.agent_models import CandidateStatus, CandidateUpdate
from ripple.models import RepositoryIndex
from ripple.tools import ToolResult


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult
    touched_targets: frozenset[str]
    strong: bool


@dataclass(frozen=True)
class LedgerOutcome:
    """Additive machine-readable result; refused updates never change state."""

    changed: bool
    reason_code: str | None = None
    refusal: str | None = None


@dataclass
class Candidate:
    target: str
    status: CandidateStatus
    reason: str
    evidence_ids: list[str] = field(default_factory=list)
    checked_refs: bool = False
    checked_tests: bool = False
    rejection_count: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)


class CandidateLedger:
    """Enforce target validity, evidence provenance, and status transitions."""

    def __init__(self, index: RepositoryIndex, cap: int = 30) -> None:
        self.index = index
        self.cap = cap
        self.candidates: dict[str, Candidate] = {}
        self.evidence: dict[str, EvidenceRecord] = {}
        self._paths = {item.path.as_posix() for item in index.files}
        self._symbols = {item.id for item in index.symbols}

    def valid_target(self, target: str) -> bool:
        return target in self._paths or target in self._symbols

    def add_evidence(self, record: EvidenceRecord) -> None:
        if record.evidence_id in self.evidence:
            raise ValueError("evidence IDs are append-only")
        if record.evidence_id != record.result.evidence_id:
            raise ValueError("evidence ID does not match tool result")
        # Isolate the recorded observation from later caller-owned mutations.
        record = deepcopy(record)
        self.evidence[record.evidence_id] = record
        if not record.result.ok:
            return
        if record.tool_name not in {"find_references", "find_tests"}:
            return
        for candidate in self.candidates.values():
            candidate_path = candidate.target.partition("::")[0]
            argument_target = next(
                (
                    value
                    for key, value in record.arguments.items()
                    if key in {"symbol_id", "target"}
                ),
                "",
            )
            argument_path = str(argument_target).partition("::")[0]
            if candidate.target == argument_target or candidate_path == argument_path:
                if record.tool_name == "find_references":
                    candidate.checked_refs = True
                else:
                    candidate.checked_tests = True

    def seed(self, target: str, reason: str, evidence_id: str) -> bool:
        if not self.valid_target(target) or target in self.candidates:
            return False
        record = self.evidence.get(evidence_id)
        # Preserve the trusted seeding API used by historical fixture builders.
        # Proposals still require existing successful evidence in apply/decide.
        if record is not None and (
            not record.result.ok or target not in record.touched_targets
        ):
            return False
        if len(self.candidates) >= self.cap:
            return False
        self.candidates[target] = Candidate(
            target=target,
            status="suspected",
            reason=reason,
            evidence_ids=[evidence_id],
            history=[{"status": "suspected", "evidence_ids": [evidence_id]}],
        )
        return True

    def apply(self, update: CandidateUpdate, latest_evidence: str | None) -> bool:
        """Compatibility wrapper for callers expecting a boolean."""

        return self.apply_checked(update, latest_evidence).changed

    def apply_checked(
        self, update: CandidateUpdate, latest_evidence: str | None
    ) -> LedgerOutcome:
        if not self.valid_target(update.target):
            return LedgerOutcome(False, "invalid_target", "target is not indexed")
        if latest_evidence is None or latest_evidence not in update.evidence_ids:
            return LedgerOutcome(
                False,
                "latest_evidence_required",
                "latest successful observation must be cited",
            )
        records = [self.evidence.get(item) for item in update.evidence_ids]
        if any(record is None for record in records):
            return LedgerOutcome(
                False, "unknown_evidence", "cited evidence does not exist"
            )
        if any(not record.result.ok for record in records if record):
            return LedgerOutcome(
                False, "failed_evidence", "failed observations cannot support updates"
            )
        if not any(
            update.target in record.touched_targets for record in records if record
        ):
            return LedgerOutcome(
                False, "untouched_target", "cited evidence did not touch this target"
            )
        current = self.candidates.get(update.target)
        if current is None:
            if update.status != "suspected":
                return LedgerOutcome(
                    False, "invalid_transition", "new candidates must be suspected"
                )
            if len(self.candidates) >= self.cap:
                return LedgerOutcome(False, "candidate_cap", "candidate cap reached")
            evidence_id = next(
                record.evidence_id
                for record in reversed(records)
                if record and update.target in record.touched_targets
            )
            return LedgerOutcome(self.seed(update.target, update.reason, evidence_id))
        if current.status == "confirmed":
            return LedgerOutcome(
                False, "already_confirmed", "confirmed candidates cannot be changed"
            )
        if current.status == "rejected":
            if update.status == "rejected":
                return LedgerOutcome(
                    False, "already_rejected", "candidate is already rejected"
                )
            if update.status != "suspected" or current.rejection_count >= 2:
                return LedgerOutcome(
                    False,
                    "rejection_limit"
                    if current.rejection_count >= 2
                    else "invalid_transition",
                    "rejected candidate cannot make this transition",
                )
        elif update.status not in {"confirmed", "rejected"}:
            return LedgerOutcome(
                False,
                "invalid_transition",
                "suspected candidate must be confirmed or rejected",
            )
        if update.status == "rejected":
            current.rejection_count += 1
        changed = current.status != update.status or current.reason != update.reason
        current.status = update.status
        current.reason = update.reason
        for evidence_id in update.evidence_ids:
            if evidence_id not in current.evidence_ids:
                current.evidence_ids.append(evidence_id)
                changed = True
        current.history.append(
            {
                "status": update.status,
                "reason": update.reason,
                "evidence_ids": list(update.evidence_ids),
            }
        )
        return LedgerOutcome(changed)

    def support(self, target: str) -> list[EvidenceRecord]:
        """Successful evidence records that actually touched ``target``."""

        return [
            record
            for record in self.evidence.values()
            if record.result.ok and target in record.touched_targets
        ]

    def decide(
        self,
        target: str,
        decision: str,
        evidence_ids: tuple[str, ...],
        reason: str,
        presented: frozenset[str],
    ) -> tuple[bool, str | None]:
        """Apply one V2 checkpoint decision; returns (changed, refusal reason).

        Stricter than ``apply``: every cited ID must be one Python presented for this
        target, must exist, and must have touched it; confirmation also needs at
        least one strong (non-lexical) record. ``keep`` never changes state.
        """

        outcome = self.decide_checked(target, decision, evidence_ids, reason, presented)
        return outcome.changed, outcome.refusal

    def decide_checked(
        self,
        target: str,
        decision: str,
        evidence_ids: tuple[str, ...],
        reason: str,
        presented: frozenset[str],
    ) -> LedgerOutcome:
        current = self.candidates.get(target)
        if current is None or current.status != "suspected":
            return LedgerOutcome(
                False, "not_suspected", "target is not a suspected candidate"
            )
        if decision not in {"confirm", "reject", "keep"}:
            return LedgerOutcome(
                False, "invalid_decision", "unknown checkpoint decision"
            )
        if decision == "keep":
            return LedgerOutcome(False)
        if not evidence_ids:
            return LedgerOutcome(False, "no_evidence", "no evidence cited")
        if any(item not in presented for item in evidence_ids):
            return LedgerOutcome(
                False,
                "not_presented",
                "cited evidence was not presented for this target",
            )
        records = [self.evidence.get(item) for item in evidence_ids]
        if any(
            record is None or target not in record.touched_targets for record in records
        ):
            return LedgerOutcome(
                False, "untouched_target", "cited evidence did not touch this target"
            )
        if any(not record.result.ok for record in records if record):
            return LedgerOutcome(
                False, "failed_evidence", "failed observations cannot support decisions"
            )
        if decision == "confirm" and not any(record.strong for record in records):
            return LedgerOutcome(
                False,
                "strong_evidence_required",
                "confirmation needs non-lexical evidence",
            )
        status: CandidateStatus = "confirmed" if decision == "confirm" else "rejected"
        if status == "rejected":
            current.rejection_count += 1
        current.status = status
        current.reason = reason
        current.evidence_ids.extend(
            item for item in evidence_ids if item not in current.evidence_ids
        )
        current.history.append(
            {
                "status": status,
                "reason": reason,
                "evidence_ids": list(evidence_ids),
                "via": "checkpoint",
            }
        )
        return LedgerOutcome(True)

    def confirmed(self) -> tuple[Candidate, ...]:
        return tuple(
            candidate
            for candidate in self.candidates.values()
            if candidate.status == "confirmed"
        )

    def submission_problem(
        self, *, require_refs: bool = True, require_tests: bool = True
    ) -> str | None:
        confirmed = self.confirmed()
        if not confirmed:
            return "no candidates are confirmed"
        unchecked = [
            item.target
            for item in confirmed
            if (require_refs and not item.checked_refs)
            or (require_tests and not item.checked_tests)
        ]
        if unchecked:
            return (
                "confirmed candidates still need reference and test checks: "
                + ", ".join(unchecked)
            )
        return None

    def prompt_view(self) -> list[dict[str, Any]]:
        return [
            {
                "target": item.target,
                "status": item.status,
                "reason": item.reason,
                "evidence_ids": item.evidence_ids,
                "checked_refs": item.checked_refs,
                "checked_tests": item.checked_tests,
            }
            for item in self.candidates.values()
        ]
