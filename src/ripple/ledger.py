"""Python-owned candidate and evidence ledgers."""

from dataclasses import dataclass, field
from typing import Any

from ripple.agent_models import CandidateStatus, CandidateUpdate
from ripple.models import RepositoryIndex
from ripple.tools import ToolResult


@dataclass
class EvidenceRecord:
    evidence_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult
    touched_targets: frozenset[str]
    strong: bool


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
        if not self.valid_target(update.target):
            return False
        if latest_evidence is None or latest_evidence not in update.evidence_ids:
            return False
        records = [self.evidence.get(item) for item in update.evidence_ids]
        if any(record is None for record in records):
            return False
        if not any(
            update.target in record.touched_targets for record in records if record
        ):
            return False
        current = self.candidates.get(update.target)
        if current is None:
            if update.status != "suspected" or len(self.candidates) >= self.cap:
                return False
            return self.seed(update.target, update.reason, update.evidence_ids[-1])
        if current.status == "confirmed":
            return False
        if current.status == "rejected":
            if update.status == "rejected":
                current.rejection_count += 1
                return False
            if update.status != "suspected" or current.rejection_count >= 2:
                return False
        elif update.status not in {"confirmed", "rejected"}:
            return False
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
        return changed

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

        current = self.candidates.get(target)
        if current is None or current.status != "suspected":
            return False, "target is not a suspected candidate"
        if decision == "keep":
            return False, None
        if not evidence_ids:
            return False, "no evidence cited"
        if any(item not in presented for item in evidence_ids):
            return False, "cited evidence was not presented for this target"
        records = [self.evidence.get(item) for item in evidence_ids]
        if any(
            record is None or target not in record.touched_targets for record in records
        ):
            return False, "cited evidence did not touch this target"
        if decision == "confirm" and not any(record.strong for record in records):
            return False, "confirmation needs non-lexical evidence"
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
        return True, None

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
