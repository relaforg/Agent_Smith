# Model Benchmark Report — Agent Smith

This report compares 9 models served by 4 providers. All of them ran with the same agent, the same prompts and the same tools, on 7 SWE-bench Verified tasks and 10 MBPP tasks. All figures come from the `solution.json` files and the moulinette validation outputs.

## 1. Setup

### Models and providers

| Model | API identifier | Provider |
|---|---|---|
| codestral | `codestral-2508` | Mistral |
| ministral-14b | `ministral-14b-2512` | Mistral |
| ministral-8b | `ministral-8b-2512` | Mistral |
| ministral-3b | `ministral-3b-2512` | Mistral |
| qwen-3.8 | `qwen/qwen3.8-27b` | Groq |
| gpt-oss-120b | `openai/gpt-oss-120b` | Groq |
| gemma-26 | `google/gemma-4-26b-a4b-it:free` | OpenRouter |
| gemma-31 | `google/gemma-4-31b-it:free` | OpenRouter |
| gemini-flash | `gemini-3.8-flash` | Google AI Studio |

All providers are used on their free tier, with several API keys per provider.

### Tasks

| # | SWE-bench Verified | Repository |
|---|---|---|
| 1 | `django__django-11066` | django |
| 2 | `pydata__xarray-4629` | xarray |
| 3 | `django__django-13741` | django |
| 4 | `sympy__sympy-18189` | sympy |
| 5 | `sympy__sympy-23534` | sympy |
| 6 | `scikit-learn__scikit-learn-13439` | scikit-learn |
| 7 | `django__django-13089` | django |

These tasks cover 4 repositories, each with its own test runner (Django, pytest, sympy). Difficulty varies: some issues describe a one-line fix, others require real diagnosis.

**MBPP**: tasks 233, 472, 262, 439, 287, 431, 417, 470, 105 and 167.

### Limits applied (from the subject)

| Benchmark | Iterations | Input tokens | Output tokens | Timeout |
|---|---:|---:|---:|---:|
| MBPP | 10 | 6,000 | 1,500 | 120 s |
| SWE-bench | 30 | 300,000 | 10,000 | 900 s |

---

## 2. SWE-bench results

### Overview

| Model | Provider | Pass | Avg. iter. | Avg. input tokens | Avg. output tokens | Avg. time |
|---|---|---:|---:|---:|---:|---:|
| **codestral** | Mistral | **4/7** | 11.9 | 74,404 | 343 | 84 s |
| **qwen-3.8** | Groq | **4/7** | 7.0 | 25,795 | 288 | 137 s |
| **ministral-3b** | Mistral | **4/7** | 22.1 | 76,875 | 1,435 | 128 s |
| gemma-26 | OpenRouter | 1/7 | 12.4 | 98,011 | 5,464 | 458 s |
| gpt-oss-120b | Groq | 0/7 | 1.1 | 3,033 | 432 | 15 s |
| ministral-14b | Mistral | 0/7 | 30.0 | 79,865 | 729 | 234 s |
| ministral-8b | Mistral | 0/7 | 30.0 | 82,209 | 798 | 172 s |
| gemini-flash | Google AI Studio | 0/7 | 1.4 | 6,448 | 53 | 372 s |
| gemma-31 | OpenRouter | 0/7 | 0.3 | 706 | 10 | 517 s |

Three models solve 4 tasks out of 7. What separates them is cost: qwen-3.8 gets there with 7 iterations and about 26k input tokens per task, versus 12 iterations and 74k tokens for codestral, and 22 iterations and 77k tokens for ministral-3b.

### Model × task breakdown

#### codestral

| # | Task | Result | Iter. | Input tokens | Output tokens | Time | End of run |
|---|---|---|---:|---:|---:|---:|---|
| 1 | `django__django-11066` | ✅ | 8 | 20,206 | 310 | 46 s | patch submitted |
| 2 | `pydata__xarray-4629` | ✅ | 7 | 20,405 | 142 | 39 s | patch submitted |
| 3 | `django__django-13741` | ❌ | 12 | 24,242 | 304 | 65 s | patch submitted (incorrect) |
| 4 | `sympy__sympy-18189` | ✅ | 5 | 177,801 | 116 | 44 s | patch submitted |
| 5 | `sympy__sympy-23534` | ❌ | 30 | 110,929 | 857 | 198 s | 30 iterations without a patch |
| 6 | `scikit-learn__scikit-learn-13439` | ✅ | 12 | 145,373 | 266 | 74 s | patch submitted |
| 7 | `django__django-13089` | ❌ | 9 | 21,870 | 408 | 122 s | patch submitted (incorrect) |

