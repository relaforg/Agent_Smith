import platform
import readline
import json
import shlex
import anyio
import inspect
import contextlib
import signal
import sys
from argparse import ArgumentParser
from agent_core.sandbox.core import Sandbox, SandboxConfig, SandboxDied
from mcp import Client, stdio_client, StdioServerParameters, types
from mcp.client import Transport
from typing import Callable
from pydantic import ValidationError

HISTORY_FILE = ".agent_smith_history"


def _format_validation_error(path: str, e: ValidationError) -> str:
    lines = [f"{path}: {e.error_count()} invalid field(s)"]
    for err in e.errors(include_url=False):
        loc = ".".join(str(part) for part in err["loc"]) or "(root)"
        got = repr(err["input"])
        if len(got) > 80:
            got = got[:77] + "..."
        lines.append(f"\t{loc}: {err['msg']} (got: {got})")
    return "\n".join(lines)


def extract_config(path: str) -> SandboxConfig | None:
    try:
        with open(path, "r") as file:
            return SandboxConfig.model_validate(json.load(file))
    except FileNotFoundError:
        print(f"{path} does not exists", file=sys.stderr)
    except PermissionError:
        print(f"{path} is not readable", file=sys.stderr)
    except json.JSONDecodeError:
        print(f"{path} does not contain valid JSON", file=sys.stderr)
    except ValidationError as e:
        print(_format_validation_error(path, e), file=sys.stderr)
    return None


def get_target(args) -> str | Transport | None:
    if args.mcp_server:
        return args.mcp_server

    if args.mcp_stdio is None:
        return None
    try:
        argv = shlex.split(args.mcp_stdio)
    except ValueError as e:
        raise SystemExit(f"--mcp-stdio: {e}")
    if not argv:
        raise SystemExit("--mcp-stdio: empty command")
    command, *rest = argv
    return stdio_client(StdioServerParameters(command=command, args=rest))


def _signature_from_schema(schema: dict) -> inspect.Signature:
    required = schema.get("required", [])
    return inspect.Signature([
        inspect.Parameter(
            name, inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if name in required else None,
        )
        for name in schema.get("properties", {})
    ])


def _make_proxy(client: Client, tool: types.Tool) -> Callable:
    def proxy(**kwargs):
        result = anyio.from_thread.run(client.call_tool, tool.name, kwargs)
        text = "\n".join(
            block.text for block in result.content
            if isinstance(block, types.TextContent)
        )
        if result.is_error:
            raise RuntimeError(text or f"{tool.name} failed")
        if result.structured_content is not None:
            return result.structured_content
        return text

    proxy.__name__ = tool.name
    proxy.__doc__ = tool.description
    proxy.__signature__ = _signature_from_schema(tool.input_schema)
    return proxy


def repl_loop(sandbox: Sandbox):
    print(f"Sandbox REPL (python {platform.python_version()})")
    while True:
        try:
            code = input(">>> ")
        except EOFError:
            print()
            return 0
        if code == "exit":
            return 0
        try:
            print(sandbox.execute(code))
        except SandboxDied as e:
            print(f"sandbox died: {e}", file=sys.stderr)
            return 1


async def run():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    readline.set_history_length(1000)
    try:
        readline.read_history_file(HISTORY_FILE)
    except (OSError, UnicodeDecodeError):
        pass

    parser = ArgumentParser()

    mcp_group = parser.add_mutually_exclusive_group()
    mcp_group.add_argument("--mcp-stdio", type=str)
    mcp_group.add_argument("--mcp-server", type=str)

    parser.add_argument("sandbox_template", nargs="?", default=None)

    args = parser.parse_args()

    config = SandboxConfig() if args.sandbox_template is None \
        else extract_config(args.sandbox_template)
    if config is None:
        return 1

    try:
        target = get_target(args)
        client_ctx = Client(
            target) if target is not None else contextlib.nullcontext()
        async with client_ctx as client:
            tools = {} if client is None else {
                t.name: _make_proxy(client, t) for t in (
                    await client.list_tools()).tools
            }

            with Sandbox(config, tools) as sandbox:
                return await anyio.to_thread.run_sync(repl_loop, sandbox)
    except Exception as e:
        print(f"startup failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        with contextlib.suppress(OSError):
            readline.write_history_file(HISTORY_FILE)


def main():
    return anyio.run(run)
