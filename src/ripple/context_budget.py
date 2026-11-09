"""Opt-in deterministic bounds for structured model-request context.

Budgets are in prompt bytes as an ASCII-escaped JSON string, which is how the P2B
experimental estimator counts the prompt inside the canonical request. They leave at
least 1,500 bytes under that estimator's 16,000 cap after the measured fixed
overhead of each operation (instructions, schema, framing allowance, output cap):
choose_next_action 5,458; decide_candidates 5,290; draft_report 8,601. The values are
local planning bounds, not tokenizer counts or provider guarantees.

Selection never edits strings and never invents data. It replaces whole items with
explicit digests or counts, in a fixed priority order, and records what it omitted.
The ledger keeps every original evidence record, and omitted context is labelled as
such so that it is never presented as missing or negative evidence. If the required
core does not fit, ``ContextBudgetExceeded`` is raised and the caller fails closed.
"""

import json
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

PROMPT_BUDGETS = {
    "choose_next_action": 9000,
    "decide_candidates": 9000,
    "draft_report": 5800,
}
OMISSION_NOTE = (
    "Some context was shortened to fit the request budget. Omitted items remain "
    "recorded in the ledger; omission is not missing or negative evidence."
)
LIST_FIELDS = (
    "hits",
    "references",
    "outline",
    "neighbors",
    "tests",
    "facts",
    "partners",
)
# (latest-observation item budget, detailed ledger entries, older summaries kept)
LEVELS = (
    (None, None, None),
    (None, None, 8),
    (3000, 10, 8),
    (1500, 5, 3),
    (600, 3, 0),
    (600, 3, 0),  # same selection; compact entries drop the per-entry flag
)
EVIDENCE_TAIL = 6


class ContextBudgetExceeded(RuntimeError):
    """The required context of a request cannot fit its budget."""


def prompt_bytes(prompt: str) -> int:
    return len(json.dumps(prompt)) - 2


def value_bytes(value: Any) -> int:
    """Escaped bytes a value contributes when embedded as compact-or-default JSON."""

    return len(json.dumps(json.dumps(value, default=str))) - 2


@dataclass
class Selection:
    operation: str
    budget: int
    level: int = 0
    omitted: dict[str, int] = field(default_factory=dict)

    def omit(self, key: str, count: int) -> None:
        if count > 0:
            self.omitted[key] = self.omitted.get(key, 0) + count

    def record(self, prompt: str, data: dict[str, Any]) -> dict[str, Any]:
        """Sizes only: never raw prompt text or source content."""

        return {
            "operation": self.operation,
            "budget": self.budget,
            "prompt_bytes": prompt_bytes(prompt),
            "fits": prompt_bytes(prompt) <= self.budget,
            "level": self.level,
            "fields": {key: value_bytes(value) for key, value in data.items()},
            "omitted": dict(sorted(self.omitted.items())),
        }


def _digest(observation: dict[str, Any]) -> dict[str, Any]:
    """Identity, status, errors and sizes of an earlier observation; no payload."""

    result = observation.get("result")
    if not isinstance(result, dict):
        return observation  # controller guidance and errors stay verbatim
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    digest = {k: v for k, v in observation.items() if k != "result"}
    digest["result"] = {
        "evidence_id": result.get("evidence_id"),
        "ok": result.get("ok"),
        "truncated": result.get("truncated"),
        "error": result.get("error"),
        "items": {k: len(v) for k, v in data.items() if isinstance(v, list)},
        "payload_omitted": True,
    }
    return digest


def _touches(item: Any, focus: Collection[str]) -> bool:
    values = item.values() if isinstance(item, dict) else ()
    return any(str(value).partition("::")[0] in focus for value in values)


def _select_items(
    observation: dict[str, Any],
    budget: int,
    focus: Collection[str],
    selection: Selection,
) -> dict[str, Any]:
    """Keep whole list items, focused ones first, original order, within budget.

    Items are considered in priority order (candidate-touching first, then original
    order) and each is kept if it still fits; an item that does not fit is skipped,
    never cut, so one oversized item cannot crowd out smaller relevant ones. Kept
    items are emitted in their original order. ``{key}_omitted`` counts every
    omitted item; ``{key}_oversized`` counts those larger than the whole budget.
    """

    result = observation.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
        return observation
    data = dict(result["data"])
    shortened = False
    for key in LIST_FIELDS:
        items = data.get(key)
        if not isinstance(items, list) or value_bytes(items) <= budget:
            continue
        focused = [i for i, item in enumerate(items) if _touches(item, focus)]
        chosen = set(focused)
        order = focused + [i for i in range(len(items)) if i not in chosen]
        kept, used, oversized = set(), 2, 0
        for index in order:
            size = value_bytes(items[index]) + 2
            if size + 2 > budget:
                oversized += 1
                continue
            if used + size > budget:
                continue
            kept.add(index)
            used += size
        data[key] = [items[i] for i in sorted(kept)]
        data[f"{key}_omitted"] = len(items) - len(kept)
        if oversized:
            data[f"{key}_oversized"] = oversized
        selection.omit(f"latest_observation_{key}", len(items) - len(kept))
        shortened = True
    if not shortened:
        return observation
    return {**observation, "result": {**result, "data": data, "truncated": True}}


