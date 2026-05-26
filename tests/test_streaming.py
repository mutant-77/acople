"""
Tests de Streaming — Parser JSON de Claude y formato SSE
Cubre: bridge.py (parse_claude_json_line, BridgeEvent.to_sse)
"""

import json


class TestClaudeJSONParser:
    """Tests de parse_claude_json_line (devuelve list[BridgeEvent])"""

    # --- Formato real del CLI de Claude Code -------------------------------

    def test_parse_assistant_message_text(self):
        """assistant con bloque de texto → TOKEN con ese texto."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Hola"}]},
        })
        events = parse_claude_json_line(line)

        assert len(events) == 1
        assert events[0].type == EventType.TOKEN
        assert events[0].data["text"] == "Hola"

    def test_parse_assistant_message_multiple_blocks(self):
        """assistant con texto + tool_use → TOKEN y TOOL_USE en orden."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "voy a escribir"},
                {"type": "tool_use", "name": "Write", "input": {"path": "a.py"}},
            ]},
        })
        events = parse_claude_json_line(line)

        assert [e.type for e in events] == [EventType.TOKEN, EventType.TOOL_USE]
        assert events[0].data["text"] == "voy a escribir"
        assert events[1].data["tool"] == "Write"
        assert events[1].data["input"]["path"] == "a.py"

    def test_parse_user_tool_result(self):
        """user con bloque tool_result → TOOL_RESULT."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": "ok"}]},
        })
        events = parse_claude_json_line(line)

        assert len(events) == 1
        assert events[0].type == EventType.TOOL_RESULT
        assert events[0].data["content"] == "ok"

    def test_parse_result_is_done(self):
        """result (fin de turno del CLI) → DONE."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "result", "subtype": "success", "result": "Hola"})
        events = parse_claude_json_line(line)

        assert len(events) == 1
        assert events[0].type == EventType.DONE

    def test_parse_system_init_ignored(self):
        """system/init y otros tipos meta no producen eventos."""
        from acople.bridge import parse_claude_json_line

        assert parse_claude_json_line(json.dumps({"type": "system", "subtype": "init"})) == []
        assert parse_claude_json_line(json.dumps({"type": "rate_limit_event"})) == []

    # --- Compatibilidad con eventos crudos de la API SSE -------------------

    def test_parse_content_block_delta(self):
        """content_block_delta → [TOKEN]."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "content_block_delta", "delta": {"text": "hello"}})
        events = parse_claude_json_line(line)

        assert len(events) == 1
        assert events[0].type == EventType.TOKEN
        assert events[0].data["text"] == "hello"

    def test_parse_content_block_delta_empty_text(self):
        """content_block_delta sin texto → []."""
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "content_block_delta", "delta": {"text": ""}})
        assert parse_claude_json_line(line) == []

    def test_parse_tool_use(self):
        """tool_use → [TOOL_USE]."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({
            "type": "tool_use",
            "name": "write_file",
            "input": {"path": "test.py", "content": "print('hi')"},
        })
        events = parse_claude_json_line(line)

        assert len(events) == 1
        assert events[0].type == EventType.TOOL_USE
        assert events[0].data["tool"] == "write_file"
        assert "path" in events[0].data["input"]

    def test_parse_tool_call(self):
        """tool_call (alias) → [TOOL_USE]."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "tool_call", "name": "read_file", "input": {}})
        events = parse_claude_json_line(line)

        assert len(events) == 1
        assert events[0].type == EventType.TOOL_USE

    def test_parse_tool_result(self):
        """tool_result (crudo) → [TOOL_RESULT]."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        line = json.dumps({"type": "tool_result", "content": "file written"})
        events = parse_claude_json_line(line)

        assert len(events) == 1
        assert events[0].type == EventType.TOOL_RESULT
        assert events[0].data["content"] == "file written"

    def test_parse_message_stop(self):
        """message_stop → [DONE]."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        events = parse_claude_json_line(json.dumps({"type": "message_stop"}))
        assert len(events) == 1
        assert events[0].type == EventType.DONE

    def test_parse_end_event(self):
        """end → [DONE]."""
        from acople import EventType
        from acople.bridge import parse_claude_json_line

        events = parse_claude_json_line(json.dumps({"type": "end"}))
        assert len(events) == 1
        assert events[0].type == EventType.DONE

    def test_parse_invalid_json_returns_empty(self):
        """JSON inválido → []."""
        from acople.bridge import parse_claude_json_line

        assert parse_claude_json_line("this is not json {{{") == []

    def test_parse_unknown_type_returns_empty(self):
        """Tipo desconocido → []."""
        from acople.bridge import parse_claude_json_line

        assert parse_claude_json_line(json.dumps({"type": "some_unknown_type", "data": "x"})) == []

    def test_parse_missing_type_returns_empty(self):
        """Sin campo type → []."""
        from acople.bridge import parse_claude_json_line

        assert parse_claude_json_line(json.dumps({"data": "no type field"})) == []


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

    def test_sse_type_not_overwritten_by_data(self):
        """Fix 5: data.type no sobreescribe el type del evento."""
        from acople import BridgeEvent, EventType

        event = BridgeEvent(EventType.TOKEN, {"type": "INJECTED", "text": "hello"})
        sse = event.to_sse()

        payload = json.loads(sse.replace("data: ", "").strip())
        assert payload["type"] == "token", f"Expected 'token', got {payload['type']!r}"
        assert payload["text"] == "hello"


class TestBuildAgentPrompt:
    """Tests de _build_agent_prompt — qué prompt recibe realmente el agente.

    Regresión: para agentes con stream_format != "json" (opencode, gemini,
    codex, kilo, qwen) el system prompt se descartaba, dejando al modelo sin
    instrucciones ni esquema → no podía producir el JSON pedido.
    """

    SYS = "Sos un asistente. Devolvé un mapa semántico JSON {entities:[...]}."
    MSGS = [
        {"role": "system", "content": SYS},
        {"role": "user", "content": "Tema: gatos"},
    ]
    COMPILED = f"{SYS}\n\nUser: Tema: gatos"

    def test_json_agent_uses_compiled_prompt(self):
        """claude (stream_format='json') recibe el compiled_prompt tal cual."""
        from acople.server import _build_agent_prompt

        out = _build_agent_prompt(
            messages=self.MSGS,
            compiled_prompt=self.COMPILED,
            sys_prompt_text=self.SYS,
            tool_catalog="",
            stream_format="json",
        )
        assert out == self.COMPILED

    def test_non_json_agent_keeps_system(self):
        """opencode (stream_format='opencode-json') DEBE recibir el system."""
        from acople.server import _build_agent_prompt

        out = _build_agent_prompt(
            messages=self.MSGS,
            compiled_prompt=self.COMPILED,
            sys_prompt_text=self.SYS,
            tool_catalog="",
            stream_format="opencode-json",
        )
        # El esquema/instrucciones (system) sobreviven...
        assert self.SYS in out
        # ...junto al último turno del usuario.
        assert "Tema: gatos" in out
        # ...y SIN los prefijos de historial "User:"/"Assistant:".
        assert "User:" not in out
        assert "Assistant:" not in out

    def test_non_json_agent_without_system(self):
        """Sin system, solo el último user (sin head extra)."""
        from acople.server import _build_agent_prompt

        msgs = [{"role": "user", "content": "Tema: gatos"}]
        out = _build_agent_prompt(
            messages=msgs,
            compiled_prompt="User: Tema: gatos",
            sys_prompt_text="",
            tool_catalog="",
            stream_format="plain",
        )
        assert out == "Tema: gatos"

    def test_non_json_agent_includes_tool_catalog(self):
        """El catálogo de tools se conserva delante del último user."""
        from acople.server import _build_agent_prompt

        out = _build_agent_prompt(
            messages=self.MSGS,
            compiled_prompt=self.COMPILED,
            sys_prompt_text=self.SYS,
            tool_catalog="AVAILABLE TOOLS:\n- search\n\n",
            stream_format="plain",
        )
        assert "AVAILABLE TOOLS" in out
        assert self.SYS in out
        assert "Tema: gatos" in out
        # Orden: system, luego catálogo, luego user.
        assert out.index(self.SYS) < out.index("AVAILABLE TOOLS") < out.index("Tema: gatos")

    def test_non_json_agent_picks_last_user(self):
        """Con varios turnos, toma el ÚLTIMO mensaje de usuario."""
        from acople.server import _build_agent_prompt

        msgs = [
            {"role": "system", "content": self.SYS},
            {"role": "user", "content": "primero"},
            {"role": "assistant", "content": "respuesta"},
            {"role": "user", "content": "ultimo"},
        ]
        out = _build_agent_prompt(
            messages=msgs,
            compiled_prompt="ignored",
            sys_prompt_text=self.SYS,
            tool_catalog="",
            stream_format="plain",
        )
        assert "ultimo" in out
        assert "primero" not in out


class TestExtractJSONObjects:
    """Tests de _extract_json_objects (Fix 1: JSON multilínea)"""

    def test_single_object(self):
        from acople.bridge import _extract_json_objects

        objects, remainder = _extract_json_objects('{"a": 1}')
        assert objects == ['{"a": 1}']
        assert remainder == ""

    def test_multiple_objects(self):
        from acople.bridge import _extract_json_objects

        text = '{"a": 1}\n{"b": 2}\n{"c": 3}'
        objects, remainder = _extract_json_objects(text)
        assert objects == ['{"a": 1}', '{"b": 2}', '{"c": 3}']
        assert remainder == ""

    def test_multiline_string(self):
        """JSON con saltos de línea dentro de string — el caso que rompía el parser anterior."""
        from acople.bridge import _extract_json_objects

        text = '{"type":"text","part":{"text":"hello\\nworld\\nfoo"}}'
        objects, remainder = _extract_json_objects(text)
        assert len(objects) == 1
        assert json.loads(objects[0])["part"]["text"] == "hello\nworld\nfoo"
        assert remainder == ""

    def test_multiline_tool_input(self):
        """Tool call con código multilínea como input."""
        from acople.bridge import _extract_json_objects

        text = '{"type":"tool_use","name":"write","input":{"content":"def foo():\\n    return 42\\n"}}'
        objects, remainder = _extract_json_objects(text)
        assert len(objects) == 1
        parsed = json.loads(objects[0])
        assert "\\n" in parsed["input"]["content"] or "\n" in parsed["input"]["content"]

    def test_nested_braces(self):
        """Objetos JSON anidados."""
        from acople.bridge import _extract_json_objects

        text = '{"outer":{"inner":{"a":1}}}'
        objects, remainder = _extract_json_objects(text)
        assert len(objects) == 1
        assert json.loads(objects[0])["outer"]["inner"]["a"] == 1

    def test_braces_inside_strings(self):
        """Llaves dentro de strings no afectan el brace counting."""
        from acople.bridge import _extract_json_objects

        text = '{"text":"hello {world} and {nested} stuff"}'
        objects, remainder = _extract_json_objects(text)
        assert len(objects) == 1
        assert json.loads(objects[0])["text"] == "hello {world} and {nested} stuff"

    def test_escaped_quotes_inside_strings(self):
        """Comillas escapadas dentro de strings."""
        from acople.bridge import _extract_json_objects

        text = '{"text":"he said \\\"hello world\\\""}'
        objects, remainder = _extract_json_objects(text)
        assert len(objects) == 1, f"Expected 1 object, got {len(objects)}: {objects}"

    def test_partial_object_at_end(self):
        """Objeto incompleto al final se devuelve como remainder."""
        from acople.bridge import _extract_json_objects

        text = '{"a": 1}\n{"b":'
        objects, remainder = _extract_json_objects(text)
        assert objects == ['{"a": 1}']
        assert remainder == '{"b":'

    def test_multiple_partial_accumulation(self):
        """Acumulación progresiva de chunks multilínea."""
        from acople.bridge import _extract_json_objects

        chunk1 = '{"type":"text","part":{"text":"hello\\n'
        chunk2 = 'world\\n'
        chunk3 = 'foo"}}'

        objects, remainder = _extract_json_objects(chunk1 + chunk2 + chunk3)
        assert len(objects) == 1
        assert json.loads(objects[0])["part"]["text"] == "hello\nworld\nfoo"
        assert remainder == ""

    def test_empty_text(self):
        """Texto vacío."""
        from acople.bridge import _extract_json_objects

        objects, remainder = _extract_json_objects("")
        assert objects == []
        assert remainder == ""

    def test_whitespace_between_objects(self):
        """Espacios y saltos de línea entre objetos."""
        from acople.bridge import _extract_json_objects

        text = '{"a": 1}  \n\n  {"b": 2}'
        objects, remainder = _extract_json_objects(text)
        assert objects == ['{"a": 1}', '{"b": 2}']
