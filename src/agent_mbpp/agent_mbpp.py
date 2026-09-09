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

SYSTEM_PROMPT = """You are an expert Python developer. Your goal is to write a Python function that solves the requested task and passes all unit tests.

Rules:
1. Provide ONLY valid executable Python code.
2. Put your Python code inside a single ```python ... ``` code block.
3. Do NOT include markdown text explanations outside the code block.
4. Ensure all necessary function definitions and imports are included.
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


async def run_mbpp_agent(task_file: str, output_file: str, model_name: str = "gpt-oss-120b"):
    config = extract_config("sandbox_template.json") or SandboxConfig()

    mcp_script = Path(__file__).parent.parent / "mcp_tools_mbpp.py"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(mcp_script)]
    )

    async with Client(stdio_client(server_params)) as client:
        tools_list = await client.list_tools()
        tools = {
            t.name.replace("-", "_"): make_proxy(client, t)
            for t in tools_list.tools
        } if client else {}

    start_time = time.perf_counter()

    task = await asyncio.to_thread(_read_task, task_file)

    llm_client = LLMClient()
    steps: list[StepMetrics] = []

    initial_user_prompt = (
        f"Task Description:\n{task.task_definition}\n\n"
        f"Function Signature:\n{task.function_definition}\n\n"
        f"Required Imports:\n{json.dumps(task.test_imports)}\n\n"
        f"Test Assertions:\n" + "\n".join(task.test_list)
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

            sandbox_input = f"{extracted_code}\n\n" + "\n".join(task.test_list)
            exec_result = sb.execute(sandbox_input)

            passed = exec_result.error is None

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
                sandbox_input=sandbox_input,
                sandbox_output=sandbox_output,
                retries=answer.retries,
            )
            steps.append(step_metric)

            if passed:
                success = True
                final_solution = extracted_code
                break

            messages.append(Message(role="assistant", content=llm_output))
            messages.append(
                Message(
                    role="user",
                    content=f"Your solution failed execution in the sandbox:\n{sandbox_output}\n\nPlease fix the implementation and return the complete corrected function in a ```python ... ``` block.",
                )
            )


    total_time_seconds = time.perf_counter() - start_time
    total_input_tokens = sum(s.input_tokens for s in steps)
    total_output_tokens = sum(s.output_tokens for s in steps)

    if not success and not error_message:
        error_message = "Exhausted 10 iterations without passing all test assertions."

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

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(solution_output.model_dump_json(indent=2))

if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="MBPP Task Solver Agent")
    parser.add_argument("--task-file", "--task-path", required=True, help="Path to task.json")
    parser.add_argument("--output", "--solution-path", required=True, help="Path to output solution.json")
    parser.add_argument("--model-name", default="gpt-oss-120b", help="Model identifier to use")
    args = parser.parse_args()
    asyncio.run(run_mbpp_agent(args.task_file, args.output, model_name=args.model_name))
