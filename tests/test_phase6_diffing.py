import subprocess
from pathlib import Path

import pytest

from ripple.diffing import DiffError, is_cosmetic_change, parse_diff, resolve_range
from ripple.scanner import scan_repository
from ripple.verify_tools import VerificationToolSession


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "RIPPLE Tests")
    _git(repo, "config", "user.email", "ripple@example.test")


def test_diff_parser_handles_a_m_d_r_zero_context_and_binary(tmp_path: Path) -> None:
    _init(tmp_path)
    (tmp_path / "modified.py").write_text("def value():\n    return 1\n")
    (tmp_path / "deleted.py").write_text("def gone():\n    return True\n")
    (tmp_path / "old.py").write_text(
        "def renamed():\n    value = 1\n    return value\n"
    )
    (tmp_path / "asset.bin").write_bytes(b"\x00\x01\x02")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")

    (tmp_path / "modified.py").write_text("def value():\n    return 2\n")
    (tmp_path / "deleted.py").unlink()
    (tmp_path / "old.py").rename(tmp_path / "new.py")
    (tmp_path / "added.py").write_text("def added():\n    return 1\n")
    (tmp_path / "asset.bin").write_bytes(b"\x00\xff\x02")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "head")
    head = _git(tmp_path, "rev-parse", "HEAD")

    snapshot = parse_diff(tmp_path, base, head)
    statuses = {item.change.path: item.change.status for item in snapshot.files}
    assert statuses["added.py"] == "A"
    assert statuses["modified.py"] == "M"
    assert statuses["deleted.py"] == "D"
    assert statuses["new.py"] == "R"
    assert snapshot.by_path("added.py").change.changed_symbols == ("added",)
    assert snapshot.by_path("deleted.py").change.changed_symbols == ("gone",)
    renamed = snapshot.by_path("old.py")
    assert renamed.change.new_path == "new.py"
    assert any(
        hunk.old_count == 1 and hunk.new_count == 1
        for hunk in snapshot.by_path("modified.py").hunks
    )
    assert snapshot.by_path("asset.bin").change.binary


def test_changed_symbol_mapping_added_deleted_nested_and_parse_failure(
    tmp_path: Path,
) -> None:
    _init(tmp_path)
    (tmp_path / "module.py").write_text(
        "def removed():\n    return 1\n\n"
        "class Outer:\n    def nested(self):\n        return 1\n"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "module.py").write_text(
        "class Outer:\n    def nested(self):\n        return 2\n\n"
        "def added():\n    return 3\n"
    )
    (tmp_path / "broken.py").write_text("def valid():\n    return 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "symbols")
    mid = _git(tmp_path, "rev-parse", "HEAD")
    changed = parse_diff(tmp_path, base, mid).by_path("module.py").change
    assert set(changed.changed_symbols) >= {"removed", "added", "Outer.nested"}

    (tmp_path / "broken.py").write_text("def broken(:\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "broken")
    head = _git(tmp_path, "rev-parse", "HEAD")
    broken = parse_diff(tmp_path, mid, head).by_path("broken.py").change
    assert broken.parse_error
    assert not broken.cosmetic_only


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("x=1\n", "x = 1\n"),
        ("x = 1\n", "# note\nx = 1\n"),
        (
            'def f():\n    """old"""\n    return 1\n',
            'def f():\n    """new"""\n    return 1\n',
        ),
    ],
)
def test_cosmetic_ast_ignores_whitespace_comments_and_docstrings(
    before: str, after: str
) -> None:
    assert is_cosmetic_change(before, after)


def test_cosmetic_ast_detects_expression_change() -> None:
    assert not is_cosmetic_change("x = 1\n", "x = 2\n")


def test_file_diff_tool_results_and_errors(tmp_path: Path) -> None:
    _init(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "same.py").write_text("x = 1\n")
    (tmp_path / "data.bin").write_bytes(b"\x00\x01")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    index = scan_repository(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "data.bin").write_bytes(b"\x00\xff")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "head")
    head = _git(tmp_path, "rev-parse", "HEAD")
    session = VerificationToolSession(index, parse_diff(tmp_path, base, head))
    assert session.invoke("file_diff", {"path": "a.py"}).ok
    assert session.invoke("file_diff", {"path": "same.py"}).error.code == "not_in_diff"
    assert session.invoke("file_diff", {"path": "missing.py"}).error.code == "not_found"
    assert session.invoke("file_diff", {"path": "data.bin"}).error.code == "binary_file"
    assert (
        session.invoke("file_diff", {"path": "../a.py"}).error.code
        == "invalid_arguments"
    )
    assert (
        session.invoke("file_diff", {"bad": "a.py"}).error.code == "invalid_arguments"
    )


def test_resolve_range_mismatch_uses_report_ancestry_and_rejects_invalid(
    tmp_path: Path,
) -> None:
    _init(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "a.py").write_text("x = 2\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "mid")
    mid = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "a.py").write_text("x = 3\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "head")
    head = _git(tmp_path, "rev-parse", "HEAD")
    effective, resolved_head, warning = resolve_range(tmp_path, base, f"{mid}..{head}")
    assert effective == base and resolved_head == head and warning
    with pytest.raises(DiffError, match="form base..head"):
        resolve_range(tmp_path, base, "HEAD")
