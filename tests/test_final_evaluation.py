import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ripple.evaluation import (
    EvaluationError,
    EvaluationManifest,
    EvaluationTask,
    classify_gold_files,
    prepare_evaluation_request,
    sanitize_request,
)
from ripple.evaluation_results import (
    _score_runs,
    adjudicated_precision,
    adjudicated_task_precision,
    average_seeds,
    build_adjudication_template,
    build_results,
    cluster_bootstrap_values,
    paired_cluster_bootstrap,
    select_adjudication_tasks,
    stage_b_metrics,
)
from ripple.final_data import select_final_tasks
from ripple.final_evaluation import (
    RawRun,
    _write_raw,
    build_schedule,
    classify_failure,
    freeze_config,
)
from ripple.llm import LLMError


def _task(identifier: str, repository: str, count: int) -> EvaluationTask:
    paths = tuple(Path(f"src/file_{position}.py") for position in range(count))
    request = f"Implement {identifier} using package.module.path and src/old.py"
    return EvaluationTask(
        id=identifier,
        repository=repository,
        repository_url=f"https://github.com/{repository}.git",
        pull_number=1,
        base_commit="a" * 40,
        head_commit="b" * 40,
        original_request=request,
        masked_request=sanitize_request(request),
        gold_changed_files=paths,
        gold_files=classify_gold_files(paths),
        benchmark_source="fixture",
        selection_bucket="2-4" if count <= 4 else "5-9" if count <= 9 else "10-20",
    )


def _raw(
    task: EvaluationTask, system: str, seed: int | None, paths: tuple[str, ...]
) -> RawRun:
    return RawRun(
        config_version="final-v1",
        config_hash="hash",
        run_id=f"{task.id}-{system}-{seed}",
        task_id=task.id,
        repository=task.repository,
        system=system,
        requested_seed=seed,
        status="completed",
        prepared_request_sha256="0" * 64,
        request_original_length=5,
        request_used_length=5,
        request_truncated=False,
        source_ranking=tuple(Path(path) for path in paths),
        completed_at=datetime.now(UTC),
    )


def test_final_selection_excludes_development_stratifies_and_caps_repositories() -> (
    None
):
    candidates = tuple(
        _task(f"task-{index}", f"owner/repo-{index % 3}", (2, 6, 11)[index % 3])
        for index in range(18)
    )
    selected = select_final_tasks(candidates, development_ids={"task-0"}, target=12)
    assert "task-0" not in {item.id for item in selected}
    assert (
        max(
            sum(item.repository == repository for item in selected)
            for repository in {item.repository for item in selected}
        )
        <= 5
    )
    assert {item.selection_bucket for item in selected} == {"2-4", "5-9", "10-20"}


def test_request_masking_limit_and_gold_split() -> None:
    masked = sanitize_request("Change src/api.py and package.api.routes")
    assert masked.count("[PATH]") == 2
    prepared = prepare_evaluation_request(masked + "x" * 3000)
    assert prepared.used_length == 2000 and prepared.truncated
    groups = classify_gold_files(
        ["src/a.py", "tests/test_a.py", "config.toml", "docs/readme.md"]
    )
    assert groups.source_python == (Path("src/a.py"),)
    assert groups.test_python == (Path("tests/test_a.py"),)
    assert groups.other_implementation == (Path("config.toml"),)
    assert groups.ignored == (Path("docs/readme.md"),)


def test_schedule_is_deterministic_interleaved_and_has_three_seeds() -> None:
    first = build_schedule(("b", "a"))
    assert first == build_schedule(("a", "b"))
    assert len(first) == 2 * (3 + 6 * 3)
    assert {item.requested_seed for item in first if item.requested_seed} == {
        17,
        42,
        1729,
    }
    assert [item.system for item in first[:3]] == ["B0", "B1", "B2"]


def test_seed_average_precedes_cluster_bootstrap_and_is_deterministic() -> None:
    rows = [
        {"task_id": "t1", "system": "RIPPLE", "repository": "r1", "f1": value}
        for value in (0.0, 0.0, 1.0)
    ] + [
        {"task_id": "t2", "system": "RIPPLE", "repository": "r2", "f1": value}
        for value in (1.0, 1.0, 1.0)
    ]
    averaged = average_seeds(rows)
    assert len(averaged) == 2
    first = cluster_bootstrap_values(averaged, lambda item: item["f1"], samples=50)
    assert first == cluster_bootstrap_values(
        averaged, lambda item: item["f1"], samples=50
    )


def test_paired_cluster_bootstrap_is_paired() -> None:
    rows = [
        {"task_id": task, "repository": repo, "system": system, "f1": value}
        for task, repo, left, right in (("a", "r1", 0.8, 0.2), ("b", "r2", 0.6, 0.5))
        for system, value in (("RIPPLE", left), ("B0", right))
    ]
    result = paired_cluster_bootstrap(rows, "RIPPLE", "B0", samples=100)
    assert result["task_count"] == 2
    assert result["mean_difference"] == pytest.approx(0.35)


def test_matched_k_including_zero() -> None:
    task = _task("task", "owner/repo", 2)
    runs = [
        _raw(task, "B0", None, ("src/file_0.py", "src/file_1.py")),
        _raw(task, "RIPPLE", 17, ()),
    ]
    rows = _score_runs(runs, {task.id: task}, {})
    baseline = next(item for item in rows if item["system"] == "B0")
    assert baseline["precision"] == baseline["recall"] == 0.0
    assert baseline["false_positives"] == 0


