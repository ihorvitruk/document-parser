import asyncio
import base64
import json
from io import BytesIO
from types import SimpleNamespace

import httpx
import openai
import pytest
from pypdf import PdfReader

from app.config import Settings
from app.errors import PermanentError, TransientError
from app.llm.openai_client import OpenAIClient, classify_error
from app.models import ParserName
from app.parsers.chunked import parse_response
from app.parsers.openai_parser import OpenAIParser
from app.parsers.registry import build_parser_factory
from tests.fakes import FakeOpenAISdk, OpenAIHandlerSdk, Recorder
from tests.pdf_factory import build_pdf

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")


def api_error(status: int, code: str | None = None) -> openai.APIStatusError:
    body = {"code": code, "message": "x"} if code else {"message": "x"}
    return openai.APIStatusError("x", response=httpx.Response(status, request=REQUEST), body=body)


def pdf_in(content) -> list[str]:
    """The text of each page of the PDF attached to a request."""
    part = content[0]
    assert part["type"] == "input_file"
    prefix = "data:application/pdf;base64,"
    assert part["file_data"].startswith(prefix)
    data = base64.b64decode(part["file_data"][len(prefix):])
    return [p.extract_text().strip() for p in PdfReader(BytesIO(data)).pages]


def answer(content) -> str:
    return json.dumps(
        {
            "pages": [
                {"page_number": i, "markdown": f"**{text}**"}
                for i, text in enumerate(pdf_in(content), start=1)
            ]
        }
    )


def client_with(sdk, **kwargs):
    return OpenAIClient("key", "gpt-5-mini", client=sdk, sleep=Recorder(), **kwargs)


@pytest.fixture
def write_pdf(tmp_path):
    def _write(data: bytes):
        path = tmp_path / "doc.pdf"
        path.write_bytes(data)
        return path

    return _write


# --- error classification ------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code", "kind", "text"),
    [
        (429, "rate_limit_exceeded", TransientError, "rate limit"),
        (429, None, TransientError, "rate limit"),
        (429, "insufficient_quota", PermanentError, "no credit left"),
        (500, None, TransientError, "unavailable"),
        (503, None, TransientError, "unavailable"),
        (401, None, PermanentError, "API key"),
        (403, None, PermanentError, "API key"),
        (404, None, PermanentError, "model"),
        (400, None, PermanentError, "rejected the request"),
    ],
)
def test_http_status_classification(status, code, kind, text):
    classified = classify_error(api_error(status, code))
    assert isinstance(classified, kind)
    assert text in str(classified)


@pytest.mark.parametrize(
    "exc",
    [openai.APIConnectionError(request=REQUEST), openai.APITimeoutError(request=REQUEST), TimeoutError()],
)
def test_network_problems_are_transient(exc):
    assert isinstance(classify_error(exc), TransientError)


def test_unknown_exception_is_not_classified():
    assert classify_error(ValueError("bug")) is None


# --- client ----------------------------------------------------------------------


async def test_rate_limit_is_retried_then_succeeds():
    sdk = FakeOpenAISdk(api_error(429), "fine")

    assert await client_with(sdk).respond([{"type": "input_text", "text": "hi"}]) == "fine"
    assert len(sdk.requests) == 2


async def test_account_without_credit_fails_at_once():
    sdk = FakeOpenAISdk(api_error(429, "insufficient_quota"), "unused")

    with pytest.raises(PermanentError, match="no credit left"):
        await client_with(sdk).respond([])
    assert len(sdk.requests) == 1


async def test_persistent_server_errors_give_up_after_the_limit():
    sdk = FakeOpenAISdk(api_error(503), api_error(503), api_error(503))

    with pytest.raises(TransientError, match="unavailable.*gave up after 3 attempts"):
        await client_with(sdk).respond([])
    assert len(sdk.requests) == 3


async def test_cut_off_response_is_retried():
    cut = SimpleNamespace(output_text='{"pages": [', status="incomplete")
    sdk = FakeOpenAISdk(cut, "complete")

    assert await client_with(sdk).respond([]) == "complete"


