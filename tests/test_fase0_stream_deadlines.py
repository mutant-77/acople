"""
Fase 0 — Substrate de fiabilidad: deadlines de stream.

Cubre:
  - Idle timeout configurable (ACOPLE_STREAM_IDLE_TIMEOUT)
  - `continue` si proceso aún vivo (returncode is None)
  - Deadline absoluto (ACOPLE_STREAM_MAX_DURATION)
  - Caso normal: sin cambios
"""

import asyncio
import os
import time as _time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class AbsoluteTimeStdout:
    """Async stdout mock with absolute-time-based delay.

    Each chunk has a ``delay_since_start``: the earliest absolute time
    (since this object was created) when the chunk becomes available.
    If ``wait_for`` cancels the read, the next attempt picks up where it
    left off (only sleeps for the **remaining** wait), not from scratch.
    """

    def __init__(self, chunks_with_delays):
        self._queue = list(chunks_with_delays)
        self._start = _time.time()

    async def read(self, n):
        while self._queue:
            ready_delay, chunk = self._queue[0]
            remaining = ready_delay - (_time.time() - self._start)
            if remaining > 0:
                try:
                    await asyncio.sleep(remaining)
                except asyncio.CancelledError:
                    raise
            self._queue.pop(0)
            return chunk
        return b''


def _make_proc(pid=42, returncode=None):
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.stderr = AsyncMock()
    proc.stderr.read.return_value = b''
    return proc


def _attach_stdout(proc, chunks_with_delays):
    stream = AbsoluteTimeStdout(chunks_with_delays)
    proc.stdout = MagicMock()
    proc.stdout.read = stream.read
    return proc


# ---------------------------------------------------------------------------
# AC0.1 — Pausa > idle timeout, proceso vivo → el stream NO corta
# ---------------------------------------------------------------------------

class TestSurvivesPause:
    """AC0.1: Con pausa > idle-timeout, el stream continúa y entrega el output posterior."""

    @pytest.mark.asyncio
    async def test_plain_stream(self):
        """Plain-text: pausa 1.1s > idle 1s → ambos chunks entregados.

        Use chunks ≫ 12 bytes (the ``<acople-tool>`` safety margin) so the
        incremental parser emits them promptly.
        """
        from acople.bridge import Acople, EventType

        os.environ["ACOPLE_STREAM_IDLE_TIMEOUT"] = "1"
        os.environ["ACOPLE_STREAM_MAX_DURATION"] = "10"
        try:
            with patch('acople.bridge.shutil.which') as mock_which:
                mock_which.return_value = "qwen"

                bridge = Acople(agent="qwen")
                proc = _attach_stdout(_make_proc(), [
                    (0, b'primer chunk .txt\n'),
                    (1.1, b'segundo chunk .txt\n'),
                    (0, b''),
                ])

                events = [e async for e in bridge._read_stream(proc)]
                full_text = "".join(e.data.get("text", "") for e in events if e.type == EventType.TOKEN)
                assert "primer chunk" in full_text
                assert "segundo chunk" in full_text
        finally:
            os.environ.pop("ACOPLE_STREAM_IDLE_TIMEOUT", None)
            os.environ.pop("ACOPLE_STREAM_MAX_DURATION", None)

    @pytest.mark.asyncio
    async def test_json_stream(self):
        """JSON (claude): pausa 1.1s > idle 1s → ambos tokens entregados."""
        from acople.bridge import Acople, EventType

        os.environ["ACOPLE_STREAM_IDLE_TIMEOUT"] = "1"
        os.environ["ACOPLE_STREAM_MAX_DURATION"] = "10"
        try:
            with patch('acople.bridge.shutil.which') as mock_which:
                mock_which.return_value = "claude"

                bridge = Acople(agent="claude")
                proc = _attach_stdout(_make_proc(), [
                    (0, b'{"type":"assistant","message":{"content":[{"type":"text","text":"A"}]}}\n'),
                    (1.1, b'{"type":"assistant","message":{"content":[{"type":"text","text":"B"}]}}\n'),
                    (0, b'{"type":"result","subtype":"success","result":"ok"}\n'),
                    (0, b''),
                ])

                events = [e async for e in bridge._read_stream(proc)]
                full_text = "".join(e.data.get("text", "") for e in events if e.type == EventType.TOKEN)
                assert "A" in full_text
                assert "B" in full_text
        finally:
            os.environ.pop("ACOPLE_STREAM_IDLE_TIMEOUT", None)
            os.environ.pop("ACOPLE_STREAM_MAX_DURATION", None)

    @pytest.mark.asyncio
    async def test_alive_process_repeated_timeouts(self):
        """Múltiples timeouts consecutivos con proceso vivo → sigue esperando hasta MAX_DURATION."""
        from acople.bridge import Acople, EventType

        os.environ["ACOPLE_STREAM_IDLE_TIMEOUT"] = "1"
        os.environ["ACOPLE_STREAM_MAX_DURATION"] = "5"
        try:
            with patch('acople.bridge.shutil.which') as mock_which:
                mock_which.return_value = "qwen"

                bridge = Acople(agent="qwen")
                proc = _make_proc()
                proc.stdout = MagicMock()

                async def always_sleeping(n):
                    await asyncio.sleep(10)

                proc.stdout.read = always_sleeping

                events = [e async for e in bridge._read_stream(proc)]
                errors = [e for e in events if e.type == EventType.ERROR]
                assert len(errors) >= 1
                assert "max duration" in errors[0].data.get("message", "").lower()
        finally:
            os.environ.pop("ACOPLE_STREAM_IDLE_TIMEOUT", None)
            os.environ.pop("ACOPLE_STREAM_MAX_DURATION", None)


