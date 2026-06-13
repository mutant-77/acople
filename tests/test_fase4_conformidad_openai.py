"""
Fase 4 — Conformidad de cable OpenAI.

Cubre:
  - _estimate_tokens helper
  - delta.role first chunk in streaming (AC4.1)
  - usage in non-stream response (AC4.2)
  - stream_options.include_usage final chunk (AC4.3)
  - Sin include_usage no hay usage chunk (AC4.4)
"""

import json
from unittest.mock import patch

from acople import BridgeEvent, EventType


class TestEstimateTokens:
    """Tests del helper _estimate_tokens."""

    def test_estimate_empty(self):
        from acople.server import _estimate_tokens

        assert _estimate_tokens("") == 1  # max(1, 0//4)

    def test_estimate_short(self):
        from acople.server import _estimate_tokens

        assert _estimate_tokens("hi") == 1  # max(1, 2//4=0)

    def test_estimate_4_chars(self):
        from acople.server import _estimate_tokens

        assert _estimate_tokens("1234") == 1  # 4//4=1

    def test_estimate_10_chars(self):
        from acople.server import _estimate_tokens

        assert _estimate_tokens("1234567890") == 2  # 10//4=2

    def test_estimate_long_text(self):
        from acople.server import _estimate_tokens

        text = "hello world this is a longer text for testing token estimation"
        expected = max(1, len(text) // 4)
        assert _estimate_tokens(text) == expected


class TestStreamingDeltaRole:
    """AC4.1: Primer chunk de streaming contiene delta.role == 'assistant'."""

    def test_first_chunk_has_delta_role_assistant(self):
        """El primer evento SSE emitido es el chunk con delta.role."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": "hello"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude", "stream": True},
            )

        assert response.status_code == 200
        lines = [l for l in response.text.splitlines() if l.startswith("data: ") and "[DONE]" not in l]
        assert len(lines) >= 2
        first = json.loads(lines[0][len("data: "):])
        assert first["choices"][0]["delta"].get("role") == "assistant"

    def test_role_chunk_before_content(self):
        """El chunk de role aparece antes que cualquier content."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": "world"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude", "stream": True},
            )

        lines = [l for l in response.text.splitlines() if l.startswith("data: ") and "[DONE]" not in l]
        first = json.loads(lines[0][len("data: "):])
        assert first["choices"][0]["delta"].get("role") == "assistant"
        assert "content" not in first["choices"][0]["delta"]

        second = json.loads(lines[1][len("data: "):])
        assert second["choices"][0]["delta"].get("content") == "world"

    def test_role_chunk_with_empty_response(self):
        """Incluso con respuesta vacía, el primer chunk tiene role."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude", "stream": True},
            )

        lines = [l for l in response.text.splitlines() if l.startswith("data: ") and "[DONE]" not in l]
        first = json.loads(lines[0][len("data: "):])
        assert first["choices"][0]["delta"].get("role") == "assistant"


class TestNonStreamUsage:
    """AC4.2: No-stream incluye usage con las 3 claves."""

    def test_non_stream_has_usage(self):
        """Respuesta no-stream incluye usage."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": "hello world"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude", "stream": False},
            )

        assert response.status_code == 200
        body = response.json()
        assert "usage" in body
        usage = body["usage"]
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]

    def test_non_stream_usage_values_reasonable(self):
        """Los valores de usage son enteros positivos."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": "hello world"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude", "stream": False},
            )

        body = response.json()
        usage = body["usage"]
        assert isinstance(usage["prompt_tokens"], int) and usage["prompt_tokens"] >= 1
        assert isinstance(usage["completion_tokens"], int) and usage["completion_tokens"] >= 1
        assert isinstance(usage["total_tokens"], int) and usage["total_tokens"] >= 2

    def test_non_stream_with_tool_calls_has_usage(self):
        """Tool calls no impiden que usage aparezca."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "bash", "input": {"cmd": "ls"}})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "run ls"}],
                    "model": "claude",
                    "stream": False,
                    "tools": [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object", "properties": {}}}}],
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert "usage" in body
        assert body["choices"][0]["message"].get("tool_calls") is not None

    def test_non_stream_json_mode_usage_present(self):
        """json_mode + no-stream incluye usage."""
        import json as _json

        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": '{"ok": true}'})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "json please"}],
                    "model": "claude",
                    "stream": False,
                    "response_format": {"type": "json_object"},
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert "usage" in body
        assert body["usage"]["completion_tokens"] >= 1

    def test_non_stream_error_no_usage(self):
        """Con error, no hay respuesta con usage."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.ERROR, {"message": "something failed"})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude", "stream": False},
            )

        assert response.status_code == 502


class TestStreamIncludeUsage:
    """AC4.3 + AC4.4: stream_options.include_usage."""

    def test_include_usage_final_chunk(self):
        """Con include_usage=true, hay un chunk final con choices=[] + usage."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": "hello"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "model": "claude",
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            )

        assert response.status_code == 200
        lines = [l for l in response.text.splitlines() if l.startswith("data: ")]
        non_done = [l for l in lines if "[DONE]" not in l]
        last_chunk = json.loads(non_done[-1][len("data: "):])
        assert last_chunk.get("choices") == []
        assert "usage" in last_chunk
        usage = last_chunk["usage"]
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]

    def test_include_usage_has_null_in_intermediate(self):
        """Con include_usage=true, los chunks intermedios tienen usage: null."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": "hello "})
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "bash", "input": {"cmd": "ls"}})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "run ls"}],
                    "model": "claude",
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "tools": [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object", "properties": {}}}}],
                },
            )

        assert response.status_code == 200
        lines = [l for l in response.text.splitlines() if l.startswith("data: ") and "[DONE]" not in l]
        for i, line in enumerate(lines[:-1]):
            chunk = json.loads(line[len("data: "):])
            assert chunk.get("usage") is None, f"Chunk {i} should have usage: null, got {chunk}"

    def test_without_include_usage_no_usage_chunk(self):
        """AC4.4: Sin include_usage, no se emite chunk de usage."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": "hello"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "model": "claude",
                    "stream": True,
                },
            )

        assert response.status_code == 200
        lines = [l for l in response.text.splitlines() if l.startswith("data: ") and "[DONE]" not in l]
        for line in lines:
            chunk = json.loads(line[len("data: "):])
            assert "usage" not in chunk, f"Chunk should not have usage: {chunk}"

    def test_include_usage_role_chunk_has_null(self):
        """El chunk de role también tiene usage: null con include_usage."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "model": "claude",
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            )

        lines = [l for l in response.text.splitlines() if l.startswith("data: ") and "[DONE]" not in l]
        first = json.loads(lines[0][len("data: "):])
        assert first.get("usage") is None

    def test_include_usage_tool_call_chunk_has_null(self):
        """Los chunks de tool_call también tienen usage: null."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "bash", "input": {"cmd": "ls"}})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "run ls"}],
                    "model": "claude",
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "tools": [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object", "properties": {}}}}],
                },
            )

        lines = [l for l in response.text.splitlines() if l.startswith("data: ") and "[DONE]" not in l]
        for line in lines[:-1]:
            chunk = json.loads(line[len("data: "):])
            if "tool_calls" in chunk.get("choices", [{}])[0].get("delta", {}):
                assert chunk.get("usage") is None