async def test_empty_response_is_retried():
    sdk = FakeOpenAISdk("  ", "real")

    assert await client_with(sdk).respond([]) == "real"


async def test_hung_call_becomes_a_transient_timeout():
    async def hang():
        await asyncio.sleep(10)

    with pytest.raises(TransientError, match="network problem or timeout"):
        await client_with(FakeOpenAISdk(hang), timeout_seconds=0.05, attempts=1).respond([])


async def test_unknown_exceptions_pass_through():
    with pytest.raises(ValueError):
        await client_with(FakeOpenAISdk(ValueError("bug"))).respond([])


# --- parser ----------------------------------------------------------------------


async def test_request_carries_the_pdf_a_prompt_and_a_strict_schema(write_pdf):
    sdk = OpenAIHandlerSdk(answer)
    parser = OpenAIParser(client_with(sdk), pages_per_chunk=5)

    await parser.parse(write_pdf(build_pdf(["only page"])))

    request = sdk.requests[0]
    assert request["model"] == "gpt-5-mini"
    message = request["input"][0]
    assert message["role"] == "user"
    assert pdf_in(message["content"]) == ["only page"]
    assert message["content"][1]["type"] == "input_text"
    assert "1 page(s)" in message["content"][1]["text"]
    fmt = request["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["strict"] is True
    assert fmt["schema"]["required"] == ["pages"]


async def test_long_document_is_chunked_and_pages_are_renumbered(write_pdf):
    sdk = OpenAIHandlerSdk(answer)
    parser = OpenAIParser(client_with(sdk), pages_per_chunk=3)

    pages = await parser.parse(write_pdf(build_pdf([f"Page {n}" for n in range(1, 8)])))

    assert [p.number for p in pages] == [1, 2, 3, 4, 5, 6, 7]
    assert [p.content for p in pages] == [f"**Page {n}**" for n in range(1, 8)]
    sizes = sorted(len(pdf_in(r["input"][0]["content"])) for r in sdk.requests)
    assert sizes == [1, 3, 3]


async def test_wrong_page_count_is_retried(write_pdf):
    good = json.dumps({"pages": [{"page_number": 1, "markdown": "a"}, {"page_number": 2, "markdown": "b"}]})
    short = json.dumps({"pages": [{"page_number": 1, "markdown": "a"}]})
    sdk = FakeOpenAISdk(short, good)
    parser = OpenAIParser(client_with(sdk), pages_per_chunk=5)

    pages = await parser.parse(write_pdf(build_pdf(["a", "b"])))

    assert [p.content for p in pages] == ["a", "b"]
    assert len(sdk.requests) == 2


def test_response_parser_accepts_an_object_with_pages_and_a_bare_array():
    wrapped = json.dumps({"pages": [{"page_number": 1, "markdown": " x "}]})
    bare = json.dumps([{"page_number": 1, "markdown": "x"}])

    assert parse_response(wrapped, 1) == ["x"]
    assert parse_response(bare, 1) == ["x"]


@pytest.mark.parametrize("text", ["not json", "{}", '{"pages": "no"}', '{"pages": [{"page_number": "a"}]}'])
def test_response_parser_rejects_malformed_answers_as_transient(text):
    with pytest.raises(TransientError, match="malformed answer"):
        parse_response(text, 1)


# --- registry --------------------------------------------------------------------


def test_factory_builds_the_openai_parser_when_a_key_is_set():
    factory = build_parser_factory(Settings(openai_api_key="k", pages_per_chunk=4))

    parser = factory(ParserName.gpt5mini)

    assert isinstance(parser, OpenAIParser)
    assert parser.pages_per_chunk == 4
    assert factory(ParserName.gpt5mini) is parser


def test_factory_refuses_the_openai_parser_without_a_key():
    with pytest.raises(PermanentError, match="OpenAI key required"):
        build_parser_factory(Settings())(ParserName.gpt5mini)
