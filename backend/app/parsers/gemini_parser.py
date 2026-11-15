from google.genai import types

from app.llm.gemini import GeminiClient
from app.models import ParserName
from app.parsers.chunked import (
    Chunk,
    ChunkedPdfParser,
    PageResult,
    build_prompt,
    parse_response,
)


class GeminiParser(ChunkedPdfParser):
    """Converts a PDF to markdown, page by page, using Gemini."""

    name = ParserName.gemini

    def __init__(self, client: GeminiClient, pages_per_chunk: int = 5, concurrency: int = 3):
        super().__init__(pages_per_chunk, concurrency)
        self.client = client

    async def convert_chunk(self, chunk: Chunk) -> list[str]:
        contents = [
            types.Part.from_bytes(data=chunk.pdf, mime_type="application/pdf"),
            build_prompt(chunk.page_count),
        ]
        return await self.client.generate(
            contents,
            parse=lambda text: parse_response(text, chunk.page_count),
            response_mime_type="application/json",
            response_schema=list[PageResult],
            temperature=0,
        )
