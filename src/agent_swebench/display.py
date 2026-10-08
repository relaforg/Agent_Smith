"""Terminal display of a SWE-bench agent run: one block per step, a summary
with the submitted patch, and with --debug everything the normal display
hides."""
import os
import sys
from typing import Optional

from models_public import SWEBenchTaskInput, StepMetrics
from agent_core.models import ExecutionResult, LLMAnswer


# Colors only in a real terminal: a redirected log stays free of escape codes
_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ

# Tool outputs (run_tests logs, grep results...) can be huge: the normal
# display keeps the head and the tail, --debug and the JSON output keep all
MAX_OUTPUT_LINES = 40
MAX_PROBLEM_LINES = 25


def _style(text: str, ansi: str) -> str:
    return f"\033[{ansi}m{text}\033[0m" if _COLOR else text


def _rule(title: str, char: str, width: int = 72) -> str:
    return f"{char * 2} {title} {char * max(0, width - len(title) - 4)}"


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


def _clip(text: str, max_lines: int) -> str:
    """Keep the first and last lines of a long text, drop the middle."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    head = max_lines // 2
    tail = max_lines - head
    hidden = len(lines) - max_lines
    return "\n".join(
        lines[:head] + [f"... ({hidden} lines hidden) ..."] + lines[-tail:])


def _print_patch(patch: str) -> None:
    """A git diff, colored line by line."""
    for line in patch.splitlines():
        if line.startswith(("+++", "---", "diff ", "index ")):
            ansi = "1"
        elif line.startswith("@@"):
            ansi = "36"
        elif line.startswith("+"):
            ansi = "32"
        elif line.startswith("-"):
            ansi = "31"
        else:
            ansi = "2"
        print(_style(f"    {line}", ansi))


def print_header(task: SWEBenchTaskInput, model_name: str,
                 tools: list[str]) -> None:
    tools_info = ", ".join(tools) if tools else "no MCP tools"
    print(_style(_rule(
        f"SWE-bench {task.instance_id} · {task.repo} · {model_name}",
        "━"), "1"))
    print(_style(f"tools: {tools_info}", "2"))
    print()
    print(_clip(task.problem_statement.strip(), MAX_PROBLEM_LINES))


def print_step(step: StepMetrics, max_iterations: int, done: bool,
               repeated: bool, note: Optional[str] = None) -> None:
    """done: the step reached final_answer.
    note: a message the agent injected instead of the raw sandbox output
    (repeat warning, truncated code...)."""
    print()
    print(_style(_rule(
        f"Step {step.step}/{max_iterations} · in {step.input_tokens}"
        f" · out {step.output_tokens} · {step.request_time_ms / 1000:.1f}s",
        "─"), "36"))
    print(_style(_indent(step.sandbox_input or "(empty reply)"), "2"))
    if done:
        print(_style("✓ final_answer submitted", "32;1"))
        return
    print(_style(_indent(_clip(step.sandbox_output, MAX_OUTPUT_LINES)), "33"))
    if note:
        print(_style(f"! {note}", "31"))
    if repeated:
        print(_style("↻ Same code as the previous step", "33"))


def print_summary(success: bool, steps: list[StepMetrics], seconds: float,
                  error: Optional[str], patch: str = "") -> None:
    total_in = sum(s.input_tokens for s in steps)
    total_out = sum(s.output_tokens for s in steps)
    verdict = f"✓ Patch submitted in {len(steps)} step(s)" if success \
        else f"✗ No patch submitted after {len(steps)} step(s)"
    print()
    print(_style(_rule(
        f"{verdict} · in {total_in} · out {total_out} · {seconds:.1f}s",
        "━"), "32;1" if success else "31;1"))
    if error and not success:
        print(_style(error, "31"))
    if patch.strip():
        print(_style("  submitted patch", "1"))
        _print_patch(patch)


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
