import json
from io import BytesIO

import pytest
from google.genai import errors
from pypdf import PdfReader

from app.config import Settings
from app.errors import PermanentError, TransientError
from app.llm.gemini import GeminiClient
from app.models import ParserName
from app.parsers.gemini_parser import GeminiParser
from app.parsers.pypdf_parser import PyPdfParser
from app.parsers.registry import build_parser_factory
from tests.fakes import FakeSdk, HandlerSdk, Recorder
from tests.pdf_factory import build_pdf, encrypt_pdf


def chunk_pages(contents) -> list[str]:
    """The text of each page of the PDF part in a request."""
    part = contents[0]
    assert part.inline_data.mime_type == "application/pdf"
    return [p.extract_text().strip() for p in PdfReader(BytesIO(part.inline_data.data)).pages]


def markdown_answer(contents) -> str:
    """What a well-behaved model returns: each page's text, in bold."""
    return json.dumps(
        [
            {"page_number": i, "markdown": f"**{text}**"}
            for i, text in enumerate(chunk_pages(contents), start=1)
        ]
    )


def parser_with(sdk, pages_per_chunk=3, concurrency=3, attempts=3):
    client = GeminiClient("key", "m", client=sdk, sleep=Recorder(), attempts=attempts)
    return GeminiParser(client, pages_per_chunk=pages_per_chunk, concurrency=concurrency)


@pytest.fixture
def write_pdf(tmp_path):
    def _write(data: bytes):
        path = tmp_path / "doc.pdf"
        path.write_bytes(data)
        return path

    return _write


async def test_long_document_is_sent_in_chunks_and_pages_are_renumbered(write_pdf):
    sdk = HandlerSdk(markdown_answer)
    path = write_pdf(build_pdf([f"Page {n}" for n in range(1, 8)]))

    pages = await parser_with(sdk, pages_per_chunk=3).parse(path)

    assert [p.number for p in pages] == [1, 2, 3, 4, 5, 6, 7]
    assert [p.content for p in pages] == [f"**Page {n}**" for n in range(1, 8)]
    sizes = sorted(len(chunk_pages(r["contents"])) for r in sdk.requests)
    assert sizes == [1, 3, 3]  # 7 pages -> 3 + 3 + 1


async def test_request_asks_for_structured_json(write_pdf):
    sdk = HandlerSdk(markdown_answer)

    await parser_with(sdk).parse(write_pdf(build_pdf(["only page"])))

    config = sdk.requests[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.temperature == 0
    assert "1 page(s)" in sdk.requests[0]["contents"][1]


async def test_pages_returned_out_of_order_are_put_back_in_order(write_pdf):
    def shuffled(contents):
        return json.dumps(
            [
                {"page_number": 2, "markdown": "second"},
                {"page_number": 1, "markdown": "first"},
            ]
        )

    pages = await parser_with(HandlerSdk(shuffled)).parse(write_pdf(build_pdf(["a", "b"])))

    assert [p.content for p in pages] == ["first", "second"]


async def test_blank_page_has_empty_markdown(write_pdf):
    def answer(contents):
        return json.dumps([{"page_number": 1, "markdown": ""}, {"page_number": 2, "markdown": "x"}])

    pages = await parser_with(HandlerSdk(answer)).parse(write_pdf(build_pdf(["", "x"])))

    assert pages[0].content == ""


async def test_wrong_number_of_pages_is_retried(write_pdf):
    good = json.dumps([{"page_number": 1, "markdown": "a"}, {"page_number": 2, "markdown": "b"}])
    too_few = json.dumps([{"page_number": 1, "markdown": "a"}])
    sdk = FakeSdk(too_few, good)

    pages = await parser_with(sdk).parse(write_pdf(build_pdf(["a", "b"])))

    assert [p.content for p in pages] == ["a", "b"]
    assert len(sdk.requests) == 2


async def test_malformed_json_is_retried_then_gives_up(write_pdf):
    sdk = FakeSdk("not json", "{}", "still not json")

    with pytest.raises(TransientError, match="malformed answer.*gave up after 3 attempts"):
        await parser_with(sdk).parse(write_pdf(build_pdf(["a"])))
    assert len(sdk.requests) == 3


async def test_rejected_key_fails_without_retrying(write_pdf):
    sdk = FakeSdk(errors.ClientError(403, {"error": {"message": "denied"}}))

    with pytest.raises(PermanentError, match="API key"):
        await parser_with(sdk).parse(write_pdf(build_pdf(["a"])))
    assert len(sdk.requests) == 1


async def test_one_failed_chunk_stops_the_others(write_pdf):
    calls = {"n": 0}

    def answer(contents):
        calls["n"] += 1
        if calls["n"] == 1:
            raise_permanent()
        return markdown_answer(contents)

    def raise_permanent():
        raise PermanentError("Gemini rejected the request")

    sdk = HandlerSdk(answer)
    path = write_pdf(build_pdf([f"p{n}" for n in range(1, 11)]))

    with pytest.raises(PermanentError):
        await parser_with(sdk, pages_per_chunk=1, concurrency=1).parse(path)
    # The failing chunk plus at most one that had already started; not all ten.
    assert len(sdk.requests) <= 2


async def test_number_of_parallel_requests_is_limited(write_pdf):
    sdk = HandlerSdk(markdown_answer, delay=0.02)
    path = write_pdf(build_pdf([f"p{n}" for n in range(1, 9)]))

    pages = await parser_with(sdk, pages_per_chunk=1, concurrency=2).parse(path)

    assert len(pages) == 8
    assert sdk.max_in_flight == 2


async def test_password_protected_pdf_is_rejected_before_any_request(write_pdf):
    sdk = HandlerSdk(markdown_answer)
    path = write_pdf(encrypt_pdf(build_pdf(["secret"]), user_password="pw"))

    with pytest.raises(PermanentError, match="password"):
        await parser_with(sdk).parse(path)
    assert sdk.requests == []


async def test_corrupt_pdf_is_rejected_before_any_request(write_pdf):
    sdk = HandlerSdk(markdown_answer)

    with pytest.raises(PermanentError, match="could not be read"):
        await parser_with(sdk).parse(write_pdf(b"%PDF-1.4\ngarbage"))
    assert sdk.requests == []


# --- registry ------------------------------------------------------------------


def test_factory_builds_the_gemini_parser_when_a_key_is_set():
    factory = build_parser_factory(Settings(gemini_api_key="k", pages_per_chunk=4))

    parser = factory(ParserName.gemini)

    assert isinstance(parser, GeminiParser)
    assert parser.pages_per_chunk == 4


def test_factory_reuses_parsers_between_documents():
    factory = build_parser_factory(Settings(gemini_api_key="k"))

    assert factory(ParserName.gemini) is factory(ParserName.gemini)
    assert factory(ParserName.pypdf) is factory(ParserName.pypdf)
    assert isinstance(factory(ParserName.pypdf), PyPdfParser)


def test_factory_refuses_a_parser_whose_key_is_missing():
    factory = build_parser_factory(Settings())

    with pytest.raises(PermanentError, match="GEMINI_API_KEY"):
        factory(ParserName.gemini)
    with pytest.raises(PermanentError, match="OpenAI key required"):
        factory(ParserName.gpt5mini)
