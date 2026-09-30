import subprocess
from pathlib import Path

import pytest

from ripple.scanner import scan_repository


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def indexed_repo(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/auth.py").write_text(
        "def refresh_token(value):\n    return value\n", encoding="utf-8"
    )
    (tmp_path / "src/service.py").write_text(
        "from auth import refresh_token\n\ndef service(value):\n    return refresh_token(value)\n",
        encoding="utf-8",
    )
    (tmp_path / "tests/test_auth.py").write_text(
        "from auth import refresh_token\n\ndef test_refresh():\n    assert refresh_token('x')\n",
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "RIPPLE Tests")
    _git(tmp_path, "config", "user.email", "ripple@example.test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    return scan_repository(tmp_path)
