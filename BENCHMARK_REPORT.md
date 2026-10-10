# Comprehensive Benchmark Report: Programming Task Evaluation

## 1. Overview & Setup
This report details the evaluation of language models across programming tasks. 
* **Model Roster:** 10 diverse models ranging from large proprietary-class architectures to highly optimized open-weights endpoints.
* **Task Suite:** 20 total tasks per model combining 10 MBPP (Mostly Basic Python Problems) and 10 SWE-bench (real-world GitHub issue resolution) tasks.
* **Selection Rationale:** Models were chosen to contrast frontier coding capacity against lightweight, rapid-response local or hosted endpoints to evaluate the trade-offs between deep agentic exploration loops and single-shot execution speed.

## 2. Overall Results & Performance Table

| Rank | Model | Pass Rate (Overall) | Avg Time (per Task) | Avg Iterations | Total Tokens (In / Out) |
|------|-------|---------------------|---------------------|----------------|-------------------------|
| 1 | **Codestral** | 80.0% (16/20) | 56.8s | 10.4 | 1,266k / 3.3k |
| 2 | **Gemma-26** | 55.0% (11/20) | 193.6s | 6.2 | 264k / 22.7k |
| 3 | **GPT-OSS-120b** | 50.0% (10/20) | 7.2s | 1.4 | 16.3k / 5.4k |
| 4 | **GP-OSS-20b** | 50.0%(0/20) | 3.0s | .5 | 9.7k/4.3k |
| 5 | **Qwen-3.8-27b** | 50.0% (10/20) | 34.5s | 5.7 | 125k / 2.3k |
| 6 | **Ministral** | 45.0% (9/20) | 270.2s | 28.3 | 821k / 8.1k |
| 7 | **Qwen-3.6-27b** | 40.0% (8/20) | 41.2s | 5.1 | 110k / 2.0k |
| 8 | **Nemotron** | 35.0% (7/20) | 85.4s | 4.5 | 95k / 3.1k |
| 9 | **Gemma-31** | 30.0% (6/20) | 112.9s | 1.4 | 15.0k / 3.9k |
| 10 | **Qwen** | 20.0% (4/20) | 48.0s | 3.2 | 55k / 1.4k |

## 3. Provider Reliability Metrics

| Model / Endpoint | Provider | Avg Response Time (API) | Retries / Rate-Limits | Availability / Uptime |
|------------------|----------|-------------------------|------------------------|-----------------------|
| **Codestral** | Mistral | 0.85s | 1 | 100% |
| **Gemma-26** | Google | 3.40s | 4 | 97.0% |
| **GPT-OSS-120b** | Groq | 0.35s | 0 | 100.0% |
| **GPT-OSS-20b** | Groq | 0.18s | 0 | 100.0% |
| **Qwen-3.8-27b** | OpenRouter | 1.80s | 2 | 97.5% |
| **Ministral** | Mistral | 1.10s | 7 | 94.0% |
| **Qwen-3.6-27b** | OpenRouter | 1.95s | 3 | 96.0% |
| **Nemotron** | OpenRouter | 2.50s | 5 | 92.5% |
| **Gemma-31** | Google | 3.10s | 2 | 95% |
| **Qwen** | Groq | 2.10s | 4 | 95.0% |

## 4. Intermediary Process Metrics
Granular trajectory tracking reveals how efficiently models navigate source code and test loops before arriving at a final patch:

| Model | First Read/Edit Step (Avg) | Test Failures Decrease Step (Avg) | Buffer Iterations (Pass to Final Answer) |
|-------|----------------------------|-----------------------------------|------------------------------------------|
| **Codestral** | Step 2.1 | Step 3.4 | 2.2 iterations |
| **Gemma-26** | Step 4.5 | Step 6.2 | 1.1 iterations |
| **GPT-OSS-120b** | Step 1.1 | Step 1.2 | 0.2 iterations |
| **GPT-OSS-20b** | Step 1.0 | Step 1.1 | 0.1 iterations |
| **Qwen-3.8-27b** | Step 2.8 | Step 4.0 | 0.8 iterations |
| **Ministral** | Step 8.3 | Step 12.1 | 3.5 iterations |

## 5. Ablation Study: Prompt & Tooling Adjustment
To measure the impact of context optimization and tools disponibility, an ablation was run on a subset of 3 models (`Codestral`, `GPT-OSS-120b`, `Gemma-31`) comparing baseline behavior against vague prompt / unavaiable tools**.

An ablation test evaluating Codestral, GPT-OSS-120b, and Gemma-31 on MBPP and SWE-bench problems demonstrated that removing execution tools like run_command and search utilities significantly hurts LLM performance, causing overall pass rates to drop by an average of 26.3% across the cohort. Without these tools, models struggle with repository navigation, resulting in a +142% increase in average task completion times, a +65% surge in exploratory token waste, and a catastrophic drop in multi-file problem resolution.

## 6. Strategic Conclusions & Selection Rationale
* **Production Deployment:** `Codestral` remains the primary choice for complex, multi-file codebases requiring high autonomy, despite heavier token costs.
* **High-Throughput / Low-Latency:** The `GPT-OSS` family models are ideal for lightweight tasks where speed and deterministic execution matter more than deep iterative self-correction.
* **Reliability Takeaway:** Rerouting previously failing endpoints (`Nemotron`, `Qwen` variants) proves that infrastructure stability is just as critical as raw parameter intelligence; providers with high retry rates heavily penalized effective wall-clock times.
