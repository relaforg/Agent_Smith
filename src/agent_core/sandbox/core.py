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

    def __enter__(self) -> "Sandbox":
        parent_r, parent_w = os.pipe()
        child_r, child_w = os.pipe()
        self._pid = os.fork()

        if self._pid == 0:
            self.namespace = {n: self._make_proxy(n) for n in self.tools}
            self.namespace["__builtins__"] = self._get_custom_builtins()
            self.namespace["final_answer"] = self._final_answer
            signal.signal(signal.SIGALRM, _timeout_handler)
            os.close(parent_r)
            os.close(child_w)
            self._tx, self._rx = os.fdopen(parent_w, "w", buffering=1), \
                os.fdopen(child_r, "r", buffering=1)
            self._apply_limit()
            self._serve()
            os._exit(0)
        else:
            print("Child pid: ", self._pid)
            os.close(parent_w)
            os.close(child_r)
            self._tx, self._rx = os.fdopen(child_w, "w", buffering=1), \
                os.fdopen(parent_r, "r", buffering=1)
        return self

    def _final_answer(self, answer: str) -> None:
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
            builtin.pop(key)
        return builtin

    def _apply_limit(self):
        limit = self.config.max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

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
                exec(bytecode, self.namespace)
        except MemoryError:
            error, memory_exceeded = traceback.format_exc(), True
        except SandboxTimeoutError:
            error, timeout = traceback.format_exc(), True
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
            packet: Packet = Packet.model_validate_json(
                self._rx.readline())
            if packet.type == "close":
                self._tx.close()
                self._rx.close()
                break
            elif packet.type == "execute" and packet.data is not None:
                result = self._exec_code(packet.data)
                self._send(
                    Packet(type="result", data=result.model_dump_json()))

    def _send(self, packet: Packet):
        self._tx.write(packet.model_dump_json() + "\n")

    def _run_tool(self, data: ToolCallData) -> ToolResult:
        f = self.tools.get(data.name)
        if f is None:
            e = NameError(data.name, "does not exists")
            return ToolResult(
                error_type=type(e).__name__,
                error_message=str(e),
                traceback=traceback.format_exc()
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
            packet = Packet.model_validate_json(self._rx.readline())
            if packet.type == "result":
                return ExecutionResult.model_validate_json(packet.data)
            elif packet.type == "tool_call":
                if packet.data is None:
                    self._send(Packet(type="tool_result"))
                    continue
                self._send(Packet(
                    type="tool_result",
                           data=self._run_tool(
                               ToolCallData.model_validate_json(packet.data)
                           ).model_dump_json()
                           ))

    def get_manual(self) -> str:
        return ""

    def __exit__(self, exc_type, exc, tb) -> None:
        with contextlib.suppress(BrokenPipeError):
            self._send(Packet(type="close"))
        with contextlib.suppress(BrokenPipeError):
            self._tx.close()
        self._rx.close()
        os.waitpid(self._pid, 0)
