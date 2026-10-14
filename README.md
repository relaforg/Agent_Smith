*This project has been created as part of the 42 curriculum by relaforg, nahecre.*

# Agent Smith

## Description

Agent Smith is a small autonomous coding agent. You give it a programming task, it asks an LLM what to do, runs the code the model writes in a sandbox, shows the model what happened, and repeats until the model submits an answer or runs out of iterations.

It is evaluated on two benchmarks:

- **MBPP**: short Python exercises ("write a function that ..."). The agent has to return the source of a function that passes hidden tests.
- **SWE-bench**: real GitHub issues from projects like Django, SymPy, xarray or scikit-learn. The agent works on the actual repository inside a Docker container and has to return a git patch that fixes the issue.

## Instructions

### Requirements

- Linux. The sandbox relies on `os.fork`, `resource.setrlimit` and `SIGALRM`, so it will not run on Windows.
- Python 3.10 and [uv](https://docs.astral.sh/uv/).
- A running Docker daemon that your user can access. Both MCP servers start containers.
- At least one LLM API key.

### Installation

```bash
uv sync
```

Put your keys in a `.env` file at the root of the repository. It is loaded automatically:

```bash
MISTRAL_API_KEY=...
GROQ_API_KEY=...
OPENROUTER_API_KEY=...
GOOGLE_API_KEY=...
```

Only the providers you have a key for are loaded. You can also give a provider several keys with numbered variables (`MISTRAL_API_KEY_1`, `MISTRAL_API_KEY_2`, ...). The provider then switches to the next key when it hits a rate limit, usefull to go around free tier rate limits.

### Running the agents

```bash
# MBPP
uv run -m agent_mbpp --task-file tasks/mbpp/task1.json \
    --output solution.json --model-name codestral

# SWE-bench (pulls the instance's Docker image the first time, which can take a while)
uv run -m agent_swebench --task-file tasks/swebench/task1.json \
    --output solution.json --model-name codestral
```

Useful options:

- `--model-name`: a model alias. Available aliases: `codestral`, `ministral-3`, `ministral-8`, `ministral-14` (Mistral), `gpt-oss-120b`, `gpt-oss-20b`, `qwen-3.8-27b` (Groq), `gemma-26`, `gemma-31` (OpenRouter), `gemini-flash` (Google).
- `--provider-url` / `--api-key`: use any OpenAI-compatible endpoint, for instance a local Ollama or vLLM server. With an unknown URL the model name is sent as is: `--provider-url http://localhost:11434/v1 --model-name qwen2.5-coder`.
- `--debug`: prints the prompts, the raw LLM replies, the sandbox results and the MCP server logs.

The agent writes a `solution.json` with the solution, a `success` flag and metrics for every step (tokens, latency, retries, the code sent to the sandbox and what came back).

### Using the sandbox on its own

The sandbox also comes with a small REPL, which is handy to poke at it or to try the MCP tools by hand:

```bash
uv run sandbox                         # default config
uv run sandbox sandbox_template.json   # custom config
uv run sandbox --mcp-stdio "python mcp_tools_swebench.py"   # with the SWE-bench tools
uv run sandbox --mcp-server http://localhost:8000/mcp       # MCP server over HTTP
```

The SWE-bench server is configured through environment variables: `DOCKER_IMAGE` (the image to start), `EVAL_SCRIPT` (the script `run_tests()` runs), and `TESTBED_PATH`. If `TESTBED_PATH` is set, the server works directly on a local checkout instead of a container, which is much faster for testing the tools.

## System architecture

```
┌──────────────────────── host process ────────────────────────┐
│                                                              │
│   agent loop  (agent_mbpp / agent_swebench)                  │
│     │      ▲                                                 │
│     │      │ LLMAnswer                                       │
│     ▼      │                                                 │
│   LLMClient ──► Mistral / Groq / OpenRouter / Google / any   │
│                 OpenAI-compatible URL                        │
│     │                                                        │
│     │ execute(code)        tool_call  ┌──────────────────┐   │
│     ▼                     ◄────────── │                  │   │
│   Sandbox (parent side)               │ forked child:    │   │
│     │  pipes, JSON lines  ──────────► │ restricted exec  │   │
│     │                     tool_result └──────────────────┘   │
│     │ MCP client (stdio)                                     │
└─────┼────────────────────────────────────────────────────────┘
      ▼
  MCP server (mcp_tools_mbpp.py / mcp_tools_swebench.py)
      │  docker exec
      ▼
  Docker container, network disabled
  (python:3.10 for MBPP, the SWE-bench instance image with /testbed)
```

The code is split like this:

```
src/
├── agent_core/
│   ├── sandbox/core.py     # the sandbox
│   ├── llm/                # LLM client, retry decorator, providers/
│   ├── cli.py              # sandbox REPL + MCP client helpers (make_proxy, open_session)
│   └── models.py           # SandboxConfig, ExecutionResult, Message, BaseProvider...
├── agent_mbpp/             # MBPP agent loop + terminal display
├── agent_swebench/         # SWE-bench agent loop + terminal display
└── models_public.py        # task / solution formats expected by the grader
mcp_tools_mbpp.py           # MCP server: run_tests
mcp_tools_swebench.py       # MCP server: repository tools
runner.py                   # copied into the MBPP container, runs the asserts
refs.py                     # copied into the SWE-bench container, symbol search
sandbox_template.json       # sandbox limits used by both agents
```

The important point is that **the code the LLM writes never touches anything real directly**. It runs in the restricted sandbox, and everything that has an effect (running tests, reading or editing the repository, running a command) goes through a tool. Tools are executed by the parent process, which forwards them to an MCP server, which runs them in a Docker container without network.

## Agent loop

Both agents follow the same loop:

1. Start the MCP server as a subprocess over stdio, list its tools and wrap each one in a regular Python function.
2. Open a sandbox with those functions in its namespace.
3. Build the prompt: a system prompt that describes the output format and the tools, then the task.
4. Ask the LLM. Extract the Python block from its reply (if there are several, the last one).
5. Run that code in the sandbox.
6. If the code called `final_answer(...)`, stop: that is the solution. Otherwise append the model's reply and the sandbox output (stdout, stderr, traceback) to the conversation and go back to 4.

The loop stops on `final_answer`, when the iteration budget is spent (10 for MBPP, 30 for SWE-bench), or when the LLM call fails for good. The namespace persists between steps, so a variable defined at step 2 is still there at step 5.

The LLM call is synchronous while the MCP session is async. The agent loop therefore runs in a worker thread, and the tool proxies send their requests back to the event loop with `asyncio.run_coroutine_threadsafe`.

### MBPP specifics

The MBPP prompt is very strict about the shape of the answer. The model has to reply with exactly this structure:

```python
code = r'''
def my_function(...):
    ...
'''
result = run_tests(code, TESTS)
print(result["output"])
if result["success"]:
    final_answer(code)
```

`TESTS` is injected into the sandbox before the first step. So in a single block the model writes the function, tests it, and submits it only if it passes. Strong models solve almost every task in one iteration. When a test fails, `runner.py` re-evaluates the left side of the failing `assert f(x) == expected` and the model gets the value its function actually returned, not just `AssertionError`. That turned out to be a useful feedback for fixing a wrong LLM answer.

The prompt also lists the allowed imports and the documentation of each tool, generated from the sandbox config and the MCP tool descriptions. When the model has used half of its output token budget, we add a message asking it to finish.

### SWE-bench specifics

SWE-bench needs many more steps, so the loop has a few guards:

- **Repetition**: if the model sends exactly the same code as the previous step, it is told so. After 3 identical blocks in a row the run is aborted, since it will not get out of it on its own.
- **Truncation**: when a reply is cut off by the token limit, the code usually ends inside a string and Python reports an "unterminated string literal". We detect that and tell the model its code was truncated, otherwise it tends to think it made a quoting mistake and sends the same long script again.
- After each step the observation reminds the model to submit with `final_answer(get_patch())` once the tests pass.
- There is a 5 second pause between steps to stay under the free tier rate limits.

## Sandbox design

The sandbox (`src/agent_core/sandbox/core.py`) is used as a context manager:

```python
with Sandbox(config, tools) as sb:
    result = sb.execute("print(1 + 1)")
```

### Process isolation

`__enter__` forks. The child is the one that runs the LLM's code. The two processes talk through two pipes with one JSON message per line (`execute`, `result`, `tool_call`, `tool_result`, `close`), validated with pydantic.

Forking means that a crash, an infinite loop or a memory blow-up in the generated code only kills the child, never the agent. `execute()` raises `SandboxDied` if the child disappears. On exit we send `close`, give the child 5 seconds to exit, then `SIGQUIT` it and reap it so no zombie stays behind.

### Restricting Python

The child executes the code with its own `__builtins__`, and the code is checked before it runs:

- **AST check**: any name or attribute starting with `__` is rejected (except `__name__` / `__main__`). This blocks the classic escapes like `().__class__.__bases__[0].__subclasses__()`. Relative imports and `import *` are rejected as well.
- **Removed builtins**: `eval`, `exec`, `compile`, `getattr`, `globals`, `vars`, `input`, `breakpoint`, `help`, `exit`, `quit`... Without `getattr` the AST check cannot be bypassed with `getattr(x, "__" + "class__")`.
- **Imports on an allowlist**: `__import__` is replaced. A module is accepted only if it matches a pattern of `authorized_imports` (`"math"`, `"collections.*"`...). Anything else raises a normal `ImportError`, which the model can catch and recover from.
- **Module proxies**: an allowed module is not handed over directly. It is wrapped in a read-only proxy that hides private attributes and checks submodules. Without it, `import random; random._os.system(...)` would work, because an allowed module gives access to every module it imported itself.
- **`open()`**: only absolute paths, resolved with `realpath` (so a symlink cannot be used to get out), and only inside `allowed_directories`. File descriptors and bytes paths are refused.

### Resource limits

- **Memory**: `RLIMIT_AS` is set in the child to `max_memory_mb`. A too large allocation raises `MemoryError`, which is caught and reported with `memory_exceeded=True`.
- **Time**: `setitimer` arms a `SIGALRM` for `max_execution_time_seconds`.
- The timer is **paused during tool calls**.

### Tools inside the sandbox

The tools are not in the child. For each tool the child gets a proxy function that serializes the call (`name`, `args`, `kwargs`), sends it to the parent and waits. The parent runs the real function, captures its stdout/stderr, and sends back the value or the error. On the child side the error is rebuilt as the same builtin exception type when possible (`ValueError` stays `ValueError`), `RuntimeError` otherwise. To the model a tool looks like any other function.

`final_answer` is the only function that lives in the child. It does not raise: it records the answer, the rest of the block still runs, and the answer is returned in the `ExecutionResult`.

### Configuration

The limits come from a JSON file validated by `SandboxConfig` (unknown keys are rejected):

```json
{
  "max_memory_mb": 2048,
  "max_execution_time_seconds": 3
}
```

`authorized_imports` and `allowed_directories` can be set the same way. The default allows math, collections, itertools, re, functools, datetime, etc.

## Tool implementation

All tools are served by MCP servers built with `FastMCP` and started over stdio by the agent. On the client side, `make_proxy` (in `cli.py`) turns each MCP tool into a Python function with a real `inspect.Signature` built from the tool's JSON schema. Arguments are bound against it before the call, so a missing argument is a normal `TypeError` the model can read, and the signature and docstring end up in the manual given to the model. Tool names have `-` replaced by `_` so they are valid Python identifiers.

### MBPP: `run_tests(code, test_list)`

The server starts a `python:3.10` container without network. `run_tests` copies a payload into it and runs `runner.py` under `timeout -s KILL 10`. The runner executes the function's source, then each assert separately, and reports which ones failed together with the actual value. The result is a JSON string. Since `json` is not on the sandbox allowlist, the agent decodes it on the host side so the model gets a plain dict.

### SWE-bench

The server starts the instance's Docker image with networking disabled and works on `/testbed`.

| Tool | How it works |
|---|---|
| `read_file(filepath, start_line, end_line)` | `sed -n` in the container, lines prefixed with their number so the model can cite them. |
| `edit_file(filepath, old_str, new_str)` | Reads the file, replaces the exact string, writes it back through a tar archive (`put_archive`). Fails if `old_str` is not found. |
| `list_files(directory, pattern)` | `find -maxdepth 1 -type f -name pattern`, returns file names. |
| `search_code(pattern, file_pattern)` | `grep -rnIH`. A glob like `django/http/*.py` is split into a directory to walk and a name for `--include`, because `--include` only matches file names. Paths that leave the repository are refused. |
| `search_function_or_class_definition_in_code(name)` | Runs `refs.py` in the container. |
| `find_references(name, filepath, line)` | Same, starting from a given occurrence. |
| `run_tests()` | Runs the instance's evaluation script with `bash -c`, returns `(exit_code, logs)`. |
| `get_patch()` | `git add -N .` then `git diff`, so newly created files show up in the patch too. |
| `run_command(command, workdir)` | Runs any command in the container, returns the exit code, stdout and stderr. |

`refs.py` deserves a word. It uses **jedi** for real definitions and references, and falls back to an AST walk when jedi is not available or finds nothing. The container has no network, so jedi cannot be installed there: the server copies `jedi` and `parso` from our own virtualenv into the container.

Containers carry a label and are removed when the server exits.

### LLM client

`LLMClient` routes a model alias to a provider that serves exactly that model. It never falls back to a different model. If a provider fails, another provider serving the same model is tried. Each call is wrapped in a retry decorator with exponential backoff that respects `Retry-After`, and providers rotate between their API keys on 429 and 5xx errors.

## Benchmark results and analysis

All raw results are in `results/`: one JSON (the agent's output) and one log per run, and the grader's verdict in the `*_validation/` folders. "Passed" means the grader checked both the solution (hidden tests for MBPP, the instance's evaluation for SWE-bench) and the limits (MBPP: 10 iterations, 6,000 input tokens, 1,500 output tokens, 120 s; SWE-bench: 30 iterations, 300,000 input tokens, 10,000 output tokens, 900 s).

Most models were used through free tiers, so some runs failed because of the provider, not the agent. We report them anyway, marked as such.

### MBPP (10 tasks)

| Model | Passed | Avg iterations | Avg input tokens | Avg output tokens | Avg time |
|---|---|---|---|---|---|
| codestral | **10/10** | 1.1 | 613 | 80 | 1.1 s |
| gpt-oss-120b | **10/10** | 1.0 | 597 | 286 | 1.4 s |
| gemma-26 | **10/10** | 1.0 | 580 | 149 | 4.4 s |
| ministral-14b | **10/10** | 1.3 | 816 | 92 | 5.4 s |
| qwen-3.8 | 9/10 | 1.9 | 2,151 | 171 | 2.7 s |
| ministral-3b | 3/10 | 7.7 | 6,197 | 419 | 4.0 s |
| ministral-8b | 0/10 | 10.0 | 7,886 | 491 | 9.3 s |
| gemini-flash | not graded (10/10 submitted) | 1.0 | 581 | 100 | 126.3 s |
| gemma-31 | not graded (8/10 runs failed on the API) | | | | |

### SWE-bench (7 tasks)

| Model | Passed | Submitted | Avg iterations | Avg input tokens | Avg output tokens | Avg time | Notes |
|---|---|---|---|---|---|---|---|
| codestral | **4/7** | 6 | 11.9 | 74,404 | 343 | 84 s | |
| qwen-3.8 | **4/7** | 4 | 7.0 | 25,795 | 288 | 137 s | 3 runs failed on the API |
| ministral-3b | **4/7** | 5 | 22.1 | 76,875 | 1,435 | 129 s | |
| gemma-26 | 1/7 | 2 | 12.4 | 98,011 | 5,464 | 458 s | 3 API failures, 1 repetition abort |
| gpt-oss-120b | 0/7 | 1 | 1.1 | 3,033 | 432 | 15 s | 6 runs failed on the API |
| ministral-14b | 0/7 | 0 | 30.0 | 79,865 | 729 | 234 s | always ran out of iterations |
| ministral-8b | 0/7 | 0 | 30.0 | 82,209 | 798 | 172 s | always ran out of iterations |
| gemini-flash | not graded | 0 | | | | | 7 runs failed on the API |
| gemma-31 | 0/3 | 0 | | | | | only 3 runs, all failed on the API |

More details in the [Benchmark report](BENCHMARK_REPORT.md).

## Resources

### References

### How we used AI

- This README was drafted with Claude (Claude Code) from the actual source code and the result files, then reviewed and edited by us.
- Compiling the dense result outputs for the BENCHMARK_REPORT.md
- Explain several topic such as what is a MCP servers
