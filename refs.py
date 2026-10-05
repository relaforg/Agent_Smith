from __future__ import annotations
import ast
import json
import os
import re
import sys
from functools import lru_cache
from itertools import islice
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPE_NODES = FUNC_NODES + (ast.ClassDef,)
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
             ".tox", ".mypy_cache", "build", "dist"}
MAX_HITS = 200

# (path, line number, source line)
Row = Tuple[str, int, str]


@lru_cache(maxsize=None)
def read_source(path: str) -> Optional[str]:
    """Return the file content, or None when it cannot be read as text."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def parse_source(path: Path, source: str) -> Optional[ast.Module]:
    """Parse `source`, or None when the file is not valid Python."""
    try:
        return ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError):
        return None


def line_text(path: str, lineno: int) -> str:
    """The raw source line, or "" when it is out of reach."""
    source = read_source(str(path))
    if source is None:
        return ""
    lines = source.splitlines()
    return lines[lineno - 1] if 0 < lineno <= len(lines) else ""


def iter_python_files(root: Path) -> Iterator[Path]:
    """Walk `root`, skipping vendored and generated directories."""
    for path in root.rglob("*.py"):
        if SKIP_DIRS.isdisjoint(path.parts):
            yield path


def dedup(rows: List[Row]) -> List[Row]:
    """One row per (file, line), sorted, so both engines agree on ordering."""
    return sorted({(path, lineno): (path, lineno, text)
                   for path, lineno, text in rows}.values())


def end_line(node: ast.AST) -> int:
    """The last line `node` spans."""
    end = getattr(node, "end_lineno", None)
    if end is not None:
        return end
    return max((child.lineno for child in ast.walk(node)
                if hasattr(child, "lineno")), default=node.lineno)


def node_line(node: ast.AST) -> int:
    """The line `node` should be reported at.

    Before 3.8 a decorated `def` reports its first decorator's line, which is
    not what "find the definition" means; step past the decorators instead.
    """
    decorators = getattr(node, "decorator_list", None)
    if decorators and node.lineno <= decorators[-1].lineno:
        return end_line(decorators[-1]) + 1
    return node.lineno


def load_jedi(libs: Optional[str]):
    if libs and os.path.isdir(libs):
        sys.path.insert(0, libs)
    try:
        import jedi
    except Exception:
        return None
    return jedi


def rows_from_names(names) -> List[Row]:
    return dedup([(str(name.module_path), name.line,
                   line_text(name.module_path, name.line))
                  for name in names
                  if name.module_path is not None and name.line])


def columns_of(source: str, line: int, name: str) -> List[int]:
    lines = source.splitlines()
    if not 0 < line <= len(lines):
        return []
    return [m.start()
            for m in re.finditer(rf"\b{re.escape(name)}\b", lines[line - 1])]


def jedi_references(jedi, root: Path, filepath: Path, line: int,
                    name: str) -> Optional[List[Row]]:
    source = read_source(str(filepath))
    if source is None:
        return None
    try:
        script = jedi.Script(path=str(filepath),
                             project=jedi.Project(str(root)))
        names = [ref
                 for column in columns_of(source, line, name)
                 for ref in script.get_references(line, column,
                                                  include_builtins=False,
                                                  scope="project")]
    except Exception:
        return None
    return rows_from_names(names) or None


def jedi_definitions(jedi, root: Path, name: str) -> Optional[List[Row]]:
    try:
        project = jedi.Project(str(root))
        names = [found
                 for pattern in (f"def {name}", f"class {name}")
                 for found in islice(project.search(pattern, all_scopes=True),
                                     MAX_HITS)
                 if found.name == name]
    except Exception:
        return None
    return rows_from_names(names) or None


def find_symbol(tree: ast.Module, name: str,
                line: int) -> Optional[ast.AST]:
    candidates = []                     # List[Tuple[int, ast.AST]]
    for node in ast.walk(tree):
        if getattr(node, "lineno", None) is None or node_line(node) != line:
            continue
        if isinstance(node, SCOPE_NODES) and node.name == name:
            candidates.append((0, node))
        elif isinstance(node, ast.arg) and node.arg == name:
            candidates.append((1, node))
        elif isinstance(node, ast.Name) and node.id == name:
            candidates.append((2 if isinstance(node.ctx, ast.Store) else 3,
                               node))
    return min(candidates, key=lambda c: c[0])[1] if candidates else None


def enclosing_scope(tree: ast.Module,
                    target: ast.AST) -> Optional[ast.AST]:
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, SCOPE_NODES) or node is target:
            continue
        if node.lineno <= target.lineno <= end_line(node):
            if best is None or node.lineno > best.lineno:
                best = node
    return best


def binds_name(scope: ast.AST, name: str) -> bool:
    bound = False
    for node in ast.walk(scope):
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return False
        if isinstance(node, ast.arg) and node.arg == name:
            bound = True
        elif isinstance(node, ast.Name) and node.id == name \
                and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound = True
        elif isinstance(node, SCOPE_NODES) and node.name == name:
            bound = True
    return bound


def iter_refs(tree: ast.AST, name: str) -> Iterator[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            yield node
        elif isinstance(node, ast.Attribute) and node.attr == name:
            yield node
        elif isinstance(node, SCOPE_NODES) and node.name == name:
            yield node
        elif isinstance(node, ast.arg) and node.arg == name:
            yield node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(name in (alias.name, alias.asname) for alias in node.names):
                yield node
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            yield node


def iter_definitions(tree: ast.AST, name: str) -> Iterator[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, SCOPE_NODES) and node.name == name:
            yield node


def imports_symbol(tree: ast.Module, name: str, modname: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in ("*", name) or alias.asname == name:
                    return True
            if node.module and node.module.split(".")[-1] == modname:
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if modname in alias.name.split("."):
                    return True
    return False


def report(path: Path, nodes) -> List[Row]:
    return [(str(path), lineno, line_text(str(path), lineno))
            for lineno in sorted({node_line(node) for node in nodes})]


def walk_project(root: Path, name: str,
                 skip: Path) -> Iterator[Tuple[Path, ast.Module]]:
    for path in iter_python_files(root):
        if path.resolve() == skip.resolve():
            continue
        source = read_source(str(path))
        if source is None or name not in source:
            continue
        tree = parse_source(path, source)
        if tree is not None:
            yield path, tree


def ast_references(root: Path, filepath: Path, line: int,
                   name: str) -> List[Row]:
    source = read_source(str(filepath))
    tree = parse_source(filepath, source) if source is not None else None
    if tree is None:
        return []

    src = find_symbol(tree, name, line)
    if src is None:
        return []

    scope = enclosing_scope(tree, src)
    if isinstance(scope, FUNC_NODES) and binds_name(scope, name):
        return report(filepath, iter_refs(scope, name))

    rows = report(filepath, iter_refs(tree, name))
    modname = filepath.stem
    for path, other in walk_project(root, name, skip=filepath):
        if imports_symbol(other, name, modname):
            rows += report(path, iter_refs(other, name))
    return dedup(rows)


def ast_definitions(root: Path, name: str) -> List[Row]:
    rows = []
    for path in iter_python_files(root):
        source = read_source(str(path))
        if source is None or name not in source:
            continue
        tree = parse_source(path, source)
        if tree is not None:
            rows += report(path, iter_definitions(tree, name))
    return dedup(rows)


def run(payload: dict) -> Tuple[List[Row], str]:
    name = payload["name"]
    root = Path(payload.get("root", "/"))
    jedi = load_jedi(payload.get("libs"))

    if payload.get("mode") == "definition":
        rows = jedi_definitions(jedi, root, name) if jedi else None
        return (rows, "jedi") if rows else (ast_definitions(root, name), "ast")

    filepath, line = Path(payload["filepath"]), payload["line"]
    rows = jedi_references(jedi, root, filepath, line, name) if jedi else None
    if rows:
        return rows, "jedi"
    return ast_references(root, filepath, line, name), "ast"


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} PAYLOAD.json", file=sys.stderr)
        return 2

    try:
        payload = json.loads(Path(sys.argv[1]).read_text())
        rows, _ = run(payload)
    except (OSError, json.JSONDecodeError, KeyError) as err:
        print(f"invalid payload: {err}", file=sys.stderr)
        return 1

    if not rows:
        print(f"no result for {payload['name']!r}", file=sys.stderr)
        return 1

    for path, lineno, text in rows:
        print(f"{path}:{lineno} {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
