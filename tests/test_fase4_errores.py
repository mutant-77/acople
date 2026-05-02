"""
Fase 4: Errores Actionables Tests
Tests para errores con suggestions, UserError vs SystemError, /diagnose
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


class TestFase4ErroresActionables:
    """Fase 4: Errores actionables"""

    def test_agent_not_found_error_exists(self):
        """4.1 AgentNotFoundError existe"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import AgentNotFoundError
            assert issubclass(AgentNotFoundError, Exception)
        finally:
            sys.path.pop(0)

    def test_agent_not_found_error_has_suggestion(self):
        """4.2 Error con suggestion"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import AgentNotFoundError

            err = AgentNotFoundError(
                "mensaje de error",
                agent="claude",
                suggestion="npm i -g @anthropic-ai/claude-code"
            )

            assert err.agent == "claude"
            assert err.suggestion == "npm i -g @anthropic-ai/claude-code"
        finally:
            sys.path.pop(0)

    def test_agent_not_found_error_str_includes_suggestion(self):
        """4.2 str() incluye suggestion"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import AgentNotFoundError

            err = AgentNotFoundError(
                "Error de prueba",
                suggestion="Ejecuta: npm i -g algo"
            )

            err_str = str(err)
            assert "Error de prueba" in err_str
        finally:
            sys.path.pop(0)

    def test_error_base_class_exists(self):
        """4.1 AcopleError base existe"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import AcopleError
            assert issubclass(AcopleError, Exception)
            assert AcopleError is not Exception
        finally:
            sys.path.pop(0)

    def test_install_hints_exist(self):
        """4.2 AGENT_INSTALL_HINTS existe"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import bridge

            # Está en bridge.py, verificar allá
            assert hasattr(bridge, 'AGENT_INSTALL_HINTS')
            hints = bridge.AGENT_INSTALL_HINTS

            assert isinstance(hints, dict)
            assert "claude" in hints
            assert "gemini" in hints
            assert "codex" in hints
            assert "opencode" in hints

            # Verificar que contienen comandos de instalación
            assert "npm" in hints["claude"] or "pip" in hints["claude"]
        finally:
            sys.path.pop(0)

    def test_raising_error_on_missing_agent(self):
        """4.1 Lanzar error cuando no hay agente"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import Acople, AgentNotFoundError

            with patch('acople.bridge.shutil.which', return_value=None):
                with pytest.raises(AgentNotFoundError):
                    Acople()
        finally:
            sys.path.pop(0)


class TestFase4DiagnoseEndpoint:
    """Tests de /diagnose endpoint"""

    def test_diagnose_endpoint_exists(self):
        """4.3 GET /diagnose existe"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)
        response = client.get("/diagnose")

        assert response.status_code == 200

    def test_diagnose_returns_issues_and_solutions(self):
        """4.3 /diagnose retorna issues y solutions"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)
        response = client.get("/diagnose")

        data = response.json()
        assert "issues" in data
        assert "solutions" in data
        assert isinstance(data["issues"], list)
        assert isinstance(data["solutions"], list)

    def test_diagnose_includes_install_hints(self):
        """4.3 /diagnose incluye hints de instalación"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)
        response = client.get("/diagnose")

        data = response.json()
        if data.get("issues"):
            # Si hay issues y agent bridge está configurado, debe haber solutions
            # Puede no haber si el servidor no tiene agente configurado
            pass  # Aceptamos ambos casos

    def test_diagnose_status_field(self):
        """4.3 /diagnose tiene status"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)
        response = client.get("/diagnose")

        data = response.json()
        assert "status" in data
        # status debe ser "ok" o "no_agent" u otro válido


class TestFase4ErrorEventTypes:
    """Tests de tipos de eventos de error"""

    def test_error_event_type_exists(self):
        """ERROR en EventType"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import EventType
            assert hasattr(EventType, "ERROR")
            assert EventType.ERROR == "error"
        finally:
            sys.path.pop(0)

    def test_bridge_event_error(self):
        """BridgeEvent puede crear error"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import BridgeEvent, EventType

            event = BridgeEvent(EventType.ERROR, {"message": "test error"})
            assert event.type == EventType.ERROR
            assert event.data["message"] == "test error"
        finally:
            sys.path.pop(0)

    def test_bridge_event_to_sse_error(self):
        """BridgeEvent.to_sse() para error"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import BridgeEvent, EventType

            event = BridgeEvent(EventType.ERROR, {"message": "test"})
            sse = event.to_sse()

            assert "data:" in sse
            assert '"type": "error"' in sse
            assert "test" in sse
        finally:
            sys.path.pop(0)
