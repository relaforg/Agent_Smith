from agent_core.sandbox.core import Sandbox, SandboxConfig
from time import sleep


def main():
    config = SandboxConfig()
    sandbox = Sandbox(config)
    sleep(1)
    sandbox.close()


if __name__ == "__main__":
    main()
