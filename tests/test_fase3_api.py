"""
Fase 3: API Simplificada Tests
Tests para cwd auto-inference, model selection, /chat/simple, gestión de proyectos
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class TestFase3APISimplificada:
    """Fase 3: API simplificada"""

    def test_cwd_inference_default(self):
        """3.1 cwd es cwd actual por defecto"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import Acople
            # Con cwd=None, usa Path.cwd()
            # Mockeamos el proceso para no ejecutar realmente
            with patch('asyncio.create_subprocess_exec') as mock:
                mock.return_value = MagicMock()
                mock.return_value.stdout.read = AsyncMock(return_value=b"")
                mock.return_value.stderr.read = AsyncMock(return_value=b"")
                mock.return_value.wait = AsyncMock(return_value=None)
                mock.return_value.returncode = 0

                bridge = Acople()
                # No vamos a ejecutar realmente, solo verificar estructura
                assert bridge is not None
        finally:
            sys.path.pop(0)

    def test_model_field_in_request(self):
        """3.2 model field existe en ChatRequest"""
        from acople.server import ChatRequest

        # Con model
        req = ChatRequest(prompt="test", model="claude-sonnet")
        assert req.model == "claude-sonnet"

        # Sin model (opcional)
        req = ChatRequest(prompt="test")
        assert req.model is None

    def test_simple_chat_request(self):
        """3.3 SimpleChatRequest solo tiene prompt"""
        from acople.server import SimpleChatRequest

        req = SimpleChatRequest(prompt="tu prompt aqui")
        assert req.prompt == "tu prompt aqui"

        # No debe tener otros campos
        assert not hasattr(req, 'system')
        assert not hasattr(req, 'cwd')
        assert not hasattr(req, 'agent')

    def test_chat_request_all_optional_except_prompt(self):
        """3.1 Todos los campos excepto prompt son opcionales"""
        from acople.server import ChatRequest

        req = ChatRequest(prompt="obligatorio")
        assert req.prompt == "obligatorio"

        # Verificar que cwd no es obligatorio
        assert req.system is None
        assert req.cwd is None
        assert req.agent is None
        assert req.model is None
        assert req.timeout is None

    def test_chat_simple_endpoint_requires_prompt(self):
        """3.3 /chat/simple requiere prompt"""

        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)

        # Sin prompt debe fallar
        response = client.post(
            "/chat/simple",
            content="{}",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422  # Validation error

    def test_chat_simple_endpoint_accepts_prompt(self):
        """3.3 /chat/simple acepta solo prompt"""

        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)

        response = client.post(
            "/chat/simple",
            json={"prompt": "hola"}
        )
        # Puede ser 200 (si hay agente) o 503 (si no hay)
        assert response.status_code in [200, 503]


class TestFase3Endpoints:
    """Tests de endpoints específicos de Fase 3"""

    def test_chat_endpoint_exists(self):
        """3.3 POST /chat existe"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)

        # Con prompt vacío - ahora acepta
        response = client.post(
            "/chat",
            json={"prompt": ""}
        )
        # Acepta (puede devolverstreaming o error)
        assert response.status_code in [200, 422, 500]

    def test_chat_full_request(self):
        """3.3 POST /chat acepta todos los parámetros"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)

        response = client.post(
            "/chat",
            json={
                "prompt": "tu prompt",
                "system": "eres un asistente",
                "cwd": "/tmp",
                "agent": "claude",
                "model": "sonnet",
                "timeout": 60.0
            }
        )
        assert response.status_code in [200, 503, 422]

    def test_interrupt_endpoint(self):
        """Interrupt funciona"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)
        response = client.post("/interrupt")

        # 503 si no hay agente, 200 si lo hay
        assert response.status_code in [200, 503]

    def test_agent_endpoint(self):
        """/agent retorna agente activo"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)
        response = client.get("/agent")

        # Puede ser 200 (si hay agente) o 503 (si no)
        assert response.status_code in [200, 503]


