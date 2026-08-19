import os
import docker
import contextlib
import atexit
import signal
import sys
import tarfile
import io
from mcp.server import MCPServer
from typing import List, Dict
from agent_core.models import SWEBenchTaskInput
from pathlib import Path


LABEL = "swebench-mcp"
LABELS = {LABEL: "swebench", f"{LABEL}.pid": str(os.getgid())}

TASK = SWEBenchTaskInput.model_validate_json(
    Path(os.environ["SWEBENCH_TASK_FILE"]).read_text()
) if os.environ.get("SWEBENCH_TASK_FILE") else None

mcp = MCPServer("swebench_mcp")

client = docker.from_env()

c = client.containers.run(
    image=TASK.docker_image if TASK is not None else "python:3.10",
    command="tail -f /dev/null", detach=True,
    network_disabled=True, mem_limit="128m",
    cpu_period=100000, cpu_quota=50000, labels=LABELS
)


def _close() -> None:
    with contextlib.suppress(docker.errors.APIError):
        c.remove(force=True)


atexit.register(_close)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


@mcp.tool()
def read_file(filepath: str, start_line: int, end_line: int) -> str:
    """Read the content of a file with line numbers."""
    if start_line > end_line:
        return "Invalid range"
    res = c.exec_run(["head", "-n", str(end_line), filepath, "|",
                     "tail", "-n", str(end_line - start_line)], demux=True)
    return "\n".join([
        f"{i + start_line}: {line}" for
        (i, line) in enumerate(res.output[0].
                               decode(errors="replace").splitlines())
    ]) if res.output[0] is not None \
        else res.output[1].decode(errors="replace") if res.output[1] is not None \
        else ""


@mcp.tool()
def edit_file(filepath: str, old_str: str, new_str: str):
    """Replace an exact string in a file with a new string."""
    res = c.exec_run(["cat", filepath], demux=True)
    if res.output[0] is None:
        return
    content: str = res.output[0].decode(errors="replace")
    content = content.replace(old_str, new_str)
    put_file(c, filepath, content)


@mcp.tool()
def list_files(directory: str, pattern: str) -> List[str]:
    """List files in a directory matching a given pattern."""
    res = c.exec_run(["find", directory, "-maxdepth", "1",
                     "-name", pattern, "-type", "f"], demux=True)
    return res.output[0].decode(errors="replace").splitlines() if res.output[0] is not None else []


@mcp.tool()
def search_code(pattern: str, file_pattern: str) -> str:
    """Perform a grep-like search in the codebase."""
    res = c.exec_run(["find", "/", "-type", "d", "!", "-readable", "-prune",
                      "-o", "-type", "f", "-name", file_pattern, "-print"],
                     demux=True)
    files = res.output[0].decode(errors="replace").splitlines(
    ) if res.output[0] is not None else []

    out = []
    for file in files:
        res = c.exec_run(["grep", "-n", pattern, file], demux=True)
        if res.output[0] is None or res.exit_code != 0:
            continue
        out.extend(
            [f"{file}:{line}" for line in res.output[0].decode(errors="replace").splitlines()])
    return "\n".join(out)


@mcp.tool()
def search_function_or_class_definition_in_code(name: str) -> str:
    """Find the definition of a function or a class."""
    ...


@mcp.tool()
def find_references(name: str, filepath: str, line: int) -> str:
    """Find all usages of a symbol (function or class)."""
    ...


@mcp.tool()
def run_tests():
    """Execute the evaluation script."""
    return "dfdfdf"


@mcp.tool()
def get_patch():
    """Retrieve the unified git diff of all changes made to the repository,
    depending on the implementation"""
    ...


@mcp.tool()
def run_command(command, workdir) -> Dict[str, str]:
    """Execute a shell command in the specified working directory.
    Returns the command’s stdout, stderr, and exit code."""
    res = c.exec_run(command, demux=True, workdir=workdir)
    return {
        "stdout": res.output[0].decode(errors="replace") if res.output[0] is not None else "",
        "stderr": res.output[1].decode(errors="replace") if res.output[1] is not None else "",
        "exit_code": res.exit_code
    }


def put_file(container, path: str, content: str) -> None:
    data, buf = content.encode(), io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=os.path.basename(path))
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    container.put_archive(os.path.dirname(path) or "/", buf)


if __name__ == "__main__":
    put_file(c, "/test", Path("tmp/test").read_text())
    # print(list_files("/", "*"))
    # print(run_command("pwd", "/test"))
    # print(read_file("/miniconda.sh", 10, 10))
    # print(run_command("cat test", "/"))
    # edit_file("/test", "Hello", "Goodbye")
    # print(run_command("cat test", "/"))
    print(search_code("Hello", "test"))
    # mcp.run()
