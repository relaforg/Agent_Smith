"""Find every reference to a symbol identified by (filepath, line, name).

Reads a JSON payload {"filepath", "name", "line"} and prints one line per
reference in grep -n format:  path:lineno:source line
"""
import ast
import json
import sys
from pathlib import Path

FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPE_NODES = FUNC_NODES + (ast.ClassDef,)
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
             ".tox", ".mypy_cache", "build", "dist"}


def parse_file(path: Path) -> tuple[str, ast.Module] | None:
    """Return (source, tree), or None if the file cannot be read or parsed."""
    try:
        source = path.read_text(encoding="utf-8")
        return source, ast.parse(source, filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None


def find_symbol(tree: ast.Module, name: str, line: int) -> ast.AST | None:
    """Locate the node that `line` points at, preferring definitions.

    (filepath, line) is a coordinate: it disambiguates which of the possibly
    many symbols called `name` the caller means.
    """
    candidates: list[tuple[int, ast.AST]] = []
    for node in ast.walk(tree):
        if getattr(node, "lineno", None) != line:
            continue
        match node:
            case ast.FunctionDef(name=n) | ast.AsyncFunctionDef(name=n) | ast.ClassDef(name=n) if n == name:
                candidates.append((0, node))
            case ast.arg(arg=n) if n == name:
                candidates.append((1, node))
            case ast.Name(id=n, ctx=ast.Store()) if n == name:
                candidates.append((2, node))
            case ast.Name(id=n) if n == name:
                candidates.append((3, node))
    return min(candidates, key=lambda c: c[0])[1] if candidates else None


def enclosing_scope(tree: ast.Module, target: ast.AST) -> ast.AST | None:
    """Return the innermost function/class containing `target`, or None."""
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, SCOPE_NODES) or node is target:
            continue
        if node.lineno <= target.lineno <= node.end_lineno:
            if best is None or node.lineno > best.lineno:
                best = node
    return best


def binds_name(scope: ast.AST, name: str) -> bool:
    """Python's rule: a name is local to a function if it is bound anywhere in it.

    A name that is merely read inside a function is a free variable coming from
    an outer scope, so pointing at such a use must not restrict the search.
    """
    bound = False
    for node in ast.walk(scope):
        match node:
            case ast.Global(names=ns) | ast.Nonlocal(names=ns) if name in ns:
                return False          # explicit declaration always wins
            case ast.arg(arg=n) if n == name:
                bound = True
            case ast.Name(id=n, ctx=ast.Store() | ast.Del()) if n == name:
                bound = True
            case ast.FunctionDef(name=n) | ast.AsyncFunctionDef(name=n) | ast.ClassDef(name=n) if n == name:
                bound = True
    return bound


def iter_refs(tree: ast.AST, name: str):
    """Yield every node referencing `name` in `tree`."""
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=n) if n == name:
                yield node
            case ast.Attribute(attr=a) if a == name:
                yield node
            case ast.FunctionDef(name=n) | ast.AsyncFunctionDef(name=n) | ast.ClassDef(name=n) if n == name:
                yield node
            case ast.arg(arg=n) if n == name:
                yield node
            case ast.Import(names=aliases) | ast.ImportFrom(names=aliases):
                for a in aliases:
                    if name in (a.name, a.asname):
                        yield node
            case ast.Global(names=names) | ast.Nonlocal(names=names) if name in names:
                yield node


def imports_symbol(tree: ast.Module, name: str, modname: str) -> bool:
    """Heuristic: does this file import `name`, or the module defining it?"""
    for node in ast.walk(tree):
        match node:
            case ast.ImportFrom(names=aliases):
                for a in aliases:
                    if a.name in ("*", name) or a.asname == name:
                        return True
                if node.module and node.module.split(".")[-1] == modname:
                    return True
            case ast.Import(names=aliases):
                for a in aliases:
                    if modname in a.name.split("."):
                        return True
    return False


def iter_python_files(root: Path):
    """Walk `root`, skipping vendored and generated directories."""
    for path in root.rglob("*.py"):
        if SKIP_DIRS.isdisjoint(path.parts):
            yield path


def report(path: Path, source: str, nodes) -> list[tuple[str, int, str]]:
    """Turn nodes into deduplicated (path, lineno, text) rows, grep -n style."""
    lines = source.splitlines()
    return [(str(path), lineno, lines[lineno - 1])
            for lineno in sorted({n.lineno for n in nodes})]


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} PAYLOAD.json", file=sys.stderr)
        return 2

    try:
        payload = json.loads(Path(sys.argv[1]).read_text())
        filepath = Path(payload["filepath"])
        name = payload["name"]
        line = payload["line"]
    except (OSError, json.JSONDecodeError, KeyError) as err:
        print(f"invalid payload: {err}", file=sys.stderr)
        return 1

    root = Path(payload.get("root", "/"))

    parsed = parse_file(filepath)
    if parsed is None:
        print(f"{filepath}: cannot read or parse", file=sys.stderr)
        return 1
    source, tree = parsed

    src = find_symbol(tree, name, line)
    if src is None:
        print(f"{filepath}:{line}: no symbol named {name!r} here", file=sys.stderr)
        return 1

    # A symbol *bound* in a function cannot be referenced anywhere else, so the
    # search stays inside it. A name merely read there belongs to an outer
    # scope and may travel via imports, like any module-level symbol.
    scope = enclosing_scope(tree, src)
    if isinstance(scope, FUNC_NODES) and binds_name(scope, name):
        rows = report(filepath, source, iter_refs(scope, name))
    else:
        rows = report(filepath, source, iter_refs(tree, name))
        modname = filepath.stem
        for other in iter_python_files(root):
            if other.resolve() == filepath.resolve():
                continue
            # Cheap textual pre-filter first: parsing every file is expensive.
            try:
                other_source = other.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if name not in other_source:
                continue
            try:
                other_tree = ast.parse(other_source, filename=str(other))
            except (SyntaxError, ValueError):
                continue
            if not imports_symbol(other_tree, name, modname):
                continue
            rows += report(other, other_source, iter_refs(other_tree, name))

    for path, lineno, text in rows:
        print(f"{path}:{lineno}:{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
