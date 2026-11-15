"""Work queue on a Redis Stream with a consumer group.

Delivery guarantees:
- An entry stays in the group's pending list until a worker acknowledges it, so a
  job is never lost when a worker dies.
- A pending entry that has been idle for too long is claimed by another worker.
- Redis counts deliveries per entry; the worker uses that count to stop retrying a
  document that keeps killing its workers.
"""

from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import ResponseError

STREAM_KEY = "docs:stream"
GROUP = "docs:workers"


@dataclass(frozen=True)
class QueueEntry:
    entry_id: str
    doc_id: str | None
    deliveries: int


async def ensure_group(redis: Redis) -> None:
    """Create the stream and group. Starting at id 0 means documents queued before
    the first worker started are still delivered."""
    try:
        await redis.xgroup_create(STREAM_KEY, GROUP, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def read_new(redis: Redis, consumer: str, block_ms: int) -> list[QueueEntry]:
    """Wait up to `block_ms` for one new entry."""
    result = await redis.xreadgroup(
        GROUP, consumer, {STREAM_KEY: ">"}, count=1, block=block_ms
    )
    entries = []
    for _stream, messages in result or []:
        for entry_id, fields in messages:
            entries.append(QueueEntry(entry_id, fields.get("doc_id"), deliveries=1))
    return entries


async def claim_stale(
    redis: Redis, consumer: str, min_idle_ms: int, count: int = 10
) -> list[QueueEntry]:
    """Take over entries that another consumer received but never acknowledged."""
    result = await redis.xautoclaim(
        STREAM_KEY, GROUP, consumer, min_idle_time=min_idle_ms, start_id="0-0", count=count
    )
    entries = []
    for entry_id, fields in result[1]:
        # XAUTOCLAIM already counted this claim as a delivery.
        pending = await redis.xpending_range(STREAM_KEY, GROUP, entry_id, entry_id, 1)
        deliveries = pending[0]["times_delivered"] if pending else 1
        entries.append(QueueEntry(entry_id, (fields or {}).get("doc_id"), deliveries))
    return entries


async def touch(redis: Redis, consumer: str, entry_id: str) -> None:
    """Reset an entry's idle time so a live worker's long job is not stolen.
    JUSTID keeps this from counting as a new delivery."""
    await redis.xclaim(
        STREAM_KEY, GROUP, consumer, min_idle_time=0, message_ids=[entry_id], justid=True
    )


async def ack(redis: Redis, entry_id: str) -> None:
    async with redis.pipeline(transaction=True) as pipe:
        pipe.xack(STREAM_KEY, GROUP, entry_id)
        pipe.xdel(STREAM_KEY, entry_id)
        await pipe.execute()
