"""P0 verification filesystem and hostile-checkout-hook regressions."""

from pathlib import Path

import pytest
from test_p0_security import commit, init
from test_phase6_verification import _git, _init, _report

from ripple.source import SourceError
from ripple.verification import _checkout, verify_repository


def test_verification_clone_ignores_hostile_checkout_hook(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    init(repo)
    (repo / "api.py").write_text("def visible():\n    return 1\n")
    base = commit(repo)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "post-checkout"
    hook.write_text('#!/bin/sh\nprintf ran > "$P0_HOOK_MARKER"\n')
    hook.chmod(0o700)
    marker = tmp_path / "executed"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hooks))
    monkeypatch.setenv("P0_HOOK_MARKER", str(marker))
    index = _checkout(repo, base, tmp_path / "clone")
    assert index.commit == base
    assert [symbol.name for symbol in index.symbols] == ["visible"]
    assert not marker.exists()


def test_verification_rejects_symlinked_report_directory(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init(repo)
    base = _git(repo, "rev-parse", "HEAD")
    _report(repo, base)
    outside = tmp_path / "outside"
    (repo / ".ripple").rename(outside)
    (repo / ".ripple").symlink_to(outside, target_is_directory=True)
    before = {
        str(p.relative_to(outside)): p.read_bytes()
        for p in outside.rglob("*")
        if p.is_file()
    }
    with pytest.raises(SourceError, match="symlink"):
        verify_repository(
            repo,
            report_value=str(outside / "reports/fixture-report.json"),
            requested_range=f"{base}..{base}",
        )
    after = {
        str(p.relative_to(outside)): p.read_bytes()
        for p in outside.rglob("*")
        if p.is_file()
    }
    assert after == before


def test_verification_rejects_symlinked_output_directory(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init(repo)
    base = _git(repo, "rev-parse", "HEAD")
    report = _report(repo, base)
    outside = tmp_path / "outside"
    outside.mkdir()
    output = repo / "verification-output"
    output.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SourceError, match="symlink"):
        verify_repository(
            repo,
            report_value=str(report),
            requested_range=f"{base}..{base}",
            output_root=output,
        )
    assert not list(outside.iterdir())
