import logging
import time
from unittest.mock import MagicMock
from dotenv import load_dotenv
import httpx

from src.agent_core.llm.llm_client import LLMClient, ModelNotFoundError
from src.agent_core.llm.retry import with_retry
from src.agent_core.models import Message

# Configure logging to display retries and routing details in terminal
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================================
# UNIT TESTS: Retry Decorator Edge Cases (Mocked)
# ============================================================================

def test_retry_transient_recovery():
    """Validates that @with_retry recovers from transient errors (429/503) after initial failures."""
    print("\n[RETRY TEST 1] Testing Transient Error Recovery (429 -> 200)...")

    attempts = 0

    @with_retry(max_retries=3, initial_delay=0.05, jitter=False)
    def mock_api_call():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            # Simulate transient 429 Rate Limit
            request = httpx.Request("POST", "https://api.example.com")
            response = httpx.Response(429, request=request, headers={"Retry-After": "0"})
            raise httpx.HTTPStatusError("Rate limited", request=request, response=response)
        return "SUCCESS"

    result = mock_api_call()
    assert result == "SUCCESS", "Expected function to recover and return SUCCESS"
    assert attempts == 3, f"Expected 3 attempts, took {attempts}"
    print(f"✓ Successfully recovered after {attempts - 1} transient failures.")


def test_retry_non_transient_fast_fail():
    """Validates that @with_retry immediately fails on 401/400 errors without retrying."""
    print("\n[RETRY TEST 2] Testing Non-Transient Fast Failure (401 Unauthorized)...")

    attempts = 0

    @with_retry(max_retries=3, initial_delay=0.05)
    def mock_api_call():
        nonlocal attempts
        attempts += 1
        request = httpx.Request("POST", "https://api.example.com")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("Unauthorized", request=request, response=response)

    try:
        mock_api_call()
        assert False, "Should have raised HTTPStatusError"
    except httpx.HTTPStatusError as e:
        assert e.response.status_code == 401
        assert attempts == 1, f"Should have failed immediately on 1st attempt, but took {attempts}"
        print("✓ Correctly failed fast on 401 without wasting retries.")


def test_retry_exhaustion():
    """Validates that @with_retry raises an exception when max retries are exceeded."""
    print("\n[RETRY TEST 3] Testing Retry Exhaustion...")

    attempts = 0

    @with_retry(max_retries=2, initial_delay=0.05)
    def mock_api_call():
        nonlocal attempts
        attempts += 1
        request = httpx.Request("POST", "https://api.example.com")
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("Service Unavailable", request=request, response=response)

    try:
        mock_api_call()
        assert False, "Should have raised HTTPStatusError"
    except httpx.HTTPStatusError:
        assert attempts == 2, f"Expected exactly 2 attempts before failing, took {attempts}"
        print("✓ Correctly raised exception after exhausting 2 retries.")


# ============================================================================
# INTEGRATION TESTS: Real LLMClient Execution
# ============================================================================

def test_client_strict_routing_and_usage():
    """Tests live chat requests, strict provider discovery, latency tracking, and usage summary."""
    print("\n============================================================")
    print(" INTEGRATION TEST: LLMClient Strict Routing & Usage ")
    print("============================================================")

    client = LLMClient(max_retries=2)
    messages = [Message(role="user", content="Say 'Hello' in exactly one word.")]

    # 1. Execute call to a valid model
    test_model = "gpt-oss-120b"
    print(f"\n[CLIENT TEST 1] Dispatching prompt to model '{test_model}'...")

    answer = client.chat(
        messages=messages,
        model=test_model,
        temperature=0.0,
        max_tokens=200,
    )

    print(f"  Response:        '{answer.content.strip()}'")
    print(f"  Provider Used:   {answer.provider}")
    print(f"  Model Used:      {answer.model}")
    print(f"  Latency:         {answer.latency_ms} ms")
    print(f"  Retries Taken:   {answer.retries}")
    print(f"  Input Tokens:    {answer.input_tokens}")
    print(f"  Output Tokens:   {answer.output_tokens}")

    assert answer.content, "Response content should not be empty"
    assert answer.latency_ms > 0, "Latency should be positive"
    assert answer.provider in ["groq", "openrouter"], f"Unexpected provider: {answer.provider}"
    print("✓ Live chat completion and answer attributes verified.")

    # 2. Test Invalid Model Strict Routing Protection
    invalid_model = "unsupported-model-9999"
    print(f"\n[CLIENT TEST 2] Testing strict non-substitution on invalid model '{invalid_model}'...")
    try:
        client.chat(messages=messages, model=invalid_model)
        assert False, f"Expected ModelNotFoundError for '{invalid_model}'"
    except ModelNotFoundError as e:
        print(f"✓ ModelNotFoundError raised correctly:\n   --> {e}")

    # 3. Print Token Usage Summary
    print("\n[CLIENT TEST 3] Verifying session usage aggregation...")
    client.print_usage_summary()


# ============================================================================
# MAIN SUITE RUNNER
# ============================================================================

if __name__ == "__main__":
    load_dotenv()

    print("=" * 60)
    print(" RUNNING UNIT TESTS FOR RETRY DECORATOR ")
    print("=" * 60)
    test_retry_transient_recovery()
    test_retry_non_transient_fast_fail()
    test_retry_exhaustion()

    print("\n" + "=" * 60)
    print(" RUNNING INTEGRATION TESTS FOR LLMCLIENT ")
    print("=" * 60)
    test_client_strict_routing_and_usage()

    print("\n" + "=" * 60)
    print(" ALL RETRY AND LLMCLIENT TESTS PASSED SUCCESSFULLY! ")
    print("=" * 60)
