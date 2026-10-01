"""Mostly deterministic Stage B verification of saved impact reports."""

import ast
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from ripple.agent import TraceWriter, _prompt_data
from ripple.agent_models import ChangeImpactReport
from ripple.diff_models import (
    STAGE_B_CONFIG_VERSION,
    DiffAnalysis,
    DiffFinding,
    FileChange,
    InvestigationDecision,
    VerificationRun,
    VerificationStats,
)
from ripple.diffing import (
    DiffError,
    DiffSnapshot,
    ParsedFileDiff,
    parse_diff,
    resolve_range,
)
from ripple.history import HistoryError, co_changed
from ripple.llm import LLMClient, LLMError
from ripple.models import RepositoryIndex
from ripple.scanner import is_test_file, scan_repository
from ripple.verification_render import render_verification_markdown
from ripple.verify_tools import VerificationToolSession

MAX_INVESTIGATION_TOOL_CALLS = 8


class VerificationError(ValueError):
    """A saved report or verification request is invalid."""


@dataclass(frozen=True)
class _Prediction:
    target: str
    path: str
    confidence: str
    migration_directory: str | None = None


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise VerificationError(result.stderr.strip() or "Git command failed")
    return result.stdout


def load_report(repo: Path, value: str) -> tuple[ChangeImpactReport, Path]:
    reports = repo / ".ripple" / "reports"
    if value == "latest":
        candidates = list(reports.glob("*.json"))
        if not candidates:
            raise VerificationError("no saved reports found for --report latest")
        latest_time = max(path.stat().st_mtime_ns for path in candidates)
        latest = sorted(
            path for path in candidates if path.stat().st_mtime_ns == latest_time
        )
        if len(latest) != 1:
            raise VerificationError(
                "--report latest is ambiguous; provide an explicit report ID or path"
            )
        path = latest[0]
    else:
        supplied = Path(value)
        candidates = [
            supplied if supplied.is_absolute() else Path.cwd() / supplied,
            reports / value,
            reports / f"{value}.json",
        ]
        path = next(
            (candidate for candidate in candidates if candidate.is_file()), None
        )
        if path is None:
            raise VerificationError(f"report not found: {value}")
    try:
        report = ChangeImpactReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError, ValueError) as error:
        raise VerificationError(f"invalid saved report {path}: {error}") from error
    return report, path.resolve()


def _checkout(repo: Path, commit: str, destination: Path) -> RepositoryIndex:
    result = subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-checkout",
            "--local",
            str(repo),
            str(destination),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise VerificationError(
            result.stderr.strip() or "could not create local verification clone"
        )
    _git(destination, "checkout", "--quiet", "--detach", commit)
    return scan_repository(destination)


def _ignored(path: str) -> bool:
    candidate = Path(path)
    lower = path.casefold()
    name = candidate.name.casefold()
    directories = {part.casefold() for part in candidate.parts[:-1]}
    return (
        bool(directories & {"doc", "docs", "documentation", "changelog", "news"})
        or name.startswith(("readme", "changelog", "changes", "authors"))
        or name in {"poetry.lock", "pdm.lock", "pipfile.lock", "uv.lock"}
        or name.endswith((".lock", "_pb2.py", "_pb2_grpc.py", ".min.js"))
        or ".generated." in name
        or lower.startswith(".github/")
    )


def _predictions(
    report: ChangeImpactReport, base: RepositoryIndex
) -> tuple[_Prediction, ...]:
    files = {item.path.as_posix(): item for item in base.files}
    values: list[_Prediction] = []
    seen: set[str] = set()
    for component in report.affected_components:
        path = component.target.partition("::")[0]
        migration_directory: str | None = None
        if component.change_type == "new_file" and path.endswith(
            "/<proposed migration>"
        ):
            migration_directory = path.removesuffix("/<proposed migration>")
        elif path not in files or files[path].is_test:
            continue
        if path in seen:
            continue
        seen.add(path)
        values.append(
            _Prediction(
                target=component.target,
                path=path,
                confidence=component.confidence,
                migration_directory=migration_directory,
            )
        )
    return tuple(values)


def _changed_keys(item: ParsedFileDiff) -> set[str]:
    return {item.change.path} | (
        {item.change.old_path} if item.change.old_path is not None else set()
    )


