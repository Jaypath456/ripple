"""Small deterministic BM25 lexical index over repository facts."""

import ast
import math
import re
import tokenize as source_tokenize
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ripple.models import RepositoryIndex

_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_TOKEN = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> tuple[str, ...]:
    """Split case, snake case, dotted names, and paths into lowercase terms."""

    separated = _ACRONYM_BOUNDARY.sub(r"\1 \2", text)
    separated = _CAMEL_BOUNDARY.sub(r"\1 \2", separated)
    return tuple(token.lower() for token in _TOKEN.findall(separated))


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    symbol: str | None
    line: int | None
    kind: Literal["symbol", "file", "string"]
    score: float
    snippet: str


@dataclass(frozen=True)
class _Document:
    path: Path
    symbol: str | None
    line: int | None
    kind: Literal["symbol", "file", "string"]
    text: str
    snippet: str
    terms: tuple[str, ...]


def _one_line(text: str, limit: int = 200) -> str:
    return " ".join(text.split())[:limit]


def _string_documents(index: RepositoryIndex) -> tuple[_Document, ...]:
    documents: list[_Document] = []
    modules = {file.path: file.module for file in index.files}
    for file in index.files:
        if file.parse_error is not None:
            continue
        try:
            with source_tokenize.open(index.repo_root / file.path) as source_file:
                tree = ast.parse(source_file.read(), filename=file.path.as_posix())
        except (OSError, SyntaxError, UnicodeError):
            continue
        strings = sorted(
            (
                node.lineno,
                node.col_offset,
                node.value,
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.strip()
        )
        for line, _, value in strings:
            snippet = _one_line(value)
            text = f"{file.path.as_posix()} {modules[file.path]} {value[:500]}"
            documents.append(
                _Document(
                    path=file.path,
                    symbol=None,
                    line=line,
                    kind="string",
                    text=text,
                    snippet=snippet,
                    terms=tokenize(text),
                )
            )
    return tuple(documents)


def build_documents(index: RepositoryIndex) -> tuple[_Document, ...]:
    """Build the stable searchable corpus for an index."""

    documents: list[_Document] = []
    for file in index.files:
        text = f"{file.path.as_posix()} {file.module}"
        documents.append(
            _Document(
                path=file.path,
                symbol=None,
                line=None,
                kind="file",
                text=text,
                snippet=file.path.as_posix(),
                terms=tokenize(text),
            )
        )
    for symbol in index.symbols:
        details = " ".join(
            part
            for part in (
                symbol.id,
                symbol.name,
                symbol.qualname,
                symbol.signature or "",
                symbol.doc or "",
                " ".join(symbol.decorators),
                " ".join(symbol.bases),
            )
            if part
        )
        snippet = symbol.signature or symbol.qualname
        if symbol.doc:
            snippet = f"{snippet} — {symbol.doc}"
        documents.append(
            _Document(
                path=symbol.path,
                symbol=symbol.id,
                line=symbol.start_line,
                kind="symbol",
                text=details,
                snippet=_one_line(snippet),
                terms=tokenize(details),
            )
        )
    documents.extend(_string_documents(index))
    return tuple(
        sorted(
            documents,
            key=lambda item: (
                item.path.as_posix(),
                item.line if item.line is not None else -1,
                item.kind,
                item.symbol or "",
                item.snippet,
            ),
        )
    )


class SearchIndex:
    """An in-memory BM25 index cheap enough to rebuild from RepositoryIndex."""

    def __init__(self, index: RepositoryIndex) -> None:
        self.documents = build_documents(index)

    def search(
        self,
        query: str,
        kind: Literal["any", "symbol", "file", "string"] = "any",
        limit: int = 10,
    ) -> tuple[tuple[SearchHit, ...], bool]:
        query_terms = tokenize(query)
        if not query_terms:
            return (), False
        documents = tuple(
            document
            for document in self.documents
            if kind == "any" or document.kind == kind
        )
        if not documents:
            return (), False

        frequencies = [Counter(document.terms) for document in documents]
        average_length = sum(len(document.terms) for document in documents) / len(
            documents
        )
        document_frequency = {
            term: sum(term in frequency for frequency in frequencies)
            for term in set(query_terms)
        }
        scored: list[tuple[float, _Document]] = []
        for document, frequency in zip(documents, frequencies, strict=True):
            score = 0.0
            length = len(document.terms)
            for term in query_terms:
                term_frequency = frequency[term]
                if term_frequency == 0:
                    continue
                matching = document_frequency[term]
                inverse_frequency = math.log(
                    1 + (len(documents) - matching + 0.5) / (matching + 0.5)
                )
                denominator = term_frequency + 1.5 * (
                    1 - 0.75 + 0.75 * length / average_length
                )
                score += inverse_frequency * term_frequency * 2.5 / denominator
            if score > 0:
                scored.append((score, document))

        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].path.as_posix(),
                item[1].symbol or "",
                item[1].line if item[1].line is not None else -1,
                item[1].kind,
                item[1].snippet,
            )
        )
        hits = tuple(
            SearchHit(
                path=document.path,
                symbol=document.symbol,
                line=document.line,
                kind=document.kind,
                score=round(score, 6),
                snippet=document.snippet,
            )
            for score, document in scored[:limit]
        )
        return hits, len(scored) > limit
