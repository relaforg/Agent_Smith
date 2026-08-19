import os
import docker
import contextlib
import atexit
import signal
import sys
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
# c.start()


def _close() -> None:
    with contextlib.suppress(docker.errors.APIError):
        c.remove(force=True)


atexit.register(_close)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


@mcp.tool()
def read_file(filepath: str, start_line: int, end_line: int) -> str:
    """Read the content of a file with line numbers."""
    res = c.exec_run(["head", "-n", str(end_line), filepath, "|",
                     "tail", "-n", str(start_line)], demux=True)
    return "\n".join([
        f"{i + start_line}:\t{line}" for
        (i, line) in enumerate(res.output[0].
                               decode(errors="replace").splitlines())
    ]) if res.output[0] is not None \
        else res.output[1].decode(errors="replace") if res.output[1] is not None \
        else ""


@mcp.tool()
def edit_file(filepath: str, old_str: str, new_str: str):
    """Replace an exact string in a file with a new string."""
    ...


@mcp.tool()
def list_files(directory: str, pattern: str) -> List[str]:
    """List files in a directory matching a given pattern."""
    res = c.exec_run(["find", directory, "-maxdepth", "1",
                     "-name", pattern, "-type", "f"])
    return res.output.decode(errors="replace").splitlines() if res.output is not None else []


@mcp.tool()
def search_code(pattern: str, file_pattern: str) -> str:
    """Perform a grep-like search in the codebase."""
    ...


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


if __name__ == "__main__":
    print(list_files("/", "*"))
    # print(run_command("pwd", "/test"))
    print(read_file("/miniconda.sh", 5, 10))
    # mcp.run()
