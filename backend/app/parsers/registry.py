from collections.abc import Callable

from app.config import Settings
from app.errors import PermanentError
from app.llm.gemini import create_gemini_client
from app.llm.openai_client import create_openai_client
from app.models import ParserInfo, ParserName
from app.parsers.base import Parser
from app.parsers.gemini_parser import GeminiParser
from app.parsers.openai_parser import OpenAIParser
from app.parsers.pypdf_parser import PyPdfParser

LABELS = {
    ParserName.pypdf: "PyPDF (text only)",
    ParserName.gemini: "Google Gemini (markdown)",
    ParserName.gpt5mini: "OpenAI GPT-5 Mini",
}


def parser_availability(name: ParserName, settings: Settings) -> tuple[bool, str | None]:
    if name is ParserName.gemini and not settings.gemini_api_key:
        return False, "GEMINI_API_KEY is not set"
    if name is ParserName.gpt5mini and not settings.openai_api_key:
        return False, "OpenAI key required (OPENAI_API_KEY is not set)"
    return True, None


def list_parsers(settings: Settings) -> list[ParserInfo]:
    infos = []
    for name in ParserName:
        available, reason = parser_availability(name, settings)
        infos.append(
            ParserInfo(name=name, label=LABELS[name], available=available, reason=reason)
        )
    return infos


def _create(name: ParserName, settings: Settings) -> Parser:
    available, reason = parser_availability(name, settings)
    if not available:
        raise PermanentError(f"The '{name}' parser is unavailable: {reason}")
    if name is ParserName.pypdf:
        return PyPdfParser()
    if name is ParserName.gemini:
        return GeminiParser(
            create_gemini_client(settings),
            pages_per_chunk=settings.pages_per_chunk,
            concurrency=settings.llm_concurrency,
        )
    return OpenAIParser(
        create_openai_client(settings),
        pages_per_chunk=settings.pages_per_chunk,
        concurrency=settings.llm_concurrency,
    )


def build_parser_factory(settings: Settings) -> Callable[[ParserName], Parser]:
    """Returns a function that gives the parser for a name, creating each one (and
    its API client) once and reusing it for every document."""
    instances: dict[ParserName, Parser] = {}

    def factory(name: ParserName) -> Parser:
        if name not in instances:
            instances[name] = _create(name, settings)
        return instances[name]

    return factory
