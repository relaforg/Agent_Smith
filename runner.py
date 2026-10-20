import ast
import contextlib
import json
import sys
import io
import traceback


def _actual_value(test: str, ns: dict) -> str:
    """For a failed `assert <expr> <op> <expected>`, re-evaluate <expr> so
    the agent sees what its function really returned, not only that the
    assertion failed. Empty when the test has another shape."""
    try:
        node = ast.parse(test).body[0]
        if not (isinstance(node, ast.Assert)
                and isinstance(node.test, ast.Compare)):
            return ""
        expr = ast.Expression(node.test.left)
        got = eval(compile(expr, "<test>", "eval"), ns)
    except BaseException:
        return ""
    return f"Actual value: {got!r}\n"


payload = json.loads(open(sys.argv[1]).read())
out, results, ns = io.StringIO(), [], {}
try:
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        exec(payload["source"], ns)
except BaseException:
    print(json.dumps({"loaded": False, "error":
                      traceback.format_exc(limit=1)}))
    sys.exit(0)

for t in payload["tests"]:
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            exec(t, ns)
        results.append({"test": t, "ok": True})
    except BaseException as e:
        error = traceback.format_exc(limit=1)
        if isinstance(e, AssertionError):
            # Redirected too: a print in the function would corrupt the JSON
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(out):
                error += _actual_value(t, ns)
        results.append({"test": t, "ok": False, "error": error})

print(json.dumps(
    {"loaded": True, "stdout": out.getvalue(), "results": results}))
