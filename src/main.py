from agent_core.sandbox.core import Sandbox, SandboxConfig


def main():
    config = SandboxConfig()
    with Sandbox(config) as sandbox:
        sandbox.execute("x=86548765")
        print(sandbox.execute("print(x)"))


if __name__ == "__main__":
    main()
