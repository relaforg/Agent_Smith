from dataclasses import asdict
import os
import time

import httpx

from agent_core.models import BaseProvider, LLMAnswer, Message


class GoogleProvider(BaseProvider):
    REASONING_EFFORT = "low"

    def __init__(self) -> None:
        keys = self.load_keys()
        if not keys:
            raise ValueError("No Google API key")
        self.keys = keys
        self.key_index = 0
        self.curr_key = keys[0]
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        self.client = httpx.Client(timeout=60.0)

        self.MODEL_MAP: dict[str, str] = {
            "gemini-flash": "gemini-3.8-flash",
            "gemini-flash-latest": "gemini-flash-latest",
        }

        self.SUPPORTED_MODELS: set[str] = set(self.MODEL_MAP.keys())

    @property
    def name(self) -> str:
        return "google"

    def load_keys(self) -> list[str]:
        keys = []
        base_key = os.environ.get("GOOGLE_API_KEY")
        if base_key:
            keys.append(base_key)

        i = 1
        while True:
            key = os.environ.get(f"GOOGLE_API_KEY_{i}")
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

        model = self.resolve_model(model)
        # Shorter than Mistral's retry loop: an MBPP task has 120s in total
        max_attempts = max(len(self.keys) * 2, 4)

        payload = {
            "model": model,
            "messages": [asdict(message) for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning_effort": self.REASONING_EFFORT,
        }
        if stop_sequences:
            payload["stop"] = stop_sequences

        for attempt in range(max_attempts):
            start = time.perf_counter()

            try:
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.curr_key}",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                latency = (time.perf_counter() - start) * 1000
                usage = data["usage"]

                return LLMAnswer(
                    content=data["choices"][0]["message"].get("content") or "",
                    input_tokens=usage["prompt_tokens"],
                    # completion_tokens leaves out the hidden thoughts, but
                    # they are still generated tokens: count them as output
                    output_tokens=usage["total_tokens"] -
                    usage["prompt_tokens"],
                    model=model,
                    provider=self.name,
                    latency_ms=latency,
                    retries=attempt,
                    finish_reason=data["choices"][0].get("finish_reason"),
                )

            except httpx.HTTPStatusError as e:
                # Rate limits (429) and "high demand" overloads (503)
                if e.response.status_code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** min(attempt, 3))
                    self.next_key()
                    continue
                raise
            except (httpx.TimeoutException, httpx.TransportError):
                time.sleep(2 ** min(attempt, 3))
                self.next_key()
                continue

        raise RuntimeError(
            f"All retries and API keys exhausted for model '{model}'.")
