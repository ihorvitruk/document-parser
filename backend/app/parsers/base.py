from pathlib import Path
from typing import Protocol

from app.models import Page, ParserName


class Parser(Protocol):
    """Turns a PDF file into pages of text or markdown.

    Raises PermanentError for files that cannot be parsed and TransientError for
    temporary failures of an external service.
    """

    name: ParserName

    async def parse(self, path: Path) -> list[Page]: ...
