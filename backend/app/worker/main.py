import asyncio
import contextlib
import logging
import signal
import socket
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app import queue
from app.config import Settings, get_settings
from app.llm.summarizer import Summarizer, create_summarizer
from app.models import ParserName
from app.parsers.base import Parser
from app.parsers.registry import build_parser_factory
from app.storage import DocumentRepository
from app.worker.processor import fail_document, process_document

logger = logging.getLogger("worker")

BLOCK_MS = 5000


class Worker:
    """Pulls documents from the stream, one at a time, and processes them."""

    def __init__(
        self,
        redis: Redis,
        settings: Settings,
        consumer: str,
        parser_factory: Callable[[ParserName], Parser] | None = None,
        summarizer: Summarizer | None = None,
        block_ms: int = BLOCK_MS,
    ):
        self.redis = redis
        self.settings = settings
        self.consumer = consumer
        self.parser_factory = parser_factory or build_parser_factory(settings)
        self.summarizer = summarizer
        self.block_ms = block_ms
        self.repo = DocumentRepository(redis, settings.document_ttl_seconds)

    async def run(self, stop: asyncio.Event) -> None:
        await queue.ensure_group(self.redis)
        logger.info("worker %s started", self.consumer)
        while not stop.is_set():
            try:
                await self.run_once(self.block_ms)
            except RedisError:
                logger.exception("Redis error, retrying shortly")
                await asyncio.sleep(2)
        logger.info("worker %s stopped", self.consumer)

    async def run_once(self, block_ms: int | None = None) -> int:
        """Handle abandoned entries first, otherwise wait for a new one.
        Returns how many entries were handled."""
        entries = await queue.claim_stale(
            self.redis, self.consumer, self.settings.claim_idle_seconds * 1000
        )
        if not entries:
            entries = await queue.read_new(
                self.redis, self.consumer, self.block_ms if block_ms is None else block_ms
            )
        for entry in entries:
            await self.handle(entry)
        return len(entries)

    async def handle(self, entry: queue.QueueEntry) -> None:
        doc_id = entry.doc_id
        if doc_id is None:
            logger.error("entry %s has no doc_id, dropping it", entry.entry_id)
        elif entry.deliveries > self.settings.max_deliveries:
            await fail_document(
                self.repo,
                self.settings,
                doc_id,
                "Processing was interrupted repeatedly and was given up",
            )
        else:
            await self._process(entry, doc_id)
        await queue.ack(self.redis, entry.entry_id)

    async def _process(self, entry: queue.QueueEntry, doc_id: str) -> None:
        logger.info("doc=%s picked up (delivery %d)", doc_id, entry.deliveries)
        heartbeat = asyncio.create_task(self._heartbeat(entry.entry_id))
        try:
            await process_document(
                doc_id, self.repo, self.settings, self.parser_factory, self.summarizer
            )
        except Exception:
            logger.exception("doc=%s crashed unexpectedly", doc_id)
            await fail_document(
                self.repo, self.settings, doc_id, "Unexpected error while processing the document"
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, entry_id: str) -> None:
        """While a job runs, keep telling Redis this worker is alive."""
        interval = max(1.0, self.settings.claim_idle_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                await queue.touch(self.redis, self.consumer, entry_id)
            except RedisError:
                logger.warning("heartbeat failed for entry %s", entry_id)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = get_settings()
    # The socket timeout must be longer than the blocking read (BLOCK_MS), otherwise
    # an idle queue looks like a Redis outage.
    redis = Redis.from_url(
        settings.redis_url, decode_responses=True, socket_timeout=BLOCK_MS / 1000 + 15
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        worker = Worker(
            redis,
            settings,
            consumer=socket.gethostname(),
            summarizer=create_summarizer(settings),
        )
        await worker.run(stop)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
