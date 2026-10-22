"""List every function and method in openfusion/ that the test suite never runs.

    python -m pytest --cov=openfusion --cov-report= && python tools/function_coverage.py

Exits 1 if any function was never entered. A function counts as tested when at least one
line of its body ran. `# pragma: no cover` on the def line excludes it (used for code
that only runs inside the macOS app, e.g. Cocoa callbacks).
"""
from __future__ import annotations

import ast
import pathlib
import sys

import coverage

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "openfusion"


def functions(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    src = path.read_text(encoding="utf-8").splitlines()

    def walk(node, prefix=""):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{ch.name}"
                if "pragma: no cover" not in src[ch.lineno - 1]:
                    body = [n for n in ch.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                                        and isinstance(n.value.value, str))] or ch.body
                    lines = set()
                    for n in body:
                        lines.update(range(n.lineno, (n.end_lineno or n.lineno) + 1))
                    yield name, ch.lineno, lines
                yield from walk(ch, name + ".")
            elif isinstance(ch, ast.ClassDef):
                yield from walk(ch, f"{prefix}{ch.name}.")
    yield from walk(tree)


def main() -> int:
    cov = coverage.Coverage(data_file=str(ROOT / ".coverage"))
    cov.load()
    data = cov.get_data()
    missing, total = [], 0
    for f in sorted(PKG.rglob("*.py")):
        ran = set(data.lines(str(f)) or [])
        for name, line, body in functions(f):
            total += 1
            if not (ran & body):
                missing.append(f"{f.relative_to(ROOT)}:{line}  {name}")
    print(f"{total - len(missing)} of {total} functions run by the tests")
    for m in missing:
        print("  never run:", m)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