def _prediction_match(
    prediction: _Prediction, snapshot: DiffSnapshot
) -> ParsedFileDiff | None:
    if prediction.migration_directory:
        prefix = f"{prediction.migration_directory}/"
        return next(
            (
                item
                for item in snapshot.files
                if item.change.status == "A"
                and item.change.path.startswith(prefix)
                and item.change.path.endswith(".py")
                and Path(item.change.path).name != "__init__.py"
            ),
            None,
        )
    return next(
        (item for item in snapshot.files if prediction.path in _changed_keys(item)),
        None,
    )


def _graph_relationship(
    base: RepositoryIndex, changed_path: str, predictions: tuple[_Prediction, ...]
) -> str | None:
    nodes = {node.path.as_posix(): node for node in base.dependency_graph}
    changed = nodes.get(changed_path)
    for prediction in predictions:
        predicted = nodes.get(prediction.path)
        if predicted is None or changed is None:
            continue
        if Path(changed_path) in (*predicted.dependencies, *predicted.dependents):
            return f"graph:{prediction.path}<->{changed_path}"
    return None


def _cochange_relationship(
    base: RepositoryIndex, changed_path: str, predictions: tuple[_Prediction, ...]
) -> str | None:
    for prediction in predictions:
        if prediction.migration_directory:
            continue
        try:
            result = co_changed(base.repo_root, prediction.path, limit=3)
        except HistoryError:
            continue
        partner = next(
            (item for item in result.partners if item.path == changed_path), None
        )
        if partner:
            return (
                f"cochange:{prediction.path}:{partner.count}/{result.commits_with_path}"
            )
    return None


