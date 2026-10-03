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
from src.agent_core.cli import extract_config, make_proxy
from src.agent_core.sandbox.core import Sandbox


SYSTEM_PROMPT = """You are an expert Python developer. You solve a task across MULTIPLE turns:
write and test a candidate function, read the real sandbox output, refine if needed, and only
then submit.

HARD RULE — ONE CODE BLOCK PER TURN:
Your response may contain EXACTLY ONE ```python ... ``` block. If you write more than one,
only the LAST block is executed — any earlier block (including a scratchpad test) is silently
discarded and never runs. This means you can NEVER test and submit in the same response.
Testing and submitting are always separate turns.

THE LOOP:
1. SCRATCHPAD TURN — write your candidate function plus print()-based test cases covering
   normal input, edge cases, and tricky return types (False vs -1 vs None, empty input, etc.).
   Do NOT call final_answer() this turn. End your turn here and wait.
2. You will receive the real stdout/stderr from running that code. Read it.
3. If anything failed or looked wrong: go back to step 1 with a fixed function. This can repeat
   as many times as needed — there is no penalty for iterating.
4. SUBMISSION TURN — only once your own printed test output has confirmed everything passes,
   write a turn containing ONLY a final_answer(...) call, with no other code above it.

CODE STYLE: internal reasoning only, no narration in code comments, no verbose docstrings,
keep the function itself compact.

- Your test cases must use assert, not just print() + a comment. A failed assert raises
  an error you will SEE in the sandbox output; a wrong print next to a correct-looking
  comment is easy to miss. Example:
      assert maximize_elements((...)) == ((7, 8), (5, 10), (3, 10), (8, 11)), "test 1 failed"
  If an assert fails, you MUST fix the function and test again before submitting —
  do not submit a function whose own test just failed.
- Every assert MUST include a message showing the actual value, e.g.:
    actual = foo(bar)
    assert actual == expected, f"Expected {expected}, got {actual}"
  A bare assert with no message gives you nothing to debug from when it fails — you will
  see only "AssertionError" and be stuck. Always make the failure show you what your
  function actually returned.
- Before concluding your FUNCTION is wrong, manually trace through your own algorithm by
  hand for the failing test case, step by step, and write out what it should produce.
  If your function's actual output matches your own by-hand trace, your function is
  correct and your ASSERTION's expected value is the thing that's wrong — fix the
  assertion, not the function. Do not rewrite a function that is already behaving
  exactly as its own logic dictates.

--- EXAMPLE: a scratchpad turn looks like this ---
```python
def add(a, b):
    return a + b

print("Test 1:", add(2, 3))   # expect 5
print("Test 2:", add(-1, 1))  # expect 0
```
(Nothing else in that response. Wait for the output before doing anything else.)

--- EXAMPLE: a submission turn, sent only AFTER the scratchpad output confirmed success,
looks like this — and contains nothing else ---
```python
final_answer('''def add(a, b):
    return a + b''')
```
"""

SYSTEM_PROMPT = """You are an expert Python developer solving one task per conversation.

ONE CODE BLOCK PER TURN: only the LAST ```python``` block in your response runs. Never put
scratchpad code and final_answer() in the same response — the scratchpad would be discarded.

LOOP:
1. SCRATCHPAD turn: write the function, then test it using the EXACT "Example Test Cases"
   given to you, copied verbatim — never reword them or recompute their expected values,
   they are the real grading tests. Add at most one extra assert of your own, only for a
   genuine edge case not already covered (empty input, single element, zero, negative).
2. Read the sandbox output.
3. A GIVEN test failing means your FUNCTION is wrong — fix the function, never edit a given
   assert's expected value, even if your function's logic seems to justify a different
   number.
   An assert YOU wrote failing is less certain — trace your function by hand for that input
   first. If your function's real behavior matches your own trace, your invented expected
   value was miscalculated: fix or drop that assert, not the function.
4. Repeat until every given test passes, then on its own turn call:
   final_answer('''<final function only, no tests>''')
5. If you are about to send the exact same code you sent last turn, stop — that means
    you are stuck. Do not resend it. Either change the function, or if the given tests
    already passed, submit immediately instead.

Use assert x == y, f"got {x}" — never a bare assert — so a failure shows the actual value.
Keep code compact: no comments, no docstrings.
as soon as the expected tests passes, run ```python final_answer("<function code>")```
"""

