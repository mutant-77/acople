"""
Fase 2 — Modo Tool-Proxy constreñido: deshabilitar tools nativas + contrato estricto.

Cubre:
  - AgentConfig.no_tools_args en configs de agentes
  - _build_cmd con disable_native_tools=True incluye no_tools_args (AC2.1)
  - _build_cmd sin disable_native_tools es idéntico (AC2.2, I7)
  - _unified_chat_workflow pasa disable_native_tools=True cuando hay tools
  - Agentes sin no_tools_args no cambian su comando
  - TOOL_CATALOG_INSTRUCTIONS endurecido
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAgentConfigNoToolsArgs:
    """AgentConfig.no_tools_args configurado correctamente por agente."""

    def test_claude_has_no_tools_args(self):
        from acople.bridge import AGENT_CONFIGS

        cfg = AGENT_CONFIGS["claude"]
        assert cfg.no_tools_args == ["--tools", ""]

    def test_opencode_no_no_tools_args(self):
        from acople.bridge import AGENT_CONFIGS

        cfg = AGENT_CONFIGS["opencode"]
        assert cfg.no_tools_args == []

    def test_gemini_no_no_tools_args(self):
        from acople.bridge import AGENT_CONFIGS

        cfg = AGENT_CONFIGS["gemini"]
        assert cfg.no_tools_args == []

    def test_codex_no_no_tools_args(self):
        from acople.bridge import AGENT_CONFIGS

        cfg = AGENT_CONFIGS["codex"]
        assert cfg.no_tools_args == []

    def test_kilo_no_no_tools_args(self):
        from acople.bridge import AGENT_CONFIGS

        cfg = AGENT_CONFIGS["kilo"]
        assert cfg.no_tools_args == []

    def test_qwen_no_no_tools_args(self):
        from acople.bridge import AGENT_CONFIGS

        cfg = AGENT_CONFIGS["qwen"]
        assert cfg.no_tools_args == []

    def test_get_config_preserves_no_tools_args(self):
        from acople.bridge import get_config

        cfg = get_config("claude")
        assert cfg.no_tools_args == ["--tools", ""]

    def test_get_config_unknown_defaults_empty(self):
        from acople.bridge import get_config

        cfg = get_config("custom-agent")
        assert cfg.no_tools_args == []


class TestBuildCmdDisableNativeTools:
    """_build_cmd con disable_native_tools."""

    def test_claude_with_disable_includes_no_tools_args(self):
        """AC2.1: disable_native_tools=True para claude añade --tools ''."""
        from acople import Acople

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/claude"):
            bridge = Acople("claude")
            cmd = bridge._build_cmd("hello", disable_native_tools=True)

        assert "--tools" in cmd
        tools_idx = cmd.index("--tools")
        assert tools_idx + 1 < len(cmd)
        assert cmd[tools_idx + 1] == ""

    def test_claude_without_disable_does_not_include_no_tools_args(self):
        """AC2.2: Sin disable, claude no incluye --tools '' (regresión)."""
        from acople import Acople

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/claude"):
            bridge = Acople("claude")
            cmd = bridge._build_cmd("hello")

        assert "--tools" not in cmd

    def test_opencode_ignores_disable_native_tools(self):
        """AC2.1: opencode sin no_tools_args no cambia con disable=True."""
        from acople import Acople

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/opencode"):
            bridge = Acople("opencode")
            cmd_with = bridge._build_cmd("hello", disable_native_tools=True)
            cmd_without = bridge._build_cmd("hello")

        assert cmd_with == cmd_without

    def test_gemini_ignores_disable_native_tools(self):
        """AC2.1: gemini sin no_tools_args no cambia con disable=True."""
        from acople import Acople

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/gemini"):
            bridge = Acople("gemini")
            cmd_with = bridge._build_cmd("hello", disable_native_tools=True)
            cmd_without = bridge._build_cmd("hello")

        assert cmd_with == cmd_without

    def test_disable_preserves_other_args_claude(self):
        """Con disable=True, los args base de claude se preservan."""
        from acople import Acople

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/claude"):
            bridge = Acople("claude")
            cmd = bridge._build_cmd("hello", disable_native_tools=True)

        assert cmd[0] == "/usr/bin/claude"
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--verbose" in cmd
        assert "--no-session-persistence" in cmd


class TestRunDisableNativeTools:
    """Acople.run pasa disable_native_tools a _build_cmd."""

    @pytest.mark.asyncio
    async def test_run_passes_disable_to_build_cmd(self):
        """run(disable_native_tools=True) lo pasa a _build_cmd."""
        from acople import Acople

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/claude"):
            bridge = Acople("claude")

        with (
            patch.object(bridge, "_build_cmd", return_value=["claude", "--print", "hi"]) as mock_build,
            patch.object(bridge, "validate_binary"),
            patch.object(bridge, "_read_stream", return_value=iter([])),
            patch.object(bridge, "_cleanup_process"),
            patch("asyncio.create_subprocess_exec") as mock_proc,
        ):
            mock_proc_instance = MagicMock()
            mock_proc_instance.stdout = AsyncMock()
            mock_proc_instance.stdout.read = AsyncMock(return_value=b"")
            mock_proc_instance.stderr = AsyncMock()
            mock_proc_instance.stderr.read = AsyncMock(return_value=b"")
            mock_proc_instance.returncode = 0
            mock_proc_instance.pid = 12345
            mock_proc_instance.stdin = MagicMock()
            mock_proc.return_value = mock_proc_instance

            collected = []
            async for ev in bridge.run("hello", disable_native_tools=True):
                collected.append(ev)

            mock_build.assert_called_once_with("hello", disable_native_tools=True)

    @pytest.mark.asyncio
    async def test_run_defaults_to_false(self):
        """run() por defecto llama a _build_cmd con disable_native_tools=False."""
        from acople import Acople

        with patch("acople.bridge.shutil.which", return_value="/usr/bin/claude"):
            bridge = Acople("claude")

        with (
            patch.object(bridge, "_build_cmd", return_value=["claude", "--print", "hi"]) as mock_build,
            patch.object(bridge, "validate_binary"),
            patch.object(bridge, "_read_stream", return_value=iter([])),
            patch.object(bridge, "_cleanup_process"),
            patch("asyncio.create_subprocess_exec") as mock_proc,
        ):
            mock_proc_instance = MagicMock()
            mock_proc_instance.stdout = AsyncMock()
            mock_proc_instance.stdout.read = AsyncMock(return_value=b"")
            mock_proc_instance.stderr = AsyncMock()
            mock_proc_instance.stderr.read = AsyncMock(return_value=b"")
            mock_proc_instance.returncode = 0
            mock_proc_instance.pid = 12345
            mock_proc_instance.stdin = MagicMock()
            mock_proc.return_value = mock_proc_instance

            collected = []
            async for ev in bridge.run("hello"):
                collected.append(ev)

            mock_build.assert_called_once_with("hello", disable_native_tools=False)


class TestWorkflowPassesDisableNativeTools:
    """_unified_chat_workflow pasa disable_native_tools=True cuando hay tools."""

    @pytest.mark.asyncio
    async def test_with_tools_passes_disable_true(self):
        """Con tools registradas, disable_native_tools=True."""
        from acople import BridgeEvent, EventType
        from acople.server import _unified_chat_workflow

        tools = [
            {"type": "function", "function": {"name": "search", "description": "", "parameters": {}}}
        ]

        call_kwargs = {}

        async def mock_run(**kwargs):
            nonlocal call_kwargs
            call_kwargs = kwargs
            yield BridgeEvent(EventType.DONE, {})

        with patch("acople.server.Acople") as MockAcople:
            mock_instance = MagicMock()
            mock_instance.agent = "claude"
            from acople import get_config
            mock_instance.config = get_config("claude")
            mock_instance.run = mock_run
            MockAcople.return_value = mock_instance

            collected = []
            async for ev in _unified_chat_workflow(
                messages=[{"role": "user", "content": "hello"}],
                agent_name="claude",
                tools=tools,
                stateful=False,
            ):
                collected.append(ev)

        assert call_kwargs.get("disable_native_tools") is True

    @pytest.mark.asyncio
    async def test_without_tools_passes_disable_false(self):
        """Sin tools, disable_native_tools=False."""
        from acople import BridgeEvent, EventType
        from acople.server import _unified_chat_workflow

        call_kwargs = {}

        async def mock_run(**kwargs):
            nonlocal call_kwargs
            call_kwargs = kwargs
            yield BridgeEvent(EventType.DONE, {})

        with patch("acople.server.Acople") as MockAcople:
            mock_instance = MagicMock()
            mock_instance.agent = "claude"
            from acople import get_config
            mock_instance.config = get_config("claude")
            mock_instance.run = mock_run
            MockAcople.return_value = mock_instance

            collected = []
            async for ev in _unified_chat_workflow(
                messages=[{"role": "user", "content": "hello"}],
                agent_name="claude",
                tools=None,
                stateful=False,
            ):
                collected.append(ev)

        assert call_kwargs.get("disable_native_tools") is False


class TestToolCatalogInstructions:
    """TOOL_CATALOG_INSTRUCTIONS endurecido."""

    def test_instructions_include_strict_directive(self):
        """Las instrucciones endurecidas contienen la directiva de no-ejecutar."""
        from acople.normalize import TOOL_CATALOG_INSTRUCTIONS

        assert "Do NOT execute any tool yourself" in TOOL_CATALOG_INSTRUCTIONS
        assert "Do NOT create files" in TOOL_CATALOG_INSTRUCTIONS
        assert "run commands" in TOOL_CATALOG_INSTRUCTIONS
        assert "only emit the marker" in TOOL_CATALOG_INSTRUCTIONS