def _function_nodes(
    source: str | None,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    if source is None:
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit(nodes: list[ast.stmt], parents: tuple[str, ...] = ()) -> None:
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                visit(node.body, (*parents, node.name))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = ".".join((*parents, node.name))
                found[qualname] = node
                visit(node.body, (*parents, node.name))

    visit(tree.body)
    return found


def _required_positional(arguments: ast.arguments) -> dict[str, bool]:
    values = [*arguments.posonlyargs, *arguments.args]
    default_start = len(values) - len(arguments.defaults)
    return {
        item.arg: position < default_start
        for position, item in enumerate(values)
        if item.arg not in {"self", "cls"}
    }


def _required_keywords(arguments: ast.arguments) -> dict[str, bool]:
    return {
        item.arg: default is None
        for item, default in zip(
            arguments.kwonlyargs, arguments.kw_defaults, strict=True
        )
    }


def _incompatible_signature(
    old: ast.FunctionDef | ast.AsyncFunctionDef,
    new: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    old_pos = _required_positional(old.args)
    new_pos = _required_positional(new.args)
    if list(old_pos) != [name for name in new_pos if name in old_pos]:
        return "positional parameter removed, renamed, or reordered"
    if any(name not in new_pos for name in old_pos):
        return "positional parameter removed or renamed"
    if any(new_pos[name] and not old_pos.get(name, False) for name in new_pos):
        return "required positional parameter added or made mandatory"
    old_kw = _required_keywords(old.args)
    new_kw = _required_keywords(new.args)
    if any(name not in new_kw for name in old_kw):
        return "keyword-only parameter removed or renamed"
    if any(new_kw[name] and not old_kw.get(name, False) for name in new_kw):
        return "required keyword-only parameter added or made mandatory"
    if old.args.vararg and not new.args.vararg:
        return "variadic positional parameter removed"
    if old.args.kwarg and not new.args.kwarg:
        return "variadic keyword parameter removed"
    return None


def _stale_callers(snapshot: DiffSnapshot, base: RepositoryIndex) -> list[DiffFinding]:
    changed_paths = {path for item in snapshot.files for path in _changed_keys(item)}
    findings: list[DiffFinding] = []
    seen: set[tuple[str, str]] = set()
    for item in snapshot.files:
        old_path = item.change.old_path or item.change.path
        if not old_path.endswith(".py") or item.change.cosmetic_only:
            continue
        old_functions = _function_nodes(item.old_source)
        new_functions = _function_nodes(item.new_source)
        for qualname in sorted(old_functions.keys() & new_functions.keys()):
            reason = _incompatible_signature(
                old_functions[qualname], new_functions[qualname]
            )
            if reason is None:
                continue
            symbol_id = f"{old_path}::{qualname}"
            for reference in base.references:
                caller = reference.source_path.as_posix()
                if (
                    reference.target_symbol != symbol_id
                    or caller in changed_paths
                    or caller == old_path
                ):
                    continue
                key = (caller, symbol_id)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    DiffFinding(
                        path=caller,
                        category="stale_caller",
                        verdict="suspicious",
                        explanation=(
                            f"Unchanged caller references {symbol_id}, whose signature "
                            f"became incompatible: {reason}."
                        ),
                        evidence=(
                            f"signature:{symbol_id}",
                            f"reference:{caller}:{reference.line}",
                        ),
                    )
                )
    return findings


def _missing_tests(
    snapshot: DiffSnapshot, base: RepositoryIndex, head: RepositoryIndex
) -> list[DiffFinding]:
    changed_paths = {path for item in snapshot.files for path in _changed_keys(item)}
    changed_tests = {path for path in changed_paths if is_test_file(Path(path))}
    findings: list[DiffFinding] = []
    for item in snapshot.files:
        old_path = item.change.old_path or item.change.path
        if (
            not old_path.endswith(".py")
            or is_test_file(Path(old_path))
            or item.change.cosmetic_only
            or item.change.binary
            or not item.change.changed_symbols
        ):
            continue
        mappings = [
            mapping
            for mapping in base.test_mappings
            if mapping.source_path.as_posix() == old_path
        ]
        mappings.extend(
            mapping
            for mapping in head.test_mappings
            if mapping.source_path.as_posix() == item.change.path
        )
        mapped_tests = {mapping.test_path.as_posix() for mapping in mappings}
        if mapped_tests & changed_tests:
            continue
        suggestion = (
            f" Existing mapped tests were unchanged: {', '.join(sorted(mapped_tests))}."
            if mapped_tests
            else " No statically mapped test was found."
        )
        findings.append(
            DiffFinding(
                path=item.change.path,
                category="missing_test",
                verdict="suspicious",
                explanation=(
                    "A non-test Python symbol changed without a changed mapped test."
                    + suggestion
                    + " Static mapping does not prove runtime coverage."
                ),
                evidence=(
                    f"changed_symbols:{','.join(item.change.changed_symbols)}",
                    *(f"mapped_test:{path}" for path in sorted(mapped_tests)),
                ),
            )
        )
    return findings


def _investigate(
    finding: DiffFinding,
    request: str,
    llm: LLMClient,
    session: VerificationToolSession,
    trace: TraceWriter,
) -> tuple[DiffFinding, int, int]:
    observations: list[dict[str, Any]] = []
    tool_calls = 0
    llm_calls = 0
    initial = session.invoke("file_diff", {"path": finding.path})
    tool_calls += 1
    observations.append(
        {"tool": "file_diff", "result": initial.model_dump(mode="json")}
    )
    trace.write(
        "investigation_tool",
        path=finding.path,
        tool="file_diff",
        evidence_id=initial.evidence_id,
        ok=initial.ok,
        result=initial.model_dump(mode="json"),
    )
    while tool_calls < MAX_INVESTIGATION_TOOL_CALLS and llm_calls < 9:
        prompt = (
            "Investigate only this Stage B finding. Choose file_diff, inspect_symbol, "
            "find_references, or submit_verdict. A submitted verdict must be justified, "
            "suspicious, or unexplained and cite evidence IDs that touch the finding path. "
            "Insufficient evidence means unexplained. Do not alter the category.\n"
            + _prompt_data(
                "repository_data",
                {
                    "request": request,
                    "finding": finding.model_dump(mode="json"),
                    "observations": observations[-3:],
                    "tool_calls": tool_calls,
                    "tool_limit": MAX_INVESTIGATION_TOOL_CALLS,
                },
            )
        )
        try:
            response = llm.investigate_diff(prompt)
            llm_calls += 1
            decision = InvestigationDecision.model_validate(response.payload)
        except (LLMError, ValidationError) as error:
            trace.write("investigation_error", path=finding.path, error=str(error))
            break
        if decision.tool_name == "submit_verdict":
            verdict = decision.verdict
            valid_records = [session.evidence.get(item) for item in decision.evidence]
            supported = bool(valid_records) and all(
                record is not None and record.result.ok for record in valid_records
            )
            related = any(
                finding.path in record.touched_paths
                for record in valid_records
                if record is not None
            )
            if (
                verdict not in {"justified", "suspicious", "unexplained"}
                or not supported
                or not related
            ):
                trace.write(
                    "investigation_verdict_rejected",
                    path=finding.path,
                    verdict=verdict,
                    evidence=list(decision.evidence),
                )
                return (
                    finding.model_copy(
                        update={
                            "verdict": "unexplained",
                            "explanation": "Model verdict lacked valid path-related evidence.",
                            "evidence": tuple(
                                item
                                for item in decision.evidence
                                if item in session.evidence
                            ),
                        }
                    ),
                    tool_calls,
                    llm_calls,
                )
            trace.write(
                "investigation_verdict",
                path=finding.path,
                verdict=verdict,
                evidence=list(decision.evidence),
            )
            return (
                finding.model_copy(
                    update={
                        "verdict": verdict,
                        "explanation": decision.explanation,
                        "evidence": decision.evidence,
                    }
                ),
                tool_calls,
                llm_calls,
            )
        result = session.invoke(decision.tool_name, decision.arguments)
        tool_calls += 1
        observations.append(
            {
                "tool": decision.tool_name,
                "arguments": decision.arguments,
                "result": result.model_dump(mode="json"),
            }
        )
        trace.write(
            "investigation_tool",
            path=finding.path,
            tool=decision.tool_name,
            evidence_id=result.evidence_id,
            ok=result.ok,
            result=result.model_dump(mode="json"),
        )
    return finding, tool_calls, llm_calls


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        Path(temporary_name).replace(path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def verify_repository(
    repo: Path,
    *,
    report_value: str,
    requested_range: str,
    llm: LLMClient | None = None,
    output_root: Path | None = None,
) -> VerificationRun:
    """Compare an original validated prediction with an implementation diff."""

    started = perf_counter()
    repo = repo.resolve()
    report, report_path = load_report(repo, report_value)
    try:
        base, head, warning = resolve_range(repo, report.commit, requested_range)
        snapshot = parse_diff(repo, base, head)
    except DiffError as error:
        raise VerificationError(str(error)) from error
    verification_id = (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        + f"-{head[:7]}-{uuid.uuid4().hex[:8]}"
    )
    root = output_root or repo / ".ripple" / "verifications"
    trace_path = root / f"{verification_id}.jsonl"
    trace = TraceWriter(trace_path)
    trace.write(
        "verification_started",
        verification_id=verification_id,
        request=report.request,
        requested_range=requested_range,
        config_version=STAGE_B_CONFIG_VERSION,
    )
    trace.write(
        "report_loaded",
        report_id=report.report_id,
        report_path=str(report_path),
        report_commit=report.commit,
    )
    trace.write("range_resolved", base=base, head=head, warning=warning)
    trace.write(
        "diff_parsed",
        files=len(snapshot.files),
        renames=[
            {"old": item.change.old_path, "new": item.change.new_path}
            for item in snapshot.files
            if item.change.status == "R"
        ],
    )
    trace.write(
        "renames_normalized",
        renames=[
            {"old": item.change.old_path, "new": item.change.new_path}
            for item in snapshot.files
            if item.change.status == "R"
        ],
    )
    trace.write(
        "symbol_mapping",
        files=[
            {
                "path": item.change.path,
                "changed_symbols": list(item.change.changed_symbols),
                "cosmetic_only": item.change.cosmetic_only,
                "parse_error": item.change.parse_error,
            }
            for item in snapshot.files
        ],
    )

    with tempfile.TemporaryDirectory(prefix="ripple-stage-b-") as temporary:
        temp = Path(temporary)
        base_index = _checkout(repo, base, temp / "base")
        head_index = _checkout(repo, head, temp / "head")
        predictions = _predictions(report, base_index)
        ignored = tuple(
            item.change for item in snapshot.files if _ignored(item.change.path)
        )
        judged = tuple(
            item for item in snapshot.files if not _ignored(item.change.path)
        )
        findings: list[DiffFinding] = []
        predicted_matches: dict[str, ParsedFileDiff] = {}
        for prediction in predictions:
            match = _prediction_match(prediction, snapshot)
            if match is not None:
                predicted_matches[prediction.path] = match

        for item in judged:
            matching = next(
                (
                    prediction
                    for prediction in predictions
                    if _prediction_match(prediction, snapshot) is item
                ),
                None,
            )
            if matching:
                findings.append(
                    DiffFinding(
                        path=item.change.path,
                        category="expected",
                        verdict="n/a",
                        explanation=f"Changed file matched original prediction {matching.target}.",
                        evidence=(
                            f"prediction:{matching.target}",
                            f"diff:{item.change.status}",
                        ),
                    )
                )
                continue
            old_path = item.change.old_path or item.change.path
            relationship = _graph_relationship(base_index, old_path, predictions)
            relationship = relationship or _graph_relationship(
                head_index, item.change.path, predictions
            )
            relationship = relationship or _cochange_relationship(
                base_index, old_path, predictions
            )
            if relationship:
                findings.append(
                    DiffFinding(
                        path=item.change.path,
                        category="adjacent",
                        verdict="justified",
                        explanation="Unpredicted change is adjacent to an original prediction.",
                        evidence=(relationship,),
                    )
                )
            else:
                findings.append(
                    DiffFinding(
                        path=item.change.path,
                        category="unexpected",
                        verdict="unexplained",
                        explanation=(
                            "No one-hop import or top-three co-change relationship "
                            "to an original prediction was found."
                        ),
                        evidence=(f"diff:{item.change.status}",),
                    )
                )

        for prediction in predictions:
            if prediction.path in predicted_matches or prediction.confidence == "low":
                continue
            findings.append(
                DiffFinding(
                    path=prediction.path,
                    category="missing_predicted",
                    verdict="n/a",
                    explanation=(
                        f"Original {prediction.confidence}-confidence prediction did not change."
                    ),
                    evidence=(f"prediction:{prediction.target}",),
                )
            )

        missing_tests = _missing_tests(snapshot, base_index, head_index)
        stale_callers = _stale_callers(snapshot, base_index)
        findings.extend(missing_tests)
        findings.extend(stale_callers)
        trace.write(
            "deterministic_classification",
            findings=[item.model_dump(mode="json") for item in findings],
        )
        trace.write("missing_test_checks", findings=len(missing_tests))
        trace.write("stale_caller_checks", findings=len(stale_callers))

        investigated = 0
        tool_calls = 0
        llm_calls = 0
        if llm is not None:
            revised: list[DiffFinding] = []
            changes = {item.change.path: item.change for item in snapshot.files}
            confidence = {item.path: item.confidence for item in predictions}
            for finding in findings:
                should_investigate = (
                    finding.category == "unexpected"
                    and not changes.get(
                        finding.path, FileChange(path=finding.path, status="M")
                    ).cosmetic_only
                ) or (
                    finding.category == "missing_predicted"
                    and confidence.get(finding.path) == "high"
                )
                if not should_investigate:
                    revised.append(finding)
                    continue
                investigated += 1
                investigation_index = (
                    head_index if finding.category == "unexpected" else base_index
                )
                updated, used_tools, used_llm = _investigate(
                    finding,
                    report.request,
                    llm,
                    VerificationToolSession(investigation_index, snapshot),
                    trace,
                )
                tool_calls += used_tools
                llm_calls += used_llm
                revised.append(updated)
            findings = revised

        relevant_changed = {
            item.change.path
            for item in judged
            if item.change.path.endswith(".py")
            and not is_test_file(Path(item.change.path))
        }
        predicted_paths = {item.path for item in predictions}
        matched_prediction_paths = {
            prediction.path
            for prediction in predictions
            if (match := _prediction_match(prediction, snapshot)) is not None
            and match.change.path in relevant_changed
        }
        matched_actual_paths = {
            _prediction_match(prediction, snapshot).change.path
            for prediction in predictions
            if _prediction_match(prediction, snapshot) is not None
            and _prediction_match(prediction, snapshot).change.path in relevant_changed
        }
        precision = (
            len(matched_prediction_paths) / len(predicted_paths)
            if predicted_paths
            else 0.0
        )
        recall = (
            len(matched_actual_paths) / len(relevant_changed)
            if relevant_changed
            else 0.0
        )
        stats = VerificationStats(
            changed_files=len(judged),
            ignored_files=len(ignored),
            findings=len(findings),
            investigated_files=investigated,
            tool_calls=tool_calls,
            llm_calls=llm_calls,
            runtime_seconds=perf_counter() - started,
        )
        analysis = DiffAnalysis(
            verification_id=verification_id,
            report_id=report.report_id,
            request=report.request,
            requested_range=requested_range,
            base=base,
            head=head,
            base_warning=warning,
            status="completed",
            changes=tuple(item.change for item in judged),
            findings=tuple(findings),
            ignored_files=ignored,
            file_precision=precision,
            file_recall=recall,
            trace_path=trace_path,
            run_statistics=stats,
        )

    json_path = root / f"{verification_id}.json"
    markdown_path = root / f"{verification_id}.md"
    _write_atomic(json_path, analysis.model_dump_json(indent=2) + "\n")
    _write_atomic(markdown_path, render_verification_markdown(analysis))
    trace.write(
        "verification_finished",
        status=analysis.status,
        file_precision=analysis.file_precision,
        file_recall=analysis.file_recall,
        statistics=analysis.run_statistics.model_dump(mode="json"),
    )
    return VerificationRun(
        analysis=analysis,
        json_path=json_path,
        markdown_path=markdown_path,
        trace_path=trace_path,
    )
