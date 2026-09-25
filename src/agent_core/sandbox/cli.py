import platform
import readline
import json
import shlex
import anyio
import asyncio
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
            name, inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=inspect.Parameter.empty if name in required else None,
        )
        for name in schema.get("properties", {})
    ])


# def make_proxy(client: Client, tool: types.Tool) -> Callable:
#     sig = _signature_from_schema(tool.input_schema)

#     def proxy(*args, **kwargs):
#         arguments = dict(sig.bind(*args, **kwargs).arguments)
#         result = anyio.from_thread.run(client.call_tool, tool.name, arguments)
#         text = "\n".join(
#             block.text for block in result.content
#             if isinstance(block, types.TextContent)
#         )
#         if result.is_error:
#             raise RuntimeError(text or f"{tool.name} failed")
#         if result.structured_content is not None:
#             return result.structured_content
#         return text

#     proxy.__name__ = tool.name
#     proxy.__doc__ = tool.description
#     proxy.__signature__ = _signature_from_schema(tool.input_schema)
#     return proxy

def make_proxy(client, tool, loop):
    """Creates a synchronous proxy function for an async MCP tool."""
    def proxy(*args, **kwargs):
        input_dict = dict(kwargs)

        # Handle both Pydantic attribute naming conventions (input_schema vs inputSchema)
        schema = getattr(tool, "input_schema", getattr(tool, "inputSchema", None))

        # Map positional arguments (*args) to parameter names
        if args and isinstance(schema, dict) and "properties" in schema:
            param_names = list(schema["properties"].keys())
            for name, val in zip(param_names, args):
                input_dict[name] = val

        coro = client.call_tool(tool.name, arguments=input_dict)
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        result = future.result()

        if hasattr(result, "content") and result.content:
            text_contents = [c.text for c in result.content if hasattr(c, "text")]
            return "\n".join(text_contents) if text_contents else result.content
        return result

    return proxy

def repl_loop(sandbox: Sandbox):
    print(f"Sandbox REPL (python {platform.python_version()})")
    while True:
        try:
            code = input(">>> ")
        except EOFError:
            print()  # To remove hidden char when Ctrl-D
            return 0
        if code == "exit":
            return 0
        try:
            res = sandbox.execute(code)
            if res.__str__():
                print(res)
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
                t.name.replace("-", "_"): make_proxy(client, t) for t in (
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
