"""
Acople — Capa de normalización agnóstica a agentes.

Filosofía:
    Acople traduce entre los formatos del cable y el formato interno común.
    Los clientes hablan dialectos distintos; aquí se aplanan.

Dialectos soportados de entrada (mensajes):
    - OpenAI / Ollama:  role=tool con tool_call_id; assistant.tool_calls
    - Anthropic:        content = [{"type":"tool_use"|"tool_result", ...}]
    - Gemini:           role=model|function; parts=[{text|function_call|function_response}]

Dialectos soportados de entrada (definiciones de tools):
    - OpenAI:    {"type":"function", "function":{...}}
    - Anthropic: {"name", "description", "input_schema"}
    - Gemini:    {"function_declarations":[{...}]}
    - Ollama:    igual que OpenAI

Agentes plain-text (Gemini CLI, Codex, OpenCode, Kilo, Qwen):
    No tienen formato propio de wire. Emiten texto. Acople les inyecta un
    catálogo en el prompt y les pide usar el marker <acople-tool>...</acople-tool>;
    bridge.py lo parsea como BridgeEvent.TOOL_USE.

Formato interno (alineado con session.VALID_ROLES):
    {"role": "system" | "user" | "assistant" | "tool_use" | "tool_result",
     "content": str}

    Para tool_use y tool_result, content es un JSON serializado:
        tool_use:    {"id": str|None, "name": str, "input": dict}
        tool_result: {"tool_call_id": str, "output": str}
"""

import json
import logging

logger = logging.getLogger("acople.normalize")

# Marker estandarizado para agentes plain-text (no Claude stream-json).
# Cualquier agente puede emitirlo y bridge.py lo parsea como TOOL_USE.
TOOL_OPEN = "<acople-tool>"
TOOL_CLOSE = "</acople-tool>"

TOOL_CATALOG_INSTRUCTIONS = (
    "When you need to call a tool, emit exactly one block per call:\n"
    f'{TOOL_OPEN}{{"name": "<tool_name>", "arguments": {{...}}}}{TOOL_CLOSE}\n'
    "Use only registered tool names. Do not wrap the block in code fences."
)


