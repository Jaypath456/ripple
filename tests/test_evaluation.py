import json
import subprocess
from inspect import signature
from pathlib import Path

import pytest
from pydantic import ValidationError

from ripple import evaluation
from ripple.agent_models import AGENT_CONFIG_VERSION, FeatureRequest
from ripple.baselines import RankedFile, bm25_baseline, structural_baseline
from ripple.cli import main
from ripple.evaluation import (
    EVALUATION_REQUEST_LIMIT,
    BaselineTaskResult,
    EvaluationError,
    EvaluationManifest,
    EvaluationTask,
    GoldFileGroups,
    build_fea_bench_task,
    check_repository_leaks,
    classify_gold_files,
    cluster_bootstrap,
    load_tasks,
    prepare_evaluation_request,
    sanitize_request,
    score_ranking,
)
from ripple.evaluation import test_set_metrics as score_tests
from ripple.scanner import scan_repository


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write(repo: Path, relative_path: str, content: str) -> None:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def commit_repository(repo: Path) -> str:
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "RIPPLE Tests")
    git(repo, "config", "user.email", "ripple@example.test")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "fixture")
    return git(repo, "rev-parse", "HEAD")


def task(
    *,
    task_id: str = "owner__repo-1",
    repository: str = "owner/repo",
    base: str = "0" * 40,
    head: str = "1" * 40,
    request: str = "Add authentication support",
    paths: tuple[str, ...] = ("src/auth.py", "src/service.py", "tests/test_auth.py"),
) -> EvaluationTask:
    groups = classify_gold_files(paths)
    return EvaluationTask(
        id=task_id,
        repository=repository,
        repository_url=f"https://github.com/{repository}.git",
        pull_number=1,
        base_commit=base,
        head_commit=head,
        original_request=request,
        masked_request=sanitize_request(request),
        gold_changed_files=paths,
        gold_files=groups,
        benchmark_source="https://huggingface.co/datasets/microsoft/FEA-Bench",
        selection_bucket="2-4",
    )


def test_task_loader_validates_fields_and_fixed_manifest(tmp_path: Path) -> None:
    payload = EvaluationManifest(
        benchmark_source="official",
        selection_method="fixed",
        tasks=(task(),),
    ).model_dump(mode="json")
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_tasks(path).tasks[0].id == "owner__repo-1"
    with pytest.raises(EvaluationError, match="exactly 10"):
        load_tasks(path, expected_count=10)

    del payload["tasks"][0]["base_commit"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvaluationError, match="invalid task manifest"):
        load_tasks(path)


def test_checked_in_manifest_is_stable_and_has_ten_tasks() -> None:
    manifest = load_tasks("evaluation/data/dev_tasks.json", expected_count=10)
    assert len({item.id for item in manifest.tasks}) == 10
    assert len({item.repository for item in manifest.tasks}) == 10
    assert all(item.benchmark_split == "lite" for item in manifest.tasks)


def test_evaluation_request_preparation_is_deterministic_prefix_only() -> None:
    short = "Add deterministic authentication support"
    assert prepare_evaluation_request(short).model_dump() == {
        "text": short,
        "original_length": len(short),
        "used_length": len(short),
        "truncated": False,
    }

    long = "alpha beta " * 200 + "discarded"
    first = prepare_evaluation_request(long)
    second = prepare_evaluation_request(long)
    assert first == second
    assert first.text == long[:EVALUATION_REQUEST_LIMIT]
    assert first.original_length == len(long)
    assert first.used_length == EVALUATION_REQUEST_LIMIT
    assert first.truncated is True
    assert FeatureRequest(text=first.text).text == first.text


