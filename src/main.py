from agent_core.sandbox.core import Sandbox, SandboxConfig


def main():
    config = SandboxConfig()
    sandbox = Sandbox(config)
    print(sandbox.execute("x = 23232"))
    print(sandbox.execute("print(x)"))
    sandbox.close()


if __name__ == "__main__":
    main()
