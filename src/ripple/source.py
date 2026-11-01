"""Bounded reads of untrusted repository files; never follow source symlinks."""

import io
import os
import stat
import tokenize
from pathlib import Path

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_LINE_CHARACTERS = 32_768


class SourceError(ValueError):
    """A file cannot be safely included in static analysis."""


def checked_path(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise SourceError(f"path must stay inside the repository: {relative}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SourceError(f"symlink is not allowed: {relative}")
    return current


def read_source_bytes(root: Path, relative: Path) -> bytes:
    path = checked_path(root, relative)
    # Also prevents a tracked file replaced by a FIFO from hanging.
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise SourceError(f"not a regular file: {relative}")
        if info.st_size > MAX_SOURCE_BYTES:
            raise SourceError(
                f"source exceeds {MAX_SOURCE_BYTES} byte limit: {relative}"
            )
        content = stream.read(MAX_SOURCE_BYTES + 1)
    if len(content) > MAX_SOURCE_BYTES:
        raise SourceError(f"source exceeds {MAX_SOURCE_BYTES} byte limit: {relative}")
    return content


def read_python_source(root: Path, relative: Path) -> str:
    content = read_source_bytes(root, relative)
    # Preserve tokenize.open's diagnostic filename without reopening the path.
    with io.BytesIO(content) as source_file:
        source_file.name = str(root / relative)
        encoding, _ = tokenize.detect_encoding(source_file.readline)
    source = content.decode(encoding)
    if any(len(line) > MAX_LINE_CHARACTERS for line in source.splitlines()):
        raise SourceError(
            f"source line exceeds {MAX_LINE_CHARACTERS} character limit: {relative}"
        )
    return source
