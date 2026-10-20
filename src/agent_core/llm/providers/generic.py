from dataclasses import asdict
import os
import time

import httpx

from agent_core.models import BaseProvider, LLMAnswer, Message


class GenericProvider(BaseProvider):
    """
    Bare OpenAI-compatible provider pointed at an arbitrary URL.

    Accepts either a base URL ("http://localhost:11434/v1") or a full
    endpoint ("https://host/v1/chat/completions"). No model mapping, no key
    rotation: the model name is sent as-is. The API key is optional (local
    servers such as Ollama / llama.cpp / vLLM often need none).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        self.endpoint = url
        self.base_url = base_url.rstrip("/")

        key = api_key or os.environ.get("LLM_API_KEY")
        self.keys: list[str] = [key] if key else []
        self.key_index = 0
        self.curr_key = key
        self.client = httpx.Client(timeout=timeout)

        self.MODEL_MAP: dict[str, str] = {}
        self.SUPPORTED_MODELS: set[str] = set()

    @property
    def name(self) -> str:
        return f"generic({self.base_url})"

    def load_keys(self) -> list[str]:
        return self.keys

    def supports_model(self, model: str) -> bool:
        # Unknown remote: we cannot know what it hosts, so let it try anything.
        return True

    def next_key(self) -> None:
        # Single (optional) key, nothing to rotate.
        return None

    def chat(
        self,
        messages: list[Message],
        model: str,
        stop_sequences: list[str] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LLMAnswer:
        headers = {}
        if self.curr_key:
            headers["Authorization"] = f"Bearer {self.curr_key}"

        payload: dict = {
            "model": model,
            "messages": [asdict(message) for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stop_sequences:
            payload["stop"] = stop_sequences

        start = time.perf_counter()
        response = self.client.post(self.endpoint, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        latency = (time.perf_counter() - start) * 1000

        choice = data["choices"][0]
        usage = data.get("usage") or {}

        return LLMAnswer(
            content=choice["message"]["content"],
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            model=model,
            provider=self.name,
            latency_ms=latency,
            retries=0,
            finish_reason=choice.get("finish_reason"),
        )
