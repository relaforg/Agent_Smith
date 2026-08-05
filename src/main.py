from agent_core.sandbox.core import Sandbox, SandboxConfig


def test():
    print("Hello external")


def main():
    config = SandboxConfig(max_execution_time_seconds=2)
    with Sandbox(config, {"test": test}) as sandbox:
        print(sandbox.execute("import pydantic"))
        print()


if __name__ == "__main__":
    main()
