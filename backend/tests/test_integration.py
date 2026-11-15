"""End-to-end tests against a real Redis, with a fake LLM.

Everything runs for real except the calls to Gemini: the FastAPI app, the Redis
Stream with its consumer group, and several workers. Run them with

    docker compose --profile test run --rm tests

They are skipped when REDIS_URL is not set.
"""

import asyncio
import json
import os
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app import queue
from app.api.main import app
from app.config import Settings, get_settings
from app.llm.gemini import GeminiClient
from app.llm.summarizer import GeminiSummarizer
from app.models import ParserName
from app.parsers.gemini_parser import GeminiParser
from app.parsers.pypdf_parser import PyPdfParser
from app.worker.main import Worker
from tests.fakes import FakeSdk, HandlerSdk, Recorder, markdown_answer
from tests.pdf_factory import build_pdf

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL")


@pytest.fixture
async def redis():
    if not REDIS_URL:
        pytest.skip("REDIS_URL is not set; run `docker compose --profile test run --rm tests`")
    client = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except RedisError:
        pytest.skip("Redis is not reachable")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def settings(tmp_path):
    # A dummy key makes the Gemini parser "available"; the real Gemini is never called.
    return Settings(
        redis_url=REDIS_URL or "",
        upload_dir=tmp_path / "uploads",
        claim_idle_seconds=120,
        gemini_api_key="test-key",
    )


@pytest.fixture
async def api(redis, settings):
    app.state.redis = redis
    app.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def fake_summary_sdk(text="Fake summary."):
    return HandlerSdk(lambda contents: text)


class Workers:
    """Starts workers in the background and records which one handled which document."""

    def __init__(self, redis, settings, summarizer=None, gemini_sdk=None):
        self.redis = redis
        self.settings = settings
        self.summarizer = summarizer
        self.gemini_sdk = gemini_sdk or HandlerSdk(markdown_answer)
        self.handled: list[tuple[str, str]] = []  # (worker name, document file name)
        self.stop = asyncio.Event()
        self.tasks: list[asyncio.Task] = []

    def _factory(self, worker_name):
        gemini = GeminiParser(
            GeminiClient("k", "m", client=self.gemini_sdk, sleep=Recorder()), pages_per_chunk=3
        )

        class Recording:
            def __init__(self, inner, owner):
                self.inner, self.owner = inner, owner

            async def parse(self, path):
                await asyncio.sleep(0.05)  # give the other worker a chance to take the next job
                self.owner.handled.append((worker_name, path.name))
                return await self.inner.parse(path)

        parsers = {
            ParserName.pypdf: Recording(PyPdfParser(), self),
            ParserName.gemini: Recording(gemini, self),
        }
        return lambda name: parsers[name]

    def start(self, count=2, settings=None):
        for i in range(count):
            name = f"worker-{i + 1}"
            worker = Worker(
                self.redis,
                settings or self.settings,
                name,
                parser_factory=self._factory(name),
                summarizer=self.summarizer,
                block_ms=100,
            )
            self.tasks.append(asyncio.create_task(worker.run(self.stop)))

    async def shutdown(self):
        self.stop.set()
        await asyncio.wait_for(asyncio.gather(*self.tasks), timeout=5)


@pytest.fixture
async def workers(redis, settings):
    summarizer = GeminiSummarizer(GeminiClient("k", "m", client=fake_summary_sdk(), sleep=Recorder()))
    pool = Workers(redis, settings, summarizer)
    yield pool
    await pool.shutdown()


async def upload(api, parser, *named_pdfs):
    files = [("files", (name, pdf, "application/pdf")) for name, pdf in named_pdfs]
    response = await api.post("/api/documents", files=files, data={"parser": parser})
    assert response.status_code == 202, response.text
    return [d["id"] for d in response.json()["documents"]]


async def wait_until_finished(api, ids, timeout=10):
    async with asyncio.timeout(timeout):
        while True:
            listing = {d["id"]: d for d in (await api.get("/api/documents")).json()}
            if all(listing[i]["status"] in ("done", "failed") for i in ids):
                return {i: (await api.get(f"/api/documents/{i}")).json() for i in ids}
            await asyncio.sleep(0.05)


