"""
Fase 1 — Extracción estructurada de marcadores + terminación forzada.

Cubre:
  - Marker filter in _read_stream (JSON paths): <acople-tool> en texto → TOOL_USE
  - Forced termination in _unified_chat_workflow: tool_call_emitted → break on TOKEN
  - Edge cases: markers partidos, malformados, buffer flush en non-TOKEN
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# Marker filter tests (parse_plain_tool_markers — scanner incremental)
# ============================================================================

class TestAcopleToolMarkerParsing:
    """parse_plain_tool_markers: scanner incremental de <acople-tool>.

    Tests focused on the JSON-path scenario where markers appear inside
    text blocks that were previously parsed as TOKEN events.
    """

    MARKER_BASH = '<acople-tool>{"name":"bash","arguments":{"cmd":"ls"}}</acople-tool>'
    MARKER_READ = '<acople-tool>{"name":"read","arguments":{"path":"/etc"}}</acople-tool>'

    def test_marker_alone(self):
        """AC1.1 Texto = marcador puro → exactamente 1 tool_use, 0 tokens."""
        from acople.normalize import parse_plain_tool_markers

        events, rem = parse_plain_tool_markers(self.MARKER_BASH, final=True)
        assert len(events) == 1
        kind, data = events[0]
        assert kind == "tool_use"
        assert data["tool"] == "bash"
        assert data["input"]["cmd"] == "ls"
        assert rem == ""

    def test_text_before_marker(self):
        """AC1.2 Texto + marcador → token con texto previo + tool_use."""
        from acople.normalize import parse_plain_tool_markers

        buffer = f"Let me run this: {self.MARKER_BASH}"
        events, rem = parse_plain_tool_markers(buffer, final=True)
        assert len(events) == 2
        assert events[0] == ("token", {"text": "Let me run this: "})
        assert events[1] == ("tool_use", {"tool": "bash", "input": {"cmd": "ls"}})
        assert rem == ""

    def test_text_after_marker_with_final(self):
        """Texto después del marcador con final=True se emite como token."""
        from acople.normalize import parse_plain_tool_markers

        buffer = f"{self.MARKER_BASH} and then some"
        events, rem = parse_plain_tool_markers(buffer, final=True)
        assert len(events) == 2
        assert events[0] == ("tool_use", {"tool": "bash", "input": {"cmd": "ls"}})
        assert events[1] == ("token", {"text": " and then some"})
        assert rem == ""

    def test_marker_split_across_two_chunks(self):
        """AC1.3 Marcador partido en 2 chunks → 1 tool_use tras el segundo."""
        from acople.normalize import parse_plain_tool_markers

        chunk1 = 'Let me check <acople-tool>{"name":"bash","arguments":{"cmd":"ls'
        chunk2 = '"}}</acople-tool> and done'

        events1, rem = parse_plain_tool_markers(chunk1, final=False)
        assert len(events1) == 1  # only the text prefix
        assert events1[0] == ("token", {"text": "Let me check "})

        events2, rem = parse_plain_tool_markers(rem + chunk2, final=False)
        # rem was the partial marker start, now combined with chunk2
        assert len(events2) == 1
        assert events2[0] == ("tool_use", {"tool": "bash", "input": {"cmd": "ls"}})
        assert rem == " and done"

    def test_malformed_marker(self):
        """AC1.4 Marcador malformado → token literal, sin tool_use."""
        from acople.normalize import parse_plain_tool_markers

        bad_marker = '<acople-tool>{invalid json}</acople-tool>'
        events, rem = parse_plain_tool_markers(bad_marker, final=True)
        assert len(events) == 1
        assert events[0] == ("token", {"text": bad_marker})

    def test_no_marker_no_text(self):
        """Buffer vacío → sin eventos."""
        from acople.normalize import parse_plain_tool_markers

        events, rem = parse_plain_tool_markers("", final=True)
        assert events == []
        assert rem == ""

    def test_no_marker_plain_text(self):
        """Texto sin marcadores → un token."""
        from acople.normalize import parse_plain_tool_markers

        events, rem = parse_plain_tool_markers("Hello world", final=True)
        assert len(events) == 1
        assert events[0] == ("token", {"text": "Hello world"})

    def test_multiple_markers(self):
        """Varios marcadores en secuencia → múltiples tool_uses."""
        from acople.normalize import parse_plain_tool_markers

        buffer = f"First: {self.MARKER_BASH} Second: {self.MARKER_READ}"
        events, rem = parse_plain_tool_markers(buffer, final=True)
        assert len(events) == 4
        assert events[0] == ("token", {"text": "First: "})
        assert events[1] == ("tool_use", {"tool": "bash", "input": {"cmd": "ls"}})
        assert events[2] == ("token", {"text": " Second: "})
        assert events[3] == ("tool_use", {"tool": "read", "input": {"path": "/etc"}})
        assert rem == ""

    def test_non_final_preserves_partial_marker(self):
        """Sin final=True, marcador sin cerrar se preserva para el siguiente chunk."""
        from acople.normalize import parse_plain_tool_markers

        partial = '<acople-tool>{"name":"bash","arguments":{"cmd":"ls"}}'
        events, rem = parse_plain_tool_markers(partial, final=False)
        assert events == []
        assert rem == partial  # preserved for next chunk

    def test_final_flushes_partial_marker_as_text(self):
        """Con final=True, marcador sin cerrar → tratado como texto literal."""
        from acople.normalize import parse_plain_tool_markers

        partial = '<acople-tool>{"name":"bash","arguments":{"cmd":"ls"}}'
        events, rem = parse_plain_tool_markers(partial, final=True)
        assert len(events) == 1
        assert events[0] == ("token", {"text": partial})
        assert rem == ""

    def test_unknown_tool_name_preserved(self):
        """Tool name no estándar se conserva."""
        from acople.normalize import parse_plain_tool_markers

        marker = '<acople-tool>{"name":"my_custom_tool","arguments":{"x":1}}</acople-tool>'
        events, rem = parse_plain_tool_markers(marker, final=True)
        assert len(events) == 1
        assert events[0][1]["tool"] == "my_custom_tool"
        assert events[0][1]["input"]["x"] == 1


class TestReadStreamMarkerBuffer:
    """Simulación del buffer de marcadores en el path JSON de _read_stream.

    Estos tests validan el comportamiento combinado:
    - Evento TOKEN → acumula en text_marker_buffer + parsea
    - Evento no-TOKEN → flush del buffer ANTES de emitir el evento
    - EOF → flush del buffer
    """

    @pytest.mark.asyncio
    async def test_token_with_marker_produces_tool_use(self):
        """TOKEN con marcador → TOOL_USE, no texto crudo (I3)."""
        from acople import BridgeEvent, EventType
        from acople.bridge import _extract_json_objects, parse_claude_json_line
        from acople.normalize import parse_plain_tool_markers

        line = json.dumps({
            "type": "assistant",
            "message": {"content": [{
                "type": "text",
                "text": '<acople-tool>{"name":"bash","arguments":{"cmd":"ls"}}</acople-tool>'
            }]},
        })
        objects, _ = _extract_json_objects(line)
        events = parse_claude_json_line(objects[0])

        # Simulate the marker filter logic from _read_stream
        text_marker_buffer = ""
        output = []
        for event in events:
            if event.type == EventType.TOKEN:
                text_marker_buffer += event.data.get("text", "")
                marker_events, text_marker_buffer = parse_plain_tool_markers(
                    text_marker_buffer, final=False
                )
                for kind, data in marker_events:
                    if kind == "tool_use":
                        output.append(BridgeEvent(EventType.TOOL_USE, data))
                    else:
                        output.append(BridgeEvent(EventType.TOKEN, data))
            else:
                if text_marker_buffer:
                    marker_events, text_marker_buffer = parse_plain_tool_markers(
                        text_marker_buffer, final=True
                    )
                    for kind, data in marker_events:
                        if kind == "tool_use":
                            output.append(BridgeEvent(EventType.TOOL_USE, data))
                        else:
                            output.append(BridgeEvent(EventType.TOKEN, data))
                output.append(event)

        assert len(output) == 1
        assert output[0].type == EventType.TOOL_USE
        assert output[0].data["tool"] == "bash"

    @pytest.mark.asyncio
    async def test_token_with_text_then_marker(self):
        """Texto + marcador en TOKEN → TOKEN + TOOL_USE."""
        from acople import BridgeEvent, EventType
        from acople.bridge import _extract_json_objects, parse_claude_json_line
        from acople.normalize import parse_plain_tool_markers

        line = json.dumps({
            "type": "assistant",
            "message": {"content": [{
                "type": "text",
                "text": 'Let me run <acople-tool>{"name":"bash","arguments":{"cmd":"ls"}}</acople-tool>'
            }]},
        })
        objects, _ = _extract_json_objects(line)
        events = parse_claude_json_line(objects[0])

        text_marker_buffer = ""
        output = []
        for event in events:
            if event.type == EventType.TOKEN:
                text_marker_buffer += event.data.get("text", "")
                marker_events, text_marker_buffer = parse_plain_tool_markers(
                    text_marker_buffer, final=False
                )
                for kind, data in marker_events:
                    if kind == "tool_use":
                        output.append(BridgeEvent(EventType.TOOL_USE, data))
                    else:
                        output.append(BridgeEvent(EventType.TOKEN, data))
            else:
                if text_marker_buffer:
                    marker_events, text_marker_buffer = parse_plain_tool_markers(
                        text_marker_buffer, final=True
                    )
                    for kind, data in marker_events:
                        if kind == "tool_use":
                            output.append(BridgeEvent(EventType.TOOL_USE, data))
                        else:
                            output.append(BridgeEvent(EventType.TOKEN, data))
                output.append(event)

        assert len(output) == 2
        assert output[0].type == EventType.TOKEN
        assert output[0].data["text"] == "Let me run "
        assert output[1].type == EventType.TOOL_USE
        assert output[1].data["tool"] == "bash"

    @pytest.mark.asyncio
    async def test_read_stream_two_markers_newline_separated(self):
        """F6 (pipeline REAL): dos marcadores separados por '\\n' atraviesan
        `_read_stream` y producen 2 TOOL_USE; el texto intermedio es solo
        whitespace. Este es el caso que los tests sintéticos no cubrían."""
        from acople.bridge import Acople, EventType

        marker1 = '<acople-tool>{"name":"search","arguments":{"q":"a"}}</acople-tool>'
        marker2 = '<acople-tool>{"name":"read","arguments":{"path":"/x"}}</acople-tool>'
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": f"{marker1}\n{marker2}"}]},
        })

        class _FakeStdout:
            def __init__(self, chunks):
                self._chunks = list(chunks)

            async def read(self, n):
                return self._chunks.pop(0) if self._chunks else b''

        with patch('acople.bridge.shutil.which', return_value="claude"):
            bridge = Acople(agent="claude")
            proc = MagicMock()
            proc.pid = 7
            proc.returncode = None
            proc.stdout = _FakeStdout([
                line.encode() + b'\n',
                b'{"type":"result","subtype":"success","result":"ok"}\n',
                b'',
            ])
            proc.stderr = AsyncMock()
            proc.stderr.read.return_value = b''

            events = [e async for e in bridge._read_stream(proc)]

        tool_uses = [e for e in events if e.type == EventType.TOOL_USE]
        assert [t.data["tool"] for t in tool_uses] == ["search", "read"]
        # Entre las dos tools solo puede haber tokens whitespace.
        between = [
            e.data.get("text", "") for e in events
            if e.type == EventType.TOKEN
        ]
        assert all(not t.strip() for t in between), f"texto inesperado: {between!r}"

    @pytest.mark.asyncio
    async def test_structured_tool_use_preserved(self):
        """Non-TOKEN event (structured TOOL_USE) se preserva y buffer se flusha antes."""
        from acople import BridgeEvent, EventType
        from acople.normalize import parse_plain_tool_markers

        text_marker_buffer = "<acople-tool>{\"name\":\"bash\",\"arguments\":{\"cmd\":\"ls\"}}</acople-tool>"
        output = []

        if text_marker_buffer:
            marker_events, text_marker_buffer = parse_plain_tool_markers(
                text_marker_buffer, final=True
            )
            for kind, data in marker_events:
                if kind == "tool_use":
                    output.append(BridgeEvent(EventType.TOOL_USE, data))
                else:
                    output.append(BridgeEvent(EventType.TOKEN, data))

        structured = BridgeEvent(EventType.TOOL_USE, {"tool": "Write", "input": {"path": "a.py"}})
        output.append(structured)

        assert len(output) == 2
        assert output[0].data["tool"] == "bash"
        assert output[1].data["tool"] == "Write"


# ============================================================================
# Forced termination tests (_unified_chat_workflow)
# ============================================================================

class TestForcedTermination:
    """Condición de parada forzada en modo proxy."""

    @pytest.mark.asyncio
    async def test_termination_on_token_after_tool_use(self):
        """AC1.5 Tras tool del cliente, TOKEN → forced terminate con DONE."""
        from acople import BridgeEvent, EventType

        tool_call = BridgeEvent(EventType.TOOL_USE, {
            "tool": "bash", "input": {"cmd": "ls"}
        })
        followup_token = BridgeEvent(EventType.TOKEN, {"text": "I will also..."})

        events = list(await self._run_termination_scenario(
            events=[tool_call, followup_token],
            registered_tools={"bash"},
        ))

        assert len(events) == 2
        assert events[0].type == EventType.TOOL_USE
        assert events[0].data["tool"] == "bash"
        # Second event should be synthetic DONE (forced termination)
        assert events[1].type == EventType.DONE

    @pytest.mark.asyncio
    async def test_termination_on_native_tool_after_client_tool(self):
        """Tras tool del cliente, tool nativa → forced terminate."""
        from acople import BridgeEvent, EventType

        events = list(await self._run_termination_scenario(
            events=[
                BridgeEvent(EventType.TOOL_USE, {"tool": "search", "input": {"q": "test"}}),
                BridgeEvent(EventType.TOOL_USE, {"tool": "bash", "input": {"cmd": "ls"}}),
            ],
            registered_tools={"search"},
        ))

        assert len(events) == 2
        assert events[0].data["tool"] == "search"
        assert events[1].type == EventType.DONE

    @pytest.mark.asyncio
    async def test_no_termination_without_proxy_mode(self):
        """Sin tools registradas → no hay terminación forzada."""
        from acople import BridgeEvent, EventType

        events = list(await self._run_termination_scenario(
            events=[
                BridgeEvent(EventType.TOOL_USE, {"tool": "bash", "input": {"cmd": "ls"}}),
                BridgeEvent(EventType.TOKEN, {"text": "Done"}),
                BridgeEvent(EventType.DONE, {}),
            ],
            registered_tools=set(),  # no client tools registered
        ))

        # All events pass through unchanged
        assert len(events) == 3
        assert events[0].type == EventType.TOOL_USE
        assert events[1].type == EventType.TOKEN
        assert events[2].type == EventType.DONE

    @pytest.mark.asyncio
    async def test_no_termination_without_client_tool(self):
        """Proxy mode pero sin tool del cliente → normal (degradación)."""
        from acople import BridgeEvent, EventType

        events = list(await self._run_termination_scenario(
            events=[
                BridgeEvent(EventType.TOKEN, {"text": "I can answer without tools"}),
                BridgeEvent(EventType.DONE, {}),
            ],
            registered_tools={"bash"},
        ))

        assert len(events) == 2
        assert events[0].type == EventType.TOKEN
        assert events[1].type == EventType.DONE

    @pytest.mark.asyncio
    async def test_multiple_tool_uses_before_termination(self):
        """F6: Varias tools en un turno → se emiten todas, luego terminate."""
        from acople import BridgeEvent, EventType

        events = list(await self._run_termination_scenario(
            events=[
                BridgeEvent(EventType.TOOL_USE, {"tool": "search", "input": {"q": "a"}}),
                BridgeEvent(EventType.TOOL_USE, {"tool": "read", "input": {"path": "/x"}}),
                BridgeEvent(EventType.TOKEN, {"text": "extra"}),
            ],
            registered_tools={"search", "read"},
        ))

        assert len(events) == 3
        assert events[0].data["tool"] == "search"
        assert events[1].data["tool"] == "read"
        assert events[2].type == EventType.DONE

    @pytest.mark.asyncio
    async def test_whitespace_token_between_client_tools_not_terminating(self):
        """F6: el separador whitespace entre dos tools del cliente NO dispara
        la terminación forzada — ambas tools se emiten."""
        from acople import BridgeEvent, EventType

        events = list(await self._run_termination_scenario(
            events=[
                BridgeEvent(EventType.TOOL_USE, {"tool": "search", "input": {"q": "a"}}),
                BridgeEvent(EventType.TOKEN, {"text": "\n"}),
                BridgeEvent(EventType.TOOL_USE, {"tool": "read", "input": {"path": "/x"}}),
                BridgeEvent(EventType.TOKEN, {"text": "now I'll continue..."}),
            ],
            registered_tools={"search", "read"},
        ))

        # search, read, DONE sintético — el "\n" se descarta, el texto real corta.
        assert [e.type for e in events] == [
            EventType.TOOL_USE, EventType.TOOL_USE, EventType.DONE,
        ]
        assert events[0].data["tool"] == "search"
        assert events[1].data["tool"] == "read"

    @pytest.mark.asyncio
    async def test_native_tools_filtered_before_client_tool(self):
        """I4: Tools nativas antes de tool del cliente se filtran, no emiten."""
        from acople import BridgeEvent, EventType

        events = list(await self._run_termination_scenario(
            events=[
                BridgeEvent(EventType.TOOL_USE, {"tool": "bash", "input": {"cmd": "ls"}}),
                BridgeEvent(EventType.TOOL_USE, {"tool": "search", "input": {"q": "hello"}}),
                BridgeEvent(EventType.DONE, {}),
            ],
            registered_tools={"search"},
        ))

        # Native bash filtered out, search passes, DONE passes (no termination needed)
        assert len(events) == 2
        assert events[0].data["tool"] == "search"
        assert events[1].type == EventType.DONE

    async def _run_termination_scenario(
        self, events, registered_tools
    ):
        """Run _unified_chat_workflow with mocked Acople.run yielding `events`.

        Only tests the termination logic — the workflow is called with minimal
        setup (stateless, no system, single user message).
        """
        from acople import BridgeEvent, EventType
        from acople.server import _unified_chat_workflow

        # Compute registered_tool_names from the set of tool names.
        # Build a tools list in OpenAI format.
        tools = None
        if registered_tools:
            tools = [
                {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
                for name in registered_tools
            ]

        # Patch Acople.run to yield our pre-defined events.
        async def mock_run(**kwargs):
            for ev in events:
                yield ev

        with patch("acople.server.Acople") as MockAcople:
            mock_instance = MagicMock()
            mock_instance.agent = "claude"
            mock_instance.config.stream_format = "json"
            MockAcople.return_value = mock_instance

            # We need config and run to be accessible
            from acople import get_config
            mock_instance.config = get_config("claude")

            mock_instance.run = mock_run

            collected = []
            async for event in _unified_chat_workflow(
                messages=[{"role": "user", "content": "hello"}],
                agent_name="claude",
                tools=tools,
                stateful=False,
            ):
                collected.append(event)

        return collected


# ============================================================================
# Golden test (I1) — byte-stable JSON event sequence
# ============================================================================

class TestGoldenMarkerSequence:
    """Secuencia byte-estable para verificar que el pipeline de marcadores
    produce eventos deterministas.
    """

    @pytest.mark.asyncio
    async def test_golden_marker_parse_sequence(self):
        """Input JSON conocido → secuencia exacta de eventos."""
        from acople import BridgeEvent, EventType
        from acople.normalize import parse_plain_tool_markers

        buffer = (
            'Pensando... '
            '<acople-tool>{"name":"bash","arguments":{"cmd":"ls -la"}}</acople-tool>'
            ' y luego '
        )
        events, _ = parse_plain_tool_markers(buffer, final=True)

        assert len(events) == 3
        assert events[0] == ("token", {"text": "Pensando... "})
        assert events[1] == ("tool_use", {"tool": "bash", "input": {"cmd": "ls -la"}})
        assert events[2] == ("token", {"text": " y luego "})
