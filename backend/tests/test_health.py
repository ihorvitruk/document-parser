import fakeredis
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api.main import app


@pytest.fixture
def client():
    # TestClient used without a context manager does not run the lifespan,
    # so we can inject a fake Redis client.
    app.state.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return TestClient(app)


def test_health_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_503_when_redis_is_down(client):
    class BrokenRedis:
        async def ping(self):
            raise RedisConnectionError("down")

    app.state.redis = BrokenRedis()
    response = client.get("/api/health")
    assert response.status_code == 503
