import platform
import readline
from agent_core.sandbox.core import Sandbox, SandboxConfig

HISTORY_FILE = ".agent_smith_history"


def main():
    config = SandboxConfig(max_execution_time_seconds=2,
                           allowed_directories=["/home/relaforg/Documents/Agent_Smith/tmp"])

    # readline.parse_and_bind("tab: complete")
    readline.set_history_length(1000)
    try:
        readline.read_history_file(HISTORY_FILE)
    except FileNotFoundError:
        pass
    print(f"Sandbox REPL (python {platform.python_version()})")
    with Sandbox(config, {}) as sandbox:
        try:
            while True:
                code = input(">>> ")
                if code == "exit":
                    return 0
                print(sandbox.execute(code))
        except KeyboardInterrupt:
            return 130
        except EOFError:
            return 0
        finally:
            readline.write_history_file(HISTORY_FILE)
