import os
from agent_core.models import ExecutionResult, SandboxConfig
from pydantic import BaseModel, Field
from typing import Literal


class Packet(BaseModel):
    type: Literal["close", "tool_call", "result"] = Field(...)
    data: str | None = Field(default=None)


class Sandbox:
    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        parent_r, parent_w = os.pipe()
        child_r, child_w = os.pipe()
        self.pid = os.fork()
        print("Child pid: ", self.pid)

        if self.pid == 0:
            os.close(parent_r)
            os.close(child_w)
            self.to_parent = os.fdopen(parent_w, "w")
            self.from_parent = os.fdopen(child_r, "r")
            self._serve()
            os._exit(0)
        else:
            # os.waitpid(self.pid, 0)
            os.close(parent_w)
            os.close(child_r)
            self.to_child = os.fdopen(child_w, "w")
            self.from_child = os.fdopen(parent_r, "r")

    def _serve(self) -> None:
        while 1:
            packet: Packet = Packet.model_validate_json(
                self.from_parent.readline())
            if packet.type == "close":
                self.to_parent.close()
                self.from_parent.close()
                break

    def execute(self, code: str) -> ExecutionResult:
        pass

    def get_manual(self) -> str:
        return ""

    def close(self) -> None:
        self.to_child.write(Packet(type="close").model_dump_json() + "\n")
        self.to_child.close()
        self.from_child.close()
        os.waitpid(self.pid, 0)
