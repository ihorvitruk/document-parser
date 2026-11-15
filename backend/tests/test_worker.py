from datetime import UTC, datetime
from uuid import uuid4

import fakeredis
import pytest

from app import queue
from app.config import Settings
from app.errors import PermanentError, TransientError
from app.models import Document, DocumentStatus, Page, ParserName
from app.storage import DocumentRepository
from app.worker.main import Worker
from tests.pdf_factory import build_pdf


class FakeParser:
    name = ParserName.pypdf

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    async def parse(self, path):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def settings(tmp_path):
    return Settings(upload_dir=tmp_path / "uploads", max_deliveries=3)


@pytest.fixture
def repo(redis, settings):
    return DocumentRepository(redis, settings.document_ttl_seconds)


@pytest.fixture
def make_worker(redis, settings):
    async def _make(parser=None, consumer="worker-a"):
        await queue.ensure_group(redis)
        factory = (lambda name: parser) if parser else None
        kwargs = {"parser_factory": factory} if factory else {}
        return Worker(redis, settings, consumer, **kwargs)

    return _make


@pytest.fixture
def submit(repo, settings):
    """Store a document with its file and queue it, as the API does."""

    async def _submit(pdf: bytes | None = None, write_file=True) -> str:
        doc_id = uuid4().hex
        now = datetime.now(UTC)
        if write_file:
            settings.upload_dir.mkdir(parents=True, exist_ok=True)
            (settings.upload_dir / f"{doc_id}.pdf").write_bytes(pdf or build_pdf(["hello"]))
        await repo.create_many(
            [
                Document(
                    id=doc_id,
                    filename="x.pdf",
                    parser=ParserName.pypdf,
                    status=DocumentStatus.queued,
                    created_at=now,
                    updated_at=now,
                )
            ]
        )
        return doc_id

    return _submit


def file_exists(settings, doc_id):
    return (settings.upload_dir / f"{doc_id}.pdf").exists()


async def pending_count(redis):
    return (await redis.xpending(queue.STREAM_KEY, queue.GROUP))["pending"]


async def test_document_is_processed_end_to_end_with_pypdf(make_worker, submit, repo, settings, redis):
    doc_id = await submit(build_pdf(["Alpha", "Beta"]))
    worker = await make_worker()

    assert await worker.run_once(block_ms=10) == 1

    doc = await repo.get(doc_id)
    assert doc.status is DocumentStatus.done
    assert doc.page_count == 2
    assert [p.content for p in doc.pages] == ["Alpha", "Beta"]
    assert not file_exists(settings, doc_id)
    assert await pending_count(redis) == 0
    assert await redis.xlen(queue.STREAM_KEY) == 0


async def test_queue_is_empty_when_nothing_was_submitted(make_worker):
    worker = await make_worker()
    assert await worker.run_once(block_ms=10) == 0


async def test_permanent_error_fails_the_document_with_a_readable_message(
    make_worker, submit, repo, settings
):
    doc_id = await submit()
    worker = await make_worker(FakeParser(error=PermanentError("The PDF is password-protected")))

    await worker.run_once(block_ms=10)

    doc = await repo.get(doc_id)
    assert doc.status is DocumentStatus.failed
    assert doc.error == "The PDF is password-protected"
    assert not file_exists(settings, doc_id)


async def test_transient_error_that_survived_retries_fails_the_document(make_worker, submit, repo):
    doc_id = await submit()
    worker = await make_worker(FakeParser(error=TransientError("Gemini is overloaded")))

    await worker.run_once(block_ms=10)

    doc = await repo.get(doc_id)
    assert doc.status is DocumentStatus.failed
    assert doc.error == "Gemini is overloaded"


