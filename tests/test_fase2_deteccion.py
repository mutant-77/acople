"""
Fase 2: Detección Automática Tests
Tests para detect_all_agents, detect_models, from_env, /agents, /models, /detect
"""

import sys
from pathlib import Path

import pytest


class TestFase2Deteccion:
    """Fase 2: Detección automática"""

    def test_detect_all_agents_returns_dict(self):
        """2.1 detect_all_agents devuelve dict"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import detect_all_agents
            result = detect_all_agents()
            assert isinstance(result, dict)
        finally:
            sys.path.pop(0)

    def test_detect_all_agents_contains_all_known(self):
        """2.1 detecta todos los agentes conocidos"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import detect_all_agents
            result = detect_all_agents()
            expected = ["claude", "gemini", "codex", "opencode", "qwen"]
            for agent in expected:
                assert agent in result, f"{agent} debe estar en resultado"
            assert all(isinstance(v, bool) for v in result.values())
        finally:
            sys.path.pop(0)

    def test_detect_agent_priority(self):
        """2.1 prioridad correcta"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import detect_agent, detect_all_agents
            # Si hay algún agente, detect_agent debe retourner uno válido
            all_agents = detect_all_agents()
            detected = detect_agent()
            if any(all_agents.values()):
                assert detected in all_agents
                assert all_agents[detected]
        finally:
            sys.path.pop(0)

    @pytest.mark.asyncio
    async def test_detect_models_returns_list(self):
        """2.2 detect_models devuelve list"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import detect_models
            result = await detect_models("nonexistent")
            assert isinstance(result, list)
        finally:
            sys.path.pop(0)

    def test_from_env_creates_bridge(self):
        """2.5 from_env crea Acople"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            # Sin variable, usa auto-detect
            import os
            old_val = os.environ.get("ACOPLE_AGENT")
            if "ACOPLE_AGENT" in os.environ:
                del os.environ["ACOPLE_AGENT"]

            try:
                from acople import from_env
                bridge = from_env()
                assert bridge is not None
                assert hasattr(bridge, "agent")
            finally:
                if old_val:
                    os.environ["ACOPLE_AGENT"] = old_val
        finally:
            sys.path.pop(0)

    def test_get_config_known_agent(self):
        """get_config funciona para agente conocido"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import get_config
            cfg = get_config("claude")
            assert cfg.bin == "claude"
            assert cfg.stream_format == "json"
        finally:
            sys.path.pop(0)

    def test_get_config_unknown_agent(self):
        """get_config crea config genérica para agente desconocido"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import get_config
            cfg = get_config("mi-agente-desconocido")
            assert cfg.bin == "mi-agente-desconocido"
            assert cfg.stream_format == "plain"
            assert cfg.prompt_flag == "-p"
        finally:
            sys.path.pop(0)


class TestFase2ServerEndpoints:
    """Tests de endpoints del servidor"""

    def test_agents_endpoint(self):
        """2.3 GET /agents"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)
        response = client.get("/agents")

        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert isinstance(data["agents"], dict)

    def test_detect_endpoint(self):
        """2.4 GET /detect"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)
        response = client.get("/detect")

        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert "server" in data

    def test_health_endpoint(self):
        """Health check"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data

    def test_models_endpoint_async(self):
        """2.3 GET /models requiere async"""
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)
        response = client.get("/models")

        assert response.status_code == 200
        data = response.json()
        assert "models" in data
