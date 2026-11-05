"""Run the locked regression suite with credentials removed and sockets blocked."""

import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path


def verify_fixtures(root):
    """Verify explicit fixtures against the immutable recorded hashes before pytest."""
    expected_paths = {
        ".ripple/hardening-fixtures/sympy/sine.py",
        ".ripple/hardening-fixtures/rich/Textualize__rich-2301/tests/_card_render.py",
        ".ripple/hardening-fixtures/rich/Textualize__rich-901/tests/_card_render.py",
    }
    try:
        rows = json.loads(
            (root / "docs/evidence/p2b-hardening/fixture_sources.json").read_text()
        )
        if (
            not isinstance(rows, list)
            or len(rows) != 3
            or {row["path"] for row in rows} != expected_paths
        ):
            raise ValueError()
        for row in rows:
            expected = row["sha256"]
            if (
                not isinstance(expected, str)
                or len(expected) != 64
                or any(c not in "0123456789abcdef" for c in expected)
            ):
                raise ValueError()
            relative = row["path"]
            if relative.endswith("sympy/sine.py"):
                path = Path(
                    os.environ.get("RIPPLE_P0_SYMPY_SINE_SOURCE", str(root / relative))
                )
            else:
                suffix = relative.split("/rich/", 1)[1]
                path = (
                    Path(
                        os.environ.get(
                            "RIPPLE_P0_RICH_FIXTURES",
                            str(root / ".ripple/hardening-fixtures/rich"),
                        )
                    )
                    / suffix
                )
            if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
                raise ValueError()
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit("Pinned fixture missing or checksum mismatch.") from None


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "src"))
    from ripple.pilot import offline, verify_environment

    verify_environment()
    fixtures = root / ".ripple/hardening-fixtures"
    os.environ.setdefault(
        "RIPPLE_P0_SYMPY_SINE_SOURCE", str(fixtures / "sympy/sine.py")
    )
    os.environ.setdefault("RIPPLE_P0_RICH_FIXTURES", str(fixtures / "rich"))
    verify_fixtures(root)
    output = Path(
        tempfile.mkdtemp(prefix="ripple-p2b-hardening-check-", dir="/private/tmp")
    )
    records = {}

    class Results:
        def pytest_runtest_logreport(self, report):
            if report.when == "call" or report.outcome != "passed":
                records[report.nodeid] = report.outcome

    import pytest

    with offline():
        result = pytest.main(
            [
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(output / "pytest"),
                *sys.argv[1:],
            ],
            plugins=[Results()],
        )
    summary = {
        "exit_code": result,
        "counts": dict(Counter(records.values())),
        "tests": records,
        "network": "Sockets blocked; no provider requests",
        "output": str(output),
    }
    (output / "test_results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("Verification evidence:", output / "test_results.json")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
