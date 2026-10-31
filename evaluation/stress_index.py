"""Bounded synthetic index measurement; no inference and no target execution.

Run in separate processes for baseline/current RSS comparisons. All inputs are
generated under a fresh temporary directory; at most 500 small modules + 2 MiB.
"""

import argparse
import json
import resource
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter


def measure(count: int) -> dict:
    import ripple
    from ripple.scanner import scan_repository
    from ripple.search import SearchIndex

    with tempfile.TemporaryDirectory(prefix="ripple-bounded-stress-") as directory:
        root = Path(directory)
        repo = root / "repo"
        repo.mkdir()
        (repo / "generated").mkdir()
        for i in range(count):
            (repo / f"module_{i:04d}.py").write_text(
                (f"from module_{i - 1:04d} import invoice_{i - 1}\n" if i else "")
                + f'def invoice_{i}(amount):\n    """Calculate invoice subtotal."""\n    return amount + {i}\n'
            )
        (repo / "generated/massive.py").write_text("#" + "x" * (2 * 1024 * 1024))
        (repo / "long_line.py").write_text("#" + "x" * 32_768)
        (repo / "malformed.py").write_text("def invalid(:\n")
        (repo / "binary.py").write_bytes(b"\x00\xff")
        (repo / "README.md").write_text(
            "Mixed language fixture; never execute target code."
        )
        (repo / "app.js").write_text("throw Error('not Python')")
        external = root / "outside.py"
        external.write_text('def outside_secret():\n    return "PRIVATE_MARKER"\n')
        (repo / "symlink.py").symlink_to(external)

        def git(*args):
            subprocess.run(
                ["git", "-C", str(repo), *args], check=True, capture_output=True
            )

        git("init", "-q")
        git("add", ".")
        git(
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=RIPPLE stress",
            "-c",
            "user.email=stress@example.test",
            "commit",
            "-qm",
            "fixture",
        )
        started = perf_counter()
        index = scan_repository(repo)
        scan_seconds = perf_counter() - started
        started = perf_counter()
        search = SearchIndex(index)
        build_search_seconds = perf_counter() - started
        started = perf_counter()
        hits, truncated = search.search("invoice subtotal", limit=10)
        search_seconds = perf_counter() - started
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {
            "mode": "bounded synthetic measurement, not real-world benchmark",
            "modules_requested": count,
            "python": sys.version,
            "implementation_path": ripple.__file__,
            "tracked_python_files": len(index.files),
            "symbols": len(index.symbols),
            "parse_errors": {
                str(f.path): f.parse_error for f in index.files if f.parse_error
            },
            "external_symbol_indexed": any(
                s.name == "outside_secret" for s in index.symbols
            ),
            "scan_seconds": scan_seconds,
            "search_build_seconds": build_search_seconds,
            "query_seconds": search_seconds,
            "search_hits": len(hits),
            "search_truncated": truncated,
            "peak_rss_mib": rss / (1024 * 1024 if sys.platform == "darwin" else 1024),
            "limits": "500 modules max, one 2 MiB file, one 32 KiB line; no stress to OOM",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modules", type=int, default=300, choices=range(1, 501))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = measure(args.modules)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
