"""Explicitly approved UB course experiments; never a verified provider contract.

The ordinary ripple.pilot CLI and Contract.load remain the strict default.
Preparing an approval is offline. Execution additionally requires a clean reviewed
commit, a private approval file, and an explicit task-specific CLI acknowledgement.
"""

import argparse
import dataclasses
import hashlib
import json
import os
import subprocess
from pathlib import Path

from ripple import pilot
from ripple.agent import RIPPLE_V2_BOUNDED
from ripple.llm import OpenAILLM, RetryPolicy
from ripple.pilot_admission import (
    Admission,
    Journal,
    Limits,
    PilotStop,
    canonical,
    digest,
    identifier,
    private_write,
    read_private_artifact,
    unique_object,
)
from ripple.pilot_formatting import (
    FORMAT_ID,
    FormattingClient,
    verify_journal_formatting,
)

PROFILE = "EXPERIMENTAL_UB_COURSE"
BASE = "af167f08c36240eb4dc3b2aff23230d3036497e4"
ROOT = Path(__file__).resolve().parents[2]


def specification():
    """Local stop thresholds and assumptions, not claimed deployment guarantees."""
    return {
        "profile": PROFILE,
        "status": "EXPERIMENTAL_UNVERIFIED",
        "provider_guarantees_verified": False,
        "model": pilot.MODEL,
        "base_url": pilot.URL,
        "limits": dataclasses.asdict(Limits()),
        "single_concurrent_request": True,
        "sdk_retries": 0,
        "redirects": False,
        "endpoint": "responses",
        "output_parameter": "max_output_tokens",
        "accounting": "retain_full_reservation_or_larger_observation",
        # Lossless whitespace-only compaction of proven structured prompt context,
        # applied identically to capture and both replays.
        "request_formatting": FORMAT_ID,
        # Operational context bounding only; every new accuracy policy stays off.
        "agent_variant": dataclasses.asdict(RIPPLE_V2_BOUNDED),
        "local_assumptions": {
            "input_estimator": "serialized_request_utf8_bytes_plus_allowance",
            "framing_allowance": 4096,
            "context_planning_ceiling": 18000,
            "requests_per_minute_ceiling": 24,
            "tokens_per_minute_ceiling": 110000,
        },
        "unverified": [
            "deployed_context",
            "maximum_output",
            "reasoning_cap_guarantee",
            "tokenizer_and_framing",
            "usage_authority",
            "provider_rate_limits",
            "concurrency_limits",
            "server_cancellation",
            "billing_and_retries",
            "parameter_support_guarantees",
        ],
    }


@dataclasses.dataclass(frozen=True)
class ExperimentalPolicy:
    """Duck-typed local admission policy; deliberately not a verified Contract."""

    model: str = pilot.MODEL
    context_limit: int = 18000  # Local planning ceiling only.
    output_limit: int = 2000  # Local request cap only.
    framing_tokens: int = 4096  # Unverified safety allowance.
    mock: bool = False
    requests_per_minute: int = 24
    tokens_per_minute: int = 110000

    @property
    def output_parameters(self):
        return {
            "responses": "max_output_tokens",
            "chat_completions": "max_completion_tokens",
        }

    def input_bound(self, request):
        return len(canonical(request)) + self.framing_tokens


class ExperimentalAdmission(Admission):
    def reserve(self, endpoint, request):
        if endpoint != "responses":
            self.stop("experimental_endpoint_changed")
        return super().reserve(endpoint, request)

    def append(self, event):
        super().append(event)  # Retain usage evidence before rejecting metadata.
        if event["kind"] != "wire_metadata":
            return
        usage = event.get("usage") or {}
        details = usage.get("output_tokens_details") or {}
        reasoning = details.get("reasoning_tokens")
        output = usage.get("output_tokens")
        if event.get("invalid_usage_fields") or (
            reasoning is not None and (output is None or reasoning > output)
        ):
            self.stop("usage_unknown_or_inconsistent")


