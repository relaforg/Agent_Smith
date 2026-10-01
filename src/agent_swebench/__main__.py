import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from mcp import Client, StdioServerParameters, stdio_client

from models_public import SolutionOutput, StepMetrics, SWEBenchTaskInput
from src.agent_core.llm.llm_client import LLMClient
from src.agent_core.models import Message, SandboxConfig
from src.agent_core.cli import extract_config, make_proxy
from src.agent_core.sandbox.core import Sandbox

SYSTEM_PROMPT_OLD = """You are an autonomous software engineer tasked with fixing bugs in repository codebases.
You operate inside an interactive Python sandbox where MCP repository tools and helpers are directly exposed as Python functions.

CRITICAL FORMATTING INSTRUCTIONS:
- You must ONLY interact with the system by writing executable Python code wrapped inside ```python ... ``` markdown blocks.
- DO NOT output JSON tool calls, XML tool tags (e.g., <tool_call>), or API-style tool formats (e.g., {"name": "python", "arguments": ...}).
- Your response will be passed directly to a Python interpreter. Any raw text outside code blocks is ignored, but Python code inside ```python ... ``` blocks will be executed immediately.
- To use tools, invoke them as standard Python function calls:

```python
# Correct usage
result = search_code("def my_function", "*.py")
print(result)
```

AVAILABLE TOOLS IN PYTHON EXECUTION NAMESPACE:
-read_file(filepath: str, start_line: int, end_line: int) -> str: Read file lines formatted with line numbers.
-edit_file(filepath: str, old_str: str, new_str: str) -> None: Perform exact string replacement in a file.
-list_files(directory: str, pattern: str) -> list[str]: List files in directory matching a pattern.
-search_code(pattern: str, file_pattern: str) -> str: Grep search across repository codebase.
-search_function_or_class_definition_in_code(name: str) -> str: Search class or function definitions.
-find_references(name: str, filepath: str, line: int) -> str: Search symbol references across files.
-run_tests() -> str: Execute evaluation test script for the repository.
-get_patch() -> str: Retrieve current unified git diff patch of modified files.
-run_command(command: str | list, workdir: str) -> dict: Run arbitrary shell command in workspace. Python command should be ran with python and not python3
-final_answer(patch: str) -> None: Terminate loop and submit final patch.

SANDBOX GUARDRAILS & EXECUTION RULES:
-NO RESTRICTED IMPORTS: Do NOT write code containing forbidden AST imports (e.g., import os, import sympy, import sys). Use only the pre-imported tools listed above.
-FILE PATHS: Always use full absolute file paths returned by tools or workspace root paths (avoid unvalidated relative paths).
-NON-INTERACTIVE COMMANDS: When using run_command, run non-interactive scripts only.

WORKFLOW GUIDELINES:
-EXPLORE & LOCATE: Use search_code, search_function_or_class_definition_in_code, or list_files to find relevant bug locations.
-READ & DIAGNOSE: Read target files with read_file to analyze root causes.
-EDIT: Use edit_file to apply minimal, surgical fixes.
-VERIFY: Call run_tests() to verify your edits against evaluation scripts.
-PATCH INSPECTION & SUBMISSION (CRITICAL STEP):
-Before ending the session, you MUST execute and print the output of get_patch() to inspect the diff:
-Python

```python
    patch = get_patch()
    print(patch)
```
Verify that:
-The patch string is non-empty and starts with standard git diff headers (e.g., diff --git a/... b/...).
-The patch contains ONLY your intended changes.
-Only after verifying the printed patch, submit it directly:
```python
    final_answer(patch)
```
RULES:
-Always format executable code inside ```python ...```  blocks.
-Keep output concise and focus strictly on executing Python code to fix the problem.
"""


