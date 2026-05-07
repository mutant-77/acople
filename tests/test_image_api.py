"""Tests para los endpoints de imagen en acople.server."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """TestClient con OPENAI_API_KEY mockeada."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        from acople.server import app
        with TestClient(app) as c:
            yield c


@pytest.fixture
def mock_image_generate():
    """Mock de ImageBridge.generate."""
    from acople.image_bridge import ImageResult

    results = [ImageResult(b64_data="AAAA", format="png", revised_prompt="test")]

    with patch("acople.server.ImageBridge") as mock_cls:
        instance = MagicMock()
        instance.generate = AsyncMock(return_value=results)
        mock_cls.return_value = instance
        yield mock_cls


class TestImageGenerateEndpoint:
    """Tests para POST /image/generate."""

    def test_generate_success(self, client, mock_image_generate):
        resp = client.post("/image/generate", json={"prompt": "a cat"})
        assert resp.status_code == 200
        data = resp.json()
        assert "images" in data
        assert len(data["images"]) == 1
        assert data["images"][0]["b64"] == "AAAA"
        assert data["model"] == "gpt-image-1"

    def test_generate_empty_prompt(self, client):
        resp = client.post("/image/generate", json={"prompt": ""})
        assert resp.status_code == 422

    def test_generate_invalid_size(self, client):
        resp = client.post("/image/generate", json={"prompt": "test", "size": "999x999"})
        assert resp.status_code == 422

    def test_generate_invalid_quality(self, client):
        resp = client.post("/image/generate", json={"prompt": "test", "quality": "ultra"})
        assert resp.status_code == 422

    def test_generate_invalid_n(self, client):
        resp = client.post("/image/generate", json={"prompt": "test", "n": 99})
        assert resp.status_code == 422

    def test_generate_invalid_output_format(self, client):
        resp = client.post("/image/generate", json={"prompt": "test", "output_format": "gif"})
        assert resp.status_code == 422

    def test_generate_no_openai_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENAI_API_KEY", None)
            from acople.server import app
            with TestClient(app) as c:
                resp = c.post("/image/generate", json={"prompt": "test"})
                assert resp.status_code == 503


class TestImageStreamEndpoint:
    """Tests para POST /image/generate/stream."""

    def test_stream_returns_sse(self, client, mock_image_generate):
        from acople.image_bridge import ImageResult

        async def mock_stream(prompt, config=None):
            from acople.bridge import BridgeEvent, EventType
            yield BridgeEvent(EventType.SYSTEM, {"message": "Generating..."})
            yield BridgeEvent(EventType.IMAGE, {"b64": "AAAA", "format": "png", "index": 0, "total": 1})
            yield BridgeEvent(EventType.DONE, {})

        instance = mock_image_generate.return_value
        instance.generate_stream = mock_stream

        resp = client.post("/image/generate/stream", json={"prompt": "a cat"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")


class TestHealthEndpoint:
    """Tests para el campo image_ready en /health."""

    def test_health_with_openai_key(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert data["image_ready"] is True

    def test_health_without_openai_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENAI_API_KEY", None)
            from acople.server import app
            with TestClient(app) as c:
                resp = c.get("/health")
                data = resp.json()
                assert data["image_ready"] is False
