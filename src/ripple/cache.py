"""Small JSON cache for deterministic repository indexes."""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ripple.models import INDEX_SCHEMA_VERSION, RepositoryIndex
from ripple.scanner import (
    ScanError,
    _repository_state,
    _RepositoryState,
    _scan_repository_state,
)

CACHE_DIRECTORY = Path(".ripple/index")


@dataclass(frozen=True)
class ScanResult:
    """An index plus cache information for the current scan invocation."""

    index: RepositoryIndex
    cache_path: Path | None
    cache_hit: bool


def _dirty_digest(state: _RepositoryState) -> str:
    digest = hashlib.sha256()
    for path in state.python_paths:
        encoded_path = path.as_posix().encode("utf-8", errors="surrogateescape")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        try:
            content = (state.repo_root / path).read_bytes()
        except FileNotFoundError:
            digest.update(b"missing")
        except OSError as error:
            digest.update(b"unreadable")
            digest.update(str(error.errno).encode("ascii"))
        else:
            digest.update(b"content")
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    return digest.hexdigest()[:16]


def _cache_path(state: _RepositoryState) -> Path:
    if state.dirty:
        filename = f"{state.commit}-dirty-{_dirty_digest(state)}.json"
    else:
        filename = f"{state.commit}.json"
    return state.repo_root / CACHE_DIRECTORY / filename


def _load_cache(path: Path, state: _RepositoryState) -> RepositoryIndex | None:
    try:
        index = RepositoryIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None

    if (
        index.schema_version != INDEX_SCHEMA_VERSION
        or index.repo_root.resolve() != state.repo_root
        or index.commit != state.commit
        or index.dirty != state.dirty
    ):
        return None
    return index


def _write_cache(path: Path, index: RepositoryIndex) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".tmp",
            text=True,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as cache_file:
            cache_file.write(index.model_dump_json(indent=2))
            cache_file.write("\n")
            cache_file.flush()
            os.fsync(cache_file.fileno())
        os.replace(temporary_path, path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ScanError(f"could not write cache {path}: {error}") from error


def scan_repository_cached(
    repo: str | Path,
    *,
    use_cache: bool = True,
) -> ScanResult:
    """Load or create the index for the current tracked repository state."""

    state = _repository_state(repo)
    if not use_cache:
        return ScanResult(
            index=_scan_repository_state(state),
            cache_path=None,
            cache_hit=False,
        )

    path = _cache_path(state)
    cached_index = _load_cache(path, state)
    if cached_index is not None:
        return ScanResult(index=cached_index, cache_path=path, cache_hit=True)

    index = _scan_repository_state(state)
    _write_cache(path, index)
    return ScanResult(index=index, cache_path=path, cache_hit=False)