def test_mvp_v1_1_prepares_all_twenty_requests_without_gold_inputs() -> None:
    manifest = load_tasks("evaluation/data/mvp_tasks.json", expected_count=20)
    prepared = {
        task.id: prepare_evaluation_request(task.masked_request)
        for task in manifest.tasks
    }
    assert AGENT_CONFIG_VERSION == "mvp-v1.1"
    assert all(
        item.used_length <= EVALUATION_REQUEST_LIMIT for item in prepared.values()
    )
    assert {task_id for task_id, item in prepared.items() if item.truncated} == {
        "aws-powertools__powertools-lambda-python-5588",
        "aws__sagemaker-python-sdk-3432",
        "embeddings-benchmark__mteb-1256",
    }


def test_task_rejects_inconsistent_mask_and_gold_groups() -> None:
    values = task().model_dump()
    values["masked_request"] = "wrong"
    with pytest.raises(ValidationError, match="masked_request"):
        EvaluationTask.model_validate(values)

    values = task().model_dump()
    values["gold_files"] = GoldFileGroups(
        source_python=(), test_python=(), other_implementation=(), ignored=()
    )
    with pytest.raises(ValidationError, match="gold_files"):
        EvaluationTask.model_validate(values)


def test_fea_bench_ingestion_uses_request_and_official_diff() -> None:
    record = {
        "instance_id": "owner__repo-7",
        "repo": "owner/repo",
        "pull_number": 7,
        "base_commit": "a" * 40,
    }
    pull = {
        "number": 7,
        "title": "Add service",
        "body": "Use src/auth.py",
        "head": {"sha": "b" * 40},
        "base": {"sha": "a" * 40},
        "merged": True,
    }
    diff = (
        "diff --git a/src/auth.py b/src/auth.py\n"
        "diff --git a/src/service.py b/src/service.py\n"
        "diff --git a/tests/test_auth.py b/tests/test_auth.py"
    )

    ingested = build_fea_bench_task(record, pull, diff, benchmark_source="official")

    assert ingested.original_request == "Add service\n\nUse src/auth.py"
    assert ingested.masked_request == "Add service\n\nUse [PATH]"
    assert ingested.gold_files.source_python == (
        Path("src/auth.py"),
        Path("src/service.py"),
    )


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("Change app/auth/service.py now", "Change [PATH] now"),
        ("Change app.auth.service now", "Change [PATH] now"),
        (
            "Keep ordinary prose about versions 1.2",
            "Keep ordinary prose about versions 1.2",
        ),
        (
            "See https://github.com/owner/repo/issues/1",
            "See https://github.com/owner/repo/issues/1",
        ),
        ("Use a.py, then a.py", "Use [PATH], then [PATH]"),
    ],
)
def test_request_sanitizer(request_text: str, expected: str) -> None:
    assert sanitize_request(request_text) == expected


def test_gold_file_classification() -> None:
    groups = classify_gold_files(
        (
            "src/app.py",
            "tests/test_app.py",
            "config/settings.yaml",
            "docs/guide.py",
            "CHANGELOG.md",
            "uv.lock",
            "generated_pb2.py",
        )
    )

    assert groups.source_python == (Path("src/app.py"),)
    assert groups.test_python == (Path("tests/test_app.py"),)
    assert groups.other_implementation == (Path("config/settings.yaml"),)
    assert set(groups.ignored) == {
        Path("docs/guide.py"),
        Path("CHANGELOG.md"),
        Path("uv.lock"),
        Path("generated_pb2.py"),
    }


def test_metrics_cover_perfect_partial_empty_and_ranked_cases() -> None:
    perfect = score_ranking(("a.py", "b.py"), ("a.py", "b.py"), prediction_k=2)
    assert (perfect.precision, perfect.recall, perfect.f1) == (1.0, 1.0, 1.0)
    assert perfect.mrr == 1.0
    assert perfect.false_positives == 0

    partial = score_ranking(
        ("x.py", "a.py", "z.py", "q.py", "b.py", "c.py"),
        ("a.py", "b.py"),
        prediction_k=3,
    )
    assert partial.precision == pytest.approx(1 / 3)
    assert partial.recall == 0.5
    assert partial.f1 == pytest.approx(0.4)
    assert partial.recall_at_5 == 1.0
    assert partial.recall_at_10 == 1.0
    assert partial.mrr == 0.5
    assert partial.false_positives == 2

    empty = score_ranking((), ("a.py",))
    assert (empty.precision, empty.recall, empty.f1, empty.mrr) == (0, 0, 0, 0)
    no_gold = score_ranking(("a.py",), ())
    assert (no_gold.precision, no_gold.recall, no_gold.f1) == (0, 0, 0)


