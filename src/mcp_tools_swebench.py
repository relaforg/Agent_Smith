import os
import io
import sys
import json
import shlex
import shutil
import atexit
import signal
import tarfile
import tempfile
import subprocess
import contextlib
import docker
from mcp.server import MCPServer
from typing import List, Dict, Optional, Tuple
from pathlib import Path


LABEL = "swebench-mcp"
LABELS = {LABEL: "swebench", f"{LABEL}.pid": str(os.getgid())}

DOCKER_IMAGE = os.environ.get("DOCKER_IMAGE", "python:3.10")
TESTBED_PATH = os.environ.get("TESTBED_PATH")
EVAL_SCRIPT = os.environ.get("EVAL_SCRIPT")

mcp = MCPServer("swebench_mcp")


def _dec(raw: Optional[bytes]) -> str:
    return raw.decode(errors="replace") if raw else ""


class DockerBackend:
    """Runs every command inside a throwaway container."""

    def __init__(self, image: str) -> None:
        self.root = "/"
        self.python = "python"
        self.scratch = "/"
        self.refs_script = "/refs.py"
        self.client = docker.from_env()
        self.container = self.client.containers.run(
            image=image, command="tail -f /dev/null", detach=True,
            network_disabled=True, labels=LABELS
        )
        self.put_file(self.refs_script,
                      Path(__file__).with_name("refs.py").read_text())

    def exec(self, cmd, workdir: Optional[str] = None) -> Tuple[int, str, str]:
        res = self.container.exec_run(cmd, demux=True, workdir=workdir)
        stdout, stderr = res.output
        return res.exit_code, _dec(stdout), _dec(stderr)

    def put_file(self, path: str, content: str) -> None:
        data, buf = content.encode(), io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=os.path.basename(path))
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        self.container.put_archive(os.path.dirname(path) or "/", buf)

    def close(self) -> None:
        with contextlib.suppress(docker.errors.APIError):
            self.container.remove(force=True)


class LocalBackend:
    """Runs every command directly in TESTBED_PATH, no container involved.

    Commands run with the testbed as working directory, so relative paths
    resolve against it; absolute paths are left untouched.
    """

    def __init__(self, path: str) -> None:
        self.python = sys.executable
        self.root = str(Path(path).resolve())
        self.scratch = tempfile.mkdtemp(prefix="swebench-mcp-")
        self.refs_script = str(Path(__file__).with_name("refs.py"))

    def exec(self, cmd, workdir: Optional[str] = None) -> Tuple[int, str, str]:
        # docker-py splits a string command for us; subprocess does not.
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        try:
            res = subprocess.run(cmd, cwd=workdir or self.root,
                                 capture_output=True, text=True,
                                 errors="replace")
        except OSError as err:
            return 127, "", str(err)
        return res.returncode, res.stdout, res.stderr

    def put_file(self, path: str, content: str) -> None:
        Path(path if os.path.isabs(path)
             else os.path.join(self.root, path)).write_text(content)

    def close(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)


backend = LocalBackend(TESTBED_PATH) if TESTBED_PATH \
    else DockerBackend(DOCKER_IMAGE)

atexit.register(backend.close)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


@mcp.tool()
def read_file(filepath: str, start_line: int, end_line: int) -> str:
    """Read the content of a file with line numbers."""
    if start_line > end_line:
        return "Invalid range"
    _, stdout, stderr = backend.exec(
        ["sed", "-n", f"{start_line},{end_line}p", filepath])
    return "\n".join([
        f"{i + start_line}: {line}"
        for (i, line) in enumerate(stdout.splitlines())
    ]) if stdout else stderr


@mcp.tool()
def edit_file(filepath: str, old_str: str, new_str: str):
    """Replace an exact string in a file with a new string."""
    _, content, _ = backend.exec(["cat", filepath])
    if not content:
        return
    backend.put_file(filepath, content.replace(old_str, new_str))


@mcp.tool()
def list_files(directory: str, pattern: str) -> List[str]:
    """List files in a directory matching a given pattern."""
    _, stdout, _ = backend.exec(["find", directory, "-maxdepth", "1",
                                 "-name", pattern, "-type", "f"])
    return stdout.splitlines()


@mcp.tool()
def search_code(pattern: str, file_pattern: str) -> str:
    """Perform a grep-like search in the codebase."""
    _, stdout, _ = backend.exec(["grep", "-rnIs", pattern,
                                 f"--include={file_pattern}", backend.root])
    return stdout


@mcp.tool()
def search_function_or_class_definition_in_code(name: str) -> str:
    """Find the definition of a function or a class."""
    _, stdout, _ = backend.exec(["grep", "-rnIs", f"def {name}", "-o",
                                 f"class {name}", "--include=*.py",
                                 backend.root])
    return stdout


@mcp.tool()
def find_references(name: str, filepath: str, line: int) -> str:
    """Find all usages of a symbol (function or class)."""
    payload = os.path.join(backend.scratch, "payload.json")
    backend.put_file(payload, json.dumps({
        "name": name,
        "filepath": filepath,
        "line": line,
        # refs.py walks `root` looking for importers; "/" in the container,
        # the testbed only when we run on the host.
        "root": backend.root
    }))
    _, stdout, stderr = backend.exec(["timeout", "-s", "KILL", "10",
                                      backend.python, backend.refs_script,
                                      payload])
    return stdout + stderr


@mcp.tool()
def run_tests() -> str:
    """Execute the evaluation script."""
    if not EVAL_SCRIPT:
        return "No evaluation script configured"
    # EVAL_SCRIPT holds the script itself, not a path: hence `bash -c`.
    _, stdout, stderr = backend.exec(["bash", "-c", EVAL_SCRIPT])
    return stdout + stderr


@mcp.tool()
def get_patch():
    """Retrieve the unified git diff of all changes made to the repository,
    depending on the implementation"""
    backend.exec(["git", "-c", "core.fileMode=false", "add", "-N", "."])
    _, stdout, _ = backend.exec(["git", "-c", "core.fileMode=false", "diff"])
    return stdout


@mcp.tool()
def run_command(command, workdir) -> Dict[str, str | int]:
    """Execute a shell command in the specified working directory.
    Returns the command’s stdout, stderr, and exit code."""
    exit_code, stdout, stderr = backend.exec(command, workdir=workdir)
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code
    }


if __name__ == "__main__":
    # print(list_files("/", "*"))
    # print(run_command("pwd", "/test"))
    # print(read_file("/miniconda.sh", 10, 10))
    # print(run_command("touch /testbed/test.tmp", "/"))
    # edit_file("/test", "Hello", "Goodbye")
    # print(run_command("cat test", "/"))
    # print(search_code("Hello", "tes"))
    # print(search_function_or_class_definition_in_code("test"))
    # print(find_references("enclosing_scope", "/refs.py", 48))
    # print(run_tests())
    # print(get_patch())
    mcp.run()
