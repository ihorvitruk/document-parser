from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings, get_settings
from app.errors import UploadError
from app.models import (
    Document,
    DocumentBrief,
    DocumentStatus,
    ParserInfo,
    ParserName,
    UploadedDocument,
    UploadResponse,
)
from app.parsers.registry import list_parsers, parser_availability
from app.storage import DocumentRepository
from app.uploads import display_name, save_pdf

router = APIRouter()


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_repository(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> DocumentRepository:
    return DocumentRepository(get_redis(request), settings.document_ttl_seconds)


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    try:
        await get_redis(request).ping()
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc
    return {"status": "ok"}


@router.get("/parsers", response_model=list[ParserInfo])
async def parsers(settings: Annotated[Settings, Depends(get_settings)]):
    return list_parsers(settings)


@router.post("/documents", status_code=202, response_model=UploadResponse)
async def upload_documents(
    parser: Annotated[ParserName, Form()],
    settings: Annotated[Settings, Depends(get_settings)],
    repo: Annotated[DocumentRepository, Depends(get_repository)],
    files: Annotated[list[UploadFile] | None, File()] = None,
):
    available, reason = parser_availability(parser, settings)
    if not available:
        raise HTTPException(status_code=400, detail=f"Parser '{parser}' is unavailable: {reason}")
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded")

    # All-or-nothing: if any file is rejected, nothing is stored or queued.
    saved: list[Path] = []
    try:
        now = datetime.now(UTC)
        docs = []
        for file in files:
            doc_id = uuid4().hex
            path = settings.upload_dir / f"{doc_id}.pdf"
            await save_pdf(file, path, settings.max_upload_bytes)
            saved.append(path)
            docs.append(
                Document(
                    id=doc_id,
                    filename=display_name(file),
                    parser=parser,
                    status=DocumentStatus.queued,
                    created_at=now,
                    updated_at=now,
                )
            )
        await repo.create_many(docs)
    except UploadError as exc:
        _remove(saved)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except BaseException:
        _remove(saved)
        raise

    return UploadResponse(
        documents=[
            UploadedDocument(id=d.id, filename=d.filename, parser=d.parser, status=d.status)
            for d in docs
        ]
    )


@router.get("/documents", response_model=list[DocumentBrief])
async def list_documents(repo: Annotated[DocumentRepository, Depends(get_repository)]):
    return await repo.list()


@router.get("/documents/{doc_id}", response_model=Document)
async def get_document(
    doc_id: str, repo: Annotated[DocumentRepository, Depends(get_repository)]
):
    doc = await repo.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _remove(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)
