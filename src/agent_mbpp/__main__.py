import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from mcp import Client, StdioServerParameters, stdio_client

from models_public import MBPPTaskInput, SolutionOutput, StepMetrics
from src.agent_core.llm.llm_client import LLMClient
from src.agent_core.models import Message, SandboxConfig
from src.agent_core.sandbox.cli import extract_config, make_proxy
from src.agent_core.sandbox.core import Sandbox

SYSTEM_PROMPT = """You are an expert Python developer. Your goal is to write a Python function that solves the requested task through a strict two-turn process.

RULES & WORKFLOW:

1. TOKEN EFFICIENCY & CODE STYLE:
   - Perform your reasoning internally before writing code.
   - Keep code clean, compact, and concise.
   - DO NOT write line-by-line comments, step-by-step code annotations, or verbose docstrings.
   - Strip unnecessary code comments to avoid token waste.

2. PHASE 1 — SCRATCHPAD & TESTING (First Turn):
   - Write your candidate Python function.
   - Include test cases and print statements to verify edge cases and return types (e.g., check expected return types for invalid/impossible cases: False vs -1, None, etc.).
   - Output ONLY a standard ```python ... ``` block.
   - DO NOT call `final_answer(...)` on this turn. Wait for the sandbox execution output.

3. PHASE 2 — SUBMISSION (After reviewing sandbox output):
   - Review the sandbox stdout/stderr from your previous turn.
   - If your logic or test assertions failed, refine your function in the scratchpad and test again.
   - Once your scratchpad output confirms all edge cases and assertions pass, call `final_answer(...)` to submit your final solution.

EXAMPLE PHASE 1 (Scratchpad Turn):
```python
def add(a, b):
    return a + b

# Run test cases in scratchpad first
print("Test 1:", add(2, 3))  # Expected: 5
print("Test 2:", add(-1, 1)) # Expected: 0
```

EXAMPLE PHASE 2 (Submission Turn):
```python

final_answer('''def add(a, b):
    return a + b''')
```

"""


def extract_python_code(raw_text: str) -> str:
    """Extract Python code from markdown code blocks or return trimmed text."""
    if "```python" in raw_text:
        return raw_text.split("```python")[1].split("```")[0].strip()
    elif "```" in raw_text:
        return raw_text.split("```")[1].split("```")[0].strip()
    return raw_text.strip()


def _read_task(path: str) -> MBPPTaskInput:
    with open(path, "r", encoding="utf-8") as f:
        return MBPPTaskInput.model_validate(json.load(f))