class TestStreamWireIdentity:
    """Spec OpenAI: todos los chunks de una completion comparten id y created."""

    def test_chunks_share_id_and_created(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": "hello "})
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "bash", "input": {"cmd": "ls"}})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "run ls"}],
                    "model": "claude",
                    "stream": True,
                    "tools": [{"type": "function", "function": {"name": "bash", "parameters": {}}}],
                },
            )

        lines = [l for l in response.text.splitlines() if l.startswith("data: ") and "[DONE]" not in l]
        chunks = [json.loads(l[len("data: "):]) for l in lines]
        ids = {c["id"] for c in chunks}
        createds = {c["created"] for c in chunks}
        assert len(ids) == 1, f"todos los chunks deben compartir id: {ids}"
        assert len(createds) == 1, f"todos los chunks deben compartir created: {createds}"


class TestStreamAlwaysCloses:
    """I2 (cable): el stream nunca termina sin finish_reason + [DONE]."""

    def test_error_without_done_still_closes(self):
        """Workflow que termina en ERROR sin DONE → finish chunk + [DONE]."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.ERROR, {"message": "agent failed to start"})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude", "stream": True},
            )

        lines = [l for l in response.text.splitlines() if l.startswith("data: ")]
        assert lines[-1] == "data: [DONE]", "el stream debe cerrar con [DONE]"
        chunks = [json.loads(l[len("data: "):]) for l in lines if "[DONE]" not in l]
        finish = [
            c for c in chunks
            if c.get("choices") and c["choices"][0].get("finish_reason") is not None
        ]
        assert len(finish) == 1
        assert finish[0]["choices"][0]["finish_reason"] == "stop"
        assert any("error" in c for c in chunks), "el error debe reportarse antes de cerrar"


class TestF7ToolsPrecedence:
    """F7: con tools registradas, response_format json se ignora (+ log)."""

    def test_stream_json_mode_ignored_with_tools(self):
        """Con tools, los tokens se streamean tal cual (2 chunks separados);
        en json_mode se bufferizarían y saldrían como 1 solo chunk saneado."""
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": "foo"})
            yield BridgeEvent(EventType.TOKEN, {"text": "bar"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "model": "claude",
                    "stream": True,
                    "response_format": {"type": "json_object"},
                    "tools": [{"type": "function", "function": {"name": "search", "parameters": {}}}],
                },
            )

        lines = [l for l in response.text.splitlines() if l.startswith("data: ") and "[DONE]" not in l]
        contents = [
            json.loads(l[len("data: "):])["choices"][0]["delta"].get("content")
            for l in lines
            if json.loads(l[len("data: "):]).get("choices")
        ]
        contents = [c for c in contents if c]
        assert contents == ["foo", "bar"], (
            f"json_mode debió ignorarse (streaming directo), got {contents!r}"
        )

    def test_non_stream_json_mode_ignored_with_tools(self):
        """Con tools, el content NO pasa por _extract_json_payload."""
        from fastapi.testclient import TestClient

        from acople.server import app

        RAW = 'prose around {"a": 1} more prose'

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": RAW})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "model": "claude",
                    "stream": False,
                    "response_format": {"type": "json_object"},
                    "tools": [{"type": "function", "function": {"name": "search", "parameters": {}}}],
                },
            )

        body = response.json()
        assert body["choices"][0]["message"]["content"] == RAW


class TestNativeToolNeverLeaks:
    """I4: sin tools registradas, las tools nativas del agente jamás llegan
    al cliente OpenAI como tool_calls (ni alteran finish_reason)."""

    def test_stream_native_tool_suppressed_without_registered_tools(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "bash", "input": {"cmd": "rm -rf /"}})
            yield BridgeEvent(EventType.TOKEN, {"text": "done"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude", "stream": True},
            )

        lines = [l for l in response.text.splitlines() if l.startswith("data: ") and "[DONE]" not in l]
        chunks = [json.loads(l[len("data: "):]) for l in lines]
        assert not any(
            "tool_calls" in c["choices"][0].get("delta", {})
            for c in chunks if c.get("choices")
        ), "tool nativa filtrada al cliente (I4)"
        finishes = [
            c["choices"][0]["finish_reason"]
            for c in chunks
            if c.get("choices") and c["choices"][0].get("finish_reason")
        ]
        assert finishes == ["stop"]

    def test_non_stream_native_tool_suppressed_without_registered_tools(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOOL_USE, {"tool": "read", "input": {"path": "/etc/passwd"}})
            yield BridgeEvent(EventType.TOKEN, {"text": "answer"})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "model": "claude", "stream": False},
            )

        body = response.json()
        assert "tool_calls" not in body["choices"][0]["message"]
        assert body["choices"][0]["finish_reason"] == "stop"
        assert body["choices"][0]["message"]["content"] == "answer"


class TestStreamUsageValues:
    """Verificación de valores de usage en streaming."""

    def test_usage_values_match_content(self):
        """prompt_tokens y completion_tokens reflejan tamaño real."""
        from fastapi.testclient import TestClient

        from acople.server import _estimate_tokens, app

        USER_MSG = "hello world test message"
        RESPONSE_TEXT = "this is the response from the agent"

        async def mock_workflow(*args, **kwargs):
            yield BridgeEvent(EventType.TOKEN, {"text": RESPONSE_TEXT})
            yield BridgeEvent(EventType.DONE, {})

        with (
            patch("acople.server._unified_chat_workflow", return_value=mock_workflow()),
            patch("acople.server._DEFAULT_AGENT", "claude"),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": USER_MSG}],
                    "model": "claude",
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            )

        lines = [l for l in response.text.splitlines() if l.startswith("data: ") and "[DONE]" not in l]
        last = json.loads(lines[-1][len("data: "):])
        expected_prompt = _estimate_tokens(json.dumps([{"role": "user", "content": USER_MSG}]))
        expected_completion = _estimate_tokens(RESPONSE_TEXT)
        assert last["usage"]["prompt_tokens"] == expected_prompt
        assert last["usage"]["completion_tokens"] == expected_completion
        assert last["usage"]["total_tokens"] == expected_prompt + expected_completion
