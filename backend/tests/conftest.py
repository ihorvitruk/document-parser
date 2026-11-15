import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import Settings, get_settings

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


@pytest.fixture
def make_client(tmp_path):
    """Build a client with a fake Redis and settings pointing at a temp upload dir."""

    def _make(**overrides) -> TestClient:
        settings = Settings(upload_dir=tmp_path / "uploads", max_upload_mb=1, **overrides)
        app.state.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def upload_dir(tmp_path):
    return tmp_path / "uploads"
