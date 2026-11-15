from app.config import Settings
from app.models import ParserInfo, ParserName

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
