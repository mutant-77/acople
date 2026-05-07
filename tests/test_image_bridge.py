"""Tests para acople.image_bridge — generación de imágenes con gpt-image-1."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acople.bridge import AcopleError, EventType
from acople.image_bridge import ImageBridge, ImageConfig


class TestImageBridgeInit:
    """Tests de inicialización."""

    def test_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENAI_API_KEY", None)
            with pytest.raises(AcopleError, match="OPENAI_API_KEY"):
                ImageBridge()

    def test_accepts_explicit_key(self):
        bridge = ImageBridge(api_key="test-key-123")
        assert bridge.api_key == "test-key-123"

    def test_reads_from_env(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "env-key-456"}):
            bridge = ImageBridge()
            assert bridge.api_key == "env-key-456"


class TestImageBridgeGenerate:
    """Tests de generación."""

    @pytest.fixture
    def bridge(self):
        return ImageBridge(api_key="test-key")

    @pytest.fixture
    def mock_response(self):
        """Mock de respuesta exitosa de OpenAI."""
        return {
            "data": [
                {
                    "b64_json": "iVBORw0KGgoAAAANSUhEUg==",
                    "revised_prompt": "A test image",
                }
            ]
        }

    async def test_generate_success(self, bridge, mock_response):
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("acople.image_bridge.httpx.AsyncClient", return_value=mock_client):
            results = await bridge.generate("a cat")

        assert len(results) == 1
        assert results[0].b64_data == "iVBORw0KGgoAAAANSUhEUg=="
        assert results[0].format == "png"
        assert results[0].revised_prompt == "A test image"

    async def test_generate_with_config(self, bridge, mock_response):
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        config = ImageConfig(size="1024x1024", quality="high", n=1, output_format="webp")

        with patch("acople.image_bridge.httpx.AsyncClient", return_value=mock_client):
            results = await bridge.generate("a dog", config)

        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["size"] == "1024x1024"
        assert payload["quality"] == "high"
        assert payload["output_format"] == "webp"
        assert payload["model"] == "gpt-image-1"

    async def test_generate_api_error(self, bridge):
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad request"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_resp
        )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("acople.image_bridge.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(AcopleError, match="400"):
                await bridge.generate("test")

    async def test_generate_timeout(self, bridge):
        import httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("acople.image_bridge.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(AcopleError, match="Timeout"):
                await bridge.generate("test")


class TestImageBridgeGenerateStream:
    """Tests de generate_stream (BridgeEvents)."""

    async def test_stream_events(self):
        bridge = ImageBridge(api_key="test-key")
        mock_response = {
            "data": [{"b64_json": "AAAA", "revised_prompt": "test"}]
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        events = []
        with patch("acople.image_bridge.httpx.AsyncClient", return_value=mock_client):
            async for event in bridge.generate_stream("test"):
                events.append(event)

        types = [e.type for e in events]
        assert EventType.SYSTEM in types
        assert EventType.IMAGE in types
        assert EventType.DONE in types

        img_event = [e for e in events if e.type == EventType.IMAGE][0]
        assert img_event.data["b64"] == "AAAA"
        assert img_event.data["format"] == "png"

    async def test_stream_error(self):
        bridge = ImageBridge(api_key="test-key")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        import httpx
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        events = []
        with patch("acople.image_bridge.httpx.AsyncClient", return_value=mock_client):
            async for event in bridge.generate_stream("test"):
                events.append(event)

        types = [e.type for e in events]
        assert EventType.ERROR in types
        assert EventType.DONE in types
