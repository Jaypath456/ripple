"""Conservative static repository facts for Phase 5."""

import ast
import json
import tokenize
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ripple.models import RepositoryIndex

FACT_LIMIT = 200
FactKind = Literal["routes", "models", "settings", "migrations", "entry_points"]
FACT_KINDS = ("routes", "models", "settings", "migrations", "entry_points")


class FactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RouteFact(FactModel):
    framework: Literal["fastapi", "flask", "django"]
    methods: tuple[str, ...]
    route: str | None
    handler: str | None
    path: str
    line: int
    unresolved: bool = False


class ModelFieldFact(FactModel):
    name: str
    annotation: str | None = None
    declaration: str | None = None
    default: Any | None = None
    line: int


class ModelFact(FactModel):
    framework: Literal["django", "sqlalchemy", "pydantic"]
    symbol_id: str
    path: str
    bases: tuple[str, ...]
    fields: tuple[ModelFieldFact, ...]
    start_line: int
    end_line: int


class SettingFact(FactModel):
    key: str
    path: str
    line: int
    access_style: Literal["os.getenv", "os.environ.get", "os.environ[]", "assignment"]
    default: Any | None = None


class MigrationFact(FactModel):
    directory: str
    files: tuple[str, ...]
    framework: Literal["django", "alembic", "generic"]


class EntryPointFact(FactModel):
    kind: Literal["main_module", "app_object", "app_factory"]
    path: str
    symbol: str | None
    line: int | None


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _literal(node: ast.AST | None) -> Any | None:
    if node is None:
        return None
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, (str, int, float, bool, type(None))) else None


def _safe_default(key: str, node: ast.AST | None) -> Any | None:
    if any(word in key.casefold() for word in ("secret", "token", "password", "key")):
        return "[REDACTED]" if node is not None else None
    return _literal(node)


def _string_arg(call: ast.Call, index: int = 0) -> str | None:
    if len(call.args) <= index:
        return None
    value = _literal(call.args[index])
    return value if isinstance(value, str) else None


def _decorator_routes(tree: ast.Module, path: Path) -> list[RouteFact]:
    facts: list[RouteFact] = []
    http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            name = _call_name(decorator.func)
            final = name.rsplit(".", 1)[-1]
            owner = name.rsplit(".", 1)[0] if "." in name else ""
            if final in http_methods and owner.rsplit(".", 1)[-1] in {"app", "router"}:
                facts.append(
                    RouteFact(
                        framework="fastapi",
                        methods=(final.upper(),),
                        route=_string_arg(decorator),
                        handler=f"{path.as_posix()}::{node.name}",
                        path=path.as_posix(),
                        line=decorator.lineno,
                        unresolved=_string_arg(decorator) is None,
                    )
                )
            elif final == "route" and owner.rsplit(".", 1)[-1] in {
                "app",
                "blueprint",
                "bp",
            }:
                methods: tuple[str, ...] = ("GET",)
                for keyword in decorator.keywords:
                    if keyword.arg == "methods":
                        try:
                            raw = ast.literal_eval(keyword.value)
                            if isinstance(raw, (list, tuple)):
                                methods = tuple(str(item).upper() for item in raw)
                        except (ValueError, TypeError):
                            pass
                facts.append(
                    RouteFact(
                        framework="flask",
                        methods=methods,
                        route=_string_arg(decorator),
                        handler=f"{path.as_posix()}::{node.name}",
                        path=path.as_posix(),
                        line=decorator.lineno,
                        unresolved=_string_arg(decorator) is None,
                    )
                )
    return facts


def _django_routes(tree: ast.Module, path: Path) -> list[RouteFact]:
    if path.name != "urls.py":
        return []
    facts: list[RouteFact] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func).rsplit(".", 1)[-1]
        if name not in {"path", "re_path"}:
            continue
        handler = ast.unparse(node.args[1]) if len(node.args) > 1 else None
        facts.append(
            RouteFact(
                framework="django",
                methods=(),
                route=_string_arg(node),
                handler=handler,
                path=path.as_posix(),
                line=node.lineno,
                unresolved=_string_arg(node) is None or handler is None,
            )
        )
    return facts


def _class_fields(node: ast.ClassDef) -> tuple[ModelFieldFact, ...]:
    fields: list[ModelFieldFact] = []
    for child in node.body:
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            fields.append(
                ModelFieldFact(
                    name=child.target.id,
                    annotation=ast.unparse(child.annotation),
                    declaration=ast.unparse(child.value) if child.value else None,
                    default=_literal(child.value),
                    line=child.lineno,
                )
            )
        elif (
            isinstance(child, ast.Assign)
            and len(child.targets) == 1
            and isinstance(child.targets[0], ast.Name)
        ):
            fields.append(
                ModelFieldFact(
                    name=child.targets[0].id,
                    declaration=ast.unparse(child.value),
                    default=_literal(child.value),
                    line=child.lineno,
                )
            )
    return tuple(fields)