def repository_identity(*, require_clean):
    def git(*args):
        return subprocess.check_output(
            ["git", "--no-optional-locks", "-C", str(ROOT), *args]
        )

    if subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", BASE, "HEAD"],
        capture_output=True,
        check=False,
    ).returncode:
        raise PilotStop("experimental_base_mismatch")
    if require_clean and git("status", "--porcelain=v1", "--untracked-files=all"):
        raise PilotStop("reviewed_clean_commit_required")
    # The strict harness hash plus all tracked/untracked runtime modules binds the
    # complete imported runtime, including this distinct experimental entry point.
    runtime_files = sorted((ROOT / "src/ripple").rglob("*.py"))
    if any(p.is_symlink() for p in runtime_files):
        raise PilotStop("experimental_runtime_symlink")
    harness = digest(
        {
            "strict_harness": pilot.harness_hash(),
            "runtime": {
                p.relative_to(ROOT).as_posix(): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in runtime_files
            },
        }
    )
    return {
        "git_commit": git("rev-parse", "HEAD").decode().strip(),
        "harness_sha256": harness,
        **pilot.verify_runtime(),
    }


def binding(package, output, *, require_clean):
    manifest = pilot.verify_package(package)
    output = pilot.validate_output_destination(output, package, manifest)
    return {
        **repository_identity(require_clean=require_clean),
        "input_package_sha256": pilot.package_hash(package),
        "output_directory": str(output),
        "experimental_policy": specification(),
    }


def approval_template(package, output, task_id):
    """Return an unapproved template; no SDK, credential access or output writes."""
    if task_id not in pilot.TASKS:
        raise PilotStop("experimental_task_unknown")
    return {
        "approved": False,
        "operator": "REPLACE_WITH_OPERATOR",
        "approval_id": "REPLACE_WITH_UNIQUE_ID",
        "acknowledges_unverified_provider": False,
        "binding": binding(package, output, require_clean=False),
        "task_id": task_id,
        "reviewed_previous_result_sha256": None,
    }


def load_approval(path):
    try:
        return json.loads(read_private_artifact(path), object_pairs_hook=unique_object)
    except (OSError, ValueError, TypeError, PilotStop):
        raise PilotStop("experimental_approval_invalid") from None


def validate_approval(approval, expected, task_id, acknowledged_task):
    if not isinstance(approval, dict) or set(approval) != {
        "approved",
        "operator",
        "approval_id",
        "acknowledges_unverified_provider",
        "binding",
        "task_id",
        "reviewed_previous_result_sha256",
    }:
        raise PilotStop("experimental_approval_invalid")
    if (
        acknowledged_task != task_id
        or approval["approved"] is not True
        or approval["acknowledges_unverified_provider"] is not True
        or not identifier(approval["operator"])
        or not identifier(approval["approval_id"])
        or approval["operator"].startswith("REPLACE_")
        or approval["approval_id"].startswith("REPLACE_")
        or approval["task_id"] != task_id
        or canonical(approval["binding"]) != canonical(expected)
    ):
        raise PilotStop("experimental_task_not_approved")


def validate_stage(gate, output, task_id, approval):
    position = pilot.TASKS.index(task_id)
    if gate.finished != list(pilot.TASKS[:position]):
        raise PilotStop("experimental_prior_result_review_required")
    previous = approval["reviewed_previous_result_sha256"]
    if position == 0:
        if previous is not None:
            raise PilotStop("experimental_prior_result_review_required")
    else:
        result = json.loads(
            read_private_artifact(output / pilot.TASKS[position - 1] / "result.json")
        )
        if (
            previous != digest(result)
            or result.get("experiment", {}).get("provider_guarantees_verified")
            is not False
            or result.get("comparisons", {}).get("on", {}).get("false_positive") != 0
        ):
            raise PilotStop("experimental_prior_result_review_required")


