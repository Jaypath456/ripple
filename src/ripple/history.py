"""Leak-safe deterministic Git co-change analysis."""

import subprocess
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict

MAX_HISTORY_COMMITS = 500


class CoChangePartner(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    count: int
    support_ratio: float


class CoChangeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    commits_with_path: int
    examined_commits: int
    partners: tuple[CoChangePartner, ...]
    truncated: bool = False


class HistoryError(ValueError):
    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def co_changed(
    repo: Path,
    path: str,
    *,
    limit: int = 10,
    max_commits: int = MAX_HISTORY_COMMITS,
) -> CoChangeResult:
    tracked = _git(repo, "ls-files", "--error-unmatch", "--", path)
    if tracked.returncode:
        raise HistoryError("not_found", f"tracked path not found: {path}")
    history = _git(
        repo,
        "log",
        f"-n{max_commits}",
        "--root",
        "--format=%x1e%H",
        "--name-only",
        "--no-renames",
        "HEAD",
    )
    if history.returncode:
        raise HistoryError("no_history", "Git history is unavailable")
    commits: list[set[str]] = []
    for block in history.stdout.split("\x1e"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) > 1:
            commits.append(set(lines[1:]))
    containing = [files for files in commits if path in files]
    if not containing:
        raise HistoryError("no_history", f"no reachable history contains: {path}")
    counts: Counter[str] = Counter(
        partner for files in containing for partner in files if partner != path
    )
    if not counts:
        raise HistoryError("no_history", f"no co-change partners found for: {path}")
    all_partners = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ordered = all_partners[:limit]
    return CoChangeResult(
        path=path,
        commits_with_path=len(containing),
        examined_commits=len(commits),
        partners=tuple(
            CoChangePartner(
                path=partner,
                count=count,
                support_ratio=count / len(containing),
            )
            for partner, count in ordered
        ),
        truncated=len(all_partners) > limit,
    )