# ---------------------------------------------------------------------------
# AC0.2 — Proceso colgado → ERROR antes de MAX_DURATION
# ---------------------------------------------------------------------------

class TestMaxDuration:
    """AC0.2: Con proceso colgado indefinidamente, stream termina con ERROR."""

    @pytest.mark.asyncio
    async def test_hanging_process_triggers_error(self):
        """Nunca devuelve EOF → ERROR con max duration."""
        from acople.bridge import Acople, EventType

        os.environ["ACOPLE_STREAM_IDLE_TIMEOUT"] = "1"
        os.environ["ACOPLE_STREAM_MAX_DURATION"] = "3"
        try:
            with patch('acople.bridge.shutil.which') as mock_which:
                mock_which.return_value = "qwen"

                bridge = Acople(agent="qwen")
                proc = _make_proc()
                proc.stdout = MagicMock()

                async def forever(n):
                    await asyncio.sleep(3600)

                proc.stdout.read = forever

                events = [e async for e in bridge._read_stream(proc)]
                errors = [e for e in events if e.type == EventType.ERROR]
                assert len(errors) >= 1
                assert "max duration" in errors[0].data.get("message", "").lower()
        finally:
            os.environ.pop("ACOPLE_STREAM_IDLE_TIMEOUT", None)
            os.environ.pop("ACOPLE_STREAM_MAX_DURATION", None)


# ---------------------------------------------------------------------------
# AC0.3 — Caso normal (output continuo + EOF) sin cambios
# ---------------------------------------------------------------------------

