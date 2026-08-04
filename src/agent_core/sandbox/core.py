import os
import io
import contextlib
import traceback
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
        parent_r, parent_w = os.pipe()
        child_r, child_w = os.pipe()
        self.pid = os.fork()
        print("Child pid: ", self.pid)

        if self.pid == 0:
            os.close(parent_r)
            os.close(child_w)
            self.tx, self.rx = os.fdopen(parent_w, "w", buffering=1), \
                os.fdopen(child_r, "r", buffering=1)
            self._serve()
            os._exit(0)
        else:
            os.close(parent_w)
            os.close(child_r)
            self.tx, self.rx = os.fdopen(child_w, "w", buffering=1), os.fdopen(
                parent_r, "r", buffering=1)

    def _serve(self) -> None:
        while 1:
            packet: Packet = Packet.model_validate_json(
                self.rx.readline())
            if packet.type == "close":
                self.tx.close()
                self.rx.close()
                break
            elif packet.type == "execute" and packet.data is not None:
                error = None
                out, err = io.StringIO(), io.StringIO()
                try:
                    with contextlib.redirect_stdout(out), \
                            contextlib.redirect_stderr(err):
                        exec(packet.data, self.namespace)
                except Exception:
                    error = traceback.format_exc()
                result = ExecutionResult(
                    stdout=out.getvalue(), stderr=err.getvalue(), error=error)
                self._send(
                    Packet(type="result", data=result.model_dump_json()))

    def _send(self, packet: Packet):
        self.tx.write(packet.model_dump_json() + "\n")

    def execute(self, code: str) -> ExecutionResult:
        self._send(Packet(type="execute", data=code))
        packet = Packet.model_validate_json(self.rx.readline())
        if packet.type != "result":
            raise IOError("Invalid packet type")
        return ExecutionResult.model_validate_json(packet.data)

    def get_manual(self) -> str:
        return ""

    def close(self) -> None:
        self._send(Packet(type="close"))
        self.tx.close()
        self.rx.close()
        os.waitpid(self.pid, 0)
