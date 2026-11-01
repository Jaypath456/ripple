"""P0 CLI repairs use scripted providers and preserve default wiring."""

from pathlib import Path
from types import SimpleNamespace

from test_phase6_verification import _git, _init, _report

from ripple.cli import main
from ripple.llm import OpenAILLM, ScriptedLLM


def test_evaluate_agent_uses_supported_arguments(monkeypatch, capsys):
    monkeypatch.setattr(OpenAILLM, "from_env", lambda: ScriptedLLM())
    calls = []

    def evaluate(*args, **kwargs):
        calls.append(kwargs)
        assert "force" not in kwargs
        return SimpleNamespace(
            valid_tasks=1, total_tasks=1, status_counts={}, aggregates=[]
        )

    monkeypatch.setattr("ripple.cli.evaluate_agent_manifest", evaluate)
    assert main(["evaluate-agent", "--task-limit", "1"]) == 0
    assert len(calls) == 1 and calls[0]["task_limit"] == 1
    assert "Tasks: 1/1" in capsys.readouterr().out


def test_verify_offline_never_constructs_configured_provider(
    tmp_path: Path, monkeypatch, capsys
):
    _init(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD")
    report = _report(tmp_path, base)
    monkeypatch.setenv("RIPPLE_LLM_API_KEY", "fixture-not-real")
    monkeypatch.setenv("RIPPLE_LLM_MODEL", "fixture-model")

    def forbidden():
        raise AssertionError("offline verification constructed a live provider")

    monkeypatch.setattr(OpenAILLM, "from_env", forbidden)
    assert (
        main(
            [
                "verify",
                str(tmp_path),
                "--report",
                str(report),
                "--range",
                f"{base}..{base}",
                "--offline",
            ]
        )
        == 0
    )
    assert "Status:" in capsys.readouterr().out


def test_verify_default_preserves_configured_provider_wiring(
    tmp_path: Path, monkeypatch
):
    provider = ScriptedLLM()
    constructed = []
    supplied = []
    monkeypatch.setenv("RIPPLE_LLM_API_KEY", "fixture-not-real")
    monkeypatch.setenv("RIPPLE_LLM_MODEL", "fixture-model")

    def configured():
        constructed.append(True)
        return provider

    def verify(*args, **kwargs):
        supplied.append(kwargs["llm"])
        return SimpleNamespace(
            analysis=SimpleNamespace(
                base_warning=None,
                status="completed",
                changes=[],
                findings=[],
                file_precision=0.0,
                file_recall=0.0,
            ),
            json_path=tmp_path / "report.json",
            markdown_path=tmp_path / "report.md",
            trace_path=tmp_path / "trace.jsonl",
        )

    monkeypatch.setattr(OpenAILLM, "from_env", configured)
    monkeypatch.setattr("ripple.cli.verify_repository", verify)
    assert (
        main(["verify", str(tmp_path), "--report", "latest", "--range", "HEAD..HEAD"])
        == 0
    )
    assert constructed == [True] and supplied == [provider]