class TestNormalBehavior:
    """AC0.3: Output normal sin pausas — comportamiento sin cambios."""

    @pytest.mark.asyncio
    async def test_plain_output_has_tokens_and_done(self):
        """Plain: tokens + DONE."""
        from acople.bridge import Acople, EventType

        with patch('acople.bridge.shutil.which') as mock_which:
            mock_which.return_value = "qwen"

            bridge = Acople(agent="qwen")
            proc = _attach_stdout(_make_proc(), [
                (0, b'hello world chunk\n'),
                (0, b'second chunk here\n'),
                (0, b''),
            ])

            events = [e async for e in bridge._read_stream(proc)]
            assert any(e.type == EventType.TOKEN for e in events)
            assert any(e.type == EventType.DONE for e in events)

    @pytest.mark.asyncio
    async def test_json_output_has_tokens_and_done(self):
        """JSON (claude): token + DONE."""
        from acople.bridge import Acople, EventType

        with patch('acople.bridge.shutil.which') as mock_which:
            mock_which.return_value = "claude"

            bridge = Acople(agent="claude")
            proc = _attach_stdout(_make_proc(), [
                (0, b'{"type":"assistant","message":{"content":[{"type":"text","text":"OK"}]}}\n'),
                (0, b'{"type":"result","subtype":"success","result":"ok"}\n'),
                (0, b''),
            ])

            events = [e async for e in bridge._read_stream(proc)]
            tokens = [e for e in events if e.type == EventType.TOKEN]
            assert len(tokens) == 1
            assert tokens[0].data.get("text", "").strip() == "OK"
            assert any(e.type == EventType.DONE for e in events)

    @pytest.mark.asyncio
    async def test_empty_stdout_ends_with_done(self):
        """Sin output → DONE."""
        from acople.bridge import Acople, EventType

        with patch('acople.bridge.shutil.which') as mock_which:
            mock_which.return_value = "qwen"

            bridge = Acople(agent="qwen")
            proc = _attach_stdout(_make_proc(), [(0, b'')])

            events = [e async for e in bridge._read_stream(proc)]
            assert any(e.type == EventType.DONE for e in events)

    @pytest.mark.asyncio
    async def test_run_start_failure_ends_with_done(self):
        """I2: si el arranque del subprocess falla, run() emite ERROR y luego
        DONE — los adapters de cable necesitan el terminal para cerrar."""
        from acople.bridge import Acople, EventType

        with patch('acople.bridge.shutil.which') as mock_which:
            mock_which.return_value = "qwen"

            bridge = Acople(agent="qwen")
            with patch.object(bridge, "_build_cmd", side_effect=RuntimeError("boom")):
                events = [e async for e in bridge.run("hola")]

            assert len(events) == 2
            assert events[0].type == EventType.ERROR
            assert "Failed to start" in events[0].data.get("message", "")
            assert events[1].type == EventType.DONE

    @pytest.mark.asyncio
    async def test_max_duration_not_extended_by_idle_read(self):
        """AC0.2: el deadline absoluto no se extiende por una lectura con
        idle-timeout largo — el read se capa al tiempo restante."""
        from acople.bridge import Acople, EventType

        os.environ["ACOPLE_STREAM_IDLE_TIMEOUT"] = "30"  # idle ≫ max
        os.environ["ACOPLE_STREAM_MAX_DURATION"] = "1"
        try:
            with patch('acople.bridge.shutil.which') as mock_which:
                mock_which.return_value = "qwen"

                bridge = Acople(agent="qwen")
                proc = _make_proc()
                proc.stdout = MagicMock()

                async def forever(n):
                    await asyncio.sleep(3600)

                proc.stdout.read = forever

                start = _time.time()
                events = [e async for e in bridge._read_stream(proc)]
                elapsed = _time.time() - start

                errors = [e for e in events if e.type == EventType.ERROR]
                assert any("max duration" in e.data.get("message", "").lower() for e in errors)
                # Sin el cap, esperaría hasta idle (30s); con el cap, ~1s.
                assert elapsed < 5, f"max duration overshoot: {elapsed:.1f}s"
        finally:
            os.environ.pop("ACOPLE_STREAM_IDLE_TIMEOUT", None)
            os.environ.pop("ACOPLE_STREAM_MAX_DURATION", None)

    @pytest.mark.asyncio
    async def test_stderr_after_done_is_system_not_error(self):
        """stderr con returncode≠0 tras un DONE ya emitido → SYSTEM, no ERROR
        (no se emite error después de que el cable haya cerrado)."""
        from acople.bridge import Acople, EventType

        with patch('acople.bridge.shutil.which') as mock_which:
            mock_which.return_value = "claude"

            bridge = Acople(agent="claude")
            proc = _make_proc(returncode=1)
            proc.wait = AsyncMock(return_value=1)
            proc.stderr = AsyncMock()
            proc.stderr.read.return_value = b'shutdown noise on stderr'
            _attach_stdout(proc, [
                (0, b'{"type":"assistant","message":{"content":[{"type":"text","text":"OK"}]}}\n'),
                (0, b'{"type":"result","subtype":"success","result":"ok"}\n'),
                (0, b''),
            ])

            events = [e async for e in bridge._read_stream(proc)]
            assert any(e.type == EventType.DONE for e in events)
            assert not any(e.type == EventType.ERROR for e in events)
            systems = [e for e in events if e.type == EventType.SYSTEM]
            assert len(systems) == 1
            assert "shutdown noise" in systems[0].data.get("message", "")

    @pytest.mark.asyncio
    async def test_dead_process_timeout_does_not_continue(self):
        """Timeout + proceso muerto (returncode≠None) → break inmediato, no MAX_DURATION."""
        from acople.bridge import Acople, EventType

        os.environ["ACOPLE_STREAM_IDLE_TIMEOUT"] = "1"
        try:
            with patch('acople.bridge.shutil.which') as mock_which:
                mock_which.return_value = "qwen"

                bridge = Acople(agent="qwen")
                proc = _make_proc(returncode=1)
                proc.stdout = MagicMock()

                async def dead_read(n):
                    await asyncio.sleep(5)

                proc.stdout.read = dead_read

                events = [e async for e in bridge._read_stream(proc)]
                assert any(e.type == EventType.DONE for e in events)
                errors = [e for e in events if e.type == EventType.ERROR]
                assert not any("max duration" in e.data.get("message", "").lower() for e in errors)
        finally:
            os.environ.pop("ACOPLE_STREAM_IDLE_TIMEOUT", None)