def _models(tree: ast.Module, path: Path) -> list[ModelFact]:
    imports = " ".join(
        ast.unparse(node)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    )
    facts: list[ModelFact] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = tuple(ast.unparse(base) for base in node.bases)
        framework: Literal["django", "sqlalchemy", "pydantic"] | None = None
        if (
            any(base.endswith("models.Model") or base == "Model" for base in bases)
            and "django" in imports
        ):
            framework = "django"
        elif any(base.endswith("BaseModel") for base in bases):
            framework = "pydantic"
        elif any(base.endswith("DeclarativeBase") for base in bases) or (
            "sqlalchemy" in imports and "Base" in bases
        ):
            framework = "sqlalchemy"
        if framework:
            facts.append(
                ModelFact(
                    framework=framework,
                    symbol_id=f"{path.as_posix()}::{node.name}",
                    path=path.as_posix(),
                    bases=bases,
                    fields=_class_fields(node),
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                )
            )
    return facts


def _settings(tree: ast.Module, path: Path) -> list[SettingFact]:
    facts: list[SettingFact] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in {"os.getenv", "os.environ.get"}:
                key = _string_arg(node)
                if key:
                    facts.append(
                        SettingFact(
                            key=key,
                            path=path.as_posix(),
                            line=node.lineno,
                            access_style=name,
                            default=_safe_default(
                                key, node.args[1] if len(node.args) > 1 else None
                            ),
                        )
                    )
        elif isinstance(node, ast.Subscript) and _call_name(node.value) == "os.environ":
            key = _literal(node.slice)
            if isinstance(key, str):
                facts.append(
                    SettingFact(
                        key=key,
                        path=path.as_posix(),
                        line=node.lineno,
                        access_style="os.environ[]",
                    )
                )
    if "settings" in path.name.casefold() or "config" in path.name.casefold():
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                key = node.targets[0].id
                if key.isupper() and _literal(node.value) is not None:
                    facts.append(
                        SettingFact(
                            key=key,
                            path=path.as_posix(),
                            line=node.lineno,
                            access_style="assignment",
                            default=_safe_default(key, node.value),
                        )
                    )
    unique = {
        (item.key, item.path, item.line, item.access_style): item for item in facts
    }
    return list(unique.values())


def _entry_points(tree: ast.Module, path: Path) -> list[EntryPointFact]:
    facts: list[EntryPointFact] = []
    if path.name == "__main__.py":
        facts.append(
            EntryPointFact(
                kind="main_module", path=path.as_posix(), symbol=None, line=1
            )
        )
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "create_app",
            "get_app",
        }:
            facts.append(
                EntryPointFact(
                    kind="app_factory",
                    path=path.as_posix(),
                    symbol=f"{path.as_posix()}::{node.name}",
                    line=node.lineno,
                )
            )
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in {"app", "application"}
            for target in node.targets
        ):
            facts.append(
                EntryPointFact(
                    kind="app_object",
                    path=path.as_posix(),
                    symbol=f"{path.as_posix()}::app",
                    line=node.lineno,
                )
            )
    return facts


def extract_repository_facts(
    index: RepositoryIndex,
) -> dict[FactKind, tuple[FactModel, ...]]:
    routes: list[FactModel] = []
    models: list[FactModel] = []
    settings: list[FactModel] = []
    entries: list[FactModel] = []
    for file in index.files:
        if file.parse_error:
            continue
        try:
            with tokenize.open(index.repo_root / file.path) as stream:
                tree = ast.parse(stream.read(), filename=file.path.as_posix())
        except (OSError, SyntaxError, UnicodeError):
            continue
        routes.extend(_decorator_routes(tree, file.path))
        routes.extend(_django_routes(tree, file.path))
        models.extend(_models(tree, file.path))
        settings.extend(_settings(tree, file.path))
        entries.extend(_entry_points(tree, file.path))

    migration_groups: dict[str, list[str]] = {}
    for file in index.files:
        parts = file.path.parts
        directory: Path | None = None
        framework: str | None = None
        if "migrations" in parts[:-1]:
            position = parts.index("migrations")
            directory = Path(*parts[: position + 1])
            framework = "django"
        elif "alembic" in parts[:-1]:
            position = (
                parts.index("versions")
                if "versions" in parts[:-1]
                else parts.index("alembic")
            )
            directory = Path(*parts[: position + 1])
            framework = "alembic"
        if directory:
            key = f"{framework}:{directory.as_posix()}"
            migration_groups.setdefault(key, []).append(file.path.as_posix())
    migrations: list[FactModel] = []
    for key, files in sorted(migration_groups.items()):
        framework, directory = key.split(":", 1)
        migrations.append(
            MigrationFact(
                directory=directory,
                files=tuple(sorted(files)),
                framework=framework,
            )
        )
    return {
        "routes": tuple(sorted(routes, key=lambda item: (item.path, item.line))),
        "models": tuple(sorted(models, key=lambda item: item.symbol_id)),
        "settings": tuple(
            sorted(
                settings,
                key=lambda item: (
                    item.key,
                    item.path,
                    item.line,
                ),
            )
        ),
        "migrations": tuple(migrations),
        "entry_points": tuple(
            sorted(
                entries,
                key=lambda item: (item.path, item.line or 0),
            )
        ),
    }


def repository_facts(
    index: RepositoryIndex, kind: FactKind, filter_text: str | None = None
) -> tuple[list[dict[str, Any]], bool]:
    facts = extract_repository_facts(index)[kind]
    serialized = [fact.model_dump(mode="json") for fact in facts]
    if filter_text:
        needle = filter_text.casefold()
        serialized = [
            item
            for item in serialized
            if needle in json.dumps(item, sort_keys=True).casefold()
        ]
    return serialized[:FACT_LIMIT], len(serialized) > FACT_LIMIT
