import os
import io
import contextlib
import traceback
import resource
import signal
import sys
import builtins
import re
from agent_core.models import ExecutionResult, SandboxConfig
from pydantic import BaseModel, Field
from typing import Literal, Dict, Callable, Any


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

    def _make_custom_import(self):
        def _import(name: str, globals=None, locals=None,
                    fromlist=(), level=0):
            full_name = name
            if fromlist is not None:
                full_name = name + "." + fromlist[0]
            for allowed in self.config.authorized_imports:
                if re.fullmatch(re.escape(allowed).replace(r"\*", ".*"),
                                full_name):
                    return builtins.__import__(name,
                                               globals,
                                               locals,
                                               fromlist,
                                               level)
            else:
                raise ImportError(
                    f"{full_name} is not available in the sandbox")
        return _import

    def _get_custom_builtins(self):
        builtin = dict(vars(builtins))
        builtin["__import__"] = self._make_custom_import()
        for key in ["eval", "exec", "compile", "input", "breakpoint"]:
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

        try:
            signal.setitimer(signal.ITIMER_REAL,
                             self.config.max_execution_time_seconds)
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                exec(code, self.namespace)
        except MemoryError:
            error, memory_exceeded = traceback.format_exc(), True
        except SandboxTimeoutError:
            error, timeout = traceback.format_exc(), True
        except Exception:
            error = traceback.format_exc()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        return ExecutionResult(
            stdout=out.getvalue(),
            stderr=err.getvalue(),
            error=error,
            memory_exceeded=memory_exceeded,
            timed_out=timeout
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
