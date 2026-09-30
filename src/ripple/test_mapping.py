"""Static mapping from test files to likely source files and symbols."""

from collections import defaultdict
from pathlib import Path

from ripple.models import (
    FileRecord,
    ImportRecord,
    ReferenceRecord,
    SymbolRecord,
    TestMappingRecord,
)

_REASON_ORDER = {"imports": 0, "references": 1, "naming": 2}


def _source_stem(test_path: Path) -> str:
    stem = test_path.stem
    if stem.startswith("test_"):
        return stem.removeprefix("test_")
    if stem.endswith("_test"):
        return stem.removesuffix("_test")
    return stem


def build_test_mappings(
    files: tuple[FileRecord, ...],
    symbols: tuple[SymbolRecord, ...],
    imports: tuple[ImportRecord, ...],
    references: tuple[ReferenceRecord, ...],
) -> tuple[TestMappingRecord, ...]:
    """Build conservative import, reference, and exact-name test mappings."""

    tests = {file.path for file in files if file.is_test}
    sources = {file.path for file in files if not file.is_test}
    symbol_paths = {symbol.id: symbol.path for symbol in symbols}
    evidence: dict[tuple[Path, Path], dict[str, set[str]]] = defaultdict(
        lambda: {
            "reasons": set(),
            "test_symbols": set(),
            "source_symbols": set(),
        }
    )

    for imported in imports:
        if imported.source_path in tests and imported.target_path in sources:
            evidence[(imported.source_path, imported.target_path)]["reasons"].add(
                "imports"
            )

    for reference in references:
        source_path = symbol_paths.get(reference.target_symbol or "")
        if reference.source_path not in tests or source_path not in sources:
            continue
        item = evidence[(reference.source_path, source_path)]
        item["reasons"].add("references")
        if reference.target_symbol is not None:
            item["source_symbols"].add(reference.target_symbol)
        if reference.enclosing_symbol is not None:
            item["test_symbols"].add(reference.enclosing_symbol)

    sources_by_stem: dict[str, list[Path]] = defaultdict(list)
    for source in sources:
        sources_by_stem[source.stem].append(source)
    for test_path in tests:
        candidates = sources_by_stem.get(_source_stem(test_path), [])
        if len(candidates) == 1:
            evidence[(test_path, candidates[0])]["reasons"].add("naming")

    return tuple(
        TestMappingRecord(
            test_path=test_path,
            source_path=source_path,
            test_symbols=tuple(sorted(item["test_symbols"])),
            source_symbols=tuple(sorted(item["source_symbols"])),
            reasons=tuple(sorted(item["reasons"], key=_REASON_ORDER.__getitem__)),
        )
        for (test_path, source_path), item in sorted(evidence.items())
    )