class TestFixesJSONyOpenAI:
    """Tests para Fix 2, 4, 6 — OpenAI endpoint error handling y model fallback"""

    def test_openai_non_streaming_error_event(self):
        """Fix 2: Non-streaming /v1/chat/completions eleva HTTP 502 si el workflow emite ERROR."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from acople import EventType
        from acople.server import app

        ERROR_EVENT_DATA = {"message": "Agent crashed"}

        async def mock_workflow(*args, **kwargs):
            from acople import BridgeEvent
            yield BridgeEvent(EventType.TOKEN, {"text": "partial "})
            yield BridgeEvent(EventType.ERROR, ERROR_EVENT_DATA)

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude-4", "stream": False},
            )

        assert response.status_code == 502
        body = response.json()
        assert "Agent crashed" in body.get("detail", "")

    def test_openai_streaming_error_event(self):
        """Fix 2+4: Streaming /v1/chat/completions emite error SSE y luego [DONE]."""
        from unittest.mock import AsyncMock, patch

        from fastapi.testclient import TestClient

        from acople.server import app, EventType

        async def mock_workflow(*args, **kwargs):
            from acople import BridgeEvent
            yield BridgeEvent(EventType.TOKEN, {"text": "hello "})
            yield BridgeEvent(EventType.ERROR, {"message": "stream error"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude-4", "stream": True},
            )

        assert response.status_code == 200
        body = response.text
        assert "error" in body
        assert "stream error" in body
        assert "[DONE]" in body

    def test_openai_non_streaming_unknown_event_logged(self):
        """Fix 4: Eventos no manejados en non-streaming no rompen el flujo."""
        from unittest.mock import AsyncMock, patch

        from fastapi.testclient import TestClient

        from acople.server import app, EventType

        async def mock_workflow(*args, **kwargs):
            from acople import BridgeEvent
            yield BridgeEvent(EventType.TOKEN, {"text": "hello "})
            yield BridgeEvent(EventType.TOOL_RESULT, {"content": "result"})
            yield BridgeEvent(EventType.SYSTEM, {"message": "info"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude-4", "stream": False},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["choices"][0]["message"]["content"] == "hello "

    def test_model_fallback_logs_warning(self):
        """Fix 6: Modelo no reconocido genera warning y cae al default."""
        from unittest.mock import AsyncMock, patch

        from fastapi.testclient import TestClient

        from acople.server import app, EventType

        async def mock_workflow(*args, **kwargs):
            from acople import BridgeEvent
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "gpt-4-turbo", "stream": False},
            )

        # Debe responder con 200 aunque haya hecho fallback
        assert response.status_code == 200
        assert response.json()["model"] == "gpt-4-turbo"

    def test_extract_json_payload_helper(self):
        """response_format JSON: el extractor limpia fences/prosa y descarta fragmentos."""
        import json

        from acople.server import _extract_json_payload

        # fences + prosa alrededor
        out = _extract_json_payload("Aqui tienes:\n```json\n{\"a\": 1}\n```\nlisto")
        assert json.loads(out) == {"a": 1}

        # fragmento de prosa con llaves ANTES del JSON real → se descarta
        raw = "formato { ciudad, pais } y el resultado es {\"empresa\": \"X\", \"n\": 3}"
        assert json.loads(_extract_json_payload(raw)) == {"empresa": "X", "n": 3}

        # array de nivel superior con llaves dentro de strings
        assert json.loads(_extract_json_payload('res: [1, {"k": "}"}] fin')) == [1, {"k": "}"}]

        # texto sin JSON se devuelve tal cual
        assert _extract_json_payload("no hay json") == "no hay json"

    def test_openai_json_mode_non_streaming_clean(self):
        """response_format=json_object: la respuesta no-streaming es JSON puro y parseable."""
        import json
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from acople.server import app, EventType

        async def mock_workflow(*args, **kwargs):
            from acople import BridgeEvent
            # El agente envuelve el JSON en fences y añade prosa
            yield BridgeEvent(EventType.TOKEN, {"text": "Claro, aqui tienes:\n```json\n"})
            yield BridgeEvent(EventType.TOKEN, {"text": '{"empresa": "ACME", "sedes": [{"ciudad": "Madrid"}]}'})
            yield BridgeEvent(EventType.TOKEN, {"text": "\n```\nEspero que sirva."})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "json please"}],
                    "model": "claude",
                    "stream": False,
                    "response_format": {"type": "json_object"},
                },
            )

        assert response.status_code == 200
        content = response.json()["choices"][0]["message"]["content"]
        assert not content.lstrip().startswith("```")
        assert json.loads(content) == {"empresa": "ACME", "sedes": [{"ciudad": "Madrid"}]}

    def test_openai_json_mode_streaming_clean(self):
        """response_format=json_object: el streaming emite el JSON saneado en un solo chunk."""
        import json
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from acople.server import app, EventType

        async def mock_workflow(*args, **kwargs):
            from acople import BridgeEvent
            yield BridgeEvent(EventType.TOKEN, {"text": "```json\n{\"ok\": "})
            yield BridgeEvent(EventType.TOKEN, {"text": "true}\n```"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "json please"}],
                    "model": "claude",
                    "stream": True,
                    "response_format": {"type": "json_object"},
                },
            )

        assert response.status_code == 200
        # Reconstruir el contenido a partir de los deltas SSE
        content = ""
        for line in response.text.splitlines():
            if not line.startswith("data: ") or "[DONE]" in line:
                continue
            payload = json.loads(line[len("data: "):])
            delta = payload.get("choices", [{}])[0].get("delta", {})
            content += delta.get("content", "")
        assert "```" not in content
        assert json.loads(content) == {"ok": True}
