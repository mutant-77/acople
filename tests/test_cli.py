"""
Tests de CLI — Cobertura de funciones del módulo cli.py
Cubre: main(), print_usage(), cmd_run(), cmd_doctor(), cmd_agents(), cmd_detect()
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMainDispatch:
    """Tests del dispatcher main()"""

    def test_main_no_args_prints_usage_and_exits(self):
        from acople.cli import main

        with patch.object(sys, "argv", ["acople"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_help_flag(self, capsys):
        from acople.cli import main

        with patch.object(sys, "argv", ["acople", "--help"]):
            main()

        captured = capsys.readouterr()
        assert "Usage" in captured.out

    def test_main_h_flag(self, capsys):
        from acople.cli import main

        with patch.object(sys, "argv", ["acople", "-h"]):
            main()

        captured = capsys.readouterr()
        assert "Usage" in captured.out

    def test_main_unknown_command(self):
        from acople.cli import main

        with patch.object(sys, "argv", ["acople", "foobar"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_agents_command(self, capsys):
        from acople.cli import main

        with patch.object(sys, "argv", ["acople", "agents"]):
            main()

        captured = capsys.readouterr()
        assert "Agentes disponibles" in captured.out

    def test_main_run_dispatches(self):
        from acople.cli import main

        with patch.object(sys, "argv", ["acople", "run", "hello"]):
            with patch("acople.cli.cmd_run", new_callable=AsyncMock):
                with patch("asyncio.run") as mock_asyncio_run:
                    main()
                    mock_asyncio_run.assert_called_once()

    def test_main_doctor_dispatches(self):
        from acople.cli import main

        with patch.object(sys, "argv", ["acople", "doctor"]):
            with patch("acople.cli.cmd_doctor", new_callable=AsyncMock):
                with patch("asyncio.run") as mock_asyncio_run:
                    main()
                    mock_asyncio_run.assert_called_once()

    def test_main_detect_dispatches(self):
        from acople.cli import main

        with patch.object(sys, "argv", ["acople", "detect"]):
            with patch("acople.cli.cmd_detect", new_callable=AsyncMock):
                with patch("asyncio.run") as mock_asyncio_run:
                    main()
                    mock_asyncio_run.assert_called_once()


class TestPrintUsage:
    """Tests de print_usage"""

    def test_print_usage_contains_commands(self, capsys):
        from acople.cli import print_usage

        print_usage()
        captured = capsys.readouterr()

        assert "run" in captured.out
        assert "doctor" in captured.out
        assert "agents" in captured.out
        assert "detect" in captured.out

    def test_print_usage_contains_examples(self, capsys):
        from acople.cli import print_usage

        print_usage()
        captured = capsys.readouterr()

        assert "Examples" in captured.out


class TestCmdRun:
    """Tests de cmd_run"""

    @pytest.mark.asyncio
    async def test_cmd_run_no_prompt_exits(self):
        from acople.cli import cmd_run

        with patch.object(sys, "argv", ["acople", "run"]):
            with pytest.raises(SystemExit) as exc_info:
                await cmd_run()
            assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_cmd_run_agent_not_found(self):
        from acople.cli import cmd_run

        with patch.object(sys, "argv", ["acople", "run", "hello"]):
            with patch("acople.bridge.shutil.which", return_value=None):
                with pytest.raises(SystemExit) as exc_info:
                    await cmd_run()
                assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_cmd_run_parses_agent_flag(self):
        from acople import AgentNotFoundError
        from acople.cli import cmd_run

        with patch.object(sys, "argv", ["acople", "run", "--agent", "gemini", "hello world"]):
            with patch("acople.cli.Acople") as mock_bridge:
                mock_bridge.side_effect = AgentNotFoundError("not found")
                with pytest.raises(SystemExit):
                    await cmd_run()
                mock_bridge.assert_called_with("gemini")

    @pytest.mark.asyncio
    async def test_cmd_run_parses_cwd_flag(self):
        from acople import BridgeEvent, EventType
        from acople.cli import cmd_run

        async def fake_run(prompt, cwd=None):
            yield BridgeEvent(EventType.DONE, {})

        with patch.object(sys, "argv", ["acople", "run", "--cwd", "/tmp", "hello"]):
            with patch("acople.cli.Acople") as mock_bridge:
                instance = MagicMock()
                instance.run = fake_run
                mock_bridge.return_value = instance
                await cmd_run()

    @pytest.mark.asyncio
    async def test_cmd_run_streams_tokens(self, capsys):
        from acople import BridgeEvent, EventType
        from acople.cli import cmd_run

        async def fake_run(prompt, cwd=None):
            yield BridgeEvent(EventType.TOKEN, {"text": "Hello "})
            yield BridgeEvent(EventType.TOKEN, {"text": "World"})
            yield BridgeEvent(EventType.DONE, {})

        with patch.object(sys, "argv", ["acople", "run", "test"]):
            with patch("acople.cli.Acople") as mock_bridge:
                instance = MagicMock()
                instance.run = fake_run
                mock_bridge.return_value = instance
                await cmd_run()

        captured = capsys.readouterr()
        assert "Hello " in captured.out
        assert "World" in captured.out
        assert "[OK]" in captured.out

    @pytest.mark.asyncio
    async def test_cmd_run_prints_errors(self, capsys):
        from acople import BridgeEvent, EventType
        from acople.cli import cmd_run

        async def fake_run(prompt, cwd=None):
            yield BridgeEvent(EventType.ERROR, {"message": "something failed"})

        with patch.object(sys, "argv", ["acople", "run", "test"]):
            with patch("acople.cli.Acople") as mock_bridge:
                instance = MagicMock()
                instance.run = fake_run
                mock_bridge.return_value = instance
                await cmd_run()

        captured = capsys.readouterr()
        assert "something failed" in captured.out


class TestCmdDoctor:
    """Tests de cmd_doctor"""

    @pytest.mark.asyncio
    async def test_doctor_prints_header(self, capsys):
        from acople.cli import cmd_doctor

        with patch("acople.bridge.shutil.which", return_value=None):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_instance.get = AsyncMock(side_effect=Exception("no server"))
                mock_client.return_value = mock_instance
                await cmd_doctor()

        captured = capsys.readouterr()
        assert "Acople Doctor" in captured.out
        assert "Python" in captured.out

    @pytest.mark.asyncio
    async def test_doctor_shows_no_agents(self, capsys):
        from acople.cli import cmd_doctor

        with patch("acople.bridge.shutil.which", return_value=None):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_instance.get = AsyncMock(side_effect=Exception("no server"))
                mock_client.return_value = mock_instance
                await cmd_doctor()

        captured = capsys.readouterr()
        assert "No instalados" in captured.out or "no se encontro" in captured.out.lower()


class TestCmdAgents:
    """Tests de cmd_agents"""

    def test_agents_lists_all(self, capsys):
        from acople.cli import cmd_agents

        cmd_agents()

        captured = capsys.readouterr()
        assert "Agentes disponibles" in captured.out
        assert "claude" in captured.out
        assert "gemini" in captured.out


class TestCmdDetect:
    """Tests de cmd_detect"""

    @pytest.mark.asyncio
    async def test_detect_prints_header(self, capsys):
        from acople.cli import cmd_detect

        with patch("acople.bridge.shutil.which", return_value=None):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_instance.get = AsyncMock(side_effect=Exception("no server"))
                mock_client.return_value = mock_instance
                await cmd_detect()

        captured = capsys.readouterr()
        assert "Detectando setup completo" in captured.out
