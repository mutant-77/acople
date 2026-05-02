"""
Tests de Bridge Core — Cobertura de funciones del módulo bridge.py
Cubre: detect_agent, detect_all_agents, detect_models, from_env, get_config,
       Acople._build_cmd, Acople._resolve_bin, Acople._read_stream
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDetectAgent:
    """Tests de detect_agent"""

    def test_detect_from_env_var(self):
        from acople.bridge import detect_agent

        with patch.dict(os.environ, {"ACOPLE_AGENT": "claude"}):
            with patch("acople.bridge.shutil.which", return_value="/usr/bin/claude"):
                result = detect_agent()
                assert result == "claude"

    def test_detect_from_env_var_not_in_path(self):
        from acople.bridge import detect_agent

        with patch.dict(os.environ, {"ACOPLE_AGENT": "nonexistent"}):
            with patch("acople.bridge.shutil.which", return_value=None):
                result = detect_agent()
                assert result is None

    def test_detect_from_path_scanning(self):
        from acople.bridge import detect_agent

        with patch.dict(os.environ, {}, clear=True):
            def fake_which(name):
                return "/usr/bin/gemini" if name == "gemini" else None

            with patch("acople.bridge.shutil.which", side_effect=fake_which):
                result = detect_agent()
                assert result == "gemini"

    def test_detect_no_agent_found(self):
        from acople.bridge import detect_agent

        with patch.dict(os.environ, {}, clear=True):
            with patch("acople.bridge.shutil.which", return_value=None):
                result = detect_agent()
                assert result is None


class TestDetectAllAgents:
    """Tests de detect_all_agents"""

    def test_returns_dict_with_all_known_agents(self):
        from acople.bridge import detect_all_agents

        with patch("acople.bridge.shutil.which", return_value=None):
            result = detect_all_agents()
            assert "claude" in result
            assert "gemini" in result
            assert "codex" in result
            assert "opencode" in result
            assert "qwen" in result

    def test_marks_found_agents_as_true(self):
        from acople.bridge import detect_all_agents

        def fake_which(name):
            return "/usr/bin/claude" if name == "claude" else None

        with patch("acople.bridge.shutil.which", side_effect=fake_which):
            result = detect_all_agents()
            assert result["claude"] is True
            assert result["gemini"] is False


class TestDetectModels:
    """Tests de detect_models"""

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_empty(self):
        from acople.bridge import detect_models

        result = await detect_models("nonexistent_agent")
        assert result == []

    @pytest.mark.asyncio
    async def test_agent_not_in_path_returns_empty(self):
        from acople.bridge import detect_models

        with patch("acople.bridge.shutil.which", return_value=None):
            result = await detect_models("claude")
            assert result == []

    @pytest.mark.asyncio
    async def test_agent_returns_model_list(self):
        from acople.bridge import detect_models

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"model-a\nmodel-b\n", b""))

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/claude"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                with patch("asyncio.wait_for", return_value=(b"model-a\nmodel-b\n", b"")):
                    result = await detect_models("claude")
                    assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_agent_timeout_returns_empty(self):
        from acople.bridge import detect_models

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/claude"):
            with patch("asyncio.create_subprocess_exec", side_effect=Exception("timeout")):
                result = await detect_models("claude")
                assert result == []


class TestFromEnvAndGetConfig:
    """Tests de from_env y get_config"""

    def test_get_config_known_agent(self):
        from acople.bridge import get_config

        config = get_config("claude")
        assert config.bin == "claude"
        assert config.stream_format == "json"

    def test_get_config_unknown_agent_defaults(self):
        from acople.bridge import get_config

        config = get_config("my_custom_agent")
        assert config.bin == "my_custom_agent"
        assert config.stream_format == "plain"
        assert config.prompt_flag == "-p"

    def test_from_env_creates_bridge(self):
        from acople.bridge import from_env

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/claude"):
            with patch("acople.bridge.detect_agent", return_value="claude"):
                bridge = from_env()
                assert bridge.agent_name == "claude"


class TestBuildCmd:
    """Tests de _build_cmd"""

    def test_build_cmd_with_prompt_flag(self):
        from acople import Acople

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/claude"):
            bridge = Acople("claude")
            cmd = bridge._build_cmd("hello world")

        assert cmd[0] == "/usr/bin/claude"
        assert "--print" in cmd
        assert "hello world" in cmd

    def test_build_cmd_without_prompt_flag(self):
        from acople import Acople

        # opencode has empty prompt_flag
        with patch("acople.bridge.shutil.which", return_value="/usr/bin/opencode"):
            bridge = Acople("opencode")
            cmd = bridge._build_cmd("hello")

        assert "hello" in cmd


class TestResolveBin:
    """Tests de _resolve_bin"""

    def test_resolve_bin_not_found(self):
        from acople import Acople

        with patch("acople.bridge.shutil.which") as mock_which:
            mock_which.side_effect = lambda x: "/usr/bin/claude" if x == "claude" else None
            bridge = Acople("claude")

            with patch("acople.bridge.shutil.which", return_value=None):
                result = bridge._resolve_bin("nonexistent")
                assert result == "nonexistent"

    def test_resolve_bin_found_unix(self):
        from acople import Acople

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/claude"):
            bridge = Acople("claude")

            with patch("os.name", "posix"):
                result = bridge._resolve_bin("claude")
                assert result == "/usr/bin/claude"

    def test_resolve_bin_found_windows(self):
        from acople import Acople

        with patch("acople.bridge.shutil.which", return_value="C:\\Program Files\\claude.cmd"):
            bridge = Acople("claude")

            with patch("os.name", "nt"):
                with patch.dict(os.environ, {"PATHEXT": ".COM;.EXE;.BAT;.CMD"}):
                    result = bridge._resolve_bin("claude")
                    assert result == "C:\\Program Files\\claude.cmd"


class TestAcopleInit:
    """Tests del constructor"""

    def test_init_no_agent_raises(self):
        from acople import Acople, AgentNotFoundError

        with patch("acople.bridge.shutil.which", return_value=None):
            with pytest.raises(AgentNotFoundError):
                Acople()

    def test_init_with_explicit_agent(self):
        from acople import Acople

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/gemini"):
            bridge = Acople("gemini")
            assert bridge.agent == "gemini"

    def test_validate_binary_raises_if_not_in_path(self):
        from acople import Acople, AgentNotFoundError

        with patch("acople.bridge.shutil.which") as mock:
            mock.return_value = "/usr/bin/claude"
            bridge = Acople("claude")

            # Now make which return None to simulate binary disappearing
            mock.return_value = None
            mock.side_effect = None
            with pytest.raises(AgentNotFoundError):
                bridge.validate_binary()


class TestServerEndpointCoverage:
    """Additional server endpoint tests for coverage"""

    def test_agent_endpoint_no_agent(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        with patch("acople.server._DEFAULT_AGENT", None):
            client = TestClient(app)
            response = client.get("/agent")
            assert response.status_code == 503

    def test_models_endpoint_no_agent(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        with patch("acople.server._DEFAULT_AGENT", None):
            client = TestClient(app)
            response = client.get("/models")
            data = response.json()
            assert data["models"] == []

    def test_diagnose_no_agents_installed(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        with patch("acople.server.detect_all_agents", return_value={
            "claude": False, "gemini": False, "codex": False, "opencode": False, "qwen": False,
        }):
            with patch("acople.server._DEFAULT_AGENT", None):
                client = TestClient(app)
                response = client.get("/diagnose")
                data = response.json()
                assert data["status"] == "no_agent"
                assert len(data["issues"]) > 0
                assert len(data["solutions"]) > 0

    def test_chat_validation_errors(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)

        # Empty prompt
        response = client.post("/chat", json={"prompt": ""})
        assert response.status_code == 422

        # Bad agent name
        response = client.post("/chat", json={"prompt": "hi", "agent": "bad;agent"})
        assert response.status_code == 422

    def test_chat_simple_empty_prompt(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        client = TestClient(app)
        response = client.post("/chat/simple", json={"prompt": "   "})
        assert response.status_code == 422
