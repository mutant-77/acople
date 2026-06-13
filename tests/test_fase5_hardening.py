"""
Fase 5 — Hardening: loop-guard, desacople process_pid, CORS regex, seguridad.
"""

import json
import os
import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# Loop-guard tests
# ============================================================================

class TestToolUseKey:
    """_tool_use_key: normalización para comparación."""

    def test_basic_key(self):
        from acople.server import _tool_use_key
        key = _tool_use_key({"tool": "write", "input": {"path": "x.txt", "content": "hello"}})
        assert key == ("write", '{"content": "hello", "path": "x.txt"}')

    def test_different_tools_different_key(self):
        from acople.server import _tool_use_key
        a = _tool_use_key({"tool": "write", "input": {"path": "x.txt"}})
        b = _tool_use_key({"tool": "read", "input": {"path": "x.txt"}})
        assert a != b

    def test_same_input_different_order(self):
        from acople.server import _tool_use_key
        a = _tool_use_key({"tool": "write", "input": {"content": "hi", "path": "x.txt"}})
        b = _tool_use_key({"tool": "write", "input": {"path": "x.txt", "content": "hi"}})
        assert a == b

    def test_empty_defaults(self):
        from acople.server import _tool_use_key
        key = _tool_use_key({})
        assert key == ("", "{}")

    def test_nested_input(self):
        from acople.server import _tool_use_key
        key = _tool_use_key({"tool": "edit", "input": {"file": "x.py", "old": "foo", "new": "bar"}})
        assert key[0] == "edit"
        assert "foo" in key[1]
        assert "bar" in key[1]


