"""
Fase 6: Extras Tests
Tests para Docker, custom agents, sessions, config file
"""

import sys
from pathlib import Path
from unittest.mock import patch


class TestFase6Docker:
    """Fase 6: Docker support"""

    def test_dockerfile_exists(self):
        """6.4 Dockerfile existe"""
        root = Path(__file__).parent.parent
        assert (root / "Dockerfile").exists()

    def test_dockerfile_has_python(self):
        """6.4 Dockerfile usa Python"""
        root = Path(__file__).parent.parent
        content = (root / "Dockerfile").read_text()

        assert "FROM python" in content

    def test_dockerfile_copies_acople(self):
        """6.4 Dockerfile copia el paquete"""
        root = Path(__file__).parent.parent
        content = (root / "Dockerfile").read_text()

        assert "COPY acople" in content

    def test_dockerfile_exposes_port(self):
        """6.4 Expone puerto"""
        root = Path(__file__).parent.parent
        content = (root / "Dockerfile").read_text()

        assert "EXPOSE" in content

    def test_dockerfile_installs_deps(self):
        """6.4 Instal a dependencies"""
        root = Path(__file__).parent.parent
        content = (root / "Dockerfile").read_text()

        assert "pip install" in content
        # Debe tener las deps del server
        assert "fastapi" in content or "uvicorn" in content


class TestFase6CustomAgents:
    """6.1 Custom agents"""

    def test_able_to_register_custom_agent(self):
        """6.1 Se puede usar agente custom con get_config"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import get_config

            # get_config para agente desconocido crea config genérica
            cfg = get_config("mi-agente-custom")
            assert cfg.bin == "mi-agente-custom"
            assert cfg.stream_format == "plain"
        finally:
            sys.path.pop(0)

    def test_agent_configs_mutable(self):
        """6.1 AGENT_CONFIGS puede extenderse"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import AGENT_CONFIGS, AgentConfig

            # Agregar custom
            AGENT_CONFIGS["custom"] = AgentConfig(
                bin="custom",
                args=["--flag"],
                prompt_flag="-p",
                stream_format="plain"
            )

            assert "custom" in AGENT_CONFIGS
            assert AGENT_CONFIGS["custom"].bin == "custom"
        finally:
            sys.path.pop(0)


class TestFase6BridgeProperties:
    """Tests de propiedades del Bridge"""

    def test_bridge_has_agent_property(self):
        """Bridge tiene propiedad .agent"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import Acople

            with patch('acople.bridge.shutil.which') as mock:
                mock.return_value = "/fake/bin/claude"
                bridge = Acople("claude")

                assert hasattr(bridge, "agent")
                assert bridge.agent == "claude"
        finally:
            sys.path.pop(0)

    def test_bridge_has_interrupt(self):
        """Bridge tiene método interrupt()"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import Acople

            bridge = Acople.__new__(Acople)
            bridge._process = None

            assert hasattr(bridge, "interrupt")
            assert callable(bridge.interrupt)
        finally:
            sys.path.pop(0)

    def test_bridge_has_run(self):
        """Bridge tiene método run()"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import Acople

            assert hasattr(Acople, "run")
        finally:
            sys.path.pop(0)


class TestFase6EventTypes:
    """Tests de todos los tipos de eventos"""

    def test_all_event_types_exist(self):
        """Todos los tipos definidos"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import EventType

            assert hasattr(EventType, "TOKEN")
            assert hasattr(EventType, "TOOL_USE")
            assert hasattr(EventType, "TOOL_RESULT")
            assert hasattr(EventType, "DONE")
            assert hasattr(EventType, "ERROR")

            # Verificar valores
            assert EventType.TOKEN == "token"
            assert EventType.TOOL_USE == "tool_use"
            assert EventType.TOOL_RESULT == "tool_result"
            assert EventType.DONE == "done"
            assert EventType.ERROR == "error"
        finally:
            sys.path.pop(0)

    def test_bridge_event_all_types(self):
        """BridgeEvent funciona con todos los tipos"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import BridgeEvent, EventType

            for event_type in EventType:
                event = BridgeEvent(event_type, {"data": "test"})
                assert event.type == event_type
        finally:
            sys.path.pop(0)


class TestFase6Streaming:
    """Tests de streaming"""

    def test_bridge_event_to_sse(self):
        """to_sse() genera formato válido"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import BridgeEvent, EventType

            event = BridgeEvent(EventType.TOKEN, {"text": "hola"})
            sse = event.to_sse()

            # Formato SSE válido
            assert sse.startswith("data: ")
            assert sse.endswith("\n\n")

            # Es JSON válido después de "data: "
            import json
            data_str = sse[6:-2]  # Quitar "data: " y "\n\n"
            parsed = json.loads(data_str)
            assert parsed["type"] == "token"
            assert parsed["text"] == "hola"
        finally:
            sys.path.pop(0)

    def test_sse_format_done(self):
        """DONE event"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import BridgeEvent, EventType

            event = BridgeEvent(EventType.DONE, {})
            sse = event.to_sse()

            assert "data:" in sse
            assert '"type": "done"' in sse
        finally:
            sys.path.pop(0)

    def test_sse_format_tool_use(self):
        """TOOL_USE event"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import BridgeEvent, EventType

            event = BridgeEvent(EventType.TOOL_USE, {"tool": "Read", "input": {"file": "test.py"}})
            sse = event.to_sse()

            assert "data:" in sse
            assert "tool" in sse
            assert "Read" in sse
        finally:
            sys.path.pop(0)