SYSTEM_PROMPT = """You are an autonomous software engineer fixing a bug in a repository.
You work in a Python sandbox where repository tools are pre-loaded as functions.

OUTPUT FORMAT
- Reply with exactly ONE ```python ... ``` block per turn. Only the first block is executed; text outside it is ignored.
- Never emit JSON/XML tool calls. Call the functions directly and print() results you want to see.
- Keep each block short: one logical action, no long comments, never copy tool output into comments.
- You cannot see the output of a block until your next turn. Do not act on results you have not seen.

ENVIRONMENT FACTS
- The repository is checked out at /testbed. Use absolute paths everywhere (e.g. /testbed/django/http/response.py).
- run_command's workdir must be absolute, e.g. "/testbed". Relative workdirs fail.
- The sandbox forbids ALL imports (os, sys, re, ...). Do not use open(); Do not import os; it does not touch the repository.
  To read use read_file, to modify use edit_file, for anything else use run_command.
- Project dependencies may be missing, so ad-hoc scripts that import the project can fail (e.g. ModuleNotFoundError).
  Do not spend turns fixing the environment. Verify with run_tests() instead.
- Never create files inside /testbed (they pollute the patch). Scratch files go in /tmp.

TOOLS (all arguments are required)
- read_file(filepath, start_line, end_line) -> str: numbered lines.
- edit_file(filepath, old_str, new_str) -> None: exact string replacement of ALL occurrences.
  It fails SILENTLY if old_str does not match (whitespace included), so always check the result with get_patch() or read_file().
  Include enough surrounding lines in old_str to make it unique.
- list_files(directory, pattern) -> list[str]: non-recursive, pattern like "*.py".
- search_code(pattern, file_pattern) -> str: grep, file_pattern like "*.py" or "django/http/*.py".
- search_function_or_class_definition_in_code(name) -> str
- find_references(name, filepath, line) -> str
- run_tests() -> str: runs the evaluation script. Output can be long; print only what you need (e.g. the last 40 lines).
- get_patch() -> str: current git diff.
-run_command(command: list[str] | str, workdir: str) -> dict: Run a command in the workspace.
  ALWAYS pass command as a LIST, e.g. ["python", "-c", script], not a shell string.
  A list is passed directly to the process with no shell parsing, so you can put any
  Python code (multi-line, with quotes) into a normal triple-quoted string as one
  argument, with zero escaping needed. Do NOT build shell strings with heredocs,
  nested quotes, or `bash -c "..."` — this reliably produces syntax errors.
  To run a one-off script without creating a file: run_command(["python", "-c", code], workdir).
- final_answer(patch) -> None: submits and ends the session.

REMINDER:
-this sandbox uses exec(), not a REPL — return values are NOT auto-printed.
Always wrap a call in print(...) if you want to see its result: print(run_tests()), print(read_file(...)).
-If a standalone script fails due to a missing/broken dependency unrelated to the bug
 (ImportError, ModuleNotFoundError), do NOT try to mock or monkey-patch it — abandon
 the repro script and rely on run_tests() instead.
-NEVER index directly into a tool's return value on first use (e.g. run_command(...)["stdout"]).
 Assign it to a variable and print() the WHOLE result first:
     result = run_command(...)
     print(result)
 Only index into a specific key once you've seen the full dict and confirmed it has what
 you expect. A command that "produces no output" often failed — the real error is in
 ["stderr"] or ["exit_code"], and discarding them makes failures invisible.
 -Only your last ```python ...``` block will be executed

WORKFLOW
1. Locate: search_code / search_function_or_class_definition_in_code, then read_file the relevant lines.
2. Diagnose the root cause before editing. Decide the smallest change that fixes it.
3. Edit with edit_file. You MUST modify a source file; explaining the fix is not enough.
4. Verify: print(get_patch()) to confirm the edit landed, then run_tests().
5. Submit only when tests pass (or you have exhausted reasonable options).
"""

def extract_python_code(raw_text: str) -> str:
    """Extract Python code from markdown code blocks or return trimmed text."""
    if "```python" in raw_text:
        return raw_text.split("```python")[-1].split("```")[0].strip()
    elif "```" in raw_text:
        return raw_text.split("```")[-1].split("```")[0].strip()
    return raw_text.strip()


def _read_task(path: str) -> SWEBenchTaskInput:
    with open(path, "r", encoding="utf-8") as f:
        return SWEBenchTaskInput.model_validate(json.load(f))


