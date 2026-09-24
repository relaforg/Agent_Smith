import contextlib
import json
import sys
import io
import traceback


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
    except BaseException:
        results.append(
            {"test": t, "ok": False, "error": traceback.format_exc(limit=1)})

print(json.dumps(
    {"loaded": True, "stdout": out.getvalue(), "results": results}))
