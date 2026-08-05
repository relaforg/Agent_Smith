from agent_core.sandbox.core import Sandbox, SandboxConfig


def main():
    config = SandboxConfig()
    with Sandbox(config) as sandbox:
        print(sandbox.execute("print('Hello context')"))


if __name__ == "__main__":
    main()