def extract_python_code(raw_text: str) -> str:
    """Extract Python code from markdown code blocks or return trimmed text."""
    if "```python" in raw_text:
        return raw_text.split("```python")[-1].split("```")[0].strip()
    elif "```" in raw_text:
        return raw_text.split("```")[-1].split("```")[0].strip()
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
                t.name.replace("-", "_"): make_proxy(
                    client, t, asyncio.get_running_loop())
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
    last_code = ""
    repeat_count = 0

    config.authorized_imports.extend(task.test_imports)

    with Sandbox(config, tools) as sb:
        for i in range(1, 11):
            print(i)
            step_start_time = time.perf_counter()
            user_feedback = ""

            # from pprint import pprint
            # pprint(messages)

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


            if extracted_code.strip() == (last_code or "").strip():
                repeat_count += 1
            else:
                repeat_count = 0
            last_code = extracted_code

            if repeat_count >= 1:
                print("REPEAT")
                user_feedback = (
                    "You repeated the exact same code as last turn and got the exact same error. "
                    "Repeating it again will not help. Stop trying to run a standalone script"
                    "understand the actual bug before writing any more repro scripts."
                )
            if repeat_count >= 3:
                error_message = "Aborted: agent stuck repeating identical actions."
                break

            last_code = extracted_code

            exec_result = sb.execute(extracted_code)

            # if exec_result.final_answer is not None:
            #     break
#                 candidate_solution = exec_result.final_answer
#
#                 test_blocks = []
#                 for test in task.test_list:
#                     dump_test = json.dumps(test)
#                     test_blocks.append(
#                         f"try:\n"
#                         f"    {test}\n"
#                         f"except Exception as e:\n"
#                         f"    failures.append(f'FAILED TEST: ' + {dump_test} + f' | Error: {{type(e)}}: {{e}}')"
#                     )
#
#                 imports = "\n".join([f"import {lib}" for lib in task.test_imports])
#
#                 full_tests = (
#                     imports +
#                     f"\n{candidate_solution}\n\n"
#                     "failures = []\n"
#                     + "\n".join(test_blocks) + "\n"
#                     "if failures:\n"
#                     "    raise AssertionError('\\n'.join(failures))\n"
#                 )
#
#                 test_exec_result = sb.execute(full_tests)
#
#                 passed = test_exec_result.error is None
#
#                 if passed:
#                     sandbox_output = "All task unit tests passed successfully."
#                     success = True
#                     final_solution = candidate_solution
#                 else:
#                     output_parts = []
#                     if test_exec_result.stdout:
#                         output_parts.append(f"stdout:\n{test_exec_result.stdout}")
#                     if test_exec_result.stderr:
#                         output_parts.append(f"stderr:\n{test_exec_result.stderr}")
#                     if test_exec_result.error:
#                         output_parts.append(f"error:\n{test_exec_result.error}")
#                     sandbox_output = (
#                         "Submitted solution failed task assertions:\n"
#                         + "\n".join(output_parts).strip()
#                     )

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

            # if success:
            #     break

            messages.append(Message(role="assistant", content=llm_output))

            # if exec_result.final_answer is not None:
            #     user_feedback += (
            #         f"Your submitted final solution failed test assertions:\n{sandbox_output}\n\n"
            #         "Please fix your function and call `final_answer(...)` again with the corrected code."
            #     )
            # else:
            #     user_feedback += (
            #         f"Sandbox execution output:\n{sandbox_output}\n\n"
            #         "IMPORTANT: You defined/executed python code, but you did NOT call `final_answer(...)`.\n"
            #         "If you are confident in your solution, submit it by calling `final_answer('''<your code>''')`."
            #     )

            messages.append(Message(role="user", content=sandbox_output))

            messages.append(Message(role="user", content=user_feedback))

            if exec_result.final_answer is not None:
                final_solution = exec_result.final_answer
                break

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
    parser.add_argument("--model-name", default="codestral", help="Model identifier to use")
    args = parser.parse_args()
    asyncio.run(run_mbpp_agent(args.task_file, args.output, model_name=args.model_name))
