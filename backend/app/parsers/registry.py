from app.config import Settings
from app.errors import PermanentError
from app.models import ParserInfo, ParserName
from app.parsers.base import Parser
from app.parsers.pypdf_parser import PyPdfParser

LABELS = {
    ParserName.pypdf: "PyPDF (text only)",
    ParserName.gemini: "Google Gemini (markdown)",
    ParserName.gpt5mini: "OpenAI GPT-5 Mini",
}

# Parsers that exist so far. The Gemini and GPT-5 Mini parsers are added to this
# mapping as they are built.
_IMPLEMENTATIONS: dict[ParserName, type[Parser]] = {
    ParserName.pypdf: PyPdfParser,
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


def get_parser(name: ParserName) -> Parser:
    implementation = _IMPLEMENTATIONS.get(name)
    if implementation is None:
        raise PermanentError(f"The '{name}' parser is not implemented")
    return implementation()
