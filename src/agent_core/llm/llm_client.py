import logging
from dataclasses import asdict
from typing import List, Dict, Optional, Any

from src.agent_core.models import BaseProvider, Message, LLMAnswer
from src.agent_core.llm.providers.groq import GroqProvider
from src.agent_core.llm.providers.openrouter import OpenRouterProvider
from src.agent_core.llm.retry import with_retry

logger = logging.getLogger(__name__)


class ModelNotFoundError(Exception):
    """Raised when no loaded provider supports the requested model."""
    pass


class LLMClient:
    def __init__(self, max_retries: int = 3):
        self.providers: List[BaseProvider] = []
        self.max_retries: int = max_retries

        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_requests: int = 0

        self.model_usage: Dict[str, Dict[str, int]] = {}

        self.initialize_providers()

    def initialize_providers(self) -> None:
        """Initializes available providers based on present API keys."""
        candidate_providers = [GroqProvider, OpenRouterProvider]

        for provider_cls in candidate_providers:
            try:
                provider_inst = provider_cls()
                self.providers.append(provider_inst)
                logger.info(f"Loaded provider: {provider_inst.name}")
            except Exception as e:
                logger.debug(f"Skipping {provider_cls.__name__}: {e}")

        if not self.providers:
            raise ValueError("No LLM provider found. Please check your environment variables.")

    def track_usage(self, answer: LLMAnswer) -> None:
        """Accumulates session and per-model token metrics directly from LLMAnswer."""
        self.total_input_tokens += answer.input_tokens
        self.total_output_tokens += answer.output_tokens
        self.total_requests += 1

        if answer.model not in self.model_usage:
            self.model_usage[answer.model] = {"input": 0, "output": 0, "requests": 0}

        self.model_usage[answer.model]["input"] += answer.input_tokens
        self.model_usage[answer.model]["output"] += answer.output_tokens
        self.model_usage[answer.model]["requests"] += 1

    def get_supported_providers_for_model(self, model: str) -> List[BaseProvider]:
        """Returns only providers that explicitly support the requested model."""
        return [p for p in self.providers if p.supports_model(model)]

    def chat(
        self,
        messages: List[Message],
        model: str,
        stop_sequences: Optional[List[str]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LLMAnswer:
        """
        Executes a chat completion with strict non-substitution model routing.

        - Finds candidate providers supporting the EXACT model requested.
        - Applies exponential backoff retries per candidate.
        - Fails over to a secondary provider hosting the EXACT model if primary fails.
        - Strictly forbids model substitution.
        """
        candidate_providers = self.get_supported_providers_for_model(model)

        if not candidate_providers:
            available_models = set()
            for p in self.providers:
                available_models.update(getattr(p, "SUPPORTED_MODELS", set()))
                available_models.update(getattr(p, "MODEL_MAP", {}).keys())

            raise ModelNotFoundError(
                f"Strict Routing Error: Requested model '{model}' is not supported by any loaded provider. "
                f"No substitution allowed for benchmarking integrity. Available: {sorted(list(available_models))}"
            )

        errors: Dict[str, Exception] = {}

        for provider in candidate_providers:
            try:
                logger.info(f"Routing model '{model}' to provider '{provider.name}'")

                attempts_made = 0

                @with_retry(max_retries=self.max_retries)
                def _execute_call() -> LLMAnswer:
                    nonlocal attempts_made
                    attempts_made += 1
                    return provider.chat(
                        messages=messages,
                        model=model,
                        stop_sequences=stop_sequences,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )

                answer = _execute_call()
                answer.retries = attempts_made - 1

                self.track_usage(answer)
                return answer

            except Exception as e:
                logger.warning(
                    f"Provider '{provider.name}' failed for model '{model}': {e}. "
                    f"Checking for alternative provider hosting identical model..."
                )
                errors[provider.name] = e

        raise RuntimeError(
            f"Strict Routing Failure: All providers supporting model '{model}' failed. "
            f"Errors: {errors}"
        )

    def print_usage_summary(self) -> None:
        """Prints a summary of cumulative session token usage."""
        total_tokens = self.total_input_tokens + self.total_output_tokens
        print("\n" + "=" * 50)
        print(" SESSION TOKEN USAGE SUMMARY ")
        print("=" * 50)
        print(f"Total Requests Executed: {self.total_requests:,}")
        print(f"Total Input Tokens:      {self.total_input_tokens:,}")
        print(f"Total Output Tokens:     {self.total_output_tokens:,}")
        print(f"Total Combined Tokens:   {total_tokens:,}")
        print("-" * 50)
        print("Breakdown by Model:")
        for md, stats in self.model_usage.items():
            combined = stats["input"] + stats["output"]
            print(
                f"  • {md} ({stats['requests']} reqs): "
                f"{combined:,} tokens (In: {stats['input']:,} / Out: {stats['output']:,})"
            )
        print("=" * 50 + "\n")
