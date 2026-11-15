import asyncio
import logging
from pathlib import Path

from pypdf import PdfReader

from app.errors import PermanentError
from app.models import Page, ParserName

logger = logging.getLogger(__name__)


class PyPdfParser:
    """Extracts plain text, one block per page. No markdown, no OCR."""

    name = ParserName.pypdf

    async def parse(self, path: Path) -> list[Page]:
        # pypdf is synchronous and CPU-bound; keep it off the event loop.
        return await asyncio.to_thread(self._parse, path)

    def _parse(self, path: Path) -> list[Page]:
        reader = self._open(path)
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                # One unreadable page should not discard the rest of the document.
                logger.warning("Could not extract text from page %d of %s", number, path.name)
                text = ""
            pages.append(Page(number=number, content=text.strip()))
        if not pages:
            raise PermanentError("The PDF has no pages")
        return pages

    @staticmethod
    def _open(path: Path) -> PdfReader:
        try:
            reader = PdfReader(path)
            if reader.is_encrypted and not reader.decrypt(""):
                raise PermanentError("The PDF is password-protected")
            len(reader.pages)  # forces the page tree to load, so damage shows up here
        except PermanentError:
            raise
        except Exception as exc:
            raise PermanentError(f"The PDF could not be read: {exc}") from exc
        return reader
