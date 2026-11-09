"""Deterministic missing-proof planning for opt-in V2 proof-driven tool selection.

The planner only proposes registered tool calls with validated arguments. It never
changes candidate status: a step supplies evidence that a later checkpoint may use,
and every checkpoint decision is still verified by ``CandidateLedger.decide_checked``.
A successful step is not proof that a file needs modification.
"""

import json
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any

from ripple.ledger import Candidate, CandidateLedger
from ripple.tools import REFERENCE_LIMIT, validate_tool_arguments

# Candidates considered for proof, in seed (lexical rank) order; equals the
# checkpoint batch size so planned work can be presented in one decision.
PROOF_CANDIDATES = 8
DIRECT_TOOLS = frozenset({"inspect_symbol", "find_references", "find_tests"})


@dataclass(frozen=True)
class ProofStep:
    target: str
    proof: str  # inspection | references | tests
    tool: str
    arguments: dict[str, Any]

    def key(self) -> str:
        return call_key(self.tool, self.arguments)

    def semantic_key(self) -> str:
        """Key of the controller's semantic cache, which ignores ``limit``."""

        return call_key(
            self.tool, {k: v for k, v in self.arguments.items() if k != "limit"}
        )

    def view(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "proof": self.proof,
            "tool": self.tool,
            "arguments": self.arguments,
        }


def call_key(name: str, canonical: Mapping[str, Any]) -> str:
    """The controller's exact-call cache key."""

    return json.dumps([name, dict(canonical)], sort_keys=True, separators=(",", ":"))


def _step(target: str, proof: str, tool: str, arguments: dict[str, Any]) -> ProofStep:
    return ProofStep(target, proof, tool, validate_tool_arguments(tool, arguments))


def direct_support(ledger: CandidateLedger, target: str) -> bool:
    """Strong successful evidence produced by a call aimed at this target's file."""

    path = target.partition("::")[0]
    for record in ledger.support(target):
        if not record.strong or record.tool_name not in DIRECT_TOOLS:
            continue
        aimed = record.arguments.get("target", record.arguments.get("symbol_id", ""))
        if str(aimed).partition("::")[0] == path:
            return True
    return False


def _symbol_inspected(ledger: CandidateLedger, path: str) -> bool:
    return any(
        record.result.ok
        and record.tool_name == "inspect_symbol"
        and str(record.arguments.get("target", "")).startswith(f"{path}::")
        for record in ledger.evidence.values()
    )


def candidate_steps(
    candidate: Candidate,
    ledger: CandidateLedger,
    *,
    require_refs: bool,
    symbols_in: Callable[[str], list[str]],
    detail: bool = False,
) -> list[ProofStep]:
    """Missing proof for one candidate, in inspection, references, tests order.

    With ``detail`` (review progression), a file candidate whose symbols were never
    inspected also gets one inspection of its first symbol, whose docstring and
    signature are behavioural evidence a file outline does not carry.
    """

    target = candidate.target
    path = target.partition("::")[0]
    steps = []
    if candidate.status == "suspected" and not direct_support(ledger, target):
        steps.append(_step(target, "inspection", "inspect_symbol", {"target": target}))
    if require_refs and not candidate.checked_refs:
        symbol = target if "::" in target else next(iter(symbols_in(path)), None)
        if symbol is not None:  # a file without symbols cannot have references
            steps.append(
                _step(
                    target,
                    "references",
                    "find_references",
                    {"symbol_id": symbol, "limit": REFERENCE_LIMIT},
                )
            )
    if not candidate.checked_tests:
        steps.append(_step(target, "tests", "find_tests", {"target": target}))
    if detail and "::" not in target and not _symbol_inspected(ledger, path):
        symbol = next(iter(symbols_in(path)), None)
        if symbol is not None:
            steps.append(_step(target, "detail", "inspect_symbol", {"target": symbol}))
    return steps


def plan_proof_steps(
    ledger: CandidateLedger,
    *,
    attempted: Collection[str],
    tests: Collection[str],
    require_refs: bool,
    symbols_in: Callable[[str], list[str]],
    exclude: Collection[str] = (),
    detail: bool = False,
) -> list[ProofStep]:
    """All useful missing-proof calls, most useful first; never a repeated call.

    Tiers: confirmed source targets still blocking submission; then breadth-first
    inspection of uninspected suspects so a checkpoint can prune them; then the
    remaining reference and test checks of inspected suspects. Rejected targets,
    test files, and calls already attempted (successful or failed) are skipped;
    ``attempted`` holds exact and limit-insensitive call keys, because the
    controller answers either kind of repeat from its cache.
    """

    confirmed: list[ProofStep] = []
    inspection: list[ProofStep] = []
    checks: list[ProofStep] = []
    seen = set(attempted)
    considered = 0
    for candidate in ledger.candidates.values():
        if (
            candidate.status == "rejected"
            or candidate.target in exclude
            or candidate.target.partition("::")[0] in tests
        ):
            continue
        if candidate.status == "suspected":
            if considered >= PROOF_CANDIDATES:
                continue
            considered += 1
        for step in candidate_steps(
            candidate,
            ledger,
            require_refs=require_refs,
            symbols_in=symbols_in,
            detail=detail,
        ):
            if step.key() in seen or step.semantic_key() in seen:
                continue
            seen.update((step.key(), step.semantic_key()))
            if candidate.status == "confirmed":
                confirmed.append(step)
            elif step.proof == "inspection":
                inspection.append(step)
            else:
                checks.append(step)
    return confirmed + inspection + checks
