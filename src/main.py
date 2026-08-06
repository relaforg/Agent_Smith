from agent_core.sandbox.core import Sandbox, SandboxConfig


def test():
    print("Hello external")


def main():
    config = SandboxConfig(max_execution_time_seconds=2,
                           allowed_directories=["/home/relaforg/Documents/Agent_Smith/tmp"])
    with Sandbox(config, {"test": test}) as sandbox:
        print(sandbox.execute("final_answer('cest bon cest fini')\nprint('test')"))
        print()


if __name__ == "__main__":
    main()
