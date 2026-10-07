import argparse
import ast
import asyncio
import contextlib
import functools
import inspect
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv
from mcp import StdioServerParameters, stdio_client

from models_public import MBPPTaskInput, SolutionOutput, StepMetrics
from agent_core.llm.llm_client import LLMClient
from agent_core.models import ExecutionResult, Message, SandboxConfig
from agent_core.cli import extract_config, make_proxy, open_session
from agent_core.sandbox.core import Sandbox
from agent_mbpp.display import (print_debug, print_debug_step, print_header,
                                print_step, print_summary)


MAX_ITERATIONS = 10
MAX_TOKENS_PER_CALL = 1500
TEMPERATURE = 0.1
# Give up once the model has resent the same code this many times in a row
MAX_REPEATS = 3

# The subject asks for the MCP tool files at the repository root
MCP_SCRIPT = Path(__file__).parents[2] / "mcp_tools_mbpp.py"

SYSTEM_PROMPT_TEMPLATE = r"""You are an expert Python programmer solving one MBPP task.

Reply with ONE ```python block and nothing else. No explanation, no comments. Do any reasoning silently: the reply holds only the code block.

The block always has this exact structure (see the example below):
1. `code = r'''...'''`: a raw string holding only the imports and the function, using the EXACT given name and signature. Never write ''' inside it.
2. `result = run_tests(code, TESTS)` then `print(result["output"])`. TESTS is already defined and holds the original tests; in the sandbox run_tests returns a dict, not a JSON string.
3. `if result["success"]: final_answer(code)`: this submits your solution and ends the task.

If a test fails you get the expected and actual values back: your function is wrong. The expected values are the ground truth, even when they seem to contradict the task wording: re-derive the rule from them and send a corrected block. A hidden test also checks your function: it must be right for every input, so when the visible tests leave the wording ambiguous, use its most standard meaning.

Rules:
- Solve the general problem; never hard-code test outputs.
- Match the expected type exactly (tuple vs list, int vs float, bool vs str).
- Allowed imports only: {imports}.
- Forbidden: eval, exec, getattr, open, any name/attribute starting with "__".

{tools}Example:
```python
code = r'''
import re
def count_digits(s):
    return len(re.findall(r'\d', s))
'''
result = run_tests(code, TESTS)
print(result["output"])
if result["success"]:
    final_answer(code)
```"""

REPEAT_FEEDBACK = (
    "You repeated the exact same code as last turn and got the exact same "
    "result. Repeating it again will not help: understand the actual bug "
    "before sending more code.")


def extract_python_code(raw_text: str) -> str:
    """Return the last ``` block, the only one the prompt says will run, or
    the trimmed reply when it has none."""
    blocks = re.findall(r"```[^\n]*\n(.*?)```", raw_text, re.DOTALL)
    return blocks[-1].strip() if blocks else raw_text.strip()


def _build_system_prompt(authorized_imports: list[str],
                         tools: dict[str, Callable]) -> str:
    """The sandbox manual the subject asks for: allowed imports and the
    documentation of every MCP tool exposed in the sandbox."""
    modules = dict.fromkeys(p.removesuffix(".*") for p in authorized_imports)
    tools_section = ""
    if tools:
        lines = [
            f"- {name}{inspect.signature(f)}: {inspect.getdoc(f) or ''}"
            for name, f in tools.items()
        ]
        tools_section = ("Sandbox tools (call them from your code):\n"
                         + "\n".join(lines) + "\n\n")
    return SYSTEM_PROMPT_TEMPLATE.format(
        imports=", ".join(modules), tools=tools_section)


def _decode_json(tool: Callable) -> Callable:
    """json may not be importable in the sandbox: decode the tool's JSON
    reply on the host so the sandbox script gets a plain dict."""
    @functools.wraps(tool)
    def decoded(*args, **kwargs):
        return json.loads(tool(*args, **kwargs))
    return decoded


def _format_output(result: ExecutionResult) -> str:
    parts = []
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    if result.error:
        parts.append(f"error:\n{result.error}")
    return "\n".join(parts).strip() or "Code executed with no output."


def _read_task(path: str) -> MBPPTaskInput:
    with open(path, "r", encoding="utf-8") as f:
        return MBPPTaskInput.model_validate(json.load(f))


def _write_output(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


async def _connect_mcp(stack: contextlib.AsyncExitStack,
                       debug: bool) -> dict[str, Callable]:
    """Start the MBPP MCP server and wrap its tools for the sandbox. The
    session is kept open by `stack`: the proxies only work while it lives."""
    if not MCP_SCRIPT.exists():
        return {}
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(MCP_SCRIPT)],
    )
    # The server logs every request on stderr: only worth showing in debug
    errlog = sys.stderr if debug \
        else stack.enter_context(open(os.devnull, "w"))
    client = await stack.enter_async_context(
        open_session(stdio_client(server_params, errlog=errlog)))
    loop = asyncio.get_running_loop()
    return {
        t.name.replace("-", "_"): make_proxy(client, t, loop)
        for t in (await client.list_tools()).tools
    }


