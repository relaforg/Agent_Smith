import os
import io
import contextlib
import traceback
import resource
import signal
import sys
import builtins
import re
import types
import ast
import inspect
import time
from agent_core.models import ExecutionResult, SandboxConfig
from pydantic import BaseModel, Field
from typing import Literal, Dict, Callable, Any
from pathlib import Path


class Packet(BaseModel):
    type: Literal["close", "execute", "tool_call",
                  "result", "tool_result"] = Field(...)
    data: str | None = Field(default=None)


class ToolCallData(BaseModel):
    name: str = Field(...)
    args: tuple = Field(default_factory=tuple)
    kwargs: dict = Field(default_factory=dict)


class ToolResult(BaseModel):
    stdout: str = Field(default="")
    stderr: str = Field(default="")
    value: Any = Field(default=None)
    error_type: str | None = Field(default=None)
    error_message: str | None = Field(default=None)
    traceback: str | None = Field(default=None)


class SandboxTimeoutError(BaseException):
    pass


class FinalAnswer(BaseException):
    pass


class SandboxDied(Exception):
    pass


def _is_authorized_import(name: str, config: SandboxConfig) -> bool:
    return any(
        re.fullmatch(re.escape(pattern).replace(r"\*", ".*"), name)
        for pattern in config.authorized_imports
    )


def _is_authorized_file(file: str, config: SandboxConfig) -> bool:
    return any(
        Path(file).is_relative_to(root) for root in config.allowed_directories
    )


class _ModuleProxy:
    def __init__(self, module, config):
        object.__setattr__(self, "_ModuleProxy__mod", module)
        object.__setattr__(self, "_ModuleProxy__cfg", config)

    def __getattribute__(self, attr: str):
        if attr.startswith("_"):
            raise AttributeError(attr)
        mod = object.__getattribute__(self, "_ModuleProxy__mod")
        cfg = object.__getattribute__(self, "_ModuleProxy__cfg")
        value = getattr(mod, attr)
        if not isinstance(value, types.ModuleType):
            return value
        if not _is_authorized_import(value.__name__, cfg):
            raise ImportError(
                f"{value.__name__} is not available in the sandbox")
        return _ModuleProxy(value, cfg)

    def __setattr__(self, attr, value):
        raise AttributeError("modules are read-only in the sandbox")


