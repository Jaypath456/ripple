"""Experimental lossless request formatting for the UB course profile only.

The inherited agent renders structured prompt context with ``json.dumps`` default
separators, and its bytes are verified unchanged by ``pilot.verify_runtime``. This
adapter therefore works on the finished prompt string at the client boundary: it
rewrites a ``<repository_data>`` block only when the block is provably the default
``json.dumps`` rendering of a JSON object, and re-emits the same parsed value with
compact separators. Every other byte, including instructions, delimiters, untrusted
data notices, retry suffixes, and any block that cannot be proven, is unchanged.

The function is pure and deterministic. Capture records the agent-rendered logical
prompt; OFF and ON replay must reproduce that exact prompt, so each would format to
the identical dispatched bytes; the journal's wire record holds those bytes and
``verify_journal_formatting`` re-derives them offline. The strict pilot never uses it.
"""

import json
import math
import re

from ripple import pilot

FORMAT_ID = "compact-structured-json-v1"
# interpret embeds the repository map (a raw string) and the request text verbatim.
STRUCTURED_OPERATIONS = frozenset(
    {"choose_next_action", "decide_candidates", "draft_report"}
)
# _prompt_data emits "<label>\n{body}\n</label>\n"; a json.dumps body has no raw
# newline, so a single-line body cannot overrun into a later block.
_BLOCK = re.compile(r"<repository_data>\n([^\n]*)\n</repository_data>\n")


def _finite(value) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


def _compact_body(body: str) -> str | None:
    """Return the compact rendering, or None when lossless origin is unproven."""

    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        return None
    # Raw strings are embedded verbatim by _prompt_data; only objects are rewritten.
    if not isinstance(parsed, dict) or not _finite(parsed):
        return None
    try:
        # Exact round trip proves default-separator origin: duplicate keys, other
        # spacing, non-default escapes, or hand-written text all fail this check.
        if json.dumps(parsed) != body:
            return None
        compact = json.dumps(parsed, separators=(",", ":"))
    except (ValueError, RecursionError):
        return None
    if json.loads(compact) != parsed:  # pragma: no cover - defensive invariant
        return None
    return compact


def compact_prompt(operation: str, prompt: str) -> str:
    """Losslessly compact structured context; unproven input is returned unchanged."""

    if operation not in STRUCTURED_OPERATIONS or not isinstance(prompt, str):
        return prompt

    def replace(match: re.Match[str]) -> str:
        compact = _compact_body(match.group(1))
        if compact is None:
            return match.group(0)
        return f"<repository_data>\n{compact}\n</repository_data>\n"

    return _BLOCK.sub(replace, prompt)


def structured_context(prompt: str) -> list[object]:
    """Parsed repository_data values, used to prove formatting preserved context."""

    values = []
    for match in _BLOCK.finditer(prompt):
        try:
            values.append(json.loads(match.group(1)))
        except (ValueError, RecursionError):
            values.append(match.group(1))
    return values


def outside_blocks(prompt: str) -> list[str]:
    """Prompt text outside repository_data bodies; formatting never changes it."""

    return _BLOCK.split(prompt)[0::2]


class FormattingClient:
    """Wrap the gated model client; only the dispatched prompt is compacted.

    ``pilot.CaptureClient`` wraps this object, so its journal and trajectory keep the
    agent-rendered logical prompt that identical OFF/ON replay must reproduce, while
    admission, reservation and the wire record see the compacted request.
    """

    def __init__(self, client):
        self.client = client
        self.model = client.model

    def __getattr__(self, operation):
        if operation not in pilot.SCHEMAS:
            raise AttributeError(operation)
        call = getattr(self.client, operation)
        return lambda prompt: call(compact_prompt(operation, prompt))

    def drain_diagnostics(self):
        return self.client.drain_diagnostics()


def verify_journal_formatting(events) -> int:
    """Offline proof of request sequencing and formatting in a journal.

    Rules, from ``CaptureClient`` and ``WireEndpoint`` ordering:

    - a ``logical_request`` opens one request; at most one ``wire_reserved`` may
      follow it (no retries, no fallback), and only while it is open and before any
      ``stop``;
    - the reservation's ``input`` must be exactly the compact form of that request's
      prompt, with identical parsed context and identical text outside the blocks;
    - attempt numbers start at one and increase by exactly one across the journal;
    - a ``logical_response`` must follow its own reservation and repeat that
      request's operation and prompt; it closes the request;
    - a request left open is valid only if it ends at a ``stop``, a task boundary or
      the end of the journal (interrupted or refused before dispatch); a new request
      may not begin while one is open otherwise.

    Returns the number of verified reservations; raises ValueError on any violation.
    """

    verified = 0
    attempt = 0
    current = None  # {"operation", "prompt", "reserved"} of the open request
    stopped = False
    for sequence, event in enumerate(events, 1):
        kind = event.get("kind")
        if kind in {"task_started", "task_finished"}:
            current, stopped = None, False
        elif kind == "stop":
            current, stopped = None, True
        elif kind == "logical_request":
            if current is not None:
                raise ValueError(
                    f"event {sequence}: request opened before the previous closed"
                )
            current = {
                "operation": event["operation"],
                "prompt": event["prompt"],
                "reserved": False,
            }
        elif kind == "wire_reserved":
            if stopped:
                raise ValueError(f"event {sequence}: reservation after a stop")
            if current is None:
                raise ValueError(
                    f"event {sequence}: reservation without an open request"
                )
            if current["reserved"]:
                raise ValueError(
                    f"event {sequence}: duplicate reservation for one request"
                )
            number = event.get("attempt")
            if type(number) is not int or number != attempt + 1:
                raise ValueError(
                    f"event {sequence}: attempt numbers are not sequential"
                )
            attempt = number
            sent = (event.get("request") or {}).get("input")
            if sent != compact_prompt(current["operation"], current["prompt"]):
                raise ValueError(
                    f"event {sequence}: dispatched input is not the formatted prompt"
                )
            if structured_context(sent) != structured_context(current["prompt"]) or (
                outside_blocks(sent) != outside_blocks(current["prompt"])
            ):
                raise ValueError(
                    f"event {sequence}: formatting changed request content"
                )
            current["reserved"] = True
            verified += 1
        elif kind == "logical_response":
            if current is None or not current["reserved"]:
                raise ValueError(
                    f"event {sequence}: response without a dispatched request"
                )
            if (event.get("operation"), event.get("prompt")) != (
                current["operation"],
                current["prompt"],
            ):
                raise ValueError(
                    f"event {sequence}: response does not match its request"
                )
            current = None
    return verified