def _write_output(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


async def run_mbpp_agent(task_file: str, output_file: str, model_name: str = "gpt-oss-120b"):
    config = extract_config("sandbox_template.json") or SandboxConfig()

    mcp_script = Path(__file__).parent / "mcp_tools_mbpp.py"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(mcp_script)],
        errlog=sys.stderr,
    )

    tools = {}
    if mcp_script.exists():
        async with Client(stdio_client(server_params)) as client:
            tools_list = await client.list_tools()
            tools = {
                t.name.replace("-", "_"): make_proxy(client, t)
                for t in tools_list.tools
            }

    start_time = time.perf_counter()

    task = await asyncio.to_thread(_read_task, task_file)

    llm_client = LLMClient()
    steps: list[StepMetrics] = []

    initial_user_prompt = (
        f"Task Description:\n{task.task_definition}\n\n"
        f"Function Signature:\n{task.function_definition}\n\n"
        f"Example Test Cases:\n" + "\n".join(task.test_list) + "\n\n"
        f"Required Imports:\n{json.dumps(task.test_imports)}\n"
    )

    messages = [
        Message(role="system", content=SYSTEM_PROMPT),
        Message(role="user", content=initial_user_prompt),
    ]

    success = False
    final_solution = ""
    error_message: Optional[str] = None
    total_requests = 0

    with Sandbox(config, tools) as sb:
        for i in range(1, 11):
            print(i)
            step_start_time = time.perf_counter()

            try:
                answer = llm_client.chat(
                    messages=messages,
                    model=model_name,
                    temperature=0.1,
                    max_tokens=1500,
                )
                total_requests += 1 + answer.retries
            except Exception as e:
                error_message = f"LLM Generation Error: {e!s}"
                break

            llm_output = answer.content
            extracted_code = extract_python_code(llm_output)

            # Execute ONLY the model's code in the sandbox
            exec_result = sb.execute(extracted_code)

            # Case A: Model invoked final_answer(code_string)
            if exec_result.final_answer is not None:
                candidate_solution = exec_result.final_answer

                test_blocks = []
                for stmt in task.test_list:
                    test_blocks.append(
                        f"try:\n"
                        f"    {stmt}\n"
                        f"except Exception as e:\n"
                        f"    failures.append(f'FAILED TEST: {stmt} | Error: {{type(e)}}: {{e}}')"
                    )

                full_tests = (
                    f"{candidate_solution}\n\n"
                    "failures = []\n"
                    + "\n".join(test_blocks) + "\n"
                    "if failures:\n"
                    "    raise AssertionError('\\n'.join(failures))\n"
                )

                # Run test assertions against candidate solution
                test_exec_result = sb.execute(full_tests)

                passed = test_exec_result.error is None

                if passed:
                    sandbox_output = "All task unit tests passed successfully."
                    success = True
                    final_solution = candidate_solution
                else:
                    output_parts = []
                    if test_exec_result.stdout:
                        output_parts.append(f"stdout:\n{test_exec_result.stdout}")
                    if test_exec_result.stderr:
                        output_parts.append(f"stderr:\n{test_exec_result.stderr}")
                    if test_exec_result.error:
                        output_parts.append(f"error:\n{test_exec_result.error}")
                    sandbox_output = (
                        "Submitted solution failed task assertions:\n"
                        + "\n".join(output_parts).strip()
                    )

            # Case B: Model ran regular code without calling final_answer
            else:
                passed = False
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
                model_name=answer.model,
                llm_output=llm_output,
                sandbox_input=extracted_code,
                sandbox_output=sandbox_output,
                retries=answer.retries,
            )
            print(step_metric.sandbox_input, "\n\n\n")
            print(step_metric.sandbox_output)
            steps.append(step_metric)

            if success:
                break

            messages.append(Message(role="assistant", content=llm_output))

            if exec_result.final_answer is not None:
                user_feedback = (
                    f"Your submitted final solution failed test assertions:\n{sandbox_output}\n\n"
                    "Please fix your function and call `final_answer(...)` again with the corrected code."
                )
            else:
                user_feedback = (
                    f"Sandbox execution output:\n{sandbox_output}\n\n"
                    "IMPORTANT: You defined/executed python code, but you did NOT call `final_answer(...)`.\n"
                    "If you are confident in your solution, submit it by calling `final_answer('''<your code>''')`."
                )

            messages.append(Message(role="user", content=user_feedback))

    total_time_seconds = time.perf_counter() - start_time
    total_input_tokens = sum(s.input_tokens for s in steps)
    total_output_tokens = sum(s.output_tokens for s in steps)

    if not success and not error_message:
        error_message = "Exhausted iterations without submitting a passing solution via final_answer()."

    solution_output = SolutionOutput(
        task_id=str(task.task_id),
        benchmark="mbpp",
        success=success,
        solution=final_solution,
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
    print(f"\n\n\nin: {total_input_tokens}\nout: {total_output_tokens}\n")
    await asyncio.to_thread(
        _write_output,
        output_file,
        solution_output.model_dump_json(indent=2)
    )


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="MBPP Task Solver Agent")
    parser.add_argument("--task-file", "--task-path", required=True, help="Path to task.json")
    parser.add_argument("--output", "--solution-path", required=True, help="Path to output solution.json")
    parser.add_argument("--model-name", default="gpt-oss-120b", help="Model identifier to use")
    args = parser.parse_args()
    asyncio.run(run_mbpp_agent(args.task_file, args.output, model_name=args.model_name))