def _safe_json(value, default=None):
    """Parse JSON tolerantly. Accepts dict (passthrough), str, or None."""
    if value is None:
        return default if default is not None else {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default if default is not None else {}
    return default if default is not None else {}


def _flatten_text(content) -> str:
    """Reduce un campo content (str | list de bloques) a texto plano."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return str(content)


def _gemini_role(role: str) -> str:
    """Mapea roles de Gemini al vocabulario interno."""
    if role == "model":
        return "assistant"
    if role == "function":
        return "tool_result"
    return role


def _handle_gemini_parts(parts: list, role: str) -> tuple[list[str], list[dict]]:
    """Extrae texto + tool_use/tool_result desde parts[] de Gemini."""
    text_parts: list[str] = []
    tail: list[dict] = []
    for part in parts:
        if isinstance(part, str):
            text_parts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        if "text" in part:
            text_parts.append(part["text"] or "")
        elif "function_call" in part:
            fc = part["function_call"] or {}
            payload = {
                "id": fc.get("id") or fc.get("name"),
                "name": fc.get("name", ""),
                "input": fc.get("args") or fc.get("arguments") or {},
            }
            tail.append({
                "role": "tool_use",
                "content": json.dumps(payload, ensure_ascii=False),
            })
        elif "function_response" in part:
            fr = part["function_response"] or {}
            response = fr.get("response")
            if isinstance(response, dict):
                output_text = json.dumps(response, ensure_ascii=False)
            else:
                output_text = _flatten_text(response)
            payload = {
                "tool_call_id": fr.get("id") or fr.get("name", ""),
                "output": output_text,
            }
            tail.append({
                "role": "tool_result",
                "content": json.dumps(payload, ensure_ascii=False),
            })
    return text_parts, tail


def normalize_incoming_messages(messages: list[dict]) -> list[dict]:
    """Convierte mensajes en cualquier formato externo → formato interno Acople.

    Reconoce:
    - OpenAI tool result:       {"role":"tool", "tool_call_id":..., "content":...}
    - OpenAI tool call:         {"role":"assistant", "tool_calls":[...]}
    - Anthropic content blocks: content = [{"type":"tool_use"|"tool_result", ...}]
    - Gemini parts:             {"role":"model"|"function"|"user", "parts":[
                                  {"text":...} | {"function_call":...} | {"function_response":...}]}
    - Mensajes de texto plano (passthrough)
    """
    out: list[dict] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        raw_role = m.get("role") or ""
        content = m.get("content")
        parts = m.get("parts")

        # OpenAI: tool result
        if raw_role == "tool":
            payload = {
                "tool_call_id": m.get("tool_call_id", ""),
                "output": _flatten_text(content),
            }
            out.append({
                "role": "tool_result",
                "content": json.dumps(payload, ensure_ascii=False),
            })
            continue

        # OpenAI: assistant con tool_calls
        if raw_role == "assistant" and m.get("tool_calls"):
            text = _flatten_text(content)
            if text:
                out.append({"role": "assistant", "content": text})
            for tc in m["tool_calls"] or []:
                fn = tc.get("function") or {}
                payload = {
                    "id": tc.get("id"),
                    "name": fn.get("name", ""),
                    "input": _safe_json(fn.get("arguments")),
                }
                out.append({
                    "role": "tool_use",
                    "content": json.dumps(payload, ensure_ascii=False),
                })
            continue

        # Gemini: estructura con parts[]
        if isinstance(parts, list):
            mapped_role = _gemini_role(raw_role) or "user"
            text_parts, tail = _handle_gemini_parts(parts, mapped_role)
            if text_parts:
                joined = "\n".join(p for p in text_parts if p)
                if joined:
                    # role=function en Gemini = solo function_response, no texto
                    base_role = "user" if mapped_role == "tool_result" else mapped_role
                    out.append({"role": base_role, "content": joined})
            out.extend(tail)
            continue

        # Anthropic: content como lista de bloques
        if isinstance(content, list):
            text_parts: list[str] = []
            tail: list[dict] = []
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                    continue
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    payload = {
                        "id": block.get("id"),
                        "name": block.get("name", ""),
                        "input": block.get("input") or {},
                    }
                    tail.append({
                        "role": "tool_use",
                        "content": json.dumps(payload, ensure_ascii=False),
                    })
                elif btype == "tool_result":
                    payload = {
                        "tool_call_id": block.get("tool_use_id") or block.get("tool_call_id", ""),
                        "output": _flatten_text(block.get("content")),
                    }
                    tail.append({
                        "role": "tool_result",
                        "content": json.dumps(payload, ensure_ascii=False),
                    })
            if text_parts:
                joined = "\n".join(p for p in text_parts if p)
                if joined:
                    out.append({"role": raw_role or "user", "content": joined})
            out.extend(tail)
            continue

        # Texto plano (passthrough con string garantizado)
        if raw_role:
            out.append({"role": _gemini_role(raw_role), "content": _flatten_text(content)})
    return out


def _iter_tool_specs(tools: list[dict]):
    """Aplana definiciones de tools de cualquier dialecto a (name, desc, params).

    Soporta:
    - OpenAI:    {"type":"function", "function":{"name","description","parameters"}}
    - Anthropic: {"name","description","input_schema"}
    - Gemini:    {"function_declarations":[{"name","description","parameters"},...]}
    - Gemini suelto / Ollama: {"name","description","parameters"}
    """
    for t in tools or []:
        if not isinstance(t, dict):
            continue

        # Gemini: wrapper function_declarations
        if "function_declarations" in t:
            for decl in t.get("function_declarations") or []:
                if not isinstance(decl, dict):
                    continue
                name = decl.get("name", "")
                if not name:
                    continue
                yield (
                    name,
                    decl.get("description", "") or "",
                    decl.get("parameters") or {},
                )
            continue

        # OpenAI: wrapper {type:"function", function:{...}}
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            fn = t["function"]
            name = fn.get("name", "")
            if not name:
                continue
            yield (
                name,
                fn.get("description", "") or "",
                fn.get("parameters") or {},
            )
            continue

        # Anthropic / Gemini suelto / Ollama: forma plana
        name = t.get("name", "")
        if not name:
            continue
        yield (
            name,
            t.get("description", "") or "",
            t.get("parameters") or t.get("input_schema") or {},
        )


def format_tool_catalog(tools: list[dict] | None) -> str:
    """Serializa definiciones de tools (cualquier dialecto) para inyectar en el prompt.

    El agente las verá como contexto. Si emite el marker estandarizado,
    bridge.py lo convertirá en BridgeEvent(TOOL_USE). Claude Code, que ya emite
    tool_use estructurado en stream-json, también funciona sin marker.
    """
    if not tools:
        return ""
    lines = []
    for name, desc, params in _iter_tool_specs(tools):
        lines.append(f"- {name}: {desc}")
        if params:
            lines.append(f"  parameters: {json.dumps(params, ensure_ascii=False)}")
    if not lines:
        return ""
    return (
        "AVAILABLE TOOLS (registered by the calling client):\n"
        + "\n".join(lines)
        + "\n\n"
        + TOOL_CATALOG_INSTRUCTIONS
    )


def parse_plain_tool_markers(
    buffer: str,
    final: bool = False,
) -> tuple[list[tuple[str, dict]], str]:
    """Scanner incremental de <acople-tool>...</acople-tool> en streams plain-text.

    Args:
        buffer: texto acumulado pendiente de scanear.
        final: si True, vacía todo (incluyendo tags sin cerrar como texto).

    Returns:
        (events, remaining_buffer) donde cada event es:
            ("token", {"text": str})   — texto fuera de markers
            ("tool_use", {"tool": str, "input": dict})

    Conserva una "cola" de seguridad cuando final=False, para no cortar
    una etiqueta a mitad entre chunks.
    """
    events: list[tuple[str, dict]] = []
    while buffer:
        open_idx = buffer.find(TOOL_OPEN)

        if open_idx == -1:
            if final:
                events.append(("token", {"text": buffer}))
                buffer = ""
            else:
                # Reservamos al final un margen del tamaño de TOOL_OPEN-1
                # por si el chunk se cortó en mitad de la etiqueta.
                safe_len = len(buffer) - len(TOOL_OPEN) + 1
                if safe_len > 0:
                    events.append(("token", {"text": buffer[:safe_len]}))
                    buffer = buffer[safe_len:]
            break

        # Emitir texto previo a la etiqueta
        if open_idx > 0:
            events.append(("token", {"text": buffer[:open_idx]}))
            buffer = buffer[open_idx:]

        close_idx = buffer.find(TOOL_CLOSE)
        if close_idx == -1:
            if final:
                # Etiqueta abierta sin cerrar: tratamos como texto literal
                events.append(("token", {"text": buffer}))
                buffer = ""
            # Si no es final, esperamos más datos
            break

        payload_str = buffer[len(TOOL_OPEN):close_idx].strip()
        buffer = buffer[close_idx + len(TOOL_CLOSE):]

        try:
            payload = json.loads(payload_str)
        except (json.JSONDecodeError, ValueError):
            logger.debug("Malformed acople-tool payload: %s", payload_str[:80])
            events.append(("token", {"text": f"{TOOL_OPEN}{payload_str}{TOOL_CLOSE}"}))
            continue

        name = payload.get("name", "unknown")
        args = payload.get("arguments")
        if args is None:
            args = payload.get("input") or {}
        events.append(("tool_use", {"tool": name, "input": args}))

    return events, buffer