def test_failure_classification_and_checkpoint_protection(tmp_path: Path) -> None:
    assert classify_failure(LLMError("HTTP 503 high demand")) == "server_error"
    assert classify_failure(LLMError("HTTP 401 auth_error")) == "authentication_failure"
    task = _task("task", "owner/repo", 2)
    run = _raw(task, "RIPPLE", 17, ())
    path = tmp_path / "run.json"
    _write_raw(path, run, force=False)
    with pytest.raises(EvaluationError, match="refusing to replace"):
        _write_raw(path, run, force=False)
    assert RawRun.model_validate_json(path.read_text()).run_id == run.run_id


def test_status_denominator_stage_b_and_adjudication() -> None:
    statuses = ["completed", "failed", "provider_failed"]
    assert sum(dict(__import__("collections").Counter(statuses)).values()) == 3
    metrics = stage_b_metrics(
        [
            {"variant": "unrelated", "detected_categories": ["unexpected"]},
            {"variant": "drop_tests", "detected_categories": []},
            {"variant": "stale_caller", "applicable": False},
            {"variant": "control", "false_alarm": True},
        ]
    )
    assert metrics["unrelated_detection_recall"] == 1.0
    assert metrics["drop_tests_detection_recall"] == 0.0
    assert metrics["stale_caller_detection_recall"] is None
    assert metrics["stale_caller_inapplicable"] == 1
    assert metrics["control_false_alarm_rate"] == 1.0
    assert adjudicated_precision(2, ["plausible_alternative", "wrong"]) == 0.75
    assert adjudicated_precision(2, [""]) is None


def test_adjudication_selection_is_predeclared_and_stratified() -> None:
    tasks = tuple(
        _task(f"task-{index}", f"owner/repo-{index}", (2, 6, 11)[index % 3])
        for index in range(18)
    )
    selected = select_adjudication_tasks(tasks)
    assert len(selected) == 15
    assert selected == select_adjudication_tasks(tuple(reversed(tasks)))


def test_results_regeneration_is_byte_deterministic(tmp_path: Path) -> None:
    task = _task("task", "owner/repo", 2)
    manifest = EvaluationManifest(
        benchmark_source="fixture", selection_method="fixture", tasks=(task,)
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2))
    config_path = tmp_path / "config.json"
    freeze_config(config_path, model="test-model", base_url="https://example.test/v1")
    raw = tmp_path / "raw"
    raw.mkdir()
    for run in (
        _raw(task, "B0", None, ("src/file_0.py",)),
        _raw(task, "RIPPLE", 17, ("src/file_0.py",)),
    ):
        payload = run.model_copy(
            update={"config_hash": json.loads(config_path.read_text())["config_hash"]}
        )
        (raw / f"{payload.run_id}.json").write_text(payload.model_dump_json(indent=2))
    readme = tmp_path / "README.md"
    summary = tmp_path / "summary.json"
    root = tmp_path / "ROOT.md"
    root.write_text(
        "intro\n<!-- final-results:start -->\nstale\n<!-- final-results:end -->\nend\n"
    )
    kwargs = {
        "root_readme": root,
        "manifest_path": manifest_path,
        "raw_root": raw,
        "config_path": config_path,
        "output_readme": readme,
        "output_summary": summary,
    }
    build_results(**kwargs)
    first = (readme.read_bytes(), summary.read_bytes(), root.read_bytes())
    build_results(**kwargs)
    assert first == (readme.read_bytes(), summary.read_bytes(), root.read_bytes())
    text = root.read_text()
    assert "stale" not in text and text.startswith("intro") and text.endswith("end\n")


def test_adjudicated_task_precision_uses_true_positives_and_seed_means(
    tmp_path: Path,
) -> None:
    task = _task("task", "owner/repo", 2)
    gold = {path.as_posix() for path in task.gold_files.source_python}  # file_0, file_1
    runs = [
        _raw(task, "RIPPLE", 17, ("src/file_0.py", "src/file_1.py", "src/extra.py")),
        _raw(task, "RIPPLE", 42, ("src/file_0.py", "src/extra.py")),
    ]
    rows = _score_runs(runs, {task.id: task}, {})
    assert {row["predicted_count"] for row in rows} == {3, 2}
    assert gold == {"src/file_0.py", "src/file_1.py"}
    labels = {(task.id, "src/extra.py"): "plausible_alternative"}
    # Seed 17: 2 TP + 1 plausible FP = 3/3. The old derivation inferred TP=1 here.
    # Seed 42: 1 TP + 1 plausible FP = 2/2. Seed mean is 1.0.
    assert adjudicated_task_precision(rows, labels, {task.id}) == 1.0
    labels[(task.id, "src/extra.py")] = "wrong"
    assert adjudicated_task_precision(rows, labels, {task.id}) == pytest.approx(
        (2 / 3 + 1 / 2) / 2
    )
    assert adjudicated_task_precision(rows, {}, {task.id}) is None

    template = build_adjudication_template(
        (task,), rows, tmp_path / "adjudication.json"
    )
    assert len(template["entries"]) == 1  # one label covers both seeds
    assert template["entries"][0]["seeds"] == [17, 42]
