from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.api.routes import router
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield
    finally:
        await app.state.redis.aclose()


app = FastAPI(title="Document Parser", lifespan=lifespan)
app.include_router(router, prefix="/api")


@app.exception_handler(RedisError)
async def redis_unavailable(_: Request, __: RedisError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Storage is unavailable"})