class _AstGuard(ast.NodeVisitor):
    def __init__(self, config: SandboxConfig) -> None:
        self.config = config

    def visit_Attribute(self, node):
        if node.attr.startswith("__"):
            raise SyntaxError(f"Forbidden attribute: {node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id.startswith("__") and node.id != "__name__" \
                and node.id != "__main__":
            raise SyntaxError(f"Forbidden name: {node.id}")
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            if not _is_authorized_import(alias.name, self.config):
                raise SyntaxError(
                    f"{alias.name} is not available in the sandbox")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.level:
            raise SyntaxError("Relative imports are not available "
                              "in the sandbox")
        if not _is_authorized_import(node.module, self.config):
            raise SyntaxError(
                f"{node.module} is not available in the sandbox")
        for alias in node.names:
            if alias.name == "*":
                raise SyntaxError("Star imports are not available "
                                  "in the sandbox")
        self.generic_visit(node)


def _timeout_handler(signum, frame):
    raise SandboxTimeoutError("Sandbox timed out")


class Sandbox:
    def __init__(self, config: SandboxConfig,
                 tools: Dict[str, Callable] | None = None) -> None:
        self.tools = tools or {}
        self.config = config
        self._pid = None

    def __enter__(self) -> "Sandbox":
        parent_r, parent_w = os.pipe()
        child_r, child_w = os.pipe()
        self._pid = os.fork()

        if self._pid == 0:
            try:
                self.namespace = {n: self._make_proxy(n) for n in self.tools}
                self.namespace["__builtins__"] = self._get_custom_builtins()
                self.namespace["final_answer"] = self.final_answer
                signal.signal(signal.SIGALRM, _timeout_handler)
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                os.close(parent_r)
                os.close(child_w)
                self._tx, self._rx = os.fdopen(parent_w, "w", buffering=1), \
                    os.fdopen(child_r, "r", buffering=1)
                self._apply_limit()
                self._serve()
            except BaseException:
                traceback.print_exc()
                os._exit(1)
            finally:
                os._exit(0)
        else:
            # print("Child pid: ", self._pid)
            os.close(parent_w)
            os.close(child_r)
            self._tx, self._rx = os.fdopen(child_w, "w", buffering=1), \
                os.fdopen(parent_r, "r", buffering=1)
        return self

    def final_answer(self, answer: str) -> None:
        """Indicate the end of agentic loop"""
        raise FinalAnswer(answer)

    def _make_custom_import(self):
        def _import(name: str, globals=None, locals=None,
                    fromlist=(), level=0):
            if not _is_authorized_import(name, self.config):
                raise ImportError(
                    f"{name} is not available in the sandbox")
            return _ModuleProxy(
                builtins.__import__(name, globals, locals, fromlist, level),
                self.config)
        return _import

    def _make_custom_open(self):
        def _open(file, mode='r', buffering=-1, encoding=None, errors=None,
                  newline=None, closefd=True, opener=None):
            if isinstance(file, int):
                raise PermissionError(
                    "Cannot open int fd directly in the sandbox")
            if isinstance(file, bytes):
                raise PermissionError(
                    "Cannot open bytes directly in the sandbox")
            if not os.path.isabs(file):
                raise PermissionError(
                    "Only absolute path are accepted in the sandbox")
            file = os.path.realpath(file)
            if not _is_authorized_file(file, self.config):
                raise PermissionError(
                    f"{file} access is forbidden in sandbox")
            return builtins.open(file, mode, buffering, encoding,
                                 errors, newline, closefd, opener)
        return _open

    def _get_custom_builtins(self):
        builtin = dict(vars(builtins))
        builtin["__import__"] = self._make_custom_import()
        builtin["open"] = self._make_custom_open()
        for key in ["eval", "exec", "compile", "input", "breakpoint",
                    "getattr", "globals", "vars", "dir", "help", "exit",
                    "quit", "copyright", "credits", "license"]:
            builtin.pop(key, None)
        return builtin

    def _apply_limit(self):
        limit = self.config.max_memory_mb * 1024 * 1024
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))

    def _make_proxy(self, name: str):
        def proxy(*args, **kwargs):
            remaining_time, _ = signal.setitimer(signal.ITIMER_REAL, 0)
            if remaining_time <= 0:
                raise SandboxTimeoutError("Sandbox timed out")
            try:
                self._send(Packet(
                    type="tool_call",
                    data=ToolCallData(
                        name=name,
                        args=args,
                        kwargs=kwargs
                    ).model_dump_json()
                ))
                reply = Packet.model_validate_json(self._rx.readline())
                result = ToolResult.model_validate_json(reply.data)
                print(result.stdout, end="")
                print(result.stderr, end="", file=sys.stderr)
                if result.error_type is not None:
                    raise self._rebuild_error(result)
            finally:
                signal.setitimer(signal.ITIMER_REAL, remaining_time)
            return result.value
        return proxy

    def _rebuild_error(self, result: ToolResult) -> Exception:
        if result.error_type is None:
            return RuntimeError(result.error_message)
        cls = getattr(builtins, result.error_type, None)
        if not (isinstance(cls, type) and issubclass(cls, Exception)):
            cls = RuntimeError
        return cls(result.error_message)

    def _exec_code(self, code: str) -> ExecutionResult:
        error = None
        memory_exceeded = False
        timeout = False
        out, err = io.StringIO(), io.StringIO()
        final_answer = None

        try:
            signal.setitimer(signal.ITIMER_REAL,
                             self.config.max_execution_time_seconds)
            tree = ast.parse(code)
            _AstGuard(self.config).visit(tree)
            bytecode = compile(tree, "<sandbox>", "exec")
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                signal.signal(signal.SIGINT, signal.default_int_handler)
                try:
                    exec(bytecode, self.namespace)
                finally:
                    signal.signal(signal.SIGINT, signal.SIG_IGN)
        except MemoryError:
            error, memory_exceeded = traceback.format_exc(), True
        except SandboxTimeoutError:
            error, timeout = traceback.format_exc(), True
        except (KeyboardInterrupt, SystemExit):
            error = traceback.format_exc()
        except FinalAnswer as e:
            final_answer = e.__str__()
        except Exception:
            error = traceback.format_exc()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        return ExecutionResult(
            stdout=out.getvalue(),
            stderr=err.getvalue(),
            error=error,
            memory_exceeded=memory_exceeded,
            timed_out=timeout,
            final_answer=final_answer
        )

    def _serve(self) -> None:
        while 1:
            line = self._rx.readline()
            if not line:
                break
            packet: Packet = Packet.model_validate_json(line)
            if packet.type == "close":
                self._tx.close()
                self._rx.close()
                break
            elif packet.type == "execute":
                result = self._exec_code(packet.data) if packet.data is not None else ExecutionResult(
                    error="empty execute packet")
                self._send(
                    Packet(type="result", data=result.model_dump_json()))
            else:
                self._send(Packet(type="result", data=ExecutionResult(
                    error="invalid packet type").model_dump_json()))

    def _send(self, packet: Packet):
        try:
            self._tx.write(packet.model_dump_json() + "\n")
        except (BrokenPipeError, ValueError) as e:
            raise SandboxDied("sandbox pipe is closed") from e

    def _recv(self) -> Packet:
        line = self._rx.readline()
        if not line:
            raise SandboxDied("sandbox process terminated unexpectedly")
        return Packet.model_validate_json(line)

    def _run_tool(self, data: ToolCallData) -> ToolResult:
        f = self.tools.get(data.name)
        if f is None:
            return ToolResult(
                error_type="NameError",
                error_message=f"{data.name} is not a known tool",
            )

        out, err = io.StringIO(), io.StringIO()

        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                value = f(*data.args, **data.kwargs)
        except BaseException as e:
            return ToolResult(
                stdout=out.getvalue(),
                stderr=err.getvalue(),
                error_type=type(e).__name__,
                error_message=str(e),
                traceback=traceback.format_exc()
            )

        return ToolResult(
            stdout=out.getvalue(),
            stderr=err.getvalue(),
            value=value
        )

    def execute(self, code: str) -> ExecutionResult:
        if self._pid is None:
            raise RuntimeError(
                "Sandbox must be used as `with Sandbox(cfg) as sb:`")
        self._send(Packet(type="execute", data=code))
        while 1:
            packet = self._recv()
            if packet.type == "result":
                if packet.data is None:
                    raise SandboxDied("sandbox returned an empty result")
                return ExecutionResult.model_validate_json(packet.data)
            if packet.type != "tool_call" or packet.data is None:
                raise SandboxDied(f"unexpected packet type: {packet.type}")
            self._send(Packet(type="tool_result",
                              data=self._answer_tool_call(packet.data)))

    def _answer_tool_call(self, data: str | None) -> str:
        try:
            if data is None:
                raise ValueError("malformed tool_call packet")
            result = self._run_tool(ToolCallData.model_validate_json(data))
        except Exception as e:
            result = ToolResult(error_type=type(
                e).__name__, error_message=str(e))

        try:
            return result.model_dump_json()
        except Exception as e:
            return ToolResult(
                error_type="TypeError",
                error_message=f"tool result is not serializable: {e}"
            ).model_dump_json()

    def _describe(self, f: Callable) -> str:
        description = {
            "name": f.__name__,
            "signature": inspect.signature(f).__str__(),
            "documentation": inspect.getdoc(f) or "(no documentation)"
        }
        return description.__str__()

    def get_manual(self) -> str:
        manual = [self._describe(f) for f in self.tools.values()]
        manual.append(self._describe(self.final_answer))
        return "\n".join(manual)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._pid is None:
            return
        with contextlib.suppress(SandboxDied):
            self._send(Packet(type="close"))
        for pipe in (self._tx, self._rx):
            with contextlib.suppress(OSError):
                pipe.close()
        self._reap()

    def _reap(self, grace: float = 5.0) -> None:
        if self._pid is None:
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                pid, _ = os.waitpid(self._pid, os.WNOHANG)
            except ChildProcessError:
                break
            if pid:
                break
            time.sleep(0.05)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.kill(self._pid, signal.SIGQUIT)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(self._pid, 0)
        self._pid = None
