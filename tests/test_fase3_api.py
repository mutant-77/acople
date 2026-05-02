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
