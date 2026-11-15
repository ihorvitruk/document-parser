from fastapi import APIRouter, HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

router = APIRouter()


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    try:
        await get_redis(request).ping()
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc
    return {"status": "ok"}