def _ledger(
    ledger: list[dict[str, Any]],
    detail: int | None,
    protected: Collection[str],
    selection: Selection,
    flag: bool = True,
) -> list[dict[str, Any]]:
    rank = {"confirmed": 0, "suspected": 1, "rejected": 2}
    order = sorted(
        range(len(ledger)),
        key=lambda i: (
            ledger[i].get("target") not in protected,
            rank.get(ledger[i].get("status"), 3),
            not ledger[i].get("non_lexical_evidence"),
            i,
        ),
    )
    detailed = {
        i
        for i in (order if detail is None else order[:detail])
        if ledger[i].get("status") != "rejected"
    }
    view = []
    for index, item in enumerate(ledger):
        if index not in detailed:
            compact = {
                "target": item.get("target"),
                "status": item.get("status"),
                "non_lexical": bool(item.get("non_lexical_evidence")),
            }
            view.append({**compact, "details_omitted": True} if flag else compact)
            selection.omit("ledger_details", 1)
            continue
        entry = dict(item)
        for key in ("supporting_evidence", "non_lexical_evidence"):
            ids = entry.get(key) or []
            if len(ids) > EVIDENCE_TAIL:
                entry[key] = ids[-EVIDENCE_TAIL:]
                entry[f"{key}_count"] = len(ids)
                selection.omit(key, len(ids) - EVIDENCE_TAIL)
        view.append(entry)
    return view


def bound_decision_context(
    context: dict[str, Any],
    render: Callable[[dict[str, Any]], str],
    *,
    focus: Collection[str],
    protected: Collection[str],
    budget: int = PROMPT_BUDGETS["choose_next_action"],
) -> tuple[str, dict[str, Any]]:
    """Return the first prompt, from the least to the most compact level, that fits.

    Level 0 is the unchanged context. Later levels digest earlier recent observations,
    keep a tail of older summaries, shorten the latest payload by whole items, and keep
    full ledger entries for ``protected`` targets (confirmed, the next proof target)
    before others. The latest observation's identity, error and evidence ID, the
    intent, budgets and submission state are always kept.
    """

    for level, (item_budget, detail, older_tail) in enumerate(LEVELS):
        selection = Selection("choose_next_action", budget, level)
        data = dict(context)
        if level:
            recent = list(context.get("last_three_observations", []))
            if recent:
                latest = recent[-1]
                if item_budget is not None:
                    latest = _select_items(latest, item_budget, focus, selection)
                earlier = [_digest(item) for item in recent[:-1]]
                selection.omit(
                    "observation_payloads",
                    sum(
                        item is not original for item, original in zip(earlier, recent)
                    ),
                )
                data["last_three_observations"] = [*earlier, latest]
            older = list(context.get("older_observation_summaries", []))
            kept = older[len(older) - older_tail :] if older_tail else []
            data["older_observation_summaries"] = kept
            data["older_observation_count"] = len(older)
            data["older_failed_count"] = sum(item.get("ok") is False for item in older)
            selection.omit("older_observation_summaries", len(older) - len(kept))
            last = level == len(LEVELS) - 1
            data["ledger"] = _ledger(
                list(context.get("ledger", [])), detail, protected, selection, not last
            )
            if last:
                data["ledger_details_omitted"] = selection.omitted.get(
                    "ledger_details", 0
                )
            data["context_selection"] = {
                "level": level,
                "omitted": dict(sorted(selection.omitted.items())),
                "note": OMISSION_NOTE,
            }
        prompt = render(data)
        if prompt_bytes(prompt) <= budget:
            return prompt, selection.record(prompt, data)
    raise ContextBudgetExceeded("decision_context_exceeds_budget")


def select_checkpoint_evidence(records: list[Any]) -> tuple[list[Any], int]:
    """At most EVIDENCE_TAIL records: strong newest first, then lexical newest."""

    order = sorted(range(len(records)), key=lambda i: (not records[i].strong, -i))
    keep = sorted(order[:EVIDENCE_TAIL])
    return [records[i] for i in keep], len(records) - len(keep)
