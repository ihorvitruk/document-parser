from datetime import UTC, datetime

import fakeredis
import pytest

from app import queue
from app.models import Document, DocumentStatus, Page, ParserName
from app.storage import DocumentRepository, doc_key


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def repo(redis):
    return DocumentRepository(redis, ttl_seconds=3600)


def make_doc(doc_id="d1"):
    now = datetime.now(UTC)
    return Document(
        id=doc_id,
        filename="a.pdf",
        parser=ParserName.pypdf,
        status=DocumentStatus.queued,
        created_at=now,
        updated_at=now,
    )


async def test_create_queues_the_document_in_the_stream(repo, redis):
    await repo.create_many([make_doc("d1"), make_doc("d2")])

    entries = await redis.xrange(queue.STREAM_KEY)
    assert [fields["doc_id"] for _, fields in entries] == ["d1", "d2"]


async def test_update_changes_fields_and_keeps_the_rest(repo):
    await repo.create_many([make_doc()])

    updated = await repo.update(
        "d1",
        status=DocumentStatus.done,
        pages=[Page(number=1, content="hi"), Page(number=2, content="there")],
        page_count=2,
        summary="short",
    )

    doc = await repo.get("d1")
    assert updated is True
    assert doc.status is DocumentStatus.done
    assert doc.filename == "a.pdf"
    assert doc.page_count == 2
    assert doc.pages[1].content == "there"
    assert doc.summary == "short"
    assert doc.updated_at >= doc.created_at


async def test_update_can_clear_a_field(repo):
    await repo.create_many([make_doc()])
    await repo.update("d1", error="boom")
    await repo.update("d1", error=None)

    assert (await repo.get("d1")).error is None


async def test_update_does_not_resurrect_an_expired_document(repo, redis):
    await repo.create_many([make_doc()])
    await redis.delete(doc_key("d1"))  # what expiry does

    assert await repo.update("d1", status=DocumentStatus.done) is False
    assert await redis.exists(doc_key("d1")) == 0
    assert await repo.get("d1") is None


async def test_documents_keep_a_ttl(repo, redis):
    await repo.create_many([make_doc()])
    await repo.update("d1", status=DocumentStatus.processing)

    assert 0 < await redis.ttl(doc_key("d1")) <= 3600