#### qwen-3.8

| # | Task | Result | Iter. | Input tokens | Output tokens | Time | End of run |
|---|---|---|---:|---:|---:|---:|---|
| 1 | `django__django-11066` | ✅ | 5 | 15,046 | 114 | 26 s | patch submitted |
| 2 | `pydata__xarray-4629` | ✅ | 6 | 18,099 | 269 | 62 s | patch submitted |
| 3 | `django__django-13741` | ❌ | 15 | 71,263 | 351 | 452 s | HTTP 413 (tokens-per-minute limit) |
| 4 | `sympy__sympy-18189` | ❌ | 4 | 10,321 | 148 | 50 s | HTTP 400 |
| 5 | `sympy__sympy-23534` | ✅ | 7 | 27,500 | 309 | 147 s | patch submitted |
| 6 | `scikit-learn__scikit-learn-13439` | ❌ | 6 | 22,932 | 535 | 119 s | HTTP 413 (tokens-per-minute limit) |
| 7 | `django__django-13089` | ✅ | 6 | 15,402 | 291 | 103 s | patch submitted |

#### ministral-3b

| # | Task | Result | Iter. | Input tokens | Output tokens | Time | End of run |
|---|---|---|---:|---:|---:|---:|---|
| 1 | `django__django-11066` | ✅ | 10 | 24,871 | 325 | 51 s | patch submitted |
| 2 | `pydata__xarray-4629` | ✅ | 13 | 38,954 | 529 | 73 s | patch submitted |
| 3 | `django__django-13741` | ✅ | 28 | 91,032 | 1,348 | 161 s | patch submitted |
| 4 | `sympy__sympy-18189` | ✅ | 16 | 45,321 | 513 | 84 s | patch submitted |
| 5 | `sympy__sympy-23534` | ❌ | 30 | 92,800 | 1,295 | 188 s | 30 iterations without a patch |
| 6 | `scikit-learn__scikit-learn-13439` | ❌ | 30 | 102,331 | 1,518 | 169 s | 30 iterations without a patch |
| 7 | `django__django-13089` | ❌ | 28 | 142,814 | 4,519 | 174 s | patch submitted (incorrect) |

#### gemma-26

| # | Task | Result | Iter. | Input tokens | Output tokens | Time | End of run |
|---|---|---|---:|---:|---:|---:|---|
| 1 | `django__django-11066` | ✅ | 7 | 44,720 | 4,026 | 133 s | patch submitted |
| 2 | `pydata__xarray-4629` | ❌ | 4 | 18,839 | 2,366 | 73 s | empty patch submitted |
| 3 | `django__django-13741` | ❌ | 22 | 193,721 | 14,468 | 887 s | quota exhausted (10,000 output-token limit exceeded) |
| 4 | `sympy__sympy-18189` | ❌ | 2 | 5,471 | 1,097 | 398 s | quota exhausted |
| 5 | `sympy__sympy-23534` | ❌ | 30 | 256,186 | 6,965 | 695 s | 30 iterations without a patch |
| 6 | `scikit-learn__scikit-learn-13439` | ❌ | 3 | 6,904 | 701 | 389 s | quota exhausted |
| 7 | `django__django-13089` | ❌ | 19 | 160,237 | 8,627 | 628 s | loop detected |

#### gpt-oss-120b

| # | Task | Result | Iter. | Input tokens | Output tokens | Time | End of run |
|---|---|---|---:|---:|---:|---:|---|
| 1 | `django__django-11066` | ❌ | 2 | 4,865 | 291 | 9 s | patch submitted (incorrect) |
| 2 | `pydata__xarray-4629` | ❌ | 2 | 6,025 | 934 | 32 s | HTTP 400 `tool_use_failed` |
| 3 | `django__django-13741` | ❌ | 0 | 0 | 0 | 0 s | HTTP 400 `tool_use_failed` |
| 4 | `sympy__sympy-18189` | ❌ | 4 | 10,340 | 1,800 | 60 s | HTTP 400 |
| 5 | `sympy__sympy-23534` | ❌ | 0 | 0 | 0 | 1 s | HTTP 400 `tool_use_failed` |
| 6 | `scikit-learn__scikit-learn-13439` | ❌ | 0 | 0 | 0 | 1 s | HTTP 400 `tool_use_failed` |
| 7 | `django__django-13089` | ❌ | 0 | 0 | 0 | 0 s | HTTP 400 `tool_use_failed` |

