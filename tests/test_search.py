import subprocess
from pathlib import Path

from ripple.scanner import scan_repository
from ripple.search import SearchIndex, tokenize


def git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def write(repo: Path, relative_path: str, content: str) -> None:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_tokenizer_splits_names_paths_and_case() -> None:
    assert tokenize("UserService") == ("user", "service")
    assert tokenize("user_service") == ("user", "service")
    assert tokenize("app/auth/service.py") == ("app", "auth", "service", "py")
    assert tokenize("HTTPClient.getUser") == ("http", "client", "get", "user")


def test_bm25_ranking_filters_limits_and_breaks_ties_deterministically(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "RIPPLE Tests")
    git(tmp_path, "config", "user.email", "ripple@example.test")
    write(
        tmp_path,
        "app/auth_service.py",
        '"""Authentication service."""\n\nLOGIN_ROUTE = "/users/login"\n\nclass UserService:\n    """Authenticate users."""\n    pass\n',
    )
    write(tmp_path, "a/foo.py", "class Foo:\n    pass\n")
    write(tmp_path, "b/foo.py", "class Foo:\n    pass\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "search fixture")

    search = SearchIndex(scan_repository(tmp_path))

    hits, truncated = search.search("user authentication service", limit=10)
    assert hits[0].path == Path("app/auth_service.py")
    assert truncated is False

    symbol_hits, _ = search.search("UserService", kind="symbol", limit=10)
    assert symbol_hits[0].symbol == "app/auth_service.py::UserService"
    assert all(hit.kind == "symbol" for hit in symbol_hits)

    string_hits, _ = search.search("users login", kind="string", limit=10)
    assert string_hits[0].kind == "string"
    assert string_hits[0].snippet == "/users/login"

    file_hits, limited = search.search("foo", kind="file", limit=1)
    assert [hit.path for hit in file_hits] == [Path("a/foo.py")]
    assert limited is True

    assert search.search("   ") == ((), False)
    assert search.search("term-that-does-not-exist") == ((), False)
