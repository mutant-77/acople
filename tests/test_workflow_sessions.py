"""
Tests del workflow unificado: memoria stateful/stateless y colapso de system.

Mockean `acople.server.Acople` para NO spawnear agentes reales: capturan el
prompt que recibiría el agente y verifican el uso (o no) del SessionManager.
"""

from unittest.mock import MagicMock, patch

from acople import BridgeEvent, EventType


def _make_fake_acople(stream_format: str, capture: dict):
    """Devuelve una clase que reemplaza a Acople y captura el prompt recibido."""

    class _Fake:
        def __init__(self, agent_name):
            self.agent_name = agent_name
            self.config = type("Cfg", (), {"stream_format": stream_format})()

        async def run(self, prompt, cwd=None, on_start=None):
            capture["prompt"] = prompt
            capture["cwd"] = cwd
            yield BridgeEvent(EventType.TOKEN, {"text": '{"ok": true}'})
            yield BridgeEvent(EventType.DONE, {})

    return _Fake


async def _drain(gen):
    events = []
    async for ev in gen:
        events.append(ev)
    return events


class TestStatelessMemory:
    """stateful=False (clientes OpenAI-compat): sin memoria salvo session_id."""

    async def test_no_session_manager_use_when_stateless(self):
        """Sin session_id y stateful=False, NO se toca el SessionManager."""
        capture = {}
        fake = _make_fake_acople("opencode-json", capture)
        sm = MagicMock()
        with patch("acople.server.Acople", fake), patch("acople.server._session_manager", sm):
            from acople.server import _unified_chat_workflow

            msgs = [
                {"role": "system", "content": "Esquema: {a:int}. Responde SOLO JSON."},
                {"role": "user", "content": "Tema: gatos"},
            ]
            await _drain(_unified_chat_workflow(
                messages=msgs, agent_name="opencode", stateful=False,
            ))

        sm.get_or_create.assert_not_called()
        sm.compile.assert_not_called()
        sm.add_message.assert_not_called()

    async def test_system_reaches_non_json_agent_stateless(self):
        """El system (instrucciones + esquema) llega a opencode aunque sea stateless."""
        capture = {}
        fake = _make_fake_acople("opencode-json", capture)
        with patch("acople.server.Acople", fake), patch("acople.server._session_manager", None):
            from acople.server import _unified_chat_workflow

            msgs = [
                {"role": "system", "content": "Esquema: {a:int}. Responde SOLO JSON."},
                {"role": "user", "content": "Tema: gatos"},
            ]
            await _drain(_unified_chat_workflow(
                messages=msgs, agent_name="opencode", stateful=False,
            ))

        assert "Esquema:" in capture["prompt"]
        assert "Tema: gatos" in capture["prompt"]

    async def test_explicit_session_id_enables_memory_even_if_stateless(self, tmp_path):
        """Con X-Session-ID (session_id explícito), sí hay memoria aunque stateful=False."""
        capture = {}
        fake = _make_fake_acople("opencode-json", capture)
        from acople.session import SessionManager

        sm = SessionManager(tmp_path / "s.db")
        with patch("acople.server.Acople", fake), patch("acople.server._session_manager", sm):
            from acople.server import _unified_chat_workflow

            msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hola"}]
            await _drain(_unified_chat_workflow(
                messages=msgs, agent_name="opencode", session_id="sess-1", stateful=False,
            ))

        # La respuesta del assistant se persistió en la sesión indicada.
        prompt2 = sm.compile("sess-1", enable_fts=False)
        assert "ok" in prompt2


class TestStatefulMemory:
    """stateful=True (clientes nativos /chat, la UI): memoria por carpeta."""

    async def test_uses_session_manager_and_persists(self, tmp_path):
        capture = {}
        fake = _make_fake_acople("opencode-json", capture)
        from acople.session import SessionManager

        sm = SessionManager(tmp_path / "s.db")
        with patch("acople.server.Acople", fake), patch("acople.server._session_manager", sm):
            from acople.server import _unified_chat_workflow

            msgs = [{"role": "user", "content": "primer turno"}]
            await _drain(_unified_chat_workflow(
                messages=msgs, agent_name="opencode", session_id="ui-1", stateful=True,
            ))

        prompt2 = sm.compile("ui-1", enable_fts=False)
        assert "primer turno" in prompt2  # se sincronizó el user
        assert "ok" in prompt2            # se persistió el assistant


class TestSystemCollapse:
    """Varios system messages se fusionan en uno (no se pierde la directiva JSON)."""

    async def test_multiple_systems_reach_json_agent(self):
        """claude (stream_format='json') recibe AMBOS systems fusionados."""
        capture = {}
        fake = _make_fake_acople("json", capture)
        with patch("acople.server.Acople", fake), patch("acople.server._session_manager", None):
            from acople.server import _unified_chat_workflow

            msgs = [
                {"role": "system", "content": "DIRECTIVA JSON"},
                {"role": "system", "content": "ESQUEMA CLIENTE"},
                {"role": "user", "content": "hola"},
            ]
            await _drain(_unified_chat_workflow(
                messages=msgs, agent_name="claude", stateful=False,
            ))

        assert "DIRECTIVA JSON" in capture["prompt"]
        assert "ESQUEMA CLIENTE" in capture["prompt"]
        assert "hola" in capture["prompt"]

    async def test_multiple_systems_survive_session_compile(self, tmp_path):
        """Con memoria, el compile conserva ambos systems fusionados (no solo el último)."""
        capture = {}
        fake = _make_fake_acople("json", capture)
        from acople.session import SessionManager

        sm = SessionManager(tmp_path / "s.db")
        with patch("acople.server.Acople", fake), patch("acople.server._session_manager", sm):
            from acople.server import _unified_chat_workflow

            msgs = [
                {"role": "system", "content": "DIRECTIVA JSON"},
                {"role": "system", "content": "ESQUEMA CLIENTE"},
                {"role": "user", "content": "hola"},
            ]
            await _drain(_unified_chat_workflow(
                messages=msgs, agent_name="claude", session_id="c-1", stateful=True,
            ))

        # claude (json) recibe el compiled_prompt; ambos systems deben estar.
        assert "DIRECTIVA JSON" in capture["prompt"]
        assert "ESQUEMA CLIENTE" in capture["prompt"]