async def test_documents_go_from_upload_to_result(api, workers, redis, settings):
    ids = await upload(
        api,
        "pypdf",
        ("a.pdf", build_pdf(["Alpha one", "Alpha two"])),
        ("b.pdf", build_pdf(["Beta"])),
    )
    ids += await upload(api, "gemini", ("c.pdf", build_pdf(["Gamma"])))
    workers.start()

    docs = await wait_until_finished(api, ids)

    assert [docs[i]["status"] for i in ids] == ["done", "done", "done"]
    assert [p["content"] for p in docs[ids[0]]["pages"]] == ["Alpha one", "Alpha two"]
    assert [p["content"] for p in docs[ids[2]]["pages"]] == ["**Gamma**"]  # markdown from "Gemini"
    assert all(docs[i]["summary"] == "Fake summary." for i in ids)
    # Nothing is left behind: queue drained, no pending entries, uploaded files removed.
    assert await redis.xlen(queue.STREAM_KEY) == 0
    assert (await redis.xpending(queue.STREAM_KEY, queue.GROUP))["pending"] == 0
    assert list(settings.upload_dir.glob("*")) == []


async def test_each_document_is_handled_exactly_once_by_one_of_the_workers(api, workers):
    pdfs = [(f"d{i}.pdf", build_pdf([f"text {i}"])) for i in range(8)]
    ids = await upload(api, "pypdf", *pdfs)
    workers.start(count=2)

    docs = await wait_until_finished(api, ids)

    assert all(d["status"] == "done" for d in docs.values())
    handled_files = sorted(file for _, file in workers.handled)
    assert handled_files == sorted(f"{i}.pdf" for i in ids)  # once each, no duplicates
    assert {worker for worker, _ in workers.handled} == {"worker-1", "worker-2"}  # work was shared


async def test_job_abandoned_by_a_crashed_worker_is_finished_by_another(api, workers, redis, settings):
    [doc_id] = await upload(api, "pypdf", ("crash.pdf", build_pdf(["survivor"])))
    await queue.ensure_group(redis)

    # A worker receives the job and dies without acknowledging it.
    [entry] = await queue.read_new(redis, "crashed-worker", block_ms=100)
    assert entry.doc_id == doc_id
    assert (await api.get(f"/api/documents/{doc_id}")).json()["status"] == "queued"

    # Another worker treats anything idle at all as abandoned and finishes it.
    workers.start(count=1, settings=settings.model_copy(update={"claim_idle_seconds": 0}))
    doc = (await wait_until_finished(api, [doc_id]))[doc_id]

    assert doc["status"] == "done"
    assert doc["pages"][0]["content"] == "survivor"
    assert (await redis.xpending(queue.STREAM_KEY, queue.GROUP))["pending"] == 0


async def test_a_job_that_keeps_crashing_workers_is_failed_not_retried_forever(api, workers, redis, settings):
    [doc_id] = await upload(api, "pypdf", ("poison.pdf", build_pdf(["never processed"])))
    await queue.ensure_group(redis)
    await queue.read_new(redis, "ghost", block_ms=100)
    for name in ("ghost-2", "ghost-3"):  # each takeover that never finishes counts as a delivery
        await queue.claim_stale(redis, name, min_idle_ms=0)

    workers.start(count=1, settings=settings.model_copy(update={"claim_idle_seconds": 0}))
    doc = (await wait_until_finished(api, [doc_id]))[doc_id]

    assert doc["status"] == "failed"
    assert "interrupted repeatedly" in doc["error"]


async def test_failed_summary_still_delivers_the_pages(api, redis, settings):
    overloaded = errors.ServerError(503, {"error": {"message": "overloaded"}})
    failing_sdk = FakeSdk(overloaded, overloaded, overloaded)
    summarizer = GeminiSummarizer(GeminiClient("k", "m", client=failing_sdk, sleep=Recorder()))
    pool = Workers(redis, settings, summarizer)
    try:
        [doc_id] = await upload(api, "pypdf", ("p.pdf", build_pdf(["page one", "page two"])))
        pool.start(count=1)

        doc = (await wait_until_finished(api, [doc_id]))[doc_id]
    finally:
        await pool.shutdown()

    assert doc["status"] == "done"
    assert [p["content"] for p in doc["pages"]] == ["page one", "page two"]
    assert doc["summary"] is None
    assert "unavailable" in doc["summary_error"]
    assert len(failing_sdk.requests) == 3  # three attempts, then it gave up


async def test_corrupt_pdf_is_reported_as_failed_with_a_reason(api, workers):
    [doc_id] = await upload(api, "pypdf", ("broken.pdf", b"%PDF-1.4\nnot really a pdf"))
    workers.start(count=1)

    doc = (await wait_until_finished(api, [doc_id]))[doc_id]

    assert doc["status"] == "failed"
    assert "could not be read" in doc["error"]
    assert doc["pages"] == []
