"""
Tests de Integración - Flujo Completo
Tests que verifican el flujo de extremo a extremo
"""

import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


class TestIntegracionCompleta:
    """Tests de integración completo"""

    def test_import_from_package(self):
        """Importar del paquete instalado"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            import acople
            assert acople.__version__ == "1.0.0"
        finally:
            sys.path.pop(0)

    def test_full_import_chain(self):
        """Importar todo sin errores"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import (
                AGENT_CONFIGS,
                Acople,
                AcopleError,
                AgentConfig,
                AgentNotFoundError,
                BridgeEvent,
                EventType,
                detect_agent,
                detect_all_agents,
                detect_models,
                from_env,
                get_config,
            )
            # Todos deben ser importables
            assert Acople is not None
            assert inspect.isclass(AcopleError)
            assert inspect.isclass(AgentNotFoundError)
            assert inspect.isclass(BridgeEvent)
            assert inspect.isclass(EventType)
            assert inspect.isclass(AgentConfig)
            assert isinstance(AGENT_CONFIGS, dict)
            assert callable(detect_agent)
            assert callable(detect_all_agents)
            assert callable(detect_models)
            assert callable(from_env)
            assert callable(get_config)
        finally:
            sys.path.pop(0)

    @pytest.mark.asyncio
    async def test_bridge_run_returns_async_iterator(self):
        """bridge.run() devuelve async iterator"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import Acople

            with patch('acople.bridge.shutil.which') as mock_which:
                mock_which.return_value = "/fake/claude"

                bridge = Acople("claude")
                result = bridge.run("test prompt")

                # Verificar que es un async iterator
                assert asyncio.iscoroutine(result) or hasattr(result, '__aiter__')
        finally:
            sys.path.pop(0)

    def test_cli_imports(self):
        """CLI imports funcionan"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import cli
            assert hasattr(cli, 'main')
            assert callable(cli.main)
        finally:
            sys.path.pop(0)

    def test_server_imports(self):
        """Server imports funcionan"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import server
            assert hasattr(server, 'app')
        finally:
            sys.path.pop(0)


class TestIntegracionServer:
    """Tests de integración del servidor"""

    def test_server_app_created(self):
        """Server app se crea correctamente"""
        from acople.server import app
        assert app is not None

    def test_server_has_title(self):
        """Server tiene título correcto"""
        from acople.server import app
        assert app.title == "Acople"

    def test_server_all_endpoints(self):
        """Todos los endpoints existen"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)

        endpoints = [
            ("/agents", "GET"),
            ("/agent", "GET"),
            ("/models", "GET"),
            ("/detect", "GET"),
            ("/diagnose", "GET"),
            ("/chat", "POST"),
            ("/chat/simple", "POST"),
            ("/interrupt", "POST"),
            ("/health", "GET"),
        ]

        for path, method in endpoints:
            if method == "GET":
                response = client.get(path)
            else:
                response = client.post(path, json={})

            # No debe ser 404 (puede ser otro error pero no "not found")
            assert response.status_code != 404


class TestIntegracionErrores:
    """Tests de manejo de errores"""

    def test_error_on_no_agent(self):
        """Error claro cuando no hay agente"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import AgentNotFoundError

            with patch('acople.bridge.shutil.which', return_value=None):
                err = AgentNotFoundError("test", suggestion="npm i -g algo")
                err_str = str(err)
                # Verificar que tiene el mensaje
                assert "test" in err_str
        finally:
            sys.path.pop(0)

    def test_validation_error_on_missing_prompt(self):
        """Error de validación cuando falta prompt"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)

        # Sin prompt
        response = client.post(
            "/chat",
            json={},
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 422


class TestIntegracionConfig:
    """Tests de configuración"""

    def test_agent_configs_complete(self):
        """Todos los agentes configurados"""
        from acople import AGENT_CONFIGS

        expected_agents = ["claude", "gemini", "codex", "opencode", "qwen"]

        for agent in expected_agents:
            assert agent in AGENT_CONFIGS

    def test_agent_config_structure(self):
        """Estructura de AgentConfig"""
        from acople import AGENT_CONFIGS

        claude = AGENT_CONFIGS["claude"]

        assert isinstance(claude.bin, str)
        assert isinstance(claude.args, list)
        assert isinstance(claude.prompt_flag, str)
        assert isinstance(claude.stream_format, str)
        assert claude.stream_format in ["json", "plain"]


class TestIntegracionImports:
    """Test imports inside package"""

    def test_bridge_has_logger(self):
        """bridge tiene logger"""
        from acople import bridge
        assert hasattr(bridge, 'logger')

    def test_bridge_logger_level(self):
        """logger está configurado"""

        from acople import bridge
        assert bridge.logger is not None


