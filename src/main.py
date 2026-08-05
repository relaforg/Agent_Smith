from agent_core.sandbox.core import Sandbox, SandboxConfig


def test():
    from time import sleep
    sleep(6)


def main():
    config = SandboxConfig(max_execution_time_seconds=2)
    with Sandbox(config, {"test": test}) as sandbox:
        print(sandbox.execute("test()"))
        print()


if __name__ == "__main__":
    main()