#### ministral-14b

| # | Task | Result | Iter. | Input tokens | Output tokens | Time | End of run |
|---|---|---|---:|---:|---:|---:|---|
| 1 | `django__django-11066` | ❌ | 30 | 88,936 | 692 | 232 s | 30 iterations without a patch |
| 2 | `pydata__xarray-4629` | ❌ | 30 | 92,398 | 733 | 241 s | 30 iterations without a patch |
| 3 | `django__django-13741` | ❌ | 30 | 67,339 | 628 | 226 s | 30 iterations without a patch |
| 4 | `sympy__sympy-18189` | ❌ | 30 | 96,203 | 880 | 233 s | 30 iterations without a patch |
| 5 | `sympy__sympy-23534` | ❌ | 30 | 65,925 | 804 | 248 s | 30 iterations without a patch |
| 6 | `scikit-learn__scikit-learn-13439` | ❌ | 30 | 74,904 | 595 | 227 s | 30 iterations without a patch |
| 7 | `django__django-13089` | ❌ | 30 | 73,353 | 770 | 233 s | 30 iterations without a patch |

#### ministral-8b

| # | Task | Result | Iter. | Input tokens | Output tokens | Time | End of run |
|---|---|---|---:|---:|---:|---:|---|
| 1 | `django__django-11066` | ❌ | 30 | 91,752 | 881 | 176 s | 30 iterations without a patch |
| 2 | `pydata__xarray-4629` | ❌ | 30 | 95,426 | 750 | 168 s | 30 iterations without a patch |
| 3 | `django__django-13741` | ❌ | 30 | 72,177 | 779 | 172 s | 30 iterations without a patch |
| 4 | `sympy__sympy-18189` | ❌ | 30 | 96,090 | 946 | 170 s | 30 iterations without a patch |
| 5 | `sympy__sympy-23534` | ❌ | 30 | 67,849 | 640 | 174 s | 30 iterations without a patch |
| 6 | `scikit-learn__scikit-learn-13439` | ❌ | 30 | 75,403 | 680 | 175 s | 30 iterations without a patch |
| 7 | `django__django-13089` | ❌ | 30 | 76,763 | 907 | 170 s | 30 iterations without a patch |

#### gemini-flash

| # | Task | Result | Iter. | Input tokens | Output tokens | Time | End of run |
|---|---|---|---:|---:|---:|---:|---|
| 1 | `django__django-11066` | ❌ | 10 | 45,135 | 371 | 890 s | quota exhausted |
| 2 | `pydata__xarray-4629` | ❌ | 0 | 0 | 0 | 623 s | quota exhausted |
| 3 | `django__django-13741` | ❌ | 0 | 0 | 0 | 389 s | quota exhausted |
| 4 | `sympy__sympy-18189` | ❌ | 0 | 0 | 0 | 208 s | quota exhausted |
| 5 | `sympy__sympy-23534` | ❌ | 0 | 0 | 0 | 83 s | quota exhausted |
| 6 | `scikit-learn__scikit-learn-13439` | ❌ | 0 | 0 | 0 | 82 s | quota exhausted |
| 7 | `django__django-13089` | ❌ | 0 | 0 | 0 | 333 s | quota exhausted |

#### gemma-31

| # | Task | Result | Iter. | Input tokens | Output tokens | Time | End of run |
|---|---|---|---:|---:|---:|---:|---|
| 1 | `django__django-11066` | ❌ | 1 | 2,119 | 29 | 761 s | quota exhausted (HTTP 429) |
| 2 | `pydata__xarray-4629` | ❌ | 0 | 0 | 0 | 392 s | quota exhausted (HTTP 429) |
| 3 | `django__django-13741` | ❌ | 0 | 0 | 0 | 396 s | quota exhausted (HTTP 429) |
| 4–7 | — | ❌ | — | — | — | — | run interrupted, no result |

