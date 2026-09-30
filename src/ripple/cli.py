"""Command-line interface for RIPPLE."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter

from ripple.cache import ScanResult, scan_repository_cached
from ripple.scanner import ScanError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ripple")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan", help="scan the tracked Python files in a Git repository"
    )
    scan_parser.add_argument("repo", type=Path, help="path to a Git repository")
    scan_parser.add_argument(
        "--json",
        action="store_true",
        help="write the complete repository index as JSON",
    )
    scan_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="bypass cache reads and writes",
    )
    return parser


def _cache_summary(result: ScanResult) -> str:
    if result.cache_path is None:
        return "disabled"
    relative_path = result.cache_path.relative_to(result.index.repo_root)
    status = "hit" if result.cache_hit else "written"
    return f"{status} ({relative_path})"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the RIPPLE command-line interface."""

    args = _parser().parse_args(argv)
    started_at = perf_counter()
    try:
        result = scan_repository_cached(args.repo, use_cache=not args.no_cache)
    except ScanError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    elapsed = perf_counter() - started_at
    index = result.index

    if args.json:
        print(index.model_dump_json(indent=2))
        return 0

    test_count = sum(file.is_test for file in index.files)
    print(f"Repository: {index.repo_root.name}")
    print(f"Commit: {index.commit[:7]}")
    print(f"Dirty: {'yes' if index.dirty else 'no'}")
    print(f"Python files: {len(index.files)}")
    print(f"Test files: {test_count}")
    print(f"Symbols: {len(index.symbols)}")
    print(f"Imports: {len(index.imports)}")
    print(f"References: {len(index.references)}")
    print(f"Parse errors: {sum(file.parse_error is not None for file in index.files)}")
    print(f"Cache: {_cache_summary(result)}")
    print(f"Scan time: {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