class TestLoopGuardInWorkflow:
    """Loop-guard injection en _unified_chat_workflow."""

    def _make_workflow(self, session_id="sess-1", tools=None):
        """Helper to instantiate the workflow generator with controlled state.

        Note: caller is responsible for setting _session_tool_history
        before calling this if loop-guard testing is needed.
        """
        from acople.server import _unified_chat_workflow
        return _unified_chat_workflow(
            messages=[{"role": "user", "content": "hi"}],
            agent_name="claude",
            session_id=session_id,
            tools=tools or [{"type": "function", "function": {"name": "write"}}],
        )

    async def _drain_and_reject_tool_use(self, workflow):
        """Drain the workflow generator, capturing yields."""
        events = []
        async for ev in workflow:
            events.append(ev)
            # break early if DONE
            if ev.type.value == "done":
                break
        return events

    @patch("acople.server.Acople")
    async def test_loop_detected_stops_immediately(self, MockAcople):
        """
        When the previous turn had tool_use("write", ...) and the current
        turn also emits tool_use("write", ...) with the same input, the
        loop-guard stops the turn immediately — no tool_use is yielded.
        """
        # Mock the agent to emit a tool_use that matches the previous turn
        mock_instance = MagicMock()
        mock_instance.config.stream_format = "plain"
        mock_instance.__aiter__.return_value = AsyncMock()

        from acople.bridge import BridgeEvent, EventType
        mock_tool_event = BridgeEvent(
            EventType.TOOL_USE,
            {"tool": "write", "input": {"path": "x.txt", "content": "hello"}},
        )

        async def mock_run(**kwargs):
            yield mock_tool_event

        mock_instance.run = mock_run
        MockAcople.return_value = mock_instance

        from acople.server import _session_tool_history
        _session_tool_history.clear()
        _session_tool_history["sess-1"] = [
            ("write", '{"content": "hello", "path": "x.txt"}'),
        ]

        events = await self._drain_and_reject_tool_use(
            self._make_workflow()
        )

        # The loop-guard should have yielded DONE, not the tool_use
        tool_uses = [e for e in events if e.type.value == "tool_use"]
        assert len(tool_uses) == 0, "Loop-guard should prevent tool_use from being emitted"

        dones = [e for e in events if e.type.value == "done"]
        assert len(dones) == 1, "Should yield DONE to break the loop"

    @patch("acople.server.Acople")
    async def test_different_tool_not_blocked(self, MockAcople):
        """
        When the current turn emits a DIFFERENT tool, the loop-guard
        lets it through.
        """
        from acople.bridge import BridgeEvent, EventType

        mock_instance = MagicMock()
        mock_instance.config.stream_format = "plain"

        async def mock_run(**kwargs):
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "read", "input": {"path": "/etc"}})

        mock_instance.run = mock_run
        MockAcople.return_value = mock_instance

        from acople.server import _session_tool_history
        _session_tool_history.clear()
        _session_tool_history["sess-1"] = [
            ("write", '{"content": "hello", "path": "x.txt"}'),
        ]

        events = await self._drain_and_reject_tool_use(
            self._make_workflow(tools=[
                {"type": "function", "function": {"name": "write"}},
                {"type": "function", "function": {"name": "read"}},
            ])
        )

        tool_uses = [e for e in events if e.type.value == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0].data.get("tool") == "read"

    @patch("acople.server.Acople")
    async def test_second_tool_matching_prev_first_not_blocked(self, MockAcople):
        """Solo el PRIMER tool del turno se compara con prev_first: un turno
        multi-tool legítimo con orden distinto ([A,B] → [B,A]) no se corta."""
        from acople.bridge import BridgeEvent, EventType

        mock_instance = MagicMock()
        mock_instance.config.stream_format = "plain"

        async def mock_run(**kwargs):
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "read", "input": {"path": "/etc"}})
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "write", "input": {"path": "x.txt", "content": "hello"}})
            yield BridgeEvent(EventType.DONE, {})

        mock_instance.run = mock_run
        MockAcople.return_value = mock_instance

        from acople.server import _session_tool_history
        _session_tool_history.clear()
        _session_tool_history["sess-1"] = [
            ("write", '{"content": "hello", "path": "x.txt"}'),
        ]

        events = await self._drain_and_reject_tool_use(
            self._make_workflow(tools=[
                {"type": "function", "function": {"name": "write"}},
                {"type": "function", "function": {"name": "read"}},
            ])
        )

        tool_uses = [e for e in events if e.type.value == "tool_use"]
        assert [t.data["tool"] for t in tool_uses] == ["read", "write"]

    @patch("acople.server.Acople")
    async def test_guard_entry_consumed_after_trigger(self, MockAcople):
        """Al dispararse, la entrada se consume (one-shot): el turno siguiente
        con el mismo tool vuelve a pasar — no hay bloqueo perpetuo."""
        from acople.bridge import BridgeEvent, EventType

        mock_instance = MagicMock()
        mock_instance.config.stream_format = "plain"

        async def mock_run(**kwargs):
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "write", "input": {"path": "x.txt", "content": "hello"}})
            yield BridgeEvent(EventType.DONE, {})

        mock_instance.run = mock_run
        MockAcople.return_value = mock_instance

        from acople.server import _session_tool_history
        _session_tool_history.clear()
        _session_tool_history["sess-1"] = [
            ("write", '{"content": "hello", "path": "x.txt"}'),
        ]

        # Turno 1: guard se dispara y consume la entrada.
        events = await self._drain_and_reject_tool_use(self._make_workflow())
        assert not [e for e in events if e.type.value == "tool_use"]
        assert "sess-1" not in _session_tool_history

        # Turno 2: misma tool → ahora pasa (repetición legítima).
        events = await self._drain_and_reject_tool_use(self._make_workflow())
        tool_uses = [e for e in events if e.type.value == "tool_use"]
        assert len(tool_uses) == 1

    @patch("acople.server.Acople")
    async def test_guard_emits_note_before_done(self, MockAcople):
        """El guard emite una nota visible (TOKEN) antes del DONE — el cliente
        no recibe una respuesta vacía sin explicación."""
        from acople.bridge import BridgeEvent, EventType

        mock_instance = MagicMock()
        mock_instance.config.stream_format = "plain"

        async def mock_run(**kwargs):
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "write", "input": {"path": "x.txt", "content": "hello"}})

        mock_instance.run = mock_run
        MockAcople.return_value = mock_instance

        from acople.server import _session_tool_history
        _session_tool_history.clear()
        _session_tool_history["sess-1"] = [
            ("write", '{"content": "hello", "path": "x.txt"}'),
        ]

        events = await self._drain_and_reject_tool_use(self._make_workflow())
        types = [e.type.value for e in events]
        assert "token" in types and "done" in types
        note = next(e for e in events if e.type.value == "token")
        assert "Loop guard" in note.data.get("text", "")
        assert types.index("token") < types.index("done")

    @patch("acople.server.Acople")
    async def test_no_previous_history_no_guard(self, MockAcople):
        """When there's no previous tool history, the tool_use is emitted normally."""
        from acople.bridge import BridgeEvent, EventType

        mock_instance = MagicMock()
        mock_instance.config.stream_format = "plain"

        async def mock_run(**kwargs):
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "write", "input": {"path": "x.txt"}})

        mock_instance.run = mock_run
        MockAcople.return_value = mock_instance

        from acople.server import _session_tool_history
        _session_tool_history.clear()

        events = await self._drain_and_reject_tool_use(
            self._make_workflow()
        )

        tool_uses = [e for e in events if e.type.value == "tool_use"]
        assert len(tool_uses) == 1

    @patch("acople.server.Acople")
    async def test_no_session_id_no_guard(self, MockAcople):
        """Without a session_id, loop-guard is bypassed (stateless mode)."""
        from acople.bridge import BridgeEvent, EventType

        mock_instance = MagicMock()
        mock_instance.config.stream_format = "plain"

        async def mock_run(**kwargs):
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "write", "input": {"path": "x.txt", "content": "hello"}})

        mock_instance.run = mock_run
        MockAcople.return_value = mock_instance

        from acople.server import _unified_chat_workflow, _session_tool_history
        _session_tool_history.clear()
        _session_tool_history["sess-other"] = [  # Different session
            ("write", '{"content": "hello", "path": "x.txt"}'),
        ]

        wf = _unified_chat_workflow(
            messages=[{"role": "user", "content": "hi"}],
            agent_name="claude",
            session_id=None,  # No session
            tools=[{"type": "function", "function": {"name": "write"}}],
        )

        events = await self._drain_and_reject_tool_use(wf)
        tool_uses = [e for e in events if e.type.value == "tool_use"]
        assert len(tool_uses) == 1


