from agent_core.sandbox.core import Sandbox, SandboxConfig


def main():
    config = SandboxConfig()
    sandbox = Sandbox(config)
    print(sandbox.execute("b'x' * (2**40)"))
    sandbox.close()


if __name__ == "__main__":
    main()
