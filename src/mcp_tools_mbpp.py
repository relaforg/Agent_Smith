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

# Every container we create carries these labels, so a run that was
# killed before it could clean up can be swept on the next start.
LABEL = "mbpp-mcp"
LABELS = {LABEL: "mbpp", f"{LABEL}.pid": str(os.getpid())}

TASK = MBPPTaskInput.model_validate_json(Path(
    os.environ["MBPP_TASK_FILE"]).read_text()) if os.environ.get(
    "MBPP_TASK_FILE") else None

mcp = MCPServer("mbpp_mcp")

client = docker.from_env()
c = client.containers.run(
    "python:3.10", command="tail -f /dev/null", detach=True,
    network_disabled=True, mem_limit="128m",
    cpu_period=100000, cpu_quota=50000, labels=LABELS
)


def _close() -> None:
    with contextlib.suppress(docker.errors.APIError):
        c.remove(force=True)


atexit.register(_close)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


def put_file(container, path: str, content: str) -> None:
    data, buf = content.encode(), io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=os.path.basename(path))
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    container.put_archive(os.path.dirname(path) or "/", buf)


@mcp.tool()
def run_tests(code: str) -> str:
    if TASK is None:
        return "No task selected"
    put_file(c, "/payload.json", json.dumps({
        "source": code + "\n" + "\n".join(TASK.test_imports),
        "tests": TASK.test_list
    }))
    res = c.exec_run(["timeout", "-s", "KILL", "10",
                     "python", "/runner.py", "/payload.json"])
    return res.output.decode(errors="replace") if res.output is not None else ""


if __name__ == "__main__":
    put_file(c, "/runner.py", Path(__file__)
             .with_name("runner.py").read_text())
    mcp.run()