@pilot.execution_interrupts()
def execute_one(package, output, task_id, approval_path, *, profile, acknowledged_task):
    """One explicit approval, one task, one shared campaign ledger; no automatic continuation."""
    if profile != PROFILE:
        raise PilotStop("experimental_profile_opt_in_required")
    if task_id not in pilot.TASKS:
        raise PilotStop("experimental_task_unknown")
    expected = binding(package, output, require_clean=True)
    output = Path(expected["output_directory"])
    approval = load_approval(approval_path)
    validate_approval(approval, expected, task_id, acknowledged_task)
    approval_sha = digest(approval)
    metadata = {
        "profile": PROFILE,
        "provider_guarantees_verified": False,
        "reservation_floor": True,
        "ledger_sha256": digest(expected),
        "approval_sha256": approval_sha,
    }
    manifest = pilot.verify_package(package)
    # Validate before creating files; the Journal lock serializes this entire task.
    journal = Journal(output)
    try:
        gate = ExperimentalAdmission(journal, ExperimentalPolicy(), experiment=metadata)
        validate_stage(gate, output, task_id, approval)
        ledger = output / "experiment.json"
        if ledger.exists() or ledger.is_symlink():
            if read_private_artifact(ledger) != canonical(expected) + b"\n":
                raise PilotStop("experimental_ledger_changed")
        elif journal.events:
            raise PilotStop("experimental_ledger_missing")
        else:
            private_write(ledger, expected)
        private_write(output / f"approval-{task_id}.json", approval)
        task = next(row for row in manifest["tasks"] if row["task_id"] == task_id)
        gate.start(task_id, task["token_allocation"])
        folder = output / task_id
        folder.mkdir(mode=0o700)
        index = pilot.fixture_index(package, task, folder)
        original_fixture = pilot.source_identity(index)

        def preflight():
            if (
                binding(package, output, require_clean=True) != expected
                or digest(load_approval(approval_path)) != approval_sha
                or read_private_artifact(ledger) != canonical(expected) + b"\n"
                or pilot.source_identity(index) != original_fixture
            ):
                gate.stop("request_identity_changed")

        gate.preflight = preflight
        pilot.verify_environment()
        # Existing constructor sets SDK retries=0 and per-I/O timeout=90s;
        # attach_gate disables redirects and adds an absolute 90s wire deadline.
        client = OpenAILLM(
            api_key=os.environ.get("RIPPLE_LLM_API_KEY", ""),
            model=pilot.MODEL,
            base_url=pilot.URL,
            retry_policy=RetryPolicy(allow_request_replay=False),
        )
        # Formatting wraps the gated client: the journal keeps the agent-rendered
        # logical prompt (which identical replay must reproduce) and the compact
        # wire request that was admitted and dispatched.
        capture = pilot.CaptureClient(
            FormattingClient(pilot.attach_gate(client, gate)), gate
        )
        result = pilot.run_pair(index, task, capture, folder, variant=RIPPLE_V2_BOUNDED)
        verify_journal_formatting(gate.journal.events)
        result["experiment"] = {
            **metadata,
            "policy": specification(),
            "production_guarantees": "UNVERIFIED",
        }
        if any(
            result[arm]["run_stats"]["stop_reason"] == "context_budget"
            for arm in ("off", "on")
        ):
            # A local context stop is not an eligible abstention/correct negative.
            # Preserve partial capture/replay evidence without scoring predictions.
            result.update(accuracy_evaluable=False, terminal_reason="context_budget")
            private_write(folder / "result.json", result)
            gate.stop("context_budget")
        result.update(pilot.expected_comparison(package, task, result))
        private_write(folder / "result.json", result)
        if result["comparisons"]["on"]["false_positive"]:
            gate.stop("unexpected_source_prediction")
        preflight()
        gate.finish()
        return result
    except BaseException as error:
        if "gate" in locals() and gate.current and not gate.stopped:
            try:
                gate.stop(
                    "execution_interrupted"
                    if isinstance(error, (KeyboardInterrupt, SystemExit))
                    else "capture_or_execution_failed"
                )
            except PilotStop:
                pass
        if isinstance(error, (PilotStop, KeyboardInterrupt, SystemExit)):
            raise
        raise PilotStop("capture_or_execution_failed") from None
    finally:
        if "gate" in locals():
            gate.persist_summary()
        journal.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare-approval", "execute-one"])
    parser.add_argument("--profile", choices=[PROFILE], required=True)
    parser.add_argument("--package", type=Path, default=ROOT / "dev/p2b")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task", choices=pilot.TASKS, required=True)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--approve-live-task", choices=pilot.TASKS)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-approval":
            print(
                json.dumps(
                    approval_template(args.package, args.output, args.task), indent=2
                )
            )
        else:
            if args.approval is None:
                raise PilotStop("experimental_approval_required")
            result = execute_one(
                args.package,
                args.output,
                args.task,
                args.approval,
                profile=args.profile,
                acknowledged_task=args.approve_live_task,
            )
            print(
                json.dumps(
                    {
                        "profile": PROFILE,
                        "task": result["task_id"],
                        "provider_guarantees_verified": False,
                        "wire_requests": result["wire_requests"],
                    }
                )
            )
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - never echo external error text
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "profile": PROFILE,
                    "provider_guarantees_verified": False,
                }
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
