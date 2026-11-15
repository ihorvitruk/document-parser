import pytest

from app.errors import PermanentError
from app.models import ParserName
from app.parsers.pypdf_parser import PyPdfParser
from app.parsers.registry import get_parser
from tests.pdf_factory import build_pdf, encrypt_pdf


@pytest.fixture
def write_pdf(tmp_path):
    def _write(data: bytes):
        path = tmp_path / "doc.pdf"
        path.write_bytes(data)
        return path

    return _write


async def test_extracts_one_block_per_page_in_order(write_pdf):
    path = write_pdf(build_pdf(["First page text", "Second page text", "Third page text"]))

    pages = await PyPdfParser().parse(path)

    assert [p.number for p in pages] == [1, 2, 3]
    assert [p.content for p in pages] == ["First page text", "Second page text", "Third page text"]


async def test_empty_page_is_kept_with_empty_content(write_pdf):
    path = write_pdf(build_pdf(["Has text", "", "Also has text"]))

    pages = await PyPdfParser().parse(path)

    assert [p.number for p in pages] == [1, 2, 3]
    assert pages[1].content == ""


async def test_special_characters_survive(write_pdf):
    path = write_pdf(build_pdf(["Total (net): 100 / 200"]))

    pages = await PyPdfParser().parse(path)

    assert pages[0].content == "Total (net): 100 / 200"


async def test_password_protected_pdf_is_a_permanent_error(write_pdf):
    path = write_pdf(encrypt_pdf(build_pdf(["secret"]), user_password="hunter2"))

    with pytest.raises(PermanentError, match="password"):
        await PyPdfParser().parse(path)


async def test_pdf_encrypted_without_user_password_is_readable(write_pdf):
    path = write_pdf(encrypt_pdf(build_pdf(["open text"]), user_password=""))

    pages = await PyPdfParser().parse(path)

    assert pages[0].content == "open text"


async def test_corrupt_pdf_is_a_permanent_error(write_pdf):
    path = write_pdf(b"%PDF-1.4\nthis is not really a pdf\n")

    with pytest.raises(PermanentError, match="could not be read"):
        await PyPdfParser().parse(path)


async def test_pdf_without_pages_is_a_permanent_error(write_pdf):
    path = write_pdf(build_pdf([]))

    with pytest.raises(PermanentError, match="no pages"):
        await PyPdfParser().parse(path)


def test_registry_returns_the_pypdf_parser():
    parser = get_parser(ParserName.pypdf)
    assert isinstance(parser, PyPdfParser)


def test_registry_reports_parsers_that_are_not_built_yet():
    with pytest.raises(PermanentError, match="not implemented"):
        get_parser(ParserName.gemini)
