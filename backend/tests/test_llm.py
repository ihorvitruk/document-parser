import asyncio
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

from app.errors import PermanentError, TransientError
from app.llm.gemini import GeminiClient, classify_error
from app.llm.retry import call_with_retries
from app.llm.summarizer import MAX_INPUT_CHARS, GeminiSummarizer, build_prompt
from app.models import Page
from tests.fakes import FakeSdk, Recorder


def flaky(*outcomes):
    """An async callable that returns or raises the given outcomes in order."""
    calls = {"n": 0}

    async def call():
        outcome = outcomes[calls["n"]]
        calls["n"] += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    call.calls = calls
    return call


# --- retry -------------------------------------------------------------------


async def test_retry_succeeds_after_transient_errors():
    sleep = Recorder()
    call = flaky(TransientError("429"), TransientError("503"), "ok")

    assert await call_with_retries(call, attempts=3, sleep=sleep) == "ok"
    assert call.calls["n"] == 3
    assert len(sleep.delays) == 2
    assert 2 <= sleep.delays[0] < 3.1  # about 2 s
    assert 8 <= sleep.delays[1] < 9.1  # about 8 s


async def test_retry_gives_up_after_the_last_attempt_and_says_so():
    sleep = Recorder()
    call = flaky(TransientError("a"), TransientError("b"), TransientError("c"))

    with pytest.raises(TransientError, match="gave up after 3 attempts"):
        await call_with_retries(call, attempts=3, sleep=sleep)
    assert call.calls["n"] == 3
    assert len(sleep.delays) == 2  # no pause after the final failure


async def test_permanent_error_is_not_retried():
    sleep = Recorder()
    call = flaky(PermanentError("bad key"), "never reached")

    with pytest.raises(PermanentError, match="bad key"):
        await call_with_retries(call, attempts=3, sleep=sleep)
    assert call.calls["n"] == 1
    assert sleep.delays == []


async def test_unknown_exceptions_are_not_retried():
    call = flaky(ValueError("bug"), "never reached")

    with pytest.raises(ValueError):
        await call_with_retries(call, attempts=3, sleep=Recorder())
    assert call.calls["n"] == 1


# --- error classification ------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "kind", "text"),
    [
        (429, TransientError, "rate limit"),
        (500, TransientError, "unavailable"),
        (503, TransientError, "unavailable"),
        (401, PermanentError, "API key"),
        (403, PermanentError, "API key"),
        (404, PermanentError, "model"),
        (400, PermanentError, "rejected the request"),
    ],
)
def test_http_status_classification(code, kind, text):
    exc = errors.APIError(code, {"error": {"message": "x", "status": "S"}})
    classified = classify_error(exc)
    assert isinstance(classified, kind)
    assert text in str(classified)


def test_invalid_key_reported_as_400_is_permanent():
    exc = errors.ClientError(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
    classified = classify_error(exc)
    assert isinstance(classified, PermanentError)
    assert "API key" in str(classified)


@pytest.mark.parametrize("exc", [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), TimeoutError()])
def test_network_problems_are_transient(exc):
    assert isinstance(classify_error(exc), TransientError)


def test_unknown_exception_is_not_classified():
    assert classify_error(ValueError("bug")) is None


# --- Gemini client -------------------------------------------------------------


def client_with(sdk, **kwargs):
    return GeminiClient("key", "test-model", client=sdk, sleep=Recorder(), **kwargs)


async def test_client_returns_text_and_sends_model_and_config():
    sdk = FakeSdk("hello")

    assert await client_with(sdk).generate("prompt", temperature=0.1) == "hello"
    request = sdk.requests[0]
    assert request["model"] == "test-model"
    assert request["contents"] == "prompt"
    assert request["config"].temperature == 0.1


async def test_client_retries_rate_limits_then_succeeds():
    sdk = FakeSdk(errors.ClientError(429, {"error": {"message": "quota"}}), "fine")

    assert await client_with(sdk).generate("p") == "fine"
    assert len(sdk.requests) == 2


async def test_client_gives_up_on_persistent_rate_limits():
    rate_limited = errors.ClientError(429, {"error": {"message": "quota"}})
    sdk = FakeSdk(rate_limited, rate_limited, rate_limited)

    with pytest.raises(TransientError, match="rate limit.*gave up after 3 attempts"):
        await client_with(sdk).generate("p")
    assert len(sdk.requests) == 3


async def test_client_does_not_retry_a_rejected_key():
    sdk = FakeSdk(errors.ClientError(403, {"error": {"message": "denied"}}), "unused")

    with pytest.raises(PermanentError, match="API key"):
        await client_with(sdk).generate("p")
    assert len(sdk.requests) == 1


async def test_client_treats_a_hung_call_as_a_transient_timeout():
    async def hang():
        await asyncio.sleep(10)

    sdk = FakeSdk(hang)

    with pytest.raises(TransientError, match="network problem or timeout"):
        await client_with(sdk, timeout_seconds=0.05, attempts=1).generate("p")


async def test_client_retries_an_empty_response():
    sdk = FakeSdk("   ", "real answer")

    assert await client_with(sdk).generate("p") == "real answer"


async def test_client_passes_unknown_exceptions_through():
    sdk = FakeSdk(ValueError("bug"))

    with pytest.raises(ValueError):
        await client_with(sdk).generate("p")


# --- summarizer ----------------------------------------------------------------


def test_prompt_contains_every_page_with_its_number():
    prompt = build_prompt([Page(number=1, content="Alpha"), Page(number=2, content="Beta")])

    assert "[Page 1]\nAlpha" in prompt
    assert "[Page 2]\nBeta" in prompt
    assert "was cut off" not in prompt


def test_prompt_skips_empty_pages():
    prompt = build_prompt([Page(number=1, content="Alpha"), Page(number=2, content="  ")])

    assert "[Page 2]" not in prompt


def test_long_document_is_truncated_and_the_model_is_told():
    prompt = build_prompt([Page(number=1, content="x" * (MAX_INPUT_CHARS + 5000))])

    assert "was cut off" in prompt
    assert len(prompt) < MAX_INPUT_CHARS + 1000


async def test_summarizer_returns_the_stripped_model_text():
    sdk = FakeSdk("  A short summary.\n")
    summarizer = GeminiSummarizer(client_with(sdk))

    assert await summarizer.summarize([Page(number=1, content="text")]) == "A short summary."