---

## 3. MBPP results

✅ passed · ❌ failed · ⚪ solution produced but not validated · ⛔ run interrupted by the API

| Model | 233 | 472 | 262 | 439 | 287 | 431 | 417 | 470 | 105 | 167 | Pass | Avg. iter. | Avg. in | Avg. out | Avg. time |
|---|---|---|---|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|
| **codestral** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **10/10** | 1.1 | 613 | 80 | 1.1 s |
| gpt-oss-120b | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **10/10** | 1.0 | 597 | 286 | 1.4 s |
| gemma-26 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **10/10** | 1.0 | 580 | 149 | 4.4 s |
| ministral-14b | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **10/10** | 1.3 | 816 | 92 | 5.4 s |
| qwen-3.8 | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **9/10** | 1.9 | 2,151 | 171 | 2.7 s |
| ministral-3b | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | **3/10** | 7.7 | 6,197 | 419 | 4.0 s |
| ministral-8b | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **0/10** | 10.0 | 7,886 | 491 | 9.3 s |
| gemini-flash | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ | **n.v.** | 1.0 | 581 | 100 | 126.3 s |
| gemma-31 | ⚪ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⚪ | ⛔ | **0/10** | 0.2 | 116 | 14 | 374.7 s |

- MBPP does not separate the good models: 4 of them score 10/10 in about one iteration. The difference is speed and cost: codestral answers in 1.1 s with 80 output tokens.
- ministral-3b and ministral-8b exceed the 6,000 input-token limit: they keep iterating without submitting.
- gemini-flash produced a solution for all 10 tasks, but none was validated (n.v.). 5 of its runs exceed the 120 s timeout anyway.

---

## 4. Provider reliability

| Model | Provider | Successful requests | Avg. latency | Median latency | Max latency | Retries | Runs interrupted by the API | Availability |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| codestral | Mistral | 94 | 2.2 s | 0.6 s | 75 s | 0 | 0/17 | 100% |
| ministral-3b | Mistral | 232 | 0.8 s | 0.5 s | 14 s | 0 | 0/17 | 100% |
| ministral-8b | Mistral | 310 | 0.8 s | 0.6 s | 23 s | 0 | 0/17 | 100% |
| ministral-14b | Mistral | 223 | 2.9 s | 2.5 s | 20 s | 0 | 0/17 | 100% |
| qwen-3.8 | Groq | 68 | 10.9 s | 0.6 s | 75 s | 0 | 3/17 | 96% |
| gpt-oss-120b | Groq | 18 | 4.0 s | 1.4 s | 35 s | 0 | 6/17 | 75% |
| gemma-26 | OpenRouter | 97 | 18.1 s | 7.5 s | 126 s | 0 | 3/17 | 97% |
| gemma-31 | OpenRouter | 3 | 96.1 s | 12.0 s | 272 s | 0 | 15/17 | 17% |
| gemini-flash | Google AI Studio | 20 | 38.6 s | 21.2 s | 173 s | 0 | 7/17 | 74% |

| Provider | Successful requests | Avg. latency | Interrupted runs | Availability |
|---|---:|---:|---:|---:|
| **Mistral** | 859 | 1.5 s | 0/68 | **100%** |
| Groq | 86 | 9.4 s | 9/34 | 91% |
| OpenRouter | 100 | 20.5 s | 18/34 | 85% |
| Google AI Studio | 20 | 38.6 s | 7/17 | 74% |

*Availability = successful requests / (successful requests + requests that failed fatally).*

- **Mistral** did not interrupt a single run, and its median latency is under 1 s.
- **Groq** is fast, but its free-tier limits block SWE-bench:
  - qwen-3.8 hit HTTP 413 errors, because the limit is 7,000 input tokens per minute;
  - gpt-oss-120b gets `tool_use_failed`: the model emits its own tool calls (`repo_browser.*`) instead of a Python block, and Groq rejects them.
- **OpenRouter** and **Google AI Studio** heavily throttle free-tier traffic. gemma-31 received 175 "rate-limited upstream" responses (HTTP 429), and average latency on these providers exceeds 18 s per request.
- The `retries` field in `solution.json` stays at 0 because every error encountered is treated as fatal (quota exhausted, 400, 413). Reliability is therefore best read from the number of interrupted runs.

