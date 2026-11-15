import json
import time
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import WatchError

from app.models import Document, DocumentBrief, Page
from app.queue import STREAM_KEY

INDEX_KEY = "docs:index"
BRIEF_FIELDS = [
    "id",
    "filename",
    "parser",
    "status",
    "error",
    "summary",
    "summary_error",
    "page_count",
    "created_at",
    "updated_at",
]
OPTIONAL_FIELDS = {"error", "summary", "summary_error"}


def doc_key(doc_id: str) -> str:
    return f"doc:{doc_id}"


def _to_hash(doc: Document) -> dict[str, str]:
    data = doc.model_dump(mode="json")
    data["pages"] = json.dumps(data["pages"])
    return {key: "" if value is None else str(value) for key, value in data.items()}


def _serialize_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, list):
        return json.dumps([p.model_dump() if isinstance(p, Page) else p for p in value])
    return str(value)


def _from_hash(data: dict[str, str]) -> dict:
    result: dict = {}
    for key, value in data.items():
        if key == "pages":
            result[key] = json.loads(value) if value else []
        elif key == "page_count":
            result[key] = int(value or 0)
        elif key in OPTIONAL_FIELDS:
            result[key] = value or None
        else:
            result[key] = value
    return result


class DocumentRepository:
    """Documents live in Redis hashes; an index sorted set keeps them ordered."""

    def __init__(self, redis: Redis, ttl_seconds: int):
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    async def create_many(self, docs: list[Document]) -> None:
        """Store documents and queue them for processing.

        One transaction, so a document exists exactly when it is queued and a
        failure never leaves some of the documents stored.
        """
        async with self.redis.pipeline(transaction=True) as pipe:
            for doc in docs:
                key = doc_key(doc.id)
                pipe.hset(key, mapping=_to_hash(doc))
                pipe.expire(key, self.ttl_seconds)
                pipe.zadd(INDEX_KEY, {doc.id: doc.created_at.timestamp()})
                pipe.xadd(STREAM_KEY, {"doc_id": doc.id})
            await pipe.execute()

    async def update(self, doc_id: str, **fields: Any) -> bool:
        """Change fields of an existing document; returns False if it has expired.

        Never recreates an expired document: the existence check and the write are
        one watched transaction.
        """
        key = doc_key(doc_id)
        mapping = {name: _serialize_field(value) for name, value in fields.items()}
        mapping["updated_at"] = datetime.now(UTC).isoformat()
        async with self.redis.pipeline(transaction=True) as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    if not await pipe.exists(key):
                        return False
                    pipe.multi()
                    pipe.hset(key, mapping=mapping)
                    await pipe.execute()
                    return True
                except WatchError:
                    continue

    async def get(self, doc_id: str) -> Document | None:
        data = await self.redis.hgetall(doc_key(doc_id))
        if not data:
            return None
        return Document(**_from_hash(data))

    async def list(self) -> list[DocumentBrief]:
        """Newest first, without page contents."""
        await self.redis.zremrangebyscore(INDEX_KEY, "-inf", time.time() - self.ttl_seconds)
        ids = await self.redis.zrevrange(INDEX_KEY, 0, -1)
        if not ids:
            return []
        async with self.redis.pipeline(transaction=False) as pipe:
            for doc_id in ids:
                pipe.hmget(doc_key(doc_id), BRIEF_FIELDS)
            rows = await pipe.execute()
        briefs = []
        for row in rows:
            data = dict(zip(BRIEF_FIELDS, row, strict=True))
            if data["id"] is None:  # the document expired before the index entry
                continue
            briefs.append(DocumentBrief(**_from_hash(data)))
        return briefs
