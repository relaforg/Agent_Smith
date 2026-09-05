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
        self.client = httpx.Client(timeout=30.0)

        self.MODEL_MAP: dict[str, str] = {
            "cohere": "cohere/north-mini-code:free",
            "gemma": "google/gemma-4-31b-it:free",
            "nemotron": "nvidia/nemotron-3-ultra-550b-a55b:free",
        }

        self.SUPPORTED_MODELS: set[str] = {
            "cohere",
            "gemma",
            "nemotron"
        }

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
         max_tokens: int = 2048
     ) -> LLMAnswer:

        model = self.resolve_model(model)

        for i in range(len(self.keys) * 2):
            start = time.perf_counter()

            try:
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.curr_key}"
                    },
                    json={
                        "model": model,
                        "messages": [asdict(message) for message in messages],
                        "temperature": temperature,
                        "stop": stop_sequences or [],
                        "max_tokens": max_tokens
                    }
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
                     retries=i,
                     finish_reason=data["choices"][0].get("finish_reason"),
                 )

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    self.next_key()
                    continue
                else:
                    raise
            except httpx.TimeoutException:
                self.next_key()
                continue

        raise RuntimeError("All API keys exhausted")