def test_recall_at_ten_and_mrr_no_hit() -> None:
    ranking = tuple(f"{index}.py" for index in range(12))
    at_ten = score_ranking(ranking, ("8.py",), prediction_k=5)
    assert at_ten.recall_at_5 == 0
    assert at_ten.recall_at_10 == 1
    assert at_ten.mrr == pytest.approx(1 / 9)
    assert score_ranking(ranking, ("missing.py",)).mrr == 0


def test_test_metrics_are_separate() -> None:
    precision, recall = score_tests(
        ("tests/test_a.py", "tests/test_extra.py"),
        ("tests/test_a.py", "tests/test_b.py"),
        cutoff=5,
    )
    assert precision == recall == 0.5


def repository_index(tmp_path: Path):
    write(tmp_path, "src/auth.py", "def token_refresh():\n    return 'token'\n")
    write(
        tmp_path,
        "src/service.py",
        "from auth import token_refresh\n\ndef refresh_service():\n    return token_refresh()\n",
    )
    write(
        tmp_path,
        "src/api.py",
        "from service import refresh_service\n\ndef endpoint():\n    return refresh_service()\n",
    )
    write(tmp_path, "src/unrelated.py", "def other():\n    return None\n")
    write(
        tmp_path,
        "tests/test_service.py",
        "from service import refresh_service\n\ndef test_refresh_service():\n    assert refresh_service()\n",
    )
    commit_repository(tmp_path)
    return scan_repository(tmp_path)


def test_b0_ranks_keywords_deduplicates_files_and_excludes_tests(
    tmp_path: Path,
) -> None:
    assert set(signature(bm25_baseline).parameters) == {"index", "request"}
    index = repository_index(tmp_path)
    result = bm25_baseline(index, "token refresh authentication")

    paths = [item.path for item in result.source_predictions]
    assert paths[0] == Path("src/auth.py")
    assert len(paths) == len(set(paths))
    assert Path("tests/test_service.py") not in paths
    assert len(result.source_predictions[0].reasons) >= 1
    assert result.test_predictions == ()


def test_b1_adds_importers_importees_and_keeps_tests_separate(tmp_path: Path) -> None:
    assert "gold" not in signature(structural_baseline).parameters
    index = repository_index(tmp_path)
    first = structural_baseline(index, "refresh service", seed_count=1)
    second = structural_baseline(index, "refresh service", seed_count=1)

    assert first == second
    by_path = {item.path: item for item in first.source_predictions}
    assert any(
        reason.startswith("one_hop:") for reason in by_path[Path("src/auth.py")].reasons
    )
    assert any(
        reason.startswith("one_hop:") for reason in by_path[Path("src/api.py")].reasons
    )
    assert [item.path for item in first.test_predictions] == [
        Path("tests/test_service.py")
    ]
    assert all("test" not in item.path.parts for item in first.source_predictions)


def result_fixture(repository: str, value: float, task_id: str) -> BaselineTaskResult:
    metrics = evaluation.SetMetrics(
        precision=value,
        recall=value,
        f1=value,
        recall_at_5=value,
        recall_at_10=value,
        mrr=value,
        false_positives=int(1 - value),
    )
    return BaselineTaskResult(
        task_id=task_id,
        repository=repository,
        base_commit="a" * 40,
        masked_request="request",
        prediction=evaluation.BaselinePrediction(
            baseline="B0",
            source_predictions=(
                RankedFile(path="a.py", score=1, reasons=("fixture",)),
            ),
        ),
        gold_files=classify_gold_files(("a.py", "b.py")),
        metrics=metrics,
        test_precision=0,
        test_recall=0,
        runtime_seconds=value,
        leak_checks=("fixture",),
    )


