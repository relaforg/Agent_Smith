import os
from agent_core.models import ExecutionResult, SandboxConfig


class Sandbox:
    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self.pid = os.fork()

        if self.pid < 0:
            exit(self.pid)

    def execute(self, code: str) -> ExecutionResult:
        pass

    def get_manual(self) -> str:
        return ""

    def close(self) -> None:
        pass
