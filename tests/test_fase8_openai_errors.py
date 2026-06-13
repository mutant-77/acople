"""
Fase 8 — Conformidad de errores OpenAI (I9) + paridad AsyncProcessProxy stdin (I10).

Cubre:
  - _openai_error helper (forma I9)
  - AC8.1: ERROR event en streaming → chunk con error.{message,type,param,code} + cierre I2
  - AC8.2: error en no-stream → HTTP 502 body forma I9, sin clave 'detail'
  - AC8.3: /chat nativo — forma de error actual no cambia (I7)
  - AC8.4: AsyncProcessProxy con subprocess real Python: stdin funciona (I10, D8)
  - AC8.5: prompt > 4000 chars fuerza stdin; path funciona via proxy
"""

import asyncio
import json
import sys
from unittest.mock import patch

import pytest

from acople import BridgeEvent, EventType


# ============================================================================
# AC8.0 — helper _openai_error (unit)
# ============================================================================

class TestOpenAIErrorHelper:
    def test_default_shape(self):
        from acople.server import _openai_error
        result = _openai_error("something went wrong")
        assert result == {
            "error": {
                "message": "something went wrong",
                "type": "server_error",
                "param": None,
                "code": None,
            }
        }

    def test_custom_type(self):
        from acople.server import _openai_error
        result = _openai_error("bad input", type_="invalid_request_error")
        assert result["error"]["type"] == "invalid_request_error"
        assert result["error"]["param"] is None

    def test_custom_code(self):
        from acople.server import _openai_error
        result = _openai_error("timed out", code="timeout")
        assert result["error"]["code"] == "timeout"
        assert result["error"]["type"] == "server_error"

    def test_always_has_four_keys(self):
        from acople.server import _openai_error
        err = _openai_error("x")["error"]
        assert set(err.keys()) == {"message", "type", "param", "code"}


# ============================================================================
# AC8.1 — ERROR event en streaming → chunk I9 + cierre I2
# ============================================================================

