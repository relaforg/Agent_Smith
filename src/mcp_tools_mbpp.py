import os
import tarfile
import io
import atexit
import contextlib
import signal
import sys
import docker
import json
from mcp.server import MCPServer
from agent_core.models import MBPPTaskInput
from pathlib import Path
from typing import List

LABEL = "mbpp-mcp"
LABELS = {LABEL: "mbpp", f"{LABEL}.pid": str(os.getpid())}

mcp = MCPServer("mbpp_mcp")

client = docker.from_env()
c = client.containers.run(
    "python:3.10", command="tail -f /dev/null", detach=True,
    network_disabled=True, labels=LABELS
)


def put_file(container, path: str, content: str) -> None:
    data, buf = content.encode(), io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=os.path.basename(path))
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    container.put_archive(os.path.dirname(path) or "/", buf)

def _close() -> None:
    with contextlib.suppress(docker.errors.APIError):
        c.remove(force=True)


atexit.register(_close)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))




def _result(success: bool, output: str) -> str:
    return json.dumps({"success": success, "output": output})


@mcp.tool()
def run_tests(code: str, test_list: List[str]) -> str:
    """Run a candidate solution against the given test assertions.

    Returns a JSON string with a `success` boolean, true when every
    assertion passed, and an `output` field describing what happened.
    """
    put_file(c, "/payload.json", json.dumps({
        "source": code,
        "tests": test_list
    }))
    res = c.exec_run(["timeout", "-s", "KILL", "10",
                     "python", "/runner.py", "/payload.json"])
    raw = res.output.decode(errors="replace") if res.output is not None else ""

    try:
        report = json.loads(raw)
        loaded = report["loaded"]
    except (json.JSONDecodeError, TypeError, KeyError):
        # The runner never printed its report: the 10s timeout killed it
        # with SIGKILL (no output at all), or the container is broken.
        return _result(False, raw.strip() or
                       f"the test runner produced no output "
                       f"(exit code {res.exit_code})")

    if not loaded:
        return _result(False, "the solution could not be loaded:\n"
                              f"{report.get('error', '')}")

    results = report.get("results", [])
    failed = [r for r in results if not r["ok"]]
    lines = [
        f"{len(results) - len(failed)}/{len(results)} assertion(s) passed"]
    lines += [f"{r['test']}\n{r['error']}" for r in failed]
    if report.get("stdout"):
        lines.append(f"--- stdout ---\n{report['stdout']}")
    return _result(not failed, "\n".join(lines))


if __name__ == "__main__":
    put_file(c, "/runner.py", Path(__file__)
             .with_name("runner.py").read_text())
    mcp.run()
