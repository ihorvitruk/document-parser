from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ParserName(StrEnum):
    pypdf = "pypdf"
    gemini = "gemini"
    gpt5mini = "gpt5mini"


class DocumentStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class Page(BaseModel):
    number: int
    content: str


class DocumentBrief(BaseModel):
    """A document without its page contents (used in lists)."""

    id: str
    filename: str
    parser: ParserName
    status: DocumentStatus
    error: str | None = None
    summary: str | None = None
    summary_error: str | None = None
    page_count: int = 0
    created_at: datetime
    updated_at: datetime


class Document(DocumentBrief):
    pages: list[Page] = Field(default_factory=list)


class UploadedDocument(BaseModel):
    id: str
    filename: str
    parser: ParserName
    status: DocumentStatus


class UploadResponse(BaseModel):
    documents: list[UploadedDocument]


class ParserInfo(BaseModel):
    name: ParserName
    label: str
    available: bool
    reason: str | None = None
