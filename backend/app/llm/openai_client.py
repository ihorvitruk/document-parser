import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import openai
from openai import AsyncOpenAI

from app.config import Settings
from app.errors import PermanentError, ProcessingError, TransientError
from app.llm.retry import call_with_retries

logger = logging.getLogger(__name__)


def classify_error(exc: Exception) -> ProcessingError | None:
    """Map an SDK or network exception to our error kinds, with a message that is
    safe and useful to show the user. Returns None for exceptions we do not know."""
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        if status == 429:
            # OpenAI uses 429 both for "slow down" and for "no credit left".
            if exc.code == "insufficient_quota":
                return PermanentError("The OpenAI account has no credit left (check billing)")
            return TransientError("OpenAI rate limit reached")
        if status == 408 or status >= 500:
            return TransientError("OpenAI is temporarily unavailable")
        if status in (401, 403):
            return PermanentError("OpenAI rejected the API key")
        if status == 404:
            return PermanentError("The configured OpenAI model was not found or is not available")
        return PermanentError("OpenAI rejected the request")
    if isinstance(exc, openai.APIConnectionError | httpx.TransportError | TimeoutError):
        return TransientError("Could not reach OpenAI (network problem or timeout)")
    return None


class OpenAIClient:
    """Thin wrapper around the OpenAI Responses API: one call with a timeout, error
    classification and bounded retries (the same contract as GeminiClient)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 120,
        attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        client: Any = None,
    ):
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self._sleep = sleep
        # The SDK's own retries are off: ours are bounded and classified.
        self._client = client or AsyncOpenAI(api_key=api_key, max_retries=0)

    async def respond(
        self, content: list[dict[str, Any]], *, parse: Callable[[str], Any] | None = None, **options: Any
    ) -> Any:
        """Send one user message made of `content` parts; return the output text, or
        `parse(text)` when given (parse runs inside the retry loop)."""
        return await call_with_retries(
            lambda: self._respond_once(content, options, parse),
            attempts=self.attempts,
            sleep=self._sleep,
        )

    async def _respond_once(
        self, content: list[dict[str, Any]], options: dict[str, Any], parse: Callable[[str], Any] | None
    ) -> Any:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self._client.responses.create(
                    model=self.model,
                    input=[{"role": "user", "content": content}],
                    **options,
                )
        except Exception as exc:
            classified = classify_error(exc)
            if classified is None:
                raise
            logger.warning("OpenAI call failed: %s: %s", type(exc).__name__, exc)
            raise classified from exc
        if getattr(response, "status", None) == "incomplete":
            raise TransientError("OpenAI cut the response short")
        text = response.output_text
        if not text or not text.strip():
            raise TransientError("OpenAI returned an empty response")
        return parse(text) if parse else text


def create_openai_client(settings: Settings) -> OpenAIClient:
    return OpenAIClient(
        settings.openai_api_key,
        settings.openai_model,
        timeout_seconds=settings.llm_timeout_seconds,
        attempts=settings.llm_max_attempts,
    )
