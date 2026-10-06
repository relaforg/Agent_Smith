import logging
from urllib.parse import urlparse

from agent_core.llm.providers.generic import GenericProvider
from agent_core.llm.providers.groq import GroqProvider
from agent_core.llm.providers.mistral import MistralProvider
from agent_core.llm.providers.openrouter import OpenRouterProvider
from agent_core.llm.retry import with_retry
from agent_core.models import BaseProvider, LLMAnswer, Message

logger = logging.getLogger(__name__)


class ModelNotFoundError(Exception):
    """Raised when no loaded provider supports the requested model."""


PROVIDER_HOSTS: dict[str, type[BaseProvider]] = {
    "api.groq.com": GroqProvider,
    "openrouter.ai": OpenRouterProvider,
    "api.mistral.ai": MistralProvider,
}


def match_provider_class(url: str) -> type[BaseProvider] | None:
    """Returns the dedicated provider class for a URL, or None if unknown."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    return PROVIDER_HOSTS.get(host)


class LLMClient:
    def __init__(
        self,
        max_retries: int = 3,
        provider_url: str | None = None,
        api_key: str | None = None,
    ):
        self.providers: list[BaseProvider] = []
        self.max_retries: int = max_retries

        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_requests: int = 0

        self.model_usage: dict[str, dict[str, int]] = {}

        self.provider_url = provider_url
        if provider_url is None:
            self.initialize_providers()
        else:
            self.initialize_from_url(provider_url, api_key)

    def initialize_from_url(self, url: str, api_key: str | None = None) -> None:
        """
        Known URL  -> the matching dedicated provider (keys come from env vars).
        Unknown URL -> GenericProvider doing a bare OpenAI-compatible request.
        """
        provider_cls = match_provider_class(url)

        if provider_cls is not None:
            provider_inst = provider_cls() 
            logger.info(f"URL '{url}' redirected to provider: {provider_inst.name}")
        else:
            provider_inst = GenericProvider(url, api_key=api_key)
            logger.info(f"URL '{url}' unknown, using bare request provider")

        self.providers.append(provider_inst)

    def initialize_providers(self) -> None:
        """Initializes available providers based on present API keys."""
        candidate_providers = [GroqProvider, OpenRouterProvider, MistralProvider]

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

    def get_supported_providers_for_model(self, model: str) -> list[BaseProvider]:
        """Returns candidate providers for the requested model.

        With an explicit provider_url the caller chose the endpoint, so the
        model name is passed through as-is (it may not be in the alias map).
        """
        if self.provider_url is not None:
            return list(self.providers)
        return [p for p in self.providers if p.supports_model(model)]

    def chat(
        self,
        messages: list[Message],
        model: str,
        stop_sequences: list[str] | None = None,
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

        errors: dict[str, Exception] = {}

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
