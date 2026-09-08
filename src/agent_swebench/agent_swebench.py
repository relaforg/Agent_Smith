import argparse
import json
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from src.agent_core.llm.llm_client import LLMClient
from src.agent_core.models import Message
from models_public import SolutionOutput, StepMetrics, SWEBenchTaskInput
from mcp_tools_swebench import DockerBackend


SYSTEM_PROMPT = """You are an expert software engineer resolving GitHub issues in Python repositories.

You have access to repository tools. To use a tool, format your request as a JSON object inside <tool_call> tags:

<tool_call>
{
  "tool": "tool_name",
  "args": { ... }
}
</tool_call>

Available Tools:
1. `read_file`: {"filepath": str, "start_line": int, "end_line": int}
2. `edit_file`: {"filepath": str, "old_str": str, "new_str": str}
3. `list_files`: {"directory": str, "pattern": str}
4. `search_code`: {"pattern": str, "file_pattern": str}
5. `search_function_or_class_definition_in_code`: {"name": str}
6. `run_command`: {"command": str_or_list, "workdir": str}
7. `run_tests`: {}
8. `get_patch`: {}

When you have fixed the issue and verified your patch, finish by outputting:
<finish>Patch applied and verified.</finish>
"""


def parse_tool_call(content: str) -> Optional[Dict[str, Any]]:
    """Extract and parse JSON tool call from <tool_call> tags."""
    match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def execute_backend_tool(backend: DockerBackend, tool_name: str, args: Dict[str, Any]) -> str:
    """Route tool calls directly to DockerBackend execution methods."""
    try:
        if tool_name == "read_file":
            _, stdout, stderr = backend.exec(["sed", "-n", f"{args.get('start_line', 1)},{args.get('end_line', 100)}p", args["filepath"]])
            return stdout if stdout else stderr
        elif tool_name == "edit_file":
            _, content, _ = backend.exec(["cat", args["filepath"]])
            if not content:
                return "File empty or not found."
            updated = content.replace(args["old_str"], args["new_str"])
            backend.put_file(args["filepath"], updated)
            return f"Successfully replaced occurrences in {args['filepath']}."
        elif tool_name == "list_files":
            _, stdout, _ = backend.exec(["find", args.get("directory", "."), "-maxdepth", "2", "-name", args.get("pattern", "*"), "-type", "f"])
            return stdout
        elif tool_name == "search_code":
            _, stdout, _ = backend.exec(["grep", "-rnIs", args["pattern"], f"--include={args.get('file_pattern', '*.py')}", backend.root])
            return stdout
        elif tool_name == "search_function_or_class_definition_in_code":
            _, stdout, _ = backend.exec(["grep", "-rnIs", f"def {args['name']}", "-o", f"class {args['name']}", "--include=*.py", backend.root])
            return stdout
        elif tool_name == "run_command":
            cmd = args["command"]
            workdir = args.get("workdir", backend.root)
            code, stdout, stderr = backend.exec(cmd, workdir=workdir)
            return f"Exit Code: {code}\nStdout: {stdout}\nStderr: {stderr}"
        elif tool_name == "get_patch":
            backend.exec(["git", "-c", "core.fileMode=false", "add", "-N", "."])
            _, stdout, _ = backend.exec(["git", "-c", "core.fileMode=false", "diff"])
            return stdout
        else:
            return f"Unknown tool: {tool_name}"
    except Exception as e:
        return f"Tool execution error: {str(e)}"


def run_swebench_agent(task_file: str, output_file: str, model_name: str = "gpt-oss-120b"):
    start_time = time.perf_counter()

    with open(task_file, "r", encoding="utf-8") as f:
        task_data = json.load(f)
    task = SWEBenchTaskInput.model_validate(task_data)

    backend = DockerBackend(task.docker_image)
    client = LLMClient()
    steps: List[StepMetrics] = []

    initial_prompt = (
        f"Problem Statement:\n{task.problem_statement}\n\n"
        f"Hints:\n{task.hints_text}\n\n"
        "Investigate the repository, find the bug, apply the fix using `edit_file`, and verify your solution."
    )

    messages = [
        Message(role="system", content=SYSTEM_PROMPT),
        Message(role="user", content=initial_prompt),
    ]

    success = False
    final_patch = ""
    error_message: Optional[str] = None
    total_requests = 0

    try:
        for iteration in range(1, 31):  # Max 30 iterations
            step_start_time = time.perf_counter()

            answer = client.chat(
                messages=messages,
                model=model_name,
                temperature=0.1,
                max_tokens=2500,
            )
            total_requests += 1 + answer.retries
            llm_output = answer.content

            if "<finish>" in llm_output:
                # Retrieve final patch
                backend.exec(["git", "-c", "core.fileMode=false", "add", "-N", "."])
                _, diff_stdout, _ = backend.exec(["git", "-c", "core.fileMode=false", "diff"])
                final_patch = diff_stdout
                success = bool(final_patch.strip())

                request_time_ms = (time.perf_counter() - step_start_time) * 1000.0
                steps.append(StepMetrics(
                    step=iteration,
                    input_tokens=answer.input_tokens,
                    output_tokens=answer.output_tokens,
                    request_time_ms=request_time_ms,
                    timestamp=datetime.now().isoformat(),
                    model_name=answer.model,
                    llm_output=llm_output,
                    sandbox_input="Finish signal received.",
                    sandbox_output="Extracted git patch.",
                    retries=answer.retries,
                ))
                break

            tool_call = parse_tool_call(llm_output)
            if tool_call:
                tool_name = tool_call.get("name", "")
                tool_args = tool_call.get("arguments", {})
                sandbox_input = json.dumps(tool_call)
                sandbox_output = execute_backend_tool(backend, tool_name, tool_args)
            else:
                sandbox_input = "No structured tool call found."
                sandbox_output = "Error: Please provide a valid <tool_call> JSON block or output <finish> when done."

            request_time_ms = (time.perf_counter() - step_start_time) * 1000.0
            steps.append(StepMetrics(
                step=iteration,
                input_tokens=answer.input_tokens,
                output_tokens=answer.output_tokens,
                request_time_ms=request_time_ms,
                timestamp=datetime.now().isoformat(),
                model_name=answer.model,
                llm_output=llm_output,
                sandbox_input=sandbox_input,
                sandbox_output=sandbox_output,
                retries=answer.retries,
            ))

            messages.append(Message(role="assistant", content=llm_output))
            messages.append(Message(role="user", content=f"Tool Output:\n{sandbox_output}"))

    except Exception as e:
        error_message = f"Agent execution failed: {str(e)}"
    finally:
        backend.close()

    total_time_seconds = time.perf_counter() - start_time
    total_input_tokens = sum(s.input_tokens for s in steps)
    total_output_tokens = sum(s.output_tokens for s in steps)

    if not success and not error_message:
        error_message = "Exhausted 30 iterations without completing patch."

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

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(solution_output.model_dump_json(indent=2))


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="SWE-bench Task Solver Agent")
    parser.add_argument("--task-file", "--task-path", required=True, help="Path to task.json")
    parser.add_argument("--output", "--solution-path", required=True, help="Path to output solution.json")
    parser.add_argument("--model-name", default="gpt-oss-120b", help="Model identifier to use")
    args = parser.parse_args()

    run_swebench_agent(args.task_file, args.output, model_name=args.model_name)
