from agent_core.sandbox.core import Sandbox, SandboxConfig


def test():
    # from time import sleep
    # sleep(6)
    return 3434


def main():
    config = SandboxConfig()
    with Sandbox(config, {"test": test}) as sandbox:
        print(sandbox.execute("x = test()"))
        print()
        print(sandbox.execute("print(x)"))


if __name__ == "__main__":
    main()