def _write_output(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


async def run_swebench_agent(
    task_file: str,
    output_file: str,
    model_name: str = "gemma-26",
    provider_url: Optional[str] = None,
    max_iterations: int = 30,
):
    config = extract_config("sandbox_template.json") or SandboxConfig()
    task = await asyncio.to_thread(_read_task, task_file)

    mcp_script = Path(__file__).parents[1] / "mcp_tools_swebench.py"
    if not mcp_script.exists():
        mcp_script = Path("mcp_tools_swebench.py")

    mcp_env = dict(os.environ)
    if task.docker_image:
        mcp_env["DOCKER_IMAGE"] = task.docker_image
    if task.eval_script:
        mcp_env["EVAL_SCRIPT"] = task.eval_script

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(mcp_script)],
        env=mcp_env,
        errlog=sys.stderr,
    )

    loop = asyncio.get_running_loop()

    # Keep MCP Client active during execution
    async with Client(stdio_client(server_params)) as client:
        tools_list = await client.list_tools()
        tools = {
            t.name.replace("-", "_"): make_proxy(client, t, loop)
            for t in tools_list.tools
        }

        # Run synchronous loop inside a separate worker thread
        def execution_loop():
            start_time = time.perf_counter()
            llm_client = LLMClient()
            steps: list[StepMetrics] = []

            initial_user_prompt = (
                f"Instance ID: {task.instance_id}\n"
                f"Repository: {task.repo}\n\n"
                f"Problem Statement:\n{task.problem_statement}\n"
            )
            if task.hints_text:
                initial_user_prompt += f"\nHints:\n{task.hints_text}\n"

            messages = [
                Message(role="system", content=SYSTEM_PROMPT),
                Message(role="user", content=initial_user_prompt),
            ]

            success = False
            final_patch = ""
            error_message: Optional[str] = None
            total_requests = 0
            last_code = None
            repeat_count = 0

            with Sandbox(config, tools) as sb:

                # sb.execute('print(run_command(["which", "python", "python3"], "/testbed"))')
                # sb.execute('print(run_command(["python", "-c", "import sympy; print(sympy.__version__)"], "/testbed"))')

                for i in range(1, max_iterations + 1):
                    step_start_time = time.perf_counter()

                    try:
                        answer = llm_client.chat(
                            messages=messages,
                            model=model_name,
                            temperature=.2,
                            max_tokens=2048,
                        )
                        total_requests += 1 + answer.retries
                    except Exception as e:
                        error_message = f"LLM Generation Error: {e!s}"
                        break

                    llm_output = answer.content
                    extracted_code = extract_python_code(llm_output)

                    if extracted_code.strip() == (last_code or "").strip():
                        repeat_count += 1
                    else:
                        repeat_count = 0
                    last_code = extracted_code

                    if repeat_count >= 1:
                        sandbox_output = (
                            "You repeated the exact same code as last turn and got the exact same error. "
                            "Repeating it again will not help. Stop trying to run a standalone script"
                            "understand the actual bug before writing any more repro scripts."
                        )
                    if repeat_count >= 3:
                        error_message = "Aborted: agent stuck repeating identical actions."
                        break

                    exec_result = sb.execute(extracted_code)

                    if exec_result.error and "unterminated" in str(exec_result.error) and "string literal" in str(exec_result.error):
                        sandbox_output = (
                            "Your code was cut off mid-generation because it was too long — this is a "
                            "truncation issue, not a quoting mistake. Do not retry the same large script. "
                            "Instead: write something much shorter, split it into a smaller step, or "
                            "better yet, skip the custom repro script entirely and call run_tests() to "
                            "check whether the existing test suite already reproduces this bug."
                        )
                        continue

                    if exec_result.final_answer is not None:
                        final_patch = str(exec_result.final_answer)
                        success = True
                        sandbox_output = "Task completed and final patch submitted successfully."
                    else:
                        output_parts = []
                        if exec_result.stdout:
                            output_parts.append(f"stdout:\n{exec_result.stdout}")
                        if exec_result.stderr:
                            output_parts.append(f"stderr:\n{exec_result.stderr}")
                        if exec_result.error:
                            output_parts.append(f"error:\n{exec_result.error}")

                        sandbox_output = "\n".join(output_parts).strip() or "Code executed with no output."

                    request_time_ms = (time.perf_counter() - step_start_time) * 1000.0

                    step_metric = StepMetrics(
                        step=i,
                        input_tokens=answer.input_tokens,
                        output_tokens=answer.output_tokens,
                        request_time_ms=request_time_ms,
                        timestamp=datetime.now().isoformat(),
                        api_url=provider_url or "",
                        model_name=answer.model,
                        llm_output=llm_output,
                        sandbox_input=extracted_code,
                        sandbox_output=sandbox_output,
                        retries=answer.retries,
                    )

                    print(step_metric.step, "\n")
                    print(step_metric.sandbox_input, "\n")
                    print(step_metric.sandbox_output, "\n")


                    steps.append(step_metric)

                    if success:
                        break

                    messages.append(Message(role="assistant", content=llm_output))

                    user_feedback = (
                        f"Observation from execution:\n{sandbox_output}\n\n"
                        "IMPORTANT: If you have completed the fix and verified it with tests, "
                        "submit your work by calling `final_answer(get_patch())`."
                    )

                    messages.append(Message(role="user", content=user_feedback))

                    time.sleep(5)

            total_time_seconds = time.perf_counter() - start_time
            total_input_tokens = sum(s.input_tokens for s in steps)
            total_output_tokens = sum(s.output_tokens for s in steps)

            if not success and not error_message:
                error_message = "Exhausted maximum iterations without submitting a patch via final_answer()."

            solution_output = SolutionOutput(
                task_id=task.instance_id,
                benchmark="swebench",
                success=success,
                solution=final_patch,
                iterations=len(steps),
                total_requests=total_requests,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                total_time_seconds=total_time_seconds,
                steps=steps,
                system_prompt=SYSTEM_PROMPT,
                error=error_message,
                timestamp=datetime.now().isoformat(),
            )

            _write_output(output_file, solution_output.model_dump_json(indent=2))

        await asyncio.to_thread(execution_loop)


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="SWE-bench Task Solver Agent")
    parser.add_argument("--task-file", "--task-path", required=True, help="Path to swebench task.json")
    parser.add_argument("--output", "--solution-path", required=True, help="Path to output solution.json")
    parser.add_argument("--model-name", default="gemma-26", help="Model identifier to use")
    parser.add_argument("--provider-url", default=None, help="Base API URL for LLM provider")
    args = parser.parse_args()

    asyncio.run(
        run_swebench_agent(
            task_file=args.task_file,
            output_file=args.output,
            model_name=args.model_name,
            provider_url=args.provider_url,
        )
    )