async def test_unexpected_exception_fails_only_that_document(make_worker, submit, repo, redis):
    bad = await submit()
    worker = await make_worker(FakeParser(error=RuntimeError("boom")))
    await worker.run_once(block_ms=10)

    good = await submit()
    worker.parser_factory = lambda name: FakeParser(result=[Page(number=1, content="fine")])
    await worker.run_once(block_ms=10)

    failed = await repo.get(bad)
    assert failed.status is DocumentStatus.failed
    assert "Unexpected error" in failed.error
    assert "boom" not in failed.error  # internals are not leaked to the user
    assert (await repo.get(good)).status is DocumentStatus.done
    assert await pending_count(redis) == 0


async def test_missing_upload_file_fails_the_document(make_worker, submit, repo):
    doc_id = await submit(write_file=False)
    worker = await make_worker()

    await worker.run_once(block_ms=10)

    doc = await repo.get(doc_id)
    assert doc.status is DocumentStatus.failed
    assert "no longer available" in doc.error


async def test_finished_document_is_not_processed_twice(make_worker, submit, repo, redis):
    doc_id = await submit()
    parser = FakeParser(result=[Page(number=1, content="x")])
    worker = await make_worker(parser)
    await worker.run_once(block_ms=10)

    # The same job is delivered again (for example the ack was lost).
    await redis.xadd(queue.STREAM_KEY, {"doc_id": doc_id})
    await worker.run_once(block_ms=10)

    assert parser.calls == 1
    assert (await repo.get(doc_id)).status is DocumentStatus.done


async def test_entry_of_an_expired_document_is_dropped(make_worker, redis, settings):
    await queue.ensure_group(redis)
    await redis.xadd(queue.STREAM_KEY, {"doc_id": "gone"})
    worker = await make_worker()

    assert await worker.run_once(block_ms=10) == 1
    assert await pending_count(redis) == 0


async def test_job_abandoned_by_a_crashed_worker_is_finished_by_another(
    submit, repo, redis, settings
):
    doc_id = await submit(build_pdf(["recovered"]))
    await queue.ensure_group(redis)

    # Worker A receives the job, starts it and dies before acknowledging it.
    await queue.read_new(redis, "worker-a", block_ms=10)
    await repo.update(doc_id, status=DocumentStatus.processing)
    assert await pending_count(redis) == 1

    # Worker B treats anything idle at all as abandoned.
    worker_b = Worker(redis, settings.model_copy(update={"claim_idle_seconds": 0}), "worker-b")
    assert await worker_b.run_once(block_ms=10) == 1

    doc = await repo.get(doc_id)
    assert doc.status is DocumentStatus.done
    assert doc.pages[0].content == "recovered"
    assert await pending_count(redis) == 0


async def test_job_that_is_not_idle_long_enough_is_left_alone(submit, redis, settings):
    await submit()
    await queue.ensure_group(redis)
    await queue.read_new(redis, "worker-a", block_ms=10)

    worker_b = Worker(redis, settings.model_copy(update={"claim_idle_seconds": 3600}), "worker-b")

    assert await worker_b.run_once(block_ms=10) == 0
    assert await pending_count(redis) == 1


async def test_document_that_keeps_crashing_workers_is_given_up_on(
    make_worker, submit, repo, redis, settings
):
    doc_id = await submit()
    worker = await make_worker()
    [entry] = await queue.read_new(redis, "worker-a", block_ms=10)
    assert entry.deliveries == 1

    # Three more workers pick it up and die; delivery counts go 2, 3, 4.
    deliveries = []
    for name in ("b", "c", "d"):
        [claimed] = await queue.claim_stale(redis, f"worker-{name}", min_idle_ms=0)
        deliveries.append(claimed.deliveries)
    assert deliveries == [2, 3, 4]

    await worker.handle(claimed)

    doc = await repo.get(doc_id)
    assert doc.status is DocumentStatus.failed
    assert "interrupted repeatedly" in doc.error
    assert not file_exists(settings, doc_id)
    assert await pending_count(redis) == 0


async def test_entry_without_doc_id_is_dropped(make_worker, redis):
    await queue.ensure_group(redis)
    await redis.xadd(queue.STREAM_KEY, {"other": "field"})
    worker = await make_worker()

    assert await worker.run_once(block_ms=10) == 1
    assert await pending_count(redis) == 0