class TestStreamingErrors:
    """AC8.1: evento ERROR en sse_adapter produce chunk conforme a I9."""

    def _run_stream_with_error(self, error_message: str) -> list[dict]:
        """Helper: dispara un workflow con un solo evento ERROR y devuelve chunks parseados."""
        from fastapi.testclient import TestClient
        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.ERROR, {"message": error_message})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            resp = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            )

        assert resp.status_code == 200
        chunks = []
        for line in resp.text.splitlines():
            if line.startswith("data: ") and "[DONE]" not in line:
                chunks.append(json.loads(line[len("data: "):]))
        return chunks

    def test_error_chunk_has_i9_shape(self):
        chunks = self._run_stream_with_error("agent failed")
        error_chunks = [c for c in chunks if "error" in c]
        assert len(error_chunks) == 1
        err = error_chunks[0]["error"]
        assert err["message"] == "agent failed"
        assert err["type"] == "server_error"
        assert "param" in err
        assert "code" in err

    def test_error_chunk_no_bare_error_string(self):
        """No debe emitirse {'error': 'some string'} ni {'error': {raw dict}}."""
        chunks = self._run_stream_with_error("crash")
        for c in chunks:
            if "error" in c:
                assert isinstance(c["error"], dict), "error debe ser un dict I9"
                assert "message" in c["error"]

    def test_stream_closes_with_done_after_error(self):
        """Después del chunk de error el stream cierra con [DONE] (I2)."""
        from fastapi.testclient import TestClient
        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.ERROR, {"message": "boom"})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            resp = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            )

        assert "[DONE]" in resp.text

    def test_exception_in_adapter_produces_i9(self):
        """Excepción inesperada en el adapter también produce forma I9."""
        from fastapi.testclient import TestClient
        from acople.server import app

        async def broken_workflow(*args, **kwargs):
            raise RuntimeError("internal boom")
            yield  # make it a generator

        with (
            patch("acople.server._unified_chat_workflow", return_value=broken_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            resp = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            )

        assert resp.status_code == 200
        error_data = None
        for line in resp.text.splitlines():
            if line.startswith("data: ") and "[DONE]" not in line:
                parsed = json.loads(line[len("data: "):])
                if "error" in parsed:
                    error_data = parsed["error"]
        assert error_data is not None
        assert isinstance(error_data, dict)
        assert "message" in error_data
        assert "type" in error_data


# ============================================================================
# AC8.2 — error en no-stream → HTTP 502 con body I9
# ============================================================================

class TestNonStreamingErrors:
    """AC8.2: error_message en no-stream → 502 con body forma I9, sin 'detail'."""

    def test_error_returns_502_with_i9_body(self):
        from fastapi.testclient import TestClient
        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.ERROR, {"message": "agent crashed"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
            )

        assert resp.status_code == 502
        body = resp.json()
        assert "detail" not in body, "no debe haber clave 'detail' (sería forma FastAPI, no OpenAI)"
        assert "error" in body
        err = body["error"]
        assert err["message"] == "agent crashed"
        assert err["type"] == "server_error"
        assert "param" in err
        assert "code" in err

    def test_error_no_detail_key(self):
        """Regresión: HTTPException producía {'detail': '...'}, ahora no."""
        from fastapi.testclient import TestClient
        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.ERROR, {"message": "something failed"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
            )

        assert "detail" not in resp.json()


# ============================================================================
# AC8.3 — /chat nativo no cambia (I7)
# ============================================================================

class TestNativeChatEndpointRegressions:
    """AC8.3: los errores del endpoint /chat nativo mantienen su forma actual."""

    def test_chat_empty_prompt_returns_400(self):
        """Prompt vacío → 400 (no-stream nativo, sin cambios de I9)."""
        from fastapi.testclient import TestClient
        from acople.server import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/chat", json={"prompt": ""})
        assert resp.status_code == 400


# ============================================================================
# AC8.4 — AsyncProcessProxy stdin (I10, D8)
# ============================================================================

class TestAsyncProcessProxyStdin:
    """AC8.4: AsyncProcessProxy expone stdin funcional con subprocess real Python."""

    @pytest.mark.asyncio
    async def test_stdin_write_drain_close(self):
        """Prompt enviado por stdin es leído por el proceso; output correcto."""
        import subprocess

        cmd = [sys.executable, "-c", "import sys; print(sys.stdin.read().strip())"]
        raw_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        from acople.bridge import AsyncProcessProxy
        proxy = AsyncProcessProxy(raw_proc)

        proxy.stdin.write(b"hello from proxy\n")
        await proxy.stdin.drain()
        proxy.stdin.close()

        out_bytes = b""
        while True:
            chunk = await asyncio.wait_for(proxy.stdout.read(4096), timeout=5.0)
            if not chunk:
                break
            out_bytes += chunk

        await proxy.wait()
        assert b"hello from proxy" in out_bytes

    @pytest.mark.asyncio
    async def test_stdin_proxy_no_attribute_error(self):
        """Acceder a proxy.stdin no lanza AttributeError."""
        import subprocess

        raw_proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        from acople.bridge import AsyncProcessProxy
        proxy = AsyncProcessProxy(raw_proc)

        # Verificar que todos los métodos existen y son accesibles
        assert hasattr(proxy, "stdin")
        stdin = proxy.stdin
        assert callable(stdin.write)
        assert asyncio.iscoroutinefunction(stdin.drain)
        assert callable(stdin.close)
        assert asyncio.iscoroutinefunction(stdin.wait_closed)

        stdin.close()
        await proxy.wait()

    @pytest.mark.asyncio
    async def test_stdin_large_prompt(self):
        """AC8.5: prompt > 4000 chars pasa correctamente por stdin del proxy."""
        import subprocess

        large_prompt = "x" * 5000
        cmd = [sys.executable, "-c", "import sys; data = sys.stdin.read(); print(len(data))"]
        raw_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        from acople.bridge import AsyncProcessProxy
        proxy = AsyncProcessProxy(raw_proc)

        proxy.stdin.write(large_prompt.encode("utf-8"))
        await proxy.stdin.drain()
        proxy.stdin.close()

        out_bytes = b""
        while True:
            chunk = await asyncio.wait_for(proxy.stdout.read(4096), timeout=5.0)
            if not chunk:
                break
            out_bytes += chunk

        await proxy.wait()
        reported_len = int(out_bytes.strip())
        assert reported_len == 5000
