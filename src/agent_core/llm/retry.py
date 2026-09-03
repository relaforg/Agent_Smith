import json
import logging
import random
import time
from functools import wraps
from typing import Callable, Any, TypeVar

import httpx

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def with_retry(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
) -> Callable[[F], F]:
    """
    Decorator for LLM API calls catching transient HTTP errors, network failures,
    and malformed API payloads during provider downtime.
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            last_exception: Exception | None = None

            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except httpx.HTTPStatusError as e:
                    last_exception = e
                    status_code = e.response.status_code

                    if status_code not in TRANSIENT_STATUS_CODES:
                        logger.error(f"Non-retryable HTTP {status_code}: {e.response.text}")
                        raise e

                    retry_after = e.response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        sleep_time = float(retry_after)
                        logger.warning(
                            f"[Attempt {attempt}/{max_retries}] 429 Rate Limit. "
                            f"Honoring Retry-After header: sleeping {sleep_time:.2f}s..."
                        )
                    else:
                        sleep_time = delay
                        logger.warning(
                            f"[Attempt {attempt}/{max_retries}] Transient HTTP {status_code}. "
                            f"Retrying in {sleep_time:.2f}s..."
                        )

                    time.sleep(sleep_time)

                except (httpx.RequestError, TimeoutError, json.JSONDecodeError, KeyError) as e:
                    last_exception = e
                    sleep_time = delay

                    logger.warning(
                        f"[Attempt {attempt}/{max_retries}] Transient network or payload parse error ({type(exc).__name__}): {exc}. "
                        f"Retrying in {sleep_time:.2f}s..."
                    )
                    time.sleep(sleep_time)

                delay *= backoff_factor

            logger.error(f"Max retries ({max_retries}) exhausted for function '{func.__name__}'.")
            raise last_exception if last_exception else RuntimeError("Max retries exceeded.")

        return wrapper  # type: ignore

    return decorator
