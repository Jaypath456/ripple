"""Deterministic module dependency graph construction and bounded queries."""

from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Literal

from ripple.models import DependencyNode, FileRecord, ImportRecord


@dataclass(frozen=True)
class GraphNeighbor:
    path: Path
    depth: int


def build_dependency_graph(
    files: tuple[FileRecord, ...],
    imports: tuple[ImportRecord, ...],
) -> tuple[DependencyNode, ...]:
    """Build runtime and type-checking-only edges between tracked Python files."""

    paths = {file.path for file in files}
    runtime: dict[Path, set[Path]] = {path: set() for path in paths}
    type_checking: dict[Path, set[Path]] = {path: set() for path in paths}

    for imported in imports:
        target = imported.target_path
        if (
            target is None
            or imported.source_path not in paths
            or target not in paths
            or target == imported.source_path
        ):
            continue
        destination = type_checking if imported.type_checking_only else runtime
        destination[imported.source_path].add(target)

    for source, dependencies in runtime.items():
        type_checking[source].difference_update(dependencies)

    dependents: dict[Path, set[Path]] = {path: set() for path in paths}
    for source, dependencies in runtime.items():
        for target in dependencies:
            dependents[target].add(source)

    return tuple(
        DependencyNode(
            path=path,
            dependencies=tuple(sorted(runtime[path])),
            dependents=tuple(sorted(dependents[path])),
            type_checking_dependencies=tuple(sorted(type_checking[path])),
        )
        for path in sorted(paths)
    )


def _adjacency(
    graph: tuple[DependencyNode, ...],
    direction: Literal["imports", "imported_by"],
) -> dict[Path, tuple[Path, ...]]:
    return {
        node.path: node.dependencies if direction == "imports" else node.dependents
        for node in graph
    }


def neighbors(
    graph: tuple[DependencyNode, ...],
    path: Path,
    direction: Literal["imports", "imported_by"],
    depth: Literal[1, 2],
) -> tuple[GraphNeighbor, ...]:
    """Return unique bounded runtime neighbors in deterministic BFS order."""

    adjacency = _adjacency(graph, direction)
    if path not in adjacency:
        raise KeyError(path)

    visited = {path}
    frontier = (path,)
    result: list[GraphNeighbor] = []
    for current_depth in range(1, depth + 1):
        next_frontier = sorted(
            {
                neighbor
                for current in frontier
                for neighbor in adjacency[current]
                if neighbor not in visited
            }
        )
        result.extend(
            GraphNeighbor(path=neighbor, depth=current_depth)
            for neighbor in next_frontier
        )
        visited.update(next_frontier)
        frontier = tuple(next_frontier)
        if not frontier:
            break
    return tuple(result)


def fan_in(graph: tuple[DependencyNode, ...], path: Path) -> int:
    """Return the number of direct runtime dependents of ``path``."""

    node = next((node for node in graph if node.path == path), None)
    if node is None:
        raise KeyError(path)
    return len(node.dependents)


def implementation_order(
    graph: tuple[DependencyNode, ...], paths: tuple[Path, ...]
) -> tuple[Path, ...]:
    """Order a selected subset dependency-first, breaking cycles deterministically."""

    requested = set(paths)
    dependencies = {
        node.path: tuple(sorted(set(node.dependencies) & requested))
        for node in graph
        if node.path in requested
    }
    for path in requested:
        dependencies.setdefault(path, ())

    try:
        return tuple(TopologicalSorter(dependencies).static_order())
    except CycleError:
        remaining = set(requested)
        ordered: list[Path] = []
        completed: set[Path] = set()

        def is_cyclic(start: Path) -> bool:
            frontier = list(dependencies[start])
            seen: set[Path] = set()
            while frontier:
                current = frontier.pop()
                if current == start:
                    return True
                if current in remaining and current not in seen:
                    seen.add(current)
                    frontier.extend(dependencies[current])
            return False

        while remaining:
            ready = sorted(
                path
                for path in remaining
                if set(dependencies[path]).issubset(completed)
            )
            cycle_members = sorted(path for path in remaining if is_cyclic(path))
            chosen = ready[0] if ready else cycle_members[0]
            ordered.append(chosen)
            completed.add(chosen)
            remaining.remove(chosen)
        return tuple(ordered)