def test_cluster_bootstrap_is_deterministic_and_contains_fixture_mean() -> None:
    items = (
        result_fixture("a/repo", 0.0, "a1"),
        result_fixture("a/repo", 0.0, "a2"),
        result_fixture("b/repo", 1.0, "b1"),
    )
    first = cluster_bootstrap(items, samples=200, seed=7)
    second = cluster_bootstrap(items, samples=200, seed=7)

    assert first == second
    interval = first["mean_f1"]
    assert interval.low <= 1 / 3 <= interval.high
    assert interval.low == 0
    assert interval.high == 1


def test_leak_check_passes_clean_base_and_rejects_wrong_head(tmp_path: Path) -> None:
    write(tmp_path, "src/auth.py", "pass\n")
    write(tmp_path, "src/service.py", "pass\n")
    base = commit_repository(tmp_path)
    clean_task = task(base=base, paths=("src/auth.py", "src/service.py"))

    assert "head_is_exact_base" in check_repository_leaks(tmp_path, clean_task)
    with pytest.raises(EvaluationError, match="wrong HEAD"):
        check_repository_leaks(tmp_path, task(base="f" * 40))


def test_leak_check_rejects_future_ref(tmp_path: Path) -> None:
    write(tmp_path, "src/auth.py", "pass\n")
    write(tmp_path, "src/service.py", "pass\n")
    base = commit_repository(tmp_path)
    write(tmp_path, "src/future.py", "GOLD_ONLY = True\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "future")
    head = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "branch", "future", head)
    git(tmp_path, "checkout", "-q", "--detach", base)

    contaminated = task(base=base, head=head, paths=("src/auth.py", "src/service.py"))
    with pytest.raises(EvaluationError, match="reachable through refs"):
        check_repository_leaks(tmp_path, contaminated)


def test_leak_check_rejects_visible_gold_only_file(tmp_path: Path) -> None:
    write(tmp_path, "src/auth.py", "pass\n")
    write(tmp_path, "src/service.py", "pass\n")
    base = commit_repository(tmp_path)
    write(tmp_path, "src/new_gold.py", "pass\n")
    leaked = task(base=base, paths=("src/auth.py", "src/new_gold.py"))

    with pytest.raises(EvaluationError, match="not clean|gold-only"):
        check_repository_leaks(tmp_path, leaked)


def test_static_scan_never_executes_target_source(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    write(
        tmp_path,
        "src/auth.py",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
    )
    write(tmp_path, "src/service.py", "pass\n")
    commit_repository(tmp_path)

    scan_repository(tmp_path)

    assert not marker.exists()


def test_cli_writes_results_and_prints_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    index = repository_index(repo)
    tasks = tuple(
        task(
            task_id=f"owner{i}__repo-{i}",
            repository=f"owner{i}/repo",
            base=index.commit,
            request="token refresh authentication",
        )
        for i in range(10)
    )
    manifest_path = tmp_path / "tasks.json"
    manifest_path.write_text(
        EvaluationManifest(
            benchmark_source="fixture", selection_method="fixed", tasks=tasks
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        evaluation,
        "prepare_repository",
        lambda _task, _workspace: (repo, ("fixture_leak_check",)),
    )
    output = tmp_path / "results.json"

    exit_code = main(
        [
            "evaluate",
            "--tasks",
            str(manifest_path),
            "--workspace",
            str(tmp_path / "work"),
            "--output",
            str(output),
            "--bootstrap-samples",
            "10",
        ]
    )

    assert exit_code == 0
    assert json.loads(output.read_text())["valid_tasks"] == 10
    stdout = capsys.readouterr().out
    assert "Baseline" in stdout
    assert "B0" in stdout and "B1" in stdout


def test_cli_handles_invalid_task_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{}", encoding="utf-8")

    assert main(["evaluate", "--tasks", str(path)]) == 1
    assert "invalid task manifest" in capsys.readouterr().err