async def run_mbpp_agent(task_file: str, output_file: str,
                         model_name: str = "gpt-oss-120b",
                         debug: bool = False):
    config = extract_config("sandbox_template.json") or SandboxConfig()
    task = _read_task(task_file)

    llm_client = LLMClient()
    steps: list[StepMetrics] = []

    initial_user_prompt = (
        f"Task Description:\n{task.task_definition}\n\n"
        f"Function Signature:\n{task.function_definition}\n\n"
        f"Example Test Cases:\n" + "\n".join(task.test_list) + "\n\n"
        f"Required Imports:\n{json.dumps(task.test_imports)}\n"
    )

    messages = [Message(role="user", content=initial_user_prompt)]

    success = False
    final_solution = ""
    error_message: Optional[str] = None
    total_requests = 0
    max_tokens = 1500

    async with contextlib.AsyncExitStack() as stack:
        tools = await _connect_mcp(stack, debug)
        sandbox_tools = {
            name: _decode_json(f) if name == "run_tests" else f
            for name, f in tools.items()
        }
        sb = stack.enter_context(Sandbox(config, sandbox_tools))
        # The namespace persists across executions: the replies can use it.
        # Imports first, as the moulinette does: the asserts may need them
        sb.execute(f"TESTS = {[*task.test_imports, *task.test_list]!r}")
        system_prompt = _build_system_prompt(config.authorized_imports, tools)
        messages.insert(0, Message(role="user", content=system_prompt))

        print_header(task, model_name, list(tools))
        if debug:
            print_debug("system prompt", system_prompt)
            print_debug("user prompt", initial_user_prompt)

        start_time = time.perf_counter()
        for i in range(10):
            try:
                answer = llm_client.chat(
                    messages=messages,
                    model=model_name,
                    temperature=TEMPERATURE,
                    max_tokens=max_tokens,
                )
                total_requests += 1 + answer.retries
            except Exception as e:
                error_message = f"LLM Generation Error: {e!s}"
                break

            llm_output = answer.content
            extracted_code = extract_python_code(llm_output)

            # In a thread: the MCP proxies need the event loop to stay free
            exec_result = await asyncio.to_thread(sb.execute, extracted_code)

            repeated = bool(steps) \
                and steps[-1].sandbox_input == extracted_code
            step = StepMetrics(
                step=i + 1,
                input_tokens=answer.input_tokens,
                output_tokens=answer.output_tokens,
                request_time_ms=answer.latency_ms,
                model_name=answer.model,
                llm_output=llm_output,
                sandbox_input=extracted_code,
                sandbox_output=_format_output(exec_result),
                retries=answer.retries,
            )
            steps.append(step)

            if sum([step.output_tokens for step in steps]) > max_tokens / 2:
                messages.append(Message(role="user",
                    content="half of token budget used, you must call final_answer soon"))

            print_step(step, MAX_ITERATIONS,
                       done=exec_result.final_answer is not None,
                       repeated=repeated)
            if debug:
                print_debug_step(answer, TEMPERATURE, 1500,
                                 exec_result, str(exec_result))

            if exec_result.final_answer is not None:
                success = True
                final_solution = exec_result.final_answer
                break

            messages.append(Message(role="assistant", content=llm_output))
            messages.append(
                Message(role="user", content=_format_output(exec_result)))
            # messages = messages[:2] + [
            #     Message(role="assistant",
            #             content=llm_output),
            #     Message(role="user", content=_format_output(exec_result)),
            # ]

    total_time_seconds = time.perf_counter() - start_time

    if not success and not error_message:
        error_message = "Exhausted iterations without submitting a passing solution via final_answer()."

    print_summary(success, steps, total_time_seconds, error_message)

    solution_output = SolutionOutput(
        task_id=str(task.task_id),
        benchmark="mbpp",
        success=success,
        solution=final_solution,
        iterations=len(steps),
        total_requests=total_requests,
        total_input_tokens=sum(s.input_tokens for s in steps),
        total_output_tokens=sum(s.output_tokens for s in steps),
        total_time_seconds=total_time_seconds,
        steps=steps,
        system_prompt=system_prompt,
        error=error_message,
        timestamp=datetime.now().isoformat(),
    )

    _write_output(output_file, solution_output.model_dump_json(indent=2))


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="MBPP Task Solver Agent")
    parser.add_argument("--task-file", "--task-path",
                        required=True, help="Path to task.json")
    parser.add_argument("--output", "--solution-path",
                        required=True, help="Path to output solution.json")
    parser.add_argument("--model-name", default="gpt-oss-120b",
                        help="Model identifier to use")
    parser.add_argument("--debug", action="store_true",
                        help="Show prompts, raw replies, sandbox results, "
                             "MCP server and LLM client logs")
    args = parser.parse_args()

    if args.debug:
        # Only our own loggers: httpx/httpcore at DEBUG would drown the output
        logging.basicConfig(format="  [%(levelname)s] %(name)s: %(message)s")
        logging.getLogger("agent_core").setLevel(logging.DEBUG)

    asyncio.run(run_mbpp_agent(args.task_file, args.output,
                               model_name=args.model_name,
                               debug=args.debug))
