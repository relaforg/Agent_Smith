from dataclasses import asdict
import os
import time

import httpx

from src.agent_core.models import BaseProvider, LLMAnswer, Message


class OpenRouterProvider(BaseProvider):
    def __init__(self) -> None:
        keys = self.load_keys()
        if not keys:
            raise ValueError("No openrouter API key")
        self.keys = keys
        self.key_index = 0
        self.curr_key = keys[0]
        self.base_url = "https://openrouter.ai/api/v1"
        # Increased timeout for long context/code generations
        self.client = httpx.Client(timeout=120.0)

        self.MODEL_MAP: dict[str, str] = {
                    "gemma": "google/gemma-4-31b-it",
                    "qwen": "qwen/qwen-2.5-coder-32b-instruct:free",
                    "cohere": "cohere/command-r-7b-12-2024:free",
                    "nemotron": "nvidia/llama-3.1-nemotron-70b-instruct:free",
                }

        self.SUPPORTED_MODELS: set[str] = set(self.MODEL_MAP.keys())

    @property
    def name(self) -> str:
        return "openrouter"

    def load_keys(self) -> list[str]:
        keys = []
        base_key = os.environ.get("OPENROUTER_API_KEY")
        if base_key:
            keys.append(base_key)

        i = 1
        while True:
            key = os.environ.get(f"OPENROUTER_API_KEY_{i}")
            if not key:
                break
            keys.append(key)
            i += 1
        return keys

    def chat(
        self,
        messages: list[Message],
        model: str,
        stop_sequences: list[str] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LLMAnswer:

        model = self.MODEL_MAP.get(model, model)
        max_attempts = max(len(self.keys) * 3, 5)

        for attempt in range(max_attempts):
            start = time.perf_counter()

            try:
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.curr_key}",
                        "HTTP-Referer": "https://github.com/swebench-agent",
                    },
                    json={
                        "model": model,
                        "messages": [asdict(message) for message in messages],
                        "temperature": temperature,
                        "stop": stop_sequences or [],
                        "max_tokens": max_tokens,
                    },
                )
                response.raise_for_status()
                data = response.json()
                latency = (time.perf_counter() - start) * 1000

                return LLMAnswer(
                    content=data["choices"][0]["message"]["content"],
                    input_tokens=data["usage"]["prompt_tokens"],
                    output_tokens=data["usage"]["completion_tokens"],
                    model=model,
                    provider=self.name,
                    latency_ms=latency,
                    retries=attempt,
                    finish_reason=data["choices"][0].get("finish_reason"),
                )

            except httpx.HTTPStatusError as e:
                # Catch rate limits (429) AND transient server failures (500, 502, 503, 504)
                if e.response.status_code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** min(attempt, 4))  # Exponential backoff
                    self.next_key()
                    continue
                raise
            except (httpx.TimeoutException, httpx.TransportError):
                time.sleep(2 ** min(attempt, 4))
                self.next_key()
                continue

        raise RuntimeError(f"All retries and API keys exhausted for model '{model}'.")
