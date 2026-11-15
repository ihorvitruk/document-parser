import base64

from app.llm.openai_client import OpenAIClient
from app.models import ParserName
from app.parsers.chunked import JSON_OBJECT, Chunk, ChunkedPdfParser, build_prompt, parse_response

# Structured outputs need an object at the top level, so the pages are wrapped.
RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "pages",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "pages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "page_number": {"type": "integer"},
                        "markdown": {"type": "string"},
                    },
                    "required": ["page_number", "markdown"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["pages"],
        "additionalProperties": False,
    },
}


class OpenAIParser(ChunkedPdfParser):
    """Converts a PDF to markdown, page by page, using GPT-5 Mini.

    OpenAI reads both the text and the rendered page images of a PDF, so scanned
    pages are handled too.
    """

    name = ParserName.gpt5mini

    def __init__(self, client: OpenAIClient, pages_per_chunk: int = 5, concurrency: int = 3):
        super().__init__(pages_per_chunk, concurrency)
        self.client = client

    async def convert_chunk(self, chunk: Chunk) -> list[str]:
        encoded = base64.b64encode(chunk.pdf).decode("ascii")
        content = [
            {
                "type": "input_file",
                "filename": f"pages-{chunk.first_page}.pdf",
                "file_data": f"data:application/pdf;base64,{encoded}",
            },
            {"type": "input_text", "text": build_prompt(chunk.page_count, JSON_OBJECT)},
        ]
        return await self.client.respond(
            content,
            parse=lambda text: parse_response(text, chunk.page_count),
            text={"format": RESPONSE_FORMAT},
        )
