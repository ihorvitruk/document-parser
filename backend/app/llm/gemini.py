import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from app.config import Settings
from app.errors import PermanentError, ProcessingError, TransientError
from app.llm.retry import call_with_retries

logger = logging.getLogger(__name__)


def classify_error(exc: Exception) -> ProcessingError | None:
    """Map an SDK or network exception to our error kinds, with a message that is
    safe and useful to show the user. Returns None for exceptions we do not know."""
    if isinstance(exc, errors.APIError):
        code = exc.code or 0
        message = str(exc.message or "").lower()
        if code == 429:
            return TransientError("Gemini rate limit reached")
        if code == 408 or code >= 500:
            return TransientError("Gemini is temporarily unavailable")
        if code in (401, 403) or "api key" in message:
            return PermanentError("Gemini rejected the API key")
        if code == 404:
            return PermanentError("The configured Gemini model was not found")
        return PermanentError("Gemini rejected the request")
    if isinstance(exc, httpx.TransportError | TimeoutError):
        return TransientError("Could not reach Gemini (network problem or timeout)")
    return None


class GeminiClient:
    """Thin wrapper around the Gemini SDK: one call with a timeout, error
    classification and bounded retries. Used for summaries and PDF parsing."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 60,
        attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        client: Any = None,
    ):
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self._sleep = sleep
        self._client = client or genai.Client(api_key=api_key)

    async def generate(
        self, contents: Any, *, parse: Callable[[str], Any] | None = None, **config: Any
    ) -> Any:
        """Return the response text, or `parse(text)` when given.

        `parse` runs inside the retry loop: if it raises TransientError (for example
        because the model returned malformed JSON) the call is made again.
        Raises TransientError (after retries) or PermanentError.
        """
        return await call_with_retries(
            lambda: self._generate_once(contents, config, parse),
            attempts=self.attempts,
            sleep=self._sleep,
        )

    async def _generate_once(
        self, contents: Any, config: dict[str, Any], parse: Callable[[str], Any] | None
    ) -> Any:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self._client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(**config) if config else None,
                )
        except Exception as exc:
            classified = classify_error(exc)
            if classified is None:
                raise
            logger.warning("Gemini call failed: %s: %s", type(exc).__name__, exc)
            raise classified from exc
        text = response.text
        if not text or not text.strip():
            raise TransientError("Gemini returned an empty response")
        return parse(text) if parse else text


def create_gemini_client(settings: Settings) -> GeminiClient:
    return GeminiClient(
        settings.gemini_api_key,
        settings.gemini_model,
        timeout_seconds=settings.llm_timeout_seconds,
        attempts=settings.llm_max_attempts,
    )
