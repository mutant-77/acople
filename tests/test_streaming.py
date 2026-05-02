"""
Tests de Streaming — Parser JSON de Claude y formato SSE
Cubre: bridge.py (parse_claude_json_line, BridgeEvent.to_sse)
"""

import json


class TestClaudeJSONParser:
    """Tests de parse_claude_json_line"""

    def test_parse_content_block_delta(self):
        """content_block_delta → TOKEN event."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "content_block_delta", "delta": {"text": "hello"}})
        event = parse_claude_json_line(line)

        assert event is not None
        assert event.type == EventType.TOKEN
        assert event.data["text"] == "hello"

    def test_parse_content_block_delta_empty_text(self):
        """content_block_delta sin texto → None."""
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "content_block_delta", "delta": {"text": ""}})
        event = parse_claude_json_line(line)

        assert event is None

    def test_parse_tool_use(self):
        """tool_use → TOOL_USE event."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({
            "type": "tool_use",
            "name": "write_file",
            "input": {"path": "test.py", "content": "print('hi')"},
        })
        event = parse_claude_json_line(line)

        assert event is not None
        assert event.type == EventType.TOOL_USE
        assert event.data["tool"] == "write_file"
        assert "path" in event.data["input"]

    def test_parse_tool_call(self):
        """tool_call (alias) → TOOL_USE event."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "tool_call", "name": "read_file", "input": {}})
        event = parse_claude_json_line(line)

        assert event is not None
        assert event.type == EventType.TOOL_USE

    def test_parse_tool_result(self):
        """tool_result → TOOL_RESULT event."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "tool_result", "content": "file written"})
        event = parse_claude_json_line(line)

        assert event is not None
        assert event.type == EventType.TOOL_RESULT
        assert event.data["content"] == "file written"

    def test_parse_message_stop(self):
        """message_stop → DONE event."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "message_stop"})
        event = parse_claude_json_line(line)

        assert event is not None
        assert event.type == EventType.DONE

    def test_parse_end_event(self):
        """end → DONE event."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "end"})
        event = parse_claude_json_line(line)

        assert event is not None
        assert event.type == EventType.DONE

    def test_parse_invalid_json_returns_none(self):
        """JSON inválido → None."""
        from acople.bridge import parse_claude_json_line

        event = parse_claude_json_line("this is not json {{{")
        assert event is None

    def test_parse_unknown_type_returns_none(self):
        """Tipo desconocido → None."""
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "some_unknown_type", "data": "test"})
        event = parse_claude_json_line(line)

        assert event is None

    def test_parse_missing_type_returns_none(self):
        """Sin campo type → None."""
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"data": "no type field"})
        event = parse_claude_json_line(line)

        assert event is None


class TestSSEFormat:
    """Tests del formato SSE (Server-Sent Events)"""

    def test_sse_starts_with_data_prefix(self):
        """SSE empieza con 'data: '."""
        from acople import BridgeEvent, EventType

        event = BridgeEvent(EventType.TOKEN, {"text": "hello"})
        sse = event.to_sse()

        assert sse.startswith("data: ")

    def test_sse_ends_with_double_newline(self):
        """SSE termina con \\n\\n."""
        from acople import BridgeEvent, EventType

        event = BridgeEvent(EventType.TOKEN, {"text": "test"})
        sse = event.to_sse()

        assert sse.endswith("\n\n")

    def test_sse_contains_valid_json(self):
        """El payload SSE es JSON válido."""
        from acople import BridgeEvent, EventType

        event = BridgeEvent(EventType.TOKEN, {"text": "hello"})
        sse = event.to_sse()

        payload = sse.replace("data: ", "").strip()
        parsed = json.loads(payload)

        assert isinstance(parsed, dict)

    def test_sse_contains_type_field(self):
        """El JSON del SSE contiene 'type'."""
        from acople import BridgeEvent, EventType

        event = BridgeEvent(EventType.TOKEN, {"text": "hello"})
        sse = event.to_sse()

        payload = json.loads(sse.replace("data: ", "").strip())
        assert "type" in payload
        assert payload["type"] == "token"

    def test_sse_token_has_text(self):
        """TOKEN event tiene campo 'text' en el SSE."""
        from acople import BridgeEvent, EventType

        event = BridgeEvent(EventType.TOKEN, {"text": "world"})
        sse = event.to_sse()

        payload = json.loads(sse.replace("data: ", "").strip())
        assert payload["text"] == "world"

    def test_sse_done_event(self):
        """DONE event se serializa correctamente."""
        from acople import BridgeEvent, EventType

        event = BridgeEvent(EventType.DONE, {})
        sse = event.to_sse()

        payload = json.loads(sse.replace("data: ", "").strip())
        assert payload["type"] == "done"

    def test_sse_error_event(self):
        """ERROR event incluye message."""
        from acople import BridgeEvent, EventType

        event = BridgeEvent(EventType.ERROR, {"message": "something broke"})
        sse = event.to_sse()

        payload = json.loads(sse.replace("data: ", "").strip())
        assert payload["type"] == "error"
        assert payload["message"] == "something broke"

    def test_sse_tool_use_event(self):
        """TOOL_USE event tiene tool e input."""
        from acople import BridgeEvent, EventType

        event = BridgeEvent(EventType.TOOL_USE, {"tool": "bash", "input": {"cmd": "ls"}})
        sse = event.to_sse()

        payload = json.loads(sse.replace("data: ", "").strip())
        assert payload["type"] == "tool_use"
        assert payload["tool"] == "bash"
