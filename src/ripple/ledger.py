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

    def confirmed(self) -> tuple[Candidate, ...]:
        return tuple(
            candidate
            for candidate in self.candidates.values()
            if candidate.status == "confirmed"
        )

    def submission_problem(self) -> str | None:
        confirmed = self.confirmed()
        if not confirmed:
            return "no candidates are confirmed"
        unchecked = [
            item.target
            for item in confirmed
            if not item.checked_refs or not item.checked_tests
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
