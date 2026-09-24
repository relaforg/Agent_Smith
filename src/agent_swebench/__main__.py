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
from src.agent_core.sandbox.cli import extract_config, make_proxy
from src.agent_core.sandbox.core import Sandbox

SYSTEM_PROMPT = """You are an autonomous software engineer tasked with fixing bugs in repository codebases.
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
-run_command(command: str | list, workdir: str) -> dict: Run arbitrary shell command in workspace.
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


def extract_python_code(raw_text: str) -> str:
    """Extract Python code from markdown code blocks or return trimmed text."""
    if "```python" in raw_text:
        return raw_text.split("```python")[1].split("```")[0].strip()
    elif "```" in raw_text:
        return raw_text.split("```")[1].split("```")[0].strip()
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
    model_name: str = "gpt-oss-120b",
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
            llm_client = LLMClient(base_url=provider_url) if provider_url else LLMClient()
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

            with Sandbox(config, tools) as sb:
                for i in range(1, max_iterations + 1):
                    step_start_time = time.perf_counter()

                    try:
                        answer = llm_client.chat(
                            messages=messages,
                            model=model_name,
                            temperature=0.1,
                            max_tokens=1000,
                        )
                        total_requests += 1 + answer.retries
                    except Exception as e:
                        error_message = f"LLM Generation Error: {e!s}"
                        break

                    llm_output = answer.content
                    extracted_code = extract_python_code(llm_output)

                    exec_result = sb.execute(extracted_code)

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
    parser.add_argument("--model-name", default="gemma", help="Model identifier to use")
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
