import os
import io
import contextlib
import traceback
import resource
from agent_core.models import ExecutionResult, SandboxConfig
from pydantic import BaseModel, Field
from typing import Literal, Dict, Callable


class Packet(BaseModel):
    type: Literal["close", "execute", "tool_call", "result"] = Field(...)
    data: str | None = Field(default=None)


class Sandbox:
    def __init__(self, config: SandboxConfig) -> None:
        self.namespace: Dict[str, Callable] = {}
        self.config = config

    def __enter__(self) -> "Sandbox":
        parent_r, parent_w = os.pipe()
        child_r, child_w = os.pipe()
        self._pid = os.fork()
        print("Child pid: ", self._pid)

        if self._pid == 0:
            os.close(parent_r)
            os.close(child_w)
            self._tx, self._rx = os.fdopen(parent_w, "w", buffering=1), \
                os.fdopen(child_r, "r", buffering=1)
            self._apply_limit()
            self._serve()
            os._exit(0)
        else:
            os.close(parent_w)
            os.close(child_r)
            self._tx, self._rx = os.fdopen(child_w, "w", buffering=1), \
                os.fdopen(parent_r, "r", buffering=1)
        return self

    def _apply_limit(self):
        limit = self.config.max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    def _exec_code(self, code: str) -> ExecutionResult:
        error = None
        memory_exceeded = False
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                exec(code, self.namespace)
        except MemoryError:
            error, memory_exceeded = traceback.format_exc(), True
        except Exception:
            error = traceback.format_exc()
        return ExecutionResult(
            stdout=out.getvalue(),
            stderr=err.getvalue(),
            error=error,
            memory_exceeded=memory_exceeded
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

    def execute(self, code: str) -> ExecutionResult:
        if self._pid is None:
            raise RuntimeError(
                "Sandbox must be used as `with Sandbox(cfg) as sb:`")
        self._send(Packet(type="execute", data=code))
        packet = Packet.model_validate_json(self._rx.readline())
        if packet.type != "result":
            raise IOError("Invalid packet type")
        return ExecutionResult.model_validate_json(packet.data)

    def get_manual(self) -> str:
        return ""

    def __exit__(self, exc_type, exc, tb) -> None:
        with contextlib.suppress(BrokenPipeError):
            self._send(Packet(type="close"))
        with contextlib.suppress(BrokenPipeError):
            self._tx.close()
        self._rx.close()
        os.waitpid(self._pid, 0)
