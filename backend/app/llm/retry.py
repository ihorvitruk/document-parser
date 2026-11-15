import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.errors import TransientError

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def call_with_retries(
    call: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_delay: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run `call`, retrying only TransientError with growing pauses (about 2 s, 8 s).

    PermanentError and anything else is raised immediately: retrying cannot fix it.
    After the last attempt the TransientError is raised again, saying how many
    attempts were made.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except TransientError as exc:
            if attempt == attempts:
                raise TransientError(f"{exc} (gave up after {attempts} attempts)") from exc
            delay = base_delay * 4 ** (attempt - 1) + random.uniform(0, 1)
            logger.warning(
                "Transient LLM error (%s), attempt %d/%d, retrying in %.1fs",
                exc,
                attempt,
                attempts,
                delay,
            )
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
