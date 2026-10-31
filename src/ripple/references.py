"""Deterministic resolution of useful Python symbol references."""

import ast
from collections import defaultdict
from pathlib import Path

from ripple.models import ImportRecord, ReferenceRecord, SymbolRecord


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        if prefix is not None:
            return f"{prefix}.{node.attr}"
    return None


def _absolute_import_module(
    imported_module: str,
    level: int,
    source_module: str,
    source_is_package: bool,
) -> str | None:
    if level == 0:
        return imported_module

    package_parts = source_module.split(".")
    if not source_is_package:
        package_parts.pop()
    parent_steps = level - 1
    if not package_parts or parent_steps >= len(package_parts):
        return None

    base_parts = package_parts[: len(package_parts) - parent_steps]
    if imported_module:
        base_parts.extend(imported_module.split("."))
    return ".".join(base_parts)


def _unique_values(candidates: dict[str, set[str]]) -> dict[str, str]:
    return {
        name: next(iter(values))
        for name, values in candidates.items()
        if len(values) == 1
    }


class _ReferenceVisitor(ast.NodeVisitor):
    def __init__(
        self,
        path: Path,
        symbols: tuple[SymbolRecord, ...],
        imports: tuple[ImportRecord, ...],
        modules_by_path: dict[Path, str],
    ) -> None:
        self.path = path
        self.enclosing_symbol: str | None = None
        self._found: list[tuple[int, int, ReferenceRecord]] = []
        self._sequence = 0

        symbols_by_id = {symbol.id: symbol for symbol in symbols}
        self._symbols_by_id = symbols_by_id
        self._symbols_by_qualname = {
            (symbol.path, symbol.qualname): symbol.id for symbol in symbols
        }
        self._symbols_by_position = {
            (symbol.start_line, symbol.name): symbol.id
            for symbol in symbols
            if symbol.path == path
        }

        top_level_candidates: dict[tuple[Path, str], set[str]] = defaultdict(set)
        for symbol in symbols:
            if "." not in symbol.qualname:
                top_level_candidates[(symbol.path, symbol.name)].add(symbol.id)
        top_level_symbols = {
            key: next(iter(values))
            for key, values in top_level_candidates.items()
            if len(values) == 1
        }

        bare_candidates: dict[str, set[str]] = defaultdict(set)
        for symbol in symbols:
            if symbol.path == path and "." not in symbol.qualname:
                bare_candidates[symbol.name].add(symbol.id)

        module_candidates: dict[str, set[Path]] = defaultdict(set)
        source_module = modules_by_path[path]
        for imported in imports:
            if imported.source_path != path or imported.target_path is None:
                continue
            aliases = dict(imported.aliases)
            if not imported.is_from_import:
                local_module = aliases.get(imported.module, imported.module)
                module_candidates[local_module].add(imported.target_path)
                continue

            absolute_module = _absolute_import_module(
                imported.module,
                imported.level,
                source_module,
                path.name == "__init__.py",
            )
            target_module = modules_by_path.get(imported.target_path)
            for name in imported.names:
                if name == "*":
                    continue
                local_name = aliases.get(name, name)
                child_module = f"{absolute_module}.{name}" if absolute_module else None
                if child_module is not None and target_module == child_module:
                    module_candidates[local_name].add(imported.target_path)
                    continue
                target_symbol = top_level_symbols.get((imported.target_path, name))
                if target_symbol is not None:
                    bare_candidates[local_name].add(target_symbol)

        self._bare_symbols = _unique_values(bare_candidates)
        self._module_bindings = {
            name: next(iter(paths))
            for name, paths in module_candidates.items()
            if len(paths) == 1
        }
        self._ambiguous_bindings = (
            self._bare_symbols.keys() & self._module_bindings.keys()
        )

    def references(self) -> tuple[ReferenceRecord, ...]:
        self._found.sort(key=lambda item: (item[2].line, item[0], item[1]))
        return tuple(record for _, _, record in self._found)

    def _indexed_symbol(self, node: ast.ClassDef | ast.FunctionDef) -> str | None:
        return self._symbols_by_position.get((node.lineno, node.name))

    def _resolve(self, node: ast.expr) -> str | None:
        dotted_name = _dotted_name(node)
        if dotted_name is None:
            return None
        parts = dotted_name.split(".")
        if parts[0] in self._ambiguous_bindings:
            return None
        if len(parts) == 1:
            return self._bare_symbols.get(dotted_name)

        root_symbol_id = self._bare_symbols.get(parts[0])
        root_is_module = parts[0] in self._module_bindings
        if root_symbol_id is not None and not root_is_module:
            root_symbol = self._symbols_by_id[root_symbol_id]
            if root_symbol.kind == "class":
                qualname = ".".join((root_symbol.qualname, *parts[1:]))
                target = self._symbols_by_qualname.get((root_symbol.path, qualname))
                if target is not None:
                    return target

        for prefix_length in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:prefix_length])
            target_path = self._module_bindings.get(prefix)
            if target_path is None:
                continue
            qualname = ".".join(parts[prefix_length:])
            target = self._symbols_by_qualname.get((target_path, qualname))
            if target is not None:
                return target
        return None

    def _record(
        self,
        node: ast.expr,
        kind: str,
        target_symbol: str | None,
        *,
        enclosing_symbol: str | None = None,
    ) -> None:
        name = _dotted_name(node)
        if name is None:
            return
        self._found.append(
            (
                node.col_offset,
                self._sequence,
                ReferenceRecord(
                    source_path=self.path,
                    line=node.lineno,
                    enclosing_symbol=(
                        self.enclosing_symbol
                        if enclosing_symbol is None
                        else enclosing_symbol
                    ),
                    target_symbol=target_symbol,
                    name=name,
                    kind=kind,
                    confidence="high" if target_symbol is not None else "low",
                ),
            )
        )
        self._sequence += 1

    def _visit_definition(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        indexed_symbol = self._indexed_symbol(node)
        definition_enclosing = indexed_symbol or self.enclosing_symbol

        for decorator in node.decorator_list:
            expression = (
                decorator.func if isinstance(decorator, ast.Call) else decorator
            )
            target = self._resolve(expression)
            if target is not None:
                self._record(
                    expression,
                    "decorator",
                    target,
                    enclosing_symbol=definition_enclosing,
                )
            if isinstance(decorator, ast.Call):
                for argument in decorator.args:
                    self.visit(argument)
                for keyword in decorator.keywords:
                    self.visit(keyword.value)

        previous_enclosing = self.enclosing_symbol
        self.enclosing_symbol = definition_enclosing
        try:
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if isinstance(base, (ast.Name, ast.Attribute)):
                        target = self._resolve(base)
                        if target is not None:
                            self._record(base, "subclass", target)
                    else:
                        self.visit(base)
                for keyword in node.keywords:
                    self.visit(keyword.value)
                for type_parameter in getattr(node, "type_params", ()):
                    self.visit(type_parameter)
            else:
                self.visit(node.args)
                if node.returns is not None:
                    self.visit(node.returns)
                for type_parameter in getattr(node, "type_params", ()):
                    self.visit(type_parameter)
            for statement in node.body:
                self.visit(statement)
        finally:
            self.enclosing_symbol = previous_enclosing

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, (ast.Name, ast.Attribute)):
            target = self._resolve(node.func)
            if target is not None or isinstance(node.func, ast.Attribute):
                self._record(node.func, "call", target)
        else:
            self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        target = self._resolve(node)
        if target is not None:
            self._record(node, "attribute", target)
        else:
            self.visit(node.value)

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        target = self._resolve(node)
        if target is not None:
            self._record(node, "name", target)


def extract_references(
    tree: ast.Module,
    path: Path,
    symbols: tuple[SymbolRecord, ...],
    imports: tuple[ImportRecord, ...],
    modules_by_path: dict[Path, str],
) -> tuple[ReferenceRecord, ...]:
    """Extract useful resolved and selected unresolved references from one file."""

    visitor = _ReferenceVisitor(path, symbols, imports, modules_by_path)
    visitor.visit(tree)
    return visitor.references()