---

## 5. Intermediary metrics

These metrics cover the SWE-bench runs that submitted a patch.

| Model | Runs measured | First read of the patched file (avg. step) | First edit (avg. step) | Iterations between first passing `run_tests` and `final_answer` |
|---|---:|---:|---:|---:|
| qwen-3.8 | 4 | **1.5** | **3.5** | 1 (2 runs out of 2) |
| codestral | 5 | 2.2 | 5.8 | 1 (3 runs out of 3) |
| ministral-3b | 4 | 5.3 | 14.0 | 1 (1 run out of 1) |
| gemma-26 | 1 | 1 | 4 | — |

- **Exploration efficiency.** qwen-3.8 opens the right file at step 1 or 2 and edits it around step 3 or 4. ministral-3b takes 14 steps on average before its first edit, which explains its 22 iterations per task.
- **Submission discipline.** Every model submits on the iteration right after its first passing test. No iteration is wasted after success, but reaching the ideal of 0 would mean submitting in the same block as the test: `if code == 0: final_answer(get_patch())`.

---

## 6. Ablation study

Each ablation compares the reference configuration (section 2) with one variant, on the same tasks and with the same model. For SWE-bench, the comparison covers tasks 1 to 6.

### A1. `run_command` documentation removed from the SWE-bench prompt

| codestral (tasks 1–6) | Reference | Without `run_command` doc |
|---|---:|---:|
| Pass | 4/6 | 4/6 |
| Avg. iterations | 12.3 | 12.2 |
| Avg. input tokens | 83,159 | 74,220 |
| Avg. time | 78 s | 71 s |

The solved tasks and the iteration count do not change: codestral never calls `run_command` anyway. The effect is clearer for gpt-oss-120b. It goes from 0/6 to 1/6, and the HTTP 400 rejections disappear (5 runs out of 6 before, 4 of them `tool_use_failed`; 0 after). The `run_command` documentation seems to push this model towards its native tools.

### A2. Search tool documentation removed (`list_files`, `search_code`, `search_function_or_class_definition_in_code`, `find_references`)

| codestral (tasks 1–6) | Reference | Without search tools |
|---|---:|---:|
| Pass | 4/6 | 4/6 |
| Avg. iterations | 12.3 | **7.3** (−41%) |
| Median iterations | 10 | **6** |
| Avg. output tokens | 332 | 196 |
| Avg. time | 78 s | **43 s** (−45%) |

| # | Task | Reference | Without search |
|---|---|---|---|
| 1 | `django__django-11066` | ✅ 8 it · 20k | ✅ 6 it · 14k |
| 2 | `pydata__xarray-4629` | ✅ 7 it · 20k | ✅ 5 it · 14k |
| 3 | `django__django-13741` | ❌ 12 it · 24k | ❌ 6 it · 20k |
| 4 | `sympy__sympy-18189` | ✅ 5 it · 177k | ✅ 5 it · 177k |
| 5 | `sympy__sympy-23534` | ❌ 30 it · 110k | ❌ 12 it · 92k |
| 6 | `scikit-learn__scikit-learn-13439` | ✅ 12 it · 145k | ✅ 10 it · 185k |

Without the search tools, codestral solves the same tasks with almost half the iterations. It opens the file mentioned in the issue directly. Almost all of these issues name the faulty file, which limits how far this result generalizes: on a vaguer issue, search would still be needed.

### A3. MBPP prompt reduced to 3 lines

```
You are solving one MBPP task.
return ```python ...``` blocks
return ```python final_answer(function_code)``` to finish the task
```

| Model | Pass (reference → reduced) | Avg. iter. | Avg. output tokens |
|---|---|---:|---:|
| codestral | **10/10 → 2/10** | 1.1 → 3.9 | 80 → 1,543 |
| gpt-oss-120b | **10/10 → 0/10** | 1.0 → 3.5 | 286 → 1,571 |

Without the full prompt's template (code in a string, then `run_tests`, then `final_answer` in the same block), codestral passes the function object to `final_answer` instead of its source code in 8 runs out of 10. The recorded solution becomes `<function ... at 0x...>`, which does not compile. Answers also get much longer: gpt-oss-120b exceeds the 1,500 output-token limit in 6 runs. The detailed prompt is therefore essential.
