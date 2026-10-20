"""Terminal display of an MBPP agent run: one block per step, a summary,
and with --debug everything the normal display hides."""
import os
import sys
from typing import Optional

from models_public import MBPPTaskInput, StepMetrics
from agent_core.models import ExecutionResult, LLMAnswer


# Colors only in a real terminal: a redirected log stays free of escape codes
_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _style(text: str, ansi: str) -> str:
    return f"\033[{ansi}m{text}\033[0m" if _COLOR else text


def _rule(title: str, char: str, width: int = 72) -> str:
    return f"{char * 2} {title} {char * max(0, width - len(title) - 4)}"


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


def print_header(task: MBPPTaskInput, model_name: str,
                 tools: list[str]) -> None:
    tools_info = ", ".join(tools) if tools else "no MCP tools"
    print(_style(_rule(
        f"MBPP task {task.task_id} · {model_name} · {tools_info}", "━"), "1"))
    print(task.task_definition)


def print_step(step: StepMetrics, max_iterations: int, done: bool,
               repeated: bool) -> None:
    """done: the step reached final_answer."""
    print()
    print(_style(_rule(
        f"Step {step.step}/{max_iterations} · in {step.input_tokens}"
        f" · out {step.output_tokens} · {step.request_time_ms / 1000:.1f}s",
        "─"), "36"))
    print(_style(_indent(step.sandbox_input or "(empty reply)"), "2"))
    if done:
        print(_style("✓ final_answer submitted", "32;1"))
        return
    print(_style(_indent(step.sandbox_output), "33"))
    if repeated:
        print(_style("↻ Same code as the previous step", "33"))


def print_summary(success: bool, steps: list[StepMetrics], seconds: float,
                  error: Optional[str]) -> None:
    total_in = sum(s.input_tokens for s in steps)
    total_out = sum(s.output_tokens for s in steps)
    verdict = f"✓ Solved in {len(steps)} step(s)" if success \
        else f"✗ Not solved after {len(steps)} step(s)"
    print()
    print(_style(_rule(
        f"{verdict} · in {total_in} · out {total_out} · {seconds:.1f}s",
        "━"), "32;1" if success else "31;1"))
    if error and not success:
        print(_style(error, "31"))


def print_debug(title: str, body: str) -> None:
    print(_style(f"  [debug] {title}", "35;1"))
    print(_style(_indent(body.strip() or "(empty)"), "35"))


def print_debug_step(answer: LLMAnswer, temperature: float, max_tokens: int,
                     exec_result: ExecutionResult, feedback: str) -> None:
    """Everything the normal display hides, for one step."""
    print_debug("llm call", (
        f"provider={answer.provider} model={answer.model}"
        f" temperature={temperature:.1f} max_tokens={max_tokens}\n"
        f"finish_reason={answer.finish_reason} retries={answer.retries}"
        f" latency={answer.latency_ms:.0f}ms"))
    print_debug("raw reply", answer.content)
    print_debug("sandbox result", (
        f"timed_out={exec_result.timed_out}"
        f" memory_exceeded={exec_result.memory_exceeded}\n"
        f"stdout:\n{exec_result.stdout}\nstderr:\n{exec_result.stderr}\n"
        f"error:\n{exec_result.error}"))
    print_debug("feedback sent to the model", feedback)
