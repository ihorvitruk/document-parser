import logging
from collections.abc import Callable

from app.config import Settings
from app.errors import PermanentError, ProcessingError
from app.llm.summarizer import Summarizer, has_text
from app.models import DocumentStatus, Page, ParserName
from app.parsers.base import Parser
from app.parsers.registry import get_parser
from app.storage import DocumentRepository

logger = logging.getLogger(__name__)

TERMINAL = {DocumentStatus.done, DocumentStatus.failed}


def upload_path(settings: Settings, doc_id: str):
    return settings.upload_dir / f"{doc_id}.pdf"


async def fail_document(
    repo: DocumentRepository, settings: Settings, doc_id: str, message: str
) -> None:
    """Mark a document failed (unless it already finished) and drop its file."""
    doc = await repo.get(doc_id)
    if doc is not None and doc.status not in TERMINAL:
        await repo.update(doc_id, status=DocumentStatus.failed, error=message)
        logger.warning("doc=%s failed: %s", doc_id, message)
    upload_path(settings, doc_id).unlink(missing_ok=True)


async def summarize_pages(
    doc_id: str, pages: list[Page], summarizer: Summarizer | None
) -> tuple[str | None, str | None]:
    """Returns (summary, summary_error). A failed summary never fails the document:
    the user still gets the parsed pages."""
    if summarizer is None:
        return None, "Summary unavailable: GEMINI_API_KEY is not set"
    if not has_text(pages):
        return None, "Summary unavailable: the document has no extractable text"
    try:
        return await summarizer.summarize(pages), None
    except ProcessingError as exc:
        logger.warning("doc=%s summary failed: %s", doc_id, exc)
        return None, f"Summary unavailable: {exc}"
    except Exception:
        logger.exception("doc=%s summary crashed unexpectedly", doc_id)
        return None, "Summary unavailable: unexpected error"


async def process_document(
    doc_id: str,
    repo: DocumentRepository,
    settings: Settings,
    parser_factory: Callable[[ParserName], Parser] = get_parser,
    summarizer: Summarizer | None = None,
) -> None:
    """Process one document to a terminal state.

    Safe to run again for the same document: one that is already done or failed is
    skipped, and one left `processing` by a crashed worker is processed afresh.
    Errors that are expected (ProcessingError) become a `failed` document; anything
    else propagates to the worker loop.
    """
    doc = await repo.get(doc_id)
    if doc is None:
        logger.info("doc=%s is gone (expired?), skipping", doc_id)
        upload_path(settings, doc_id).unlink(missing_ok=True)
        return
    if doc.status in TERMINAL:
        logger.info("doc=%s already %s, skipping", doc_id, doc.status)
        return

    await repo.update(doc_id, status=DocumentStatus.processing)
    path = upload_path(settings, doc_id)
    try:
        if not path.exists():
            raise PermanentError("The uploaded file is no longer available")
        pages = await parser_factory(doc.parser).parse(path)
        await repo.update(doc_id, pages=pages, page_count=len(pages))
        summary, summary_error = await summarize_pages(doc_id, pages, summarizer)
        await repo.update(
            doc_id, status=DocumentStatus.done, summary=summary, summary_error=summary_error
        )
        logger.info(
            "doc=%s done: %d pages via %s, summary %s",
            doc_id,
            len(pages),
            doc.parser,
            "ok" if summary else "unavailable",
        )
    except ProcessingError as exc:
        await repo.update(doc_id, status=DocumentStatus.failed, error=str(exc))
        logger.warning("doc=%s failed: %s", doc_id, exc)
    path.unlink(missing_ok=True)