class TestLoopGuardStateClean:
    """Loop-guard state: _session_tool_history se actualiza correctamente."""

    @patch("acople.server.Acople")
    async def test_history_stored_after_clean_completion(self, MockAcople):
        """After a clean turn with tool uses, history is stored."""
        from acople.bridge import BridgeEvent, EventType

        mock_instance = MagicMock()
        mock_instance.config.stream_format = "plain"

        async def mock_run(**kwargs):
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "write", "input": {"path": "y.txt", "content": "test"}})
            yield BridgeEvent(EventType.DONE, {})

        mock_instance.run = mock_run
        MockAcople.return_value = mock_instance

        from acople.server import _unified_chat_workflow, _session_tool_history
        _session_tool_history.clear()

        wf = _unified_chat_workflow(
            messages=[{"role": "user", "content": "write to y.txt"}],
            agent_name="claude",
            session_id="sess-history",
            tools=[{"type": "function", "function": {"name": "write"}}],
        )

        async for ev in wf:
            pass

        stored = _session_tool_history.get("sess-history")
        assert stored is not None
        assert len(stored) == 1
        assert stored[0][0] == "write"

    @patch("acople.server.Acople")
    async def test_no_tool_use_no_history(self, MockAcople):
        """When no tool uses in a turn, no history is stored."""
        from acople.bridge import BridgeEvent, EventType

        mock_instance = MagicMock()
        mock_instance.config.stream_format = "plain"

        async def mock_run(**kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": "Hello"})
            yield BridgeEvent(EventType.DONE, {})

        mock_instance.run = mock_run
        MockAcople.return_value = mock_instance

        from acople.server import _unified_chat_workflow, _session_tool_history
        _session_tool_history.clear()

        wf = _unified_chat_workflow(
            messages=[{"role": "user", "content": "hello"}],
            agent_name="claude",
            session_id="sess-no-tools",
            tools=[{"type": "function", "function": {"name": "write"}}],
        )

        async for ev in wf:
            pass

        # Should not have stored anything (no tool uses)
        assert "sess-no-tools" not in _session_tool_history


# ============================================================================
# Process PID desacople tests
# ============================================================================

class TestProcessPidDesacople:
    """process_pid siempre es uuid, independiente de session_id (AC5.1)."""

    def test_process_pid_not_session_id(self):
        """
        Confirm that _unified_chat_workflow uses uuid.uuid4(), not session_id,
        by inspecting the source.
        """
        from acople import server
        import inspect
        source = inspect.getsource(server._unified_chat_workflow)
        # Should contain uuid4()
        assert "uuid.uuid4" in source
        # Should NOT have `process_pid = final_session_id`
        assert "process_pid = final_session_id" not in source

    @patch("acople.server.Acople")
    async def test_two_concurrent_workflows_same_session_no_collision(self, MockAcople):
        """AC5.1 (real): dos workflows CONCURRENTES con la misma session_id
        registran pids distintos (uuids válidos) sin colisionar, y ambos
        mapean a la sesión del cliente en PROCESS_SESSIONS."""
        from acople.bridge import BridgeEvent, EventType
        from acople.server import (
            ACTIVE_PROCESSES,
            PROCESS_SESSIONS,
            _unified_chat_workflow,
        )

        ACTIVE_PROCESSES.clear()
        PROCESS_SESSIONS.clear()

        snapshots: list[dict] = []

        mock_instance = MagicMock()
        mock_instance.config.stream_format = "plain"

        async def mock_run(**kwargs):
            on_start = kwargs.get("on_start")
            proc = MagicMock()
            proc.returncode = 0
            if on_start:
                on_start(proc)
            snapshots.append(dict(PROCESS_SESSIONS))
            yield BridgeEvent(EventType.TOKEN, {"text": "x"})
            yield BridgeEvent(EventType.DONE, {})

        mock_instance.run = mock_run
        MockAcople.return_value = mock_instance

        wf1 = _unified_chat_workflow(
            messages=[{"role": "user", "content": "hi"}],
            agent_name="claude",
            session_id="same-session",
        )
        wf2 = _unified_chat_workflow(
            messages=[{"role": "user", "content": "hello"}],
            agent_name="claude",
            session_id="same-session",
        )

        # wf1 arranca y queda suspendido a mitad de stream (proceso "vivo").
        await wf1.__anext__()
        # wf2 corre completo mientras wf1 sigue registrado.
        async for _ in wf2:
            pass

        overlap = snapshots[1]  # estado visto por wf2 al registrarse
        assert len(overlap) == 2, "ambos procesos deben coexistir sin colisión"
        assert set(overlap.values()) == {"same-session"}
        for pid in overlap:
            uuid.UUID(pid)  # cada clave es un uuid válido, no la session_id

        # Drenar wf1 → cleanup de ambos registros.
        async for _ in wf1:
            pass
        assert ACTIVE_PROCESSES == {}
        assert PROCESS_SESSIONS == {}


class TestInterruptBySession:
    """/interrupt?session_id=... funciona tras el desacople F10."""

    def _seed(self, session_id="sess-int"):
        from acople.server import ACTIVE_PROCESSES, PROCESS_SESSIONS
        ACTIVE_PROCESSES.clear()
        PROCESS_SESSIONS.clear()
        pid = str(uuid.uuid4())
        proc = MagicMock()
        proc.returncode = None
        ACTIVE_PROCESSES[pid] = proc
        PROCESS_SESSIONS[pid] = session_id
        return pid, proc

    def test_interrupt_by_client_session_id(self):
        """El cliente interrumpe con SU session_id, no con el uuid interno."""
        from fastapi.testclient import TestClient

        from acople.server import ACTIVE_PROCESSES, PROCESS_SESSIONS, app

        pid, proc = self._seed("sess-int")
        try:
            client = TestClient(app)
            response = client.post("/interrupt?session_id=sess-int")
            assert response.status_code == 200
            assert response.json()["interrupted"] == 1
            assert proc.terminate.called or proc.send_signal.called
        finally:
            ACTIVE_PROCESSES.clear()
            PROCESS_SESSIONS.clear()

    def test_interrupt_unknown_session_404(self):
        from fastapi.testclient import TestClient

        from acople.server import ACTIVE_PROCESSES, PROCESS_SESSIONS, app

        self._seed("sess-int")
        try:
            client = TestClient(app)
            response = client.post("/interrupt?session_id=no-such-session")
            assert response.status_code == 404
        finally:
            ACTIVE_PROCESSES.clear()
            PROCESS_SESSIONS.clear()


# ============================================================================
# CORS regex tests
# ============================================================================

class TestCorsRegex:
    """CORS allow_origin_regex (AC5.2)."""

    def test_default_regex_matches_localhost(self):
        """Default regex allows http://localhost on any port."""
        from acople.server import _cors_origin_regex
        pattern = re.compile(_cors_origin_regex)

        assert pattern.match("http://localhost")
        assert pattern.match("http://localhost:8080")
        assert pattern.match("http://localhost:5173")
        assert pattern.match("https://localhost")
        assert pattern.match("https://localhost:443")
        assert pattern.match("http://localhost:3000")

    def test_default_regex_rejects_others(self):
        """Default regex rejects non-localhost origins."""
        from acople.server import _cors_origin_regex
        pattern = re.compile(_cors_origin_regex)

        assert not pattern.match("http://evil.com")
        assert not pattern.match("https://attacker.org")
        assert not pattern.match("http://localhost.evil.com")
        assert not pattern.match("http://192.168.1.1:8080")
        assert not pattern.match("http://127.0.0.1:9000")

    def test_cors_middleware_uses_regex(self):
        """Confirm CORSMiddleware uses allow_origin_regex, not allow_origins."""
        from pathlib import Path
        src = Path(__file__).parent.parent / "acople" / "server.py"
        text = src.read_text(encoding="utf-8")
        assert "allow_origin_regex=" in text
        assert "allow_origins=" not in text, (
            "el middleware no debe usar allow_origins (el wildcard de puerto "
            "no funciona en Starlette)"
        )

    def test_cors_regex_read_from_env(self):
        """El regex CORS se lee de ACOPLE_CORS_ORIGINS en el arranque."""
        from pathlib import Path
        src = Path(__file__).parent.parent / "acople" / "server.py"
        text = src.read_text(encoding="utf-8")
        assert 'os.environ.get("ACOPLE_CORS_ORIGINS"' in text

    def test_middleware_headers_allowed(self):
        """Allowed headers include CORS-relevant ones."""
        from pathlib import Path
        src = Path(__file__).parent.parent / "acople" / "server.py"
        text = src.read_text(encoding="utf-8")
        assert "X-Session-ID" in text
        assert "Authorization" in text


# ============================================================================
# Security docs tests
# ============================================================================

class TestSecurityDocs:
    """Security documentation exists and covers key risks."""

    def test_security_doc_exists(self):
        """docs/security.md exists."""
        from pathlib import Path
        doc = Path(__file__).parent.parent / "docs" / "security.md"
        assert doc.exists(), "docs/security.md must exist"

    def test_security_doc_has_api_key(self):
        """Security doc mentions API key."""
        from pathlib import Path
        content = Path(__file__).parent.parent / "docs" / "security.md"
        text = content.read_text(encoding="utf-8")
        assert "API_KEY" in text or "api key" in text.lower()

    def test_security_doc_has_isolated_cwd(self):
        """Security doc recommends isolated working directory."""
        from pathlib import Path
        content = Path(__file__).parent.parent / "docs" / "security.md"
        text = content.read_text(encoding="utf-8")
        assert "ACOPLE_DEFAULT_CWD" in text or "isolated" in text.lower()

    def test_security_doc_has_tool_proxy_mode(self):
        """Security doc covers Tool-Proxy mode."""
        from pathlib import Path
        content = Path(__file__).parent.parent / "docs" / "security.md"
        text = content.read_text(encoding="utf-8")
        assert "Tool-Proxy" in text or "tool-proxy" in text.lower() or "function calling" in text.lower()

    def test_security_doc_has_resource_limits(self):
        """Security doc covers resource limits (timeouts, concurrency)."""
        from pathlib import Path
        content = Path(__file__).parent.parent / "docs" / "security.md"
        text = content.read_text(encoding="utf-8")
        assert "ACOPLE_MAX_CONCURRENT" in text or "ACOPLE_STREAM" in text or "timeout" in text.lower()

    def test_security_doc_has_known_risky_flags(self):
        """Security doc covers dangerous flags."""
        from pathlib import Path
        content = Path(__file__).parent.parent / "docs" / "security.md"
        text = content.read_text(encoding="utf-8")
        assert "dangerously" in text or "risky" in text.lower() or "Dangerous" in text

    def test_security_doc_has_recommendations(self):
        """Security doc ends with production recommendations."""
        from pathlib import Path
        content = Path(__file__).parent.parent / "docs" / "security.md"
        text = content.read_text(encoding="utf-8")
        assert "Recommendations" in text or "recommendations" in text.lower() or "production" in text.lower()
