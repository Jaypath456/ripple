"""Direct P0 source-boundary regressions, including stale-index readers."""

import hashlib
import json
import os
import tokenize
from pathlib import Path

import pytest
from test_p0_security import commit, init

from ripple import source
from ripple.cache import scan_repository_cached
from ripple.facts import extract_repository_facts
from ripple.scanner import scan_repository
from ripple.search import SearchIndex
from ripple.source import (
    MAX_LINE_CHARACTERS,
    MAX_SOURCE_BYTES,
    SourceError,
    read_python_source,
    read_source_bytes,
)
from ripple.tools import INSPECT_SOURCE_CHARACTERS, ToolSession


def test_source_byte_limit_accepts_boundary_and_rejects_one_extra(tmp_path: Path):
    assert MAX_SOURCE_BYTES == 2 * 1024 * 1024
    content = b"#\n" * (MAX_SOURCE_BYTES // 2)
    source = tmp_path / "source.py"
    source.write_bytes(content)
    assert read_source_bytes(tmp_path, Path("source.py")) == content
    source.write_bytes(content + b"#")
    with pytest.raises(SourceError, match="byte limit"):
        read_source_bytes(tmp_path, Path("source.py"))


def test_scanner_rejects_source_over_two_mib_with_short_lines(tmp_path: Path):
    init(tmp_path)
    content = b"def visible():\n    return 1\n" + b"#\n" * (MAX_SOURCE_BYTES // 2)
    assert len(content) > 2 * 1024 * 1024
    assert max(map(len, content.splitlines())) < MAX_LINE_CHARACTERS
    (tmp_path / "source.py").write_bytes(content)
    commit(tmp_path)
    index = scan_repository_cached(tmp_path).index
    assert "2097152 byte limit" in index.files[0].parse_error
    assert not index.symbols
    assert not ToolSession(index).invoke("inspect_symbol", {"target": "source.py"}).ok


def test_pinned_sympy_sine_is_indexed_successfully(tmp_path: Path):
    source = os.environ.get("RIPPLE_P0_SYMPY_SINE_SOURCE")
    if not source:
        pytest.skip("Set RIPPLE_P0_SYMPY_SINE_SOURCE to the pinned corpus file")
    data = Path(source).read_bytes()
    assert hashlib.sha256(data).hexdigest() == (
        "843a524e21ba0ceff1a4e4756108f37ad61d0e6bb42c319973dfb913bfd33576"
    )
    assert 1024 * 1024 < len(data) < MAX_SOURCE_BYTES
    init(tmp_path)
    relative = Path("sympy/integrals/rubi/rules/sine.py")
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes(data)
    commit(tmp_path)
    index = scan_repository(tmp_path)
    assert index.files[0].path == relative
    assert index.files[0].parse_error is None
    assert len(index.symbols) == 1
    assert len(index.imports) == 10


def test_cache_rebuilds_when_source_limits_change(tmp_path: Path, monkeypatch):
    init(tmp_path)
    content = b"def visible():\n    return 1\n" + b"#\n" * 600_000
    (tmp_path / "source.py").write_bytes(content)
    commit(tmp_path)
    monkeypatch.setattr(source, "MAX_SOURCE_BYTES", 1024 * 1024)
    old = scan_repository_cached(tmp_path)
    assert old.index.schema_version == 4 and old.index.files[0].parse_error
    monkeypatch.setattr(source, "MAX_SOURCE_BYTES", 2 * 1024 * 1024)
    rebuilt = scan_repository_cached(tmp_path)
    assert not rebuilt.cache_hit
    assert rebuilt.cache_path == old.cache_path
    assert rebuilt.index.files[0].parse_error is None
    assert [symbol.name for symbol in rebuilt.index.symbols] == ["visible"]
    assert scan_repository_cached(tmp_path).cache_hit


def test_schema_four_cache_without_source_policy_is_rebuilt(tmp_path: Path):
    init(tmp_path)
    (tmp_path / "source.py").write_text("def visible():\n    return 1\n")
    commit(tmp_path)
    initial = scan_repository_cached(tmp_path)
    assert initial.cache_path is not None
    payload = json.loads(initial.cache_path.read_text())
    payload.pop("_source_policy")
    assert payload["schema_version"] == 4
    initial.cache_path.write_text(json.dumps(payload))
    rebuilt = scan_repository_cached(tmp_path)
    assert not rebuilt.cache_hit
    assert rebuilt.index.symbols == initial.index.symbols
    assert json.loads(rebuilt.cache_path.read_text())["_source_policy"] == {
        "max_source_bytes": 2097152,
        "max_line_characters": 32768,
    }


def test_line_limit_accepts_boundary_and_rejects_one_extra(tmp_path: Path):
    assert MAX_LINE_CHARACTERS == 32_768
    content = "#" + "x" * (MAX_LINE_CHARACTERS - 1) + "\n"
    source = tmp_path / "source.py"
    source.write_text(content)
    assert read_python_source(tmp_path, Path("source.py")) == content
    source.write_text("#" + content)
    with pytest.raises(SourceError, match="line exceeds"):
        read_python_source(tmp_path, Path("source.py"))


@pytest.mark.parametrize(
    "content",
    [
        b"# coding: nonexistent-codec\nx = 1\n",
        b"\xffinvalid\n",
        b"\xef\xbb\xbf# coding: latin-1\nx = 1\n",
    ],
)
def test_encoding_errors_preserve_frozen_reader_diagnostics(
    tmp_path: Path, content: bytes
):
    path = tmp_path / "source.py"
    path.write_bytes(content)
    with pytest.raises(SyntaxError) as before, tokenize.open(path) as stream:
        stream.read()
    with pytest.raises(SyntaxError) as after:
        read_python_source(tmp_path, Path("source.py"))
    assert str(after.value) == str(before.value)


@pytest.mark.parametrize("line_length", [16_384, 27_514, 32_768])
def test_valid_python_lines_remain_indexable(tmp_path: Path, line_length: int):
    init(tmp_path)
    line = 'VALUE = "' + "x" * (line_length - len('VALUE = ""')) + '"'
    assert len(line) == line_length
    (tmp_path / "source.py").write_text(line + "\ndef visible():\n    return VALUE\n")
    commit(tmp_path)
    index = scan_repository(tmp_path)
    assert index.files[0].parse_error is None
    assert [symbol.name for symbol in index.symbols] == ["visible"]


def test_scanner_safely_rejects_valid_literal_line_over_32768(tmp_path: Path):
    init(tmp_path)
    line_length = 32_769
    line = 'VALUE = "' + "x" * (line_length - len('VALUE = ""')) + '"'
    (tmp_path / "source.py").write_text(line + "\ndef visible():\n    return VALUE\n")
    commit(tmp_path)
    index = scan_repository_cached(tmp_path).index
    assert "line exceeds 32768 character limit" in index.files[0].parse_error
    assert not index.symbols
    assert not ToolSession(index).invoke("inspect_symbol", {"target": "source.py"}).ok


@pytest.mark.parametrize(
    "task_id,expected_sha,longest_line",
    [
        (
            "Textualize__rich-2301",
            "9c11d1c7189972e98a865e4e3bdf877282839f8c9e7843a6f9a59a31c201b680",
            27_514,
        ),
        (
            "Textualize__rich-901",
            "048ba0f89f19f8e77cefcc77a0678f6b0ad2bafde7816667c4a5dc05a70b622b",
            26_718,
        ),
    ],
)
def test_pinned_rich_fixture_is_indexed_with_its_string_document(
    tmp_path: Path, task_id: str, expected_sha: str, longest_line: int
):
    fixtures = os.environ.get("RIPPLE_P0_RICH_FIXTURES")
    if not fixtures:
        pytest.skip("Set RIPPLE_P0_RICH_FIXTURES to the pinned corpus directory")
    relative = Path("tests/_card_render.py")
    data = (Path(fixtures) / task_id / relative).read_bytes()
    assert hashlib.sha256(data).hexdigest() == expected_sha
    assert max(map(len, data.decode().splitlines())) == longest_line
    init(tmp_path)
    destination = tmp_path / relative
    destination.parent.mkdir()
    destination.write_bytes(data)
    commit(tmp_path)
    index = scan_repository(tmp_path)
    assert index.files[0].parse_error is None and index.files[0].is_test
    assert not index.symbols and not index.imports
    documents = [
        document
        for document in SearchIndex(index).documents
        if document.path == relative and document.kind == "string"
    ]
    assert len(documents) == 1


def test_cache_rebuilds_when_line_limit_changes(tmp_path: Path, monkeypatch):
    init(tmp_path)
    (tmp_path / "source.py").write_text(
        "#" + "x" * 20_000 + "\ndef visible():\n    return 1\n"
    )
    commit(tmp_path)
    monkeypatch.setattr(source, "MAX_LINE_CHARACTERS", 16_384)
    old = scan_repository_cached(tmp_path)
    assert old.index.files[0].parse_error
    monkeypatch.setattr(source, "MAX_LINE_CHARACTERS", 32_768)
    rebuilt = scan_repository_cached(tmp_path)
    assert not rebuilt.cache_hit and rebuilt.cache_path == old.cache_path
    assert rebuilt.index.files[0].parse_error is None
    assert [symbol.name for symbol in rebuilt.index.symbols] == ["visible"]
    assert scan_repository_cached(tmp_path).cache_hit


@pytest.mark.parametrize("relative", ["../outside.py", "/outside.py"])
def test_source_reader_rejects_path_escape(tmp_path: Path, relative: str):
    with pytest.raises(SourceError, match="stay inside"):
        read_source_bytes(tmp_path, Path(relative))


def test_source_reader_rejects_symlink_directory(tmp_path: Path):
    repo, outside = tmp_path / "repo", tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (outside / "source.py").write_text("EXTERNAL_SOURCE_MARKER = 1\n")
    (repo / "nested").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SourceError, match="symlink"):
        read_source_bytes(repo, Path("nested/source.py"))


def test_source_reader_rejects_fifo_without_waiting_for_a_writer(tmp_path: Path):
    os.mkfifo(tmp_path / "source.py")
    with pytest.raises(SourceError, match="not a regular file"):
        read_source_bytes(tmp_path, Path("source.py"))


@pytest.mark.parametrize("reader", ["search", "facts", "inspect", "dirty_cache"])
def test_stale_index_reader_cannot_follow_replaced_source(tmp_path: Path, reader: str):
    repo = tmp_path / "repo"
    init(repo)
    source = repo / "api.py"
    source.write_text("def visible():\n    return 'public'\n")
    commit(repo)
    index = scan_repository(repo)
    outside = tmp_path / "private.py"
    outside.write_text("EXTERNAL_SOURCE_MARKER = 'EXTERNAL_SOURCE_MARKER'\n")
    source.unlink()
    source.symlink_to(outside)
    if reader == "search":
        hits, _ = SearchIndex(index).search("EXTERNAL_SOURCE_MARKER")
        assert not hits
    elif reader == "facts":
        assert not extract_repository_facts(index)["settings"]
    elif reader == "inspect":
        result = ToolSession(index).invoke(
            "inspect_symbol", {"target": "api.py::visible"}
        )
        assert not result.ok
    else:
        result = scan_repository_cached(repo).index
        assert not result.symbols
        assert "EXTERNAL_SOURCE_MARKER" not in result.model_dump_json()


def test_inspection_character_cap_reports_truncation(tmp_path: Path):
    init(tmp_path)
    lines = ["def visible():"] + [
        f"    value_{number} = '{'x' * 400}'" for number in range(40)
    ]
    (tmp_path / "api.py").write_text("\n".join(lines) + "\n")
    commit(tmp_path)
    result = ToolSession(scan_repository(tmp_path)).invoke(
        "inspect_symbol", {"target": "api.py::visible"}
    )
    assert result.ok and result.truncated
    assert len(result.data["source"]) == INSPECT_SOURCE_CHARACTERS
