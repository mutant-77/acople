"""
Acople HTTP Server - SSE API para conectar apps con agentes

Endpoints:
    GET  /agents      → lista agentes disponibles
    GET  /models     → lista modelos disponibles
    GET  /detect     → auto-detecta setup completo
    GET  /diagnose   → diagnostics + soluciones
    POST /chat       → streaming SSE (full)
    POST /chat/simple → streaming SSE (prompt only)
    POST /interrupt  → interrumpe generación
    GET  /health    → health check
    POST /v1/chat/completions → OpenAI compatibility layer
    GET  /v1/models → OpenAI models list
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from acople import (
    AGENT_CONFIGS,
    Acople,
    AgentNotFoundError,
    BridgeEvent,
    EventType,
    detect_agent,
    detect_all_agents,
    detect_models,
    process_system_messages,
    resolve_session_id,
)
from acople.image_bridge import ImageBridge, ImageConfig
from acople.normalize import format_tool_catalog, normalize_incoming_messages
from acople.security import (
    ValidationError,
    validate_agent_name,
    validate_cwd,
    validate_image_n,
    validate_image_output_format,
    validate_image_quality,
    validate_image_size,
    validate_prompt,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("AcopleServer")


def _normalize_content(content) -> str:
    """Normaliza el contenido de un mensaje (soporta texto plano y bloques de Claude)."""
    if isinstance(content, list):
        text = ""
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text += block.get("text", "") + "\n"
                elif block.get("type") == "image_url":
                    text += "[image]\n"
                else:
                    text += json.dumps(block, ensure_ascii=False) + "\n"
            elif isinstance(block, str):
                text += block + "\n"
        return text.strip()
    
    # Normalización básica para strings
    s = str(content).strip()
    s = s.replace("\r\n", "\n")
    return s

def _estimate_tokens(text: str) -> int:
    """Estima tokens de un texto (approx 4 chars/token)."""
    return max(1, len(text) // 4)


def _openai_error(message: str, type_: str = "server_error", code: str | None = None) -> dict:
    """Forma canónica de error OpenAI (I9)."""
    return {"error": {"message": message, "type": type_, "param": None, "code": code}}


def _tool_use_to_openai(event_data: dict, index: int) -> dict:
    """Convert a BridgeEvent(TOOL_USE) payload into OpenAI tool_call shape."""
    args = event_data.get("input", {})
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False)
    return {
        "index": index,
        "id": "call_" + uuid.uuid4().hex[:24],
        "type": "function",
        "function": {
            "name": event_data.get("tool", "unknown"),
            "arguments": args,
        },
    }


def _balanced_json_candidates(text: str) -> list[str]:
    """Encuentra todos los valores JSON balanceados ({...} o [...]) de nivel
    superior dentro del texto, respetando strings y escapes.

    Funciona aunque el JSON venga envuelto en prosa o fences markdown, y
    aunque existan fragmentos de llaves sueltos en el texto (ej: prosa como
    ``formato { ciudad, pais }``): cada candidato se devuelve por separado
    para que el llamador elija el que realmente parsea.
    """
    candidates: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch not in "{[":
            i += 1
            continue
        opener, closer = (ch, "}") if ch == "{" else (ch, "]")
        depth = 0
        in_string = False
        escape = False
        end = -1
        for j in range(i, n):
            c = text[j]
            if escape:
                escape = False
                continue
            if c == "\\" and in_string:
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end != -1:
            candidates.append(text[i:end + 1])
            i = end + 1
        else:
            break  # apertura sin cierre: no hay más candidatos completos
    return candidates


def _extract_json_payload(text: str) -> str:
    """Devuelve JSON limpio a partir de la respuesta cruda de un agente.

    Tolera fences de código markdown y prosa alrededor del JSON. Recoge todos
    los valores balanceados del texto y devuelve, ya normalizado, el mayor que
    parsee como JSON válido. Si nada parsea, devuelve el texto original
    (recortado) sin cambios.
    """
    if not text:
        return text

    best: str | None = None
    best_len = -1
    for cand in _balanced_json_candidates(text):
        try:
            parsed = json.loads(cand)
        except Exception:
            continue
        if len(cand) > best_len:
            best = json.dumps(parsed, ensure_ascii=False)
            best_len = len(cand)

    return best if best is not None else text.strip()


def _render_queue(queue: list[dict]) -> str:
    """Renderiza la cola desde el último user para agentes plain-text.

    - user: contenido crudo
    - assistant: contenido crudo
    - tool_use: ``[Llamaste a "{name}" con: {input}]``
    - tool_result: ``[Resultado de "{name}": {output}]`` (correlacionado por
      ``tool_call_id`` → ``name`` de los ``tool_use`` en la misma cola)
    """
    id_to_name: dict[str, str] = {}
    for m in queue:
        if m.get("role") == "tool_use":
            try:
                data = json.loads(m.get("content", "{}"))
                tid = data.get("id")
                if tid:
                    id_to_name[tid] = data.get("name", "")
            except (json.JSONDecodeError, TypeError):
                pass

    parts: list[str] = []
    for m in queue:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "user":
            parts.append(content)
        elif role == "assistant":
            parts.append(content)
        elif role == "tool_use":
            try:
                data = json.loads(content)
                name = data.get("name", "?")
                inp = data.get("input", {})
                inp_str = json.dumps(inp, ensure_ascii=False)
                parts.append(f'[Llamaste a "{name}" con: {inp_str}]')
            except (json.JSONDecodeError, TypeError):
                parts.append(content)
        elif role == "tool_result":
            try:
                data = json.loads(content)
                tid = data.get("tool_call_id", "")
                output = data.get("output", "")
                name = id_to_name.get(tid, tid)
                parts.append(f'[Resultado de "{name}": {output}]')
            except (json.JSONDecodeError, TypeError):
                parts.append(content)
    return "\n".join(parts)


def _build_agent_prompt(
    *,
    messages: list[dict],
    compiled_prompt: str,
    sys_prompt_text: str,
    tool_catalog: str,
    stream_format: str,
    tool_choice: str | dict | None = None,
) -> str:
    """Construye el prompt final que recibe el agente subyacente.

    - stream_format == "json" (claude): usamos ``compiled_prompt`` completo; el
      compactor ya le embebe system + historial y el CLI lo maneja bien.
    - resto (opencode, gemini, codex, kilo, qwen): el ``compiled_prompt`` trae
      prefijos "User:"/"Assistant:" y todo el historial, lo que confunde al
      modelo (responde al contexto histórico en vez de a la tarea actual). Le
      enviamos la cola desde el último mensaje ``user`` hasta el final: ahí
      pueden venir ``tool_use`` y ``tool_result`` del turno actual. El system
      se conserva porque ahí viven las instrucciones de la tarea y el esquema.
    """
    if stream_format == "json":
        return compiled_prompt

    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    if last_user_idx == -1:
        # Sin user no hay cola que renderizar. compiled_prompt ya trae system,
        # catálogo y hint inyectados aguas arriba: devolverlo tal cual evita
        # duplicar el catálogo al re-añadir el head.
        return compiled_prompt

    body = _render_queue(messages[last_user_idx:])

    choice_hint = ""
    if tool_catalog:
        if isinstance(tool_choice, str) and tool_choice in ("required", "auto", "none"):
            choice_hint = f"\nTool selection policy: {tool_choice}."
        elif isinstance(tool_choice, dict) and tool_choice.get("function", {}).get("name"):
            choice_hint = f"\nThe client requests you call: {tool_choice['function']['name']}."

    head_parts: list[str] = []
    if sys_prompt_text:
        head_parts.append(sys_prompt_text)
    if tool_catalog:
        head_parts.append(tool_catalog.rstrip("\n"))
    if choice_hint:
        head_parts.append(choice_hint.strip())
    head = "\n\n".join(head_parts)
    return f"{head}\n\n{body}" if head else body


_DEFAULT_AGENT: str | None = None
_session_manager = None
ACTIVE_PROCESSES: dict[str, asyncio.subprocess.Process] = {}
# Mapa process_pid (uuid interno) → session_id de la request que lo lanzó.
# Permite que /interrupt?session_id=... siga funcionando tras el desacople
# de process_pid (F10): el cliente conoce su session_id, no el uuid interno.
PROCESS_SESSIONS: dict[str, str | None] = {}
MAX_CONCURRENT = int(os.environ.get("ACOPLE_MAX_CONCURRENT", "5"))

# Loop-guard state: per-session record of the last emitted tool sequence.
# Key = session_id, value = list of (tool, input) pairs from the previous turn.
# Acotado a _MAX_TOOL_HISTORY_SESSIONS entradas (evicción FIFO) para que el
# dict no crezca sin límite con session_ids de un solo uso.
_session_tool_history: dict[str, list[tuple[str, str]]] = {}
_MAX_TOOL_HISTORY_SESSIONS = 256


def _tool_use_key(event_data: dict) -> tuple[str, str]:
    """Normalize a tool_use event into a comparable (tool, input) pair."""
    tool = event_data.get("tool", "")
    inp = event_data.get("input", {})
    inp_str = json.dumps(inp, sort_keys=True, ensure_ascii=False)
    return (tool, inp_str)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _DEFAULT_AGENT, _session_manager
    try:
        _DEFAULT_AGENT = detect_agent()
        if _DEFAULT_AGENT:
            logger.info(f"[OK] Agente detectado: {_DEFAULT_AGENT}")
        else:
            logger.warning("[--] Ningun agente en PATH")

        if os.environ.get("ACOPLE_SESSIONS", "true").lower() not in ("false", "0", "no"):
            from acople.session import SessionManager
            
            # Localized & Ephemeral Logic:
            # 1. Usar .acople/sessions.db en el CWD actual
            local_db_dir = Path.cwd() / ".acople"
            local_db_path = local_db_dir / "sessions.db"
            
            # 2. Borrar en cada arranque para pizarra limpia
            if local_db_dir.exists():
                logger.info(f"Limpiando memoria previa en {local_db_dir}...")
                import shutil
                try:
                    shutil.rmtree(local_db_dir)
                except Exception as e:
                    logger.warning(f"No se pudo limpiar .acople: {e}")
            
            local_db_dir.mkdir(parents=True, exist_ok=True)
            _session_manager = SessionManager(local_db_path)
            logger.info(f"[OK] Memoria local efímera activada en: {local_db_path}")
    except Exception as e:
        logger.error(f"Error inicializando: {e}", exc_info=True)
        _session_manager = None
    yield
    if _session_manager:
        _session_manager.cleanup_expired(max_age_days=7)
        _session_manager.close()
        _session_manager = None


API_KEY = os.environ.get("ACOPLE_API_KEY")

async def verify_api_key(request: Request):
    if not API_KEY:
        return  # No key configured = no auth (local dev)

    # Check X-API-Key header, api_key query param, or Authorization Bearer token
    key = request.headers.get("X-API-Key") or request.query_params.get("api_key")

    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        key = auth_header.split(" ", 1)[1]

    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

app = FastAPI(
    title="Acople",
    description="Universal bridge to IDE AI agents",
    version="1.3.0",
    lifespan=lifespan,
    dependencies=[Depends(verify_api_key)],
)

_cors_origin_regex = os.environ.get("ACOPLE_CORS_ORIGINS", r"^https?://localhost(:\d+)?$")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_cors_origin_regex,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization", "X-Session-ID", "X-Session-Options", "X-Acople-Cwd"],
)


class ChatRequest(BaseModel):
    prompt: str
    system: str | None = None
    cwd: str | None = None
    agent: str | None = None
    model: str | None = None
    timeout: float | None = None
    session_id: str | None = None
    tools: list[dict] | None = None
    tool_choice: str | dict | None = None


class SimpleChatRequest(BaseModel):
    prompt: str
    agent: str | None = None


class ImageGenerateRequest(BaseModel):
    prompt: str
    size: str = "auto"
    quality: str = "auto"
    n: int = 1
    output_format: str = "png"


@app.get("/agents")
def list_agents():
    """Lista todos los agentes."""
    result = detect_all_agents()
    return {"agents": result, "active": _DEFAULT_AGENT}


@app.get("/agent")
def active_agent():
    """Agente activo."""
    if not _DEFAULT_AGENT:
        raise HTTPException(503, "Ningún agente disponible")
    return {"agent": _DEFAULT_AGENT}


@app.get("/models")
async def list_models(agent: str | None = None):
    """Lista modelos del agente. NOTE: No todos los agentes soportan --list-models."""
    target_agent = agent or _DEFAULT_AGENT

    models = await detect_models(target_agent) if target_agent else []
    return {"agent": target_agent, "models": models}


@app.get("/detect")
def detect():
    """Auto-detecta setup completo."""
    agents = detect_all_agents()
    return {
        "agents": agents,
        "active": _DEFAULT_AGENT,
        "server": "ok",
    }


@app.get("/ui", response_class=HTMLResponse)
def get_ui():
    """Sirve la interfaz de pruebas (HTML)."""
    ui_path = os.path.join(os.path.dirname(__file__), "ui.html")
    if not os.path.exists(ui_path):
        raise HTTPException(status_code=404, detail="ui.html not found")
    with open(ui_path, encoding="utf-8") as f:
        return f.read()


@app.get("/diagnose")
def diagnose():
    """Diagnostics y soluciones."""
    agents = detect_all_agents()
    issues = []
    solutions = []

    installed = [a for a, ok in agents.items() if ok]

    if not installed:
        issues.append("Ningún agente instalado")
        solutions.extend([
            "Claude Code: npm i -g @anthropic-ai/claude-code",
            "Gemini CLI: npm i -g @google/gemini-cli",
            "Codex CLI: npm i -g @openai/codex",
            "OpenCode: npm i -g opencode",
            "Kilo: npm i -g kilo",
            "Qwen: pip install qwen-agent",
        ])

    if _DEFAULT_AGENT:
        status = "ok"
    else:
        status = "no_agent"
        issues.append("Agente no inicializado")

    return {
        "status": status,
        "issues": issues,
        "solutions": solutions,
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    """Chat avanzado usando el workflow unificado (Pipeline Senior)."""
    if len(ACTIVE_PROCESSES) >= MAX_CONCURRENT:
        raise HTTPException(status_code=429, detail=f"Max {MAX_CONCURRENT} concurrent sessions")

    agent_name = req.agent or _DEFAULT_AGENT
    if not agent_name:
        raise HTTPException(status_code=400, detail="No agent available")

    # Adaptar ChatRequest a formato de lista de mensajes
    messages = [{"role": "user", "content": req.prompt}]
    if req.system:
        messages.insert(0, {"role": "system", "content": req.system})

    workflow = _unified_chat_workflow(
        messages=messages,
        agent_name=agent_name,
        session_id=req.session_id,
        cwd=req.cwd,
        model=req.model,
        tools=req.tools,
        tool_choice=req.tool_choice,
    )

    async def chat_sse():
        async for event in workflow:
            yield event.to_sse()

    return StreamingResponse(
        chat_sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# OpenAI Compatibility Layer (Shim)
# ---------------------------------------------------------------------------

@app.get("/v1/models")
async def list_openai_models():
    """OpenAI-compatible models list."""
    agents = detect_all_agents()
    data = []
    for name, installed in agents.items():
        if installed:
            data.append({
                "id": name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "acople"
            })
    return {"object": "list", "data": data}


def _get_max_history(request: Request) -> int:
    """Lee max_history de header X-Session-Options."""
    options = request.headers.get("X-Session-Options", "")
    for opt in options.split(","):
        opt = opt.strip()
        if opt.startswith("max_history="):
            try:
                return max(1, min(100, int(opt.split("=", 1)[1])))
            except ValueError:
                pass
    return 10


async def _unified_chat_workflow(
    messages: list[dict],
    agent_name: str,
    session_id: str | None = None,
    cwd: str | None = None,
    max_history: int = 10,
    model: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    stateful: bool = True,
) -> AsyncIterator[BridgeEvent]:
    """
    Workflow unificado (Pipeline Senior) para el manejo de chats.
    Centraliza: Normalización, Identidad, Memoria, Ejecución y Persistencia.

    stateful: si False (clientes OpenAI-compat) NO se usa memoria por carpeta;
    cada request es autónomo salvo que traiga un ``session_id`` explícito. Si
    True (clientes nativos /chat, p.ej. la UI) se mantiene la persistencia
    automática por carpeta.
    """
    # 0. Normalización agnóstica: OpenAI/Anthropic/Ollama → formato interno.
    messages = normalize_incoming_messages(messages)

    # 1. Normalización de Identidad y CWD
    sys_prompt_text, extracted_cwd = process_system_messages(messages)
    effective_cwd = cwd or extracted_cwd

    # 1.b Colapsar múltiples system messages en uno solo. La sesión (Compactor)
    # y el compilado asumen un único system por sesión: si llegan varios (p.ej.
    # la directiva JSON que añade el endpoint OpenAI + el system del cliente),
    # sync_new_messages borra-y-reinserta por cada uno y solo sobrevive el
    # último, perdiendo instrucciones. Fusionamos antes de persistir/compilar.
    non_system = [m for m in messages if m.get("role") != "system"]
    if sys_prompt_text:
        messages = [{"role": "system", "content": sys_prompt_text}, *non_system]
    else:
        messages = non_system

    # 2. Resolución de Sesión / Memoria.
    # Solo persistimos+replayamos historial cuando hay identidad estable: un
    # session_id explícito (p.ej. header X-Session-ID) o un cliente nativo
    # (stateful=True). Sin eso, modo stateless: el prompt se arma solo desde
    # los mensajes entrantes y peticiones independientes no se contaminan.
    final_session_id = session_id
    compiled_prompt = ""
    use_memory = bool(_session_manager) and (final_session_id is not None or stateful)

    if use_memory:
        if not final_session_id:
            # Fallback a CWD-based ID para persistencia automática por carpeta
            from acople import resolve_session_id
            final_session_id = resolve_session_id({}, messages, agent=agent_name, cwd=effective_cwd)

        _session_manager.get_or_create(final_session_id)

        # Actualizar metadatos del proyecto
        metadata = {"agent": agent_name, "model": model or agent_name}
        if effective_cwd:
            metadata["cwd"] = effective_cwd
            metadata["project_hash"] = hashlib.md5(effective_cwd.encode()).hexdigest()[:12]
        _session_manager.update_metadata(final_session_id, **metadata)

        # Sincronizar e Historial (Compactor)
        compiled_prompt = _session_manager.compile(
            session_id=final_session_id,
            incoming=messages,
            agent=agent_name,
            max_history=max_history
        )

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"UNIFIED PROMPT (len={len(compiled_prompt)}): {compiled_prompt[:200]}...")
    else:
        # Modo stateless — sin persistencia ni replay. El prompt se arma desde
        # los mensajes entrantes (que el cliente OpenAI ya envía completos),
        # incluyendo el system (instrucciones + esquema) para agentes "json".
        history_parts = []
        if sys_prompt_text:
            history_parts.append(sys_prompt_text)
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "user":
                history_parts.append(f"User: {content}")
            elif role == "assistant":
                history_parts.append(f"Assistant: {content}")
            elif role == "tool_use":
                history_parts.append(f"[Tool: {content}]")
            elif role == "tool_result":
                history_parts.append(f"[Tool Result: {content}]")
        compiled_prompt = "\n\n".join(history_parts)
        final_session_id = str(uuid.uuid4())

    # 2.b Inyección del catálogo de tools (si el cliente las registró)
    tool_catalog = format_tool_catalog(tools)
    if tool_catalog:
        choice_hint = ""
        if isinstance(tool_choice, str) and tool_choice in ("required", "auto", "none"):
            choice_hint = f"\nTool selection policy: {tool_choice}."
        elif isinstance(tool_choice, dict) and tool_choice.get("function", {}).get("name"):
            choice_hint = f"\nThe client requests you call: {tool_choice['function']['name']}."
        compiled_prompt = f"{tool_catalog}{choice_hint}\n\n{compiled_prompt}"

    # 3. Ejecución del Agente
    active = Acople(agent_name)
    process_pid = str(uuid.uuid4())

    # Compute registered tool names for proxy-mode termination
    _registered_tool_names: set[str] = set()
    for _t in tools or []:
        if not isinstance(_t, dict):
            continue
        if _t.get("type") == "function" and isinstance(_t.get("function"), dict):
            _tn = _t["function"].get("name", "")
        else:
            _tn = _t.get("name", "")
        if _tn:
            _registered_tool_names.add(_tn)
    _proxy_mode = bool(_registered_tool_names)
    _tool_call_emitted = False

    agent_prompt = _build_agent_prompt(
        messages=messages,
        compiled_prompt=compiled_prompt,
        sys_prompt_text=sys_prompt_text,
        tool_catalog=tool_catalog,
        stream_format=active.config.stream_format,
        tool_choice=tool_choice,
    )

    def register_proc(p):
        ACTIVE_PROCESSES[process_pid] = p
        # El session_id ORIGINAL del cliente (es el único que él conoce y puede
        # pasar a /interrupt); final_session_id solo como fallback (modo
        # stateful por carpeta). En stateless puro queda un uuid no-matchable,
        # igual que antes del fix.
        PROCESS_SESSIONS[process_pid] = session_id or final_session_id

    response_content = ""
    captured_tool_uses: list[dict] = []
    # Referencia explícita al generador del agente: la terminación forzada hace
    # `break`, y sin un aclose() explícito el finally de Acople.run (que mata el
    # subprocess) quedaría a merced del finalizador GC de async generators —
    # asíncrono y sin orden garantizado (I5).
    agent_stream = active.run(
        prompt=agent_prompt,
        cwd=effective_cwd,
        on_start=register_proc,
        disable_native_tools=_proxy_mode,
    )
    try:
        async for event in agent_stream:
            if event.type == EventType.TOKEN:
                text = event.data.get("text", "")
                if _proxy_mode and _tool_call_emitted:
                    # F6: dos marcadores consecutivos casi siempre llegan
                    # separados por whitespace ("\n", espacios). Ese separador
                    # NO significa "el agente sigue": se descarta sin cerrar
                    # el turno, para no truncar tool calls paralelas.
                    if not text.strip():
                        continue
                    # Texto real tras las tools → terminación forzada. No se
                    # acumula en response_content: el cliente nunca lo recibió.
                    yield BridgeEvent(EventType.DONE, {})
                    break
                response_content += text
                yield event
            elif event.type == EventType.TOOL_USE:
                tool_name = event.data.get("tool", "")
                if _proxy_mode and tool_name not in _registered_tool_names:
                    if _tool_call_emitted:
                        yield BridgeEvent(EventType.DONE, {})
                        break
                    continue
                _tool_call_emitted = True

                # Loop-guard (stateful): SOLO el primer tool_use del turno se
                # compara con el primero del turno anterior (nombre + args).
                # Comparar tools posteriores contra prev_first mataba turnos
                # multi-tool legítimos con orden distinto ([A,B] → [B,A]).
                # Al dispararse, la entrada se CONSUME (one-shot): una
                # repetición legítima en el turno siguiente vuelve a pasar;
                # un bucle real se re-detecta al turno siguiente.
                # Usa el session_id original (no final_session_id, que en
                # stateless es un uuid efímero).
                if _proxy_mode and session_id and not captured_tool_uses:
                    prev_tools = _session_tool_history.get(session_id)
                    if prev_tools and _tool_use_key(event.data) == prev_tools[0]:
                        logger.warning(
                            "Loop-guard triggered for session %s: "
                            "agent repeating tool %s",
                            session_id, tool_name,
                        )
                        _session_tool_history.pop(session_id, None)
                        note = (
                            "[acople] Loop guard: the agent attempted to repeat "
                            f'the identical tool call "{tool_name}" from the '
                            "previous turn; the turn was stopped."
                        )
                        response_content += note
                        yield BridgeEvent(EventType.TOKEN, {"text": note})
                        yield BridgeEvent(EventType.DONE, {})
                        break

                captured_tool_uses.append(dict(event.data))
                yield event
            else:
                yield event

    except Exception as e:
        logger.error(f"Unified workflow error for {agent_name}: {e}")
        yield BridgeEvent(EventType.ERROR, {"message": str(e)})
    else:
        # Store tool sequence for loop-guard (only after clean completion)
        # Uses original session_id — final_session_id may be ephemeral UUID
        # in stateless mode, which would pollute the guard with junk keys.
        if _proxy_mode and session_id and captured_tool_uses:
            # Re-insertar al final (orden de inserción = orden de uso) y
            # evictar las sesiones más antiguas si se supera la cota.
            _session_tool_history.pop(session_id, None)
            _session_tool_history[session_id] = [
                _tool_use_key(tu) for tu in captured_tool_uses
            ]
            while len(_session_tool_history) > _MAX_TOOL_HISTORY_SESSIONS:
                _session_tool_history.pop(next(iter(_session_tool_history)))
    finally:
        # Cierre determinista: ejecuta el finally de Acople.run (cleanup del
        # subprocess) DENTRO de esta request, antes de liberar el slot de
        # concurrencia. No-op si el generador ya se agotó.
        try:
            await agent_stream.aclose()
        except Exception as e:
            logger.warning(f"agent_stream.aclose() failed: {e}")
        ACTIVE_PROCESSES.pop(process_pid, None)
        PROCESS_SESSIONS.pop(process_pid, None)
        # 4. Persistencia final: texto del assistant y cada tool_use.
        # Solo si usamos memoria (en stateless final_session_id es efímero y la
        # sesión nunca se creó).
        if use_memory and _session_manager and final_session_id:
            if response_content:
                _session_manager.add_message(final_session_id, "assistant", response_content)
            for tu in captured_tool_uses:
                try:
                    _session_manager.add_message(
                        final_session_id,
                        "tool_use",
                        json.dumps(tu, ensure_ascii=False),
                    )
                except Exception as e:
                    logger.warning(f"Failed to persist tool_use: {e}")





@app.post("/v1/chat/completions")
async def openai_compatibility(request: Request):
    """
    OpenAI-compatible endpoint. Usando el Workflow Unificado (Pipeline Senior).
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    stream = body.get("stream", False)
    full_model = body.get("model", _DEFAULT_AGENT or "claude")
    tools = body.get("tools") or None
    tool_choice = body.get("tool_choice")
    stream_options = body.get("stream_options")
    include_usage = isinstance(stream_options, dict) and stream_options.get("include_usage", False)

    # Build the set of tool names the client actually registered so we can
    # filter out native agent tool calls (bash, read, write, etc.) that the
    # client never asked for.  Forwarding those causes the client to send back
    # tool results, which spawns another agent invocation that calls them
    # again → infinite loop.
    registered_tool_names: set[str] = set()
    for _t in tools or []:
        if not isinstance(_t, dict):
            continue
        if _t.get("type") == "function" and isinstance(_t.get("function"), dict):
            _tn = _t["function"].get("name", "")
        else:
            _tn = _t.get("name", "")
        if _tn:
            registered_tool_names.add(_tn)

    # response_format (estándar OpenAI): si se pide JSON, debemos garantizar
    # que la respuesta sea JSON limpio (sin fences ni prosa alrededor).
    response_format = body.get("response_format")
    rf_type = response_format.get("type") if isinstance(response_format, dict) else None
    json_mode = rf_type in ("json_object", "json_schema")
    if json_mode and tools:
        # F7: las tools del cliente tienen precedencia. La directiva JSON
        # prohíbe usar tools y contradice el catálogo inyectado; aplicar ambas
        # deja el comportamiento al azar del modelo. Se ignora json_mode.
        logger.warning(
            "response_format=%r ignorado: la request registra tools (F7, "
            "tools tienen precedencia)", rf_type,
        )
        json_mode = False
    if json_mode:
        # Reforzar la instrucción al agente además del saneado posterior.
        # Los CLIs agénticos (gemini, codex, kilo) tienden a ignorar un system
        # prompt al inicio y a responder con prosa/markdown, así que además de
        # la directiva de sistema añadimos un recordatorio al final del último
        # turno de usuario (los modelos atienden más a la última instrucción).
        _json_directive = (
            "CRITICAL OUTPUT FORMAT. Your ENTIRE response must be a single valid "
            "JSON object and NOTHING else. Start directly with '{' and end with "
            "'}'. FORBIDDEN: introductory text (e.g. 'Here is'), bulleted or "
            "numbered lists, markdown, bold, code fences (```), explanations, "
            "clarifying questions, and using tools or creating files. The response "
            "must be parseable as-is by a JSON parser (JSON.parse)."
        )
        _json_reminder = (
            "\n\n(Reminder: reply ONLY with the JSON object — no prose, no "
            "markdown, no code fences. Start with '{'.)"
        )
        messages = [{"role": "system", "content": _json_directive}, *messages]
        for _i in range(len(messages) - 1, -1, -1):
            if messages[_i].get("role") == "user" and isinstance(messages[_i].get("content"), str):
                messages[_i] = {**messages[_i], "content": messages[_i]["content"] + _json_reminder}
                break

    # Normalización Senior: Mapear nombre de modelo a binario de agente conocido.
    # Se usa AGENT_CONFIGS como única fuente de verdad para no quedar desfasado
    # respecto a los agentes realmente soportados (claude, gemini, codex,
    # opencode, kilo, qwen).
    raw_name = full_model.split("/")[-1] if "/" in full_model else full_model
    raw_name_lower = raw_name.lower()

    agent_name = None
    for known in AGENT_CONFIGS:
        if raw_name_lower == known or raw_name_lower.startswith(known):
            agent_name = known
            break

    # Fallback al agente por defecto si no hay coincidencia clara o el binario no existe
    if not agent_name or not shutil.which(agent_name):
        orig_agent = agent_name
        agent_name = _DEFAULT_AGENT or "claude"
        if orig_agent is None:
            logger.warning(
                "Model %r did not match any known agent; falling back to %r",
                full_model, agent_name,
            )
        elif orig_agent != agent_name:
            logger.warning(
                "Agent %r not in PATH; falling back to %r",
                orig_agent, agent_name,
            )
        
    max_history = _get_max_history(request)

    # Endpoint OpenAI-compat: stateless por defecto (como la API real de OpenAI;
    # el cliente envía el historial completo). La memoria server-side es opt-in
    # vía header X-Session-ID. Un header con formato inválido se ignora.
    client_session_id = request.headers.get("X-Session-ID")
    if client_session_id:
        from acople import validate_session_id
        try:
            validate_session_id(client_session_id)
        except ValueError:
            logger.warning("X-Session-ID inválido ignorado: %r", client_session_id)
            client_session_id = None

    client_cwd = request.headers.get("X-Acople-Cwd")
    if client_cwd:
        try:
            client_cwd = str(validate_cwd(client_cwd))
        except ValidationError:
            logger.warning("X-Acople-Cwd inválido ignorado: %r", client_cwd)
            client_cwd = None

    if not client_cwd:
        from acople.bridge import _DEFAULT_CWD
        if _DEFAULT_CWD:
            client_cwd = str(_DEFAULT_CWD)
        else:
            import tempfile
            client_cwd = tempfile.gettempdir()
            logger.warning(
                "X-Acople-Cwd no recibido y ACOPLE_DEFAULT_CWD no configurado — "
                "el agente correrá en el directorio temporal del sistema (%s). "
                "Configura ACOPLE_DEFAULT_CWD en .env para evitar esto.",
                client_cwd,
            )

    workflow = _unified_chat_workflow(
        messages=messages,
        agent_name=agent_name,
        session_id=client_session_id,
        cwd=client_cwd,
        max_history=max_history,
        model=full_model,
        tools=tools,
        tool_choice=tool_choice,
        stateful=False,
    )

    if stream:
        async def sse_adapter():
            tool_index = 0
            has_tool_calls = False
            json_buffer = ""  # en json_mode acumulamos tokens para limpiarlos al final
            completion_text = ""
            stream_closed = False  # True tras emitir finish_reason + [DONE]
            prompt_tokens = _estimate_tokens(json.dumps(messages))
            # Spec OpenAI: todos los chunks de una misma completion comparten
            # `id` y `created`. Se generan una vez por stream.
            completion_id = "chatcmpl-" + uuid.uuid4().hex[:12]
            created = int(time.time())

            def base_chunk() -> dict:
                return {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": full_model,
                }

            try:
                # AC4.1: emit delta.role first chunk
                role_chunk = {
                    **base_chunk(),
                    "choices": [{"delta": {"role": "assistant"}, "index": 0, "finish_reason": None}]
                }
                if include_usage:
                    role_chunk["usage"] = None
                yield f"data: {json.dumps(role_chunk)}\n\n"

                async for event in workflow:
                    if event.type == EventType.TOKEN:
                        text = event.data.get("text", "")
                        if json_mode:
                            # No emitir tokens crudos: el JSON puede llegar con
                            # fences/prosa parciales. Se sanea y emite en DONE.
                            json_buffer += text
                            continue
                        completion_text += text
                        chunk = {
                            **base_chunk(),
                            "choices": [{"delta": {"content": text}, "index": 0, "finish_reason": None}]
                        }
                        if include_usage:
                            chunk["usage"] = None
                        yield f"data: {json.dumps(chunk)}\n\n"
                    elif event.type == EventType.TOOL_USE:
                        tool_name = event.data.get("tool", "")
                        # I4: suprimir tools nativas del agente (bash, read,
                        # write…) que el cliente nunca registró. Aplica TAMBIÉN
                        # cuando no hay tools registradas: un cliente OpenAI sin
                        # `tools` jamás debe recibir tool_calls (ni heredar
                        # finish_reason=tool_calls de tools nativas).
                        if tool_name not in registered_tool_names:
                            continue
                        has_tool_calls = True
                        tool_call = _tool_use_to_openai(event.data, tool_index)
                        tool_index += 1
                        tool_args_text = tool_call.get("function", {}).get("arguments", "")
                        completion_text += tool_args_text
                        chunk = {
                            **base_chunk(),
                            "choices": [{"delta": {"tool_calls": [tool_call]}, "index": 0, "finish_reason": None}]
                        }
                        if include_usage:
                            chunk["usage"] = None
                        yield f"data: {json.dumps(chunk)}\n\n"
                    elif event.type == EventType.DONE:
                        if json_mode and json_buffer:
                            cleaned = _extract_json_payload(json_buffer)
                            completion_text += cleaned
                            content_chunk = {
                                **base_chunk(),
                                "choices": [{"delta": {"content": cleaned}, "index": 0, "finish_reason": None}]
                            }
                            if include_usage:
                                content_chunk["usage"] = None
                            yield f"data: {json.dumps(content_chunk)}\n\n"
                        finish_reason = "tool_calls" if has_tool_calls else "stop"
                        chunk = {
                            **base_chunk(),
                            "choices": [{"delta": {}, "index": 0, "finish_reason": finish_reason}]
                        }
                        if include_usage:
                            chunk["usage"] = None
                        yield f"data: {json.dumps(chunk)}\n\n"

                        if include_usage:
                            completion_tokens = _estimate_tokens(completion_text)
                            usage_chunk = {
                                **base_chunk(),
                                "choices": [],
                                "usage": {
                                    "prompt_tokens": prompt_tokens,
                                    "completion_tokens": completion_tokens,
                                    "total_tokens": prompt_tokens + completion_tokens,
                                }
                            }
                            yield f"data: {json.dumps(usage_chunk)}\n\n"

                        yield "data: [DONE]\n\n"
                        stream_closed = True
                    elif event.type == EventType.ERROR:
                        logger.error("SSE adapter received error event: %s", event.data.get("message", ""))
                        yield f"data: {json.dumps(_openai_error(event.data.get('message', 'Unknown error')))}\n\n"
                    else:
                        logger.debug("SSE adapter ignoring unhandled event: %s", event.type)

                # I2 (cable): si el workflow se agotó sin DONE (p.ej. error
                # temprano), cerramos igualmente con finish_reason + [DONE]
                # para que un cliente OpenAI estricto nunca quede colgado.
                if not stream_closed:
                    chunk = {
                        **base_chunk(),
                        "choices": [{
                            "delta": {},
                            "index": 0,
                            "finish_reason": "tool_calls" if has_tool_calls else "stop",
                        }]
                    }
                    if include_usage:
                        chunk["usage"] = None
                    yield f"data: {json.dumps(chunk)}\n\n"
                    yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error(f"SSE Adapter error: {e}")
                yield f"data: {json.dumps(_openai_error(str(e)))}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                # I5: si el cliente desconecta a mitad de stream, cerrar el
                # workflow aquí (y no en el GC) para que el subprocess muera
                # dentro de la request.
                try:
                    await workflow.aclose()
                except Exception:
                    pass
        
        return StreamingResponse(
            sse_adapter(), 
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
    else:
        full_response = ""
        collected_tool_calls: list[dict] = []
        error_message: str | None = None
        try:
            async for event in workflow:
                if event.type == EventType.TOKEN:
                    full_response += event.data.get("text", "")
                elif event.type == EventType.TOOL_USE:
                    tool_name = event.data.get("tool", "")
                    # I4: igual que en streaming — sin registro, sin tool_calls.
                    if tool_name not in registered_tool_names:
                        continue
                    tc = _tool_use_to_openai(event.data, len(collected_tool_calls))
                    tc.pop("index", None)
                    collected_tool_calls.append(tc)
                elif event.type == EventType.ERROR:
                    error_message = event.data.get("message", "Unknown error")
                    logger.error("OpenAI non-streaming error: %s", error_message)
                    break
                elif event.type == EventType.DONE:
                    pass  # natural end
                else:
                    logger.debug("OpenAI non-streaming ignoring event: %s", event.type)
        finally:
            # I5: el break en ERROR abandona el workflow a medias — cerrarlo
            # aquí garantiza el cleanup del subprocess dentro de la request.
            try:
                await workflow.aclose()
            except Exception:
                pass

        if error_message:
            return JSONResponse(status_code=502, content=_openai_error(error_message))

        content = full_response or None
        if json_mode and full_response:
            content = _extract_json_payload(full_response)

        message: dict = {"role": "assistant", "content": content}
        if collected_tool_calls:
            message["tool_calls"] = collected_tool_calls

        prompt_text = json.dumps(messages)
        prompt_tokens = _estimate_tokens(prompt_text)
        completion_content = content or ""
        completion_tokens = _estimate_tokens(completion_content)

        return {
            "id": "chatcmpl-" + uuid.uuid4().hex[:12],
            "object": "chat.completion",
            "created": int(time.time()),
            "model": full_model,
            "choices": [{
                "message": message,
                "index": 0,
                "finish_reason": "tool_calls" if collected_tool_calls else "stop"
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
        }


@app.post("/chat/simple")
async def chat_simple(req: SimpleChatRequest):
    """Chat minimal - ahora con soporte de memoria unificado."""
    if len(ACTIVE_PROCESSES) >= MAX_CONCURRENT:
        raise HTTPException(status_code=429, detail=f"Max {MAX_CONCURRENT} concurrent sessions")

    agent_name = req.agent or _DEFAULT_AGENT
    if not agent_name:
        raise HTTPException(status_code=400, detail="No agent available")

    messages = [{"role": "user", "content": req.prompt}]

    workflow = _unified_chat_workflow(
        messages=messages,
        agent_name=agent_name
    )

    async def simple_sse():
        async for event in workflow:
            yield event.to_sse()

    return StreamingResponse(
        simple_sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/interrupt")
def interrupt(session_id: str | None = None):
    """Interrumpe una o todas las generaciones activas."""
    if not ACTIVE_PROCESSES:
        return {"ok": True, "message": "No hay procesos activos"}

    if session_id:
        # Tras el desacople F10, ACTIVE_PROCESSES se indexa por un uuid interno
        # que el cliente no conoce. Se resuelve vía PROCESS_SESSIONS (sesión de
        # la request que lanzó cada proceso); se acepta también el uuid crudo.
        targets = [
            (pid, proc) for pid, proc in list(ACTIVE_PROCESSES.items())
            if pid == session_id or PROCESS_SESSIONS.get(pid) == session_id
        ]
        if not targets:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        interrupted = 0
        for pid, proc in targets:
            try:
                if proc.returncode is None:
                    if sys.platform == "win32":
                        proc.terminate()
                    else:
                        import signal as _signal
                        proc.send_signal(_signal.SIGINT)
                    interrupted += 1
            except Exception as e:
                logger.error(f"Error interrumpiendo {pid} ({session_id}): {e}")
        return {"ok": True, "interrupted": interrupted}

    count = 0
    for sid, proc in list(ACTIVE_PROCESSES.items()):
        try:
            if proc.returncode is None:
                if sys.platform == "win32":
                    proc.terminate()
                else:
                    import signal as _signal
                    proc.send_signal(_signal.SIGINT)
                count += 1
        except Exception as e:
            logger.error(f"Error interrumpiendo {sid}: {e}")

    return {"ok": True, "interrupted": count}


# ---------------------------------------------------------------------------
# Image Generation Endpoints
# ---------------------------------------------------------------------------

@app.post("/image/generate")
async def generate_image(req: ImageGenerateRequest):
    """Genera imagen(es) con gpt-image-1. Devuelve JSON con base64."""
    try:
        validate_prompt(req.prompt)
        validate_image_size(req.size)
        validate_image_quality(req.quality)
        validate_image_n(req.n)
        validate_image_output_format(req.output_format)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        bridge = ImageBridge()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    config = ImageConfig(
        size=req.size,
        quality=req.quality,
        n=req.n,
        output_format=req.output_format,
    )

    try:
        results = await bridge.generate(req.prompt, config)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "images": [
            {"b64": r.b64_data, "format": r.format, "revised_prompt": r.revised_prompt}
            for r in results
        ],
        "model": "gpt-image-1",
    }


@app.post("/image/generate/stream")
async def generate_image_stream(req: ImageGenerateRequest):
    """Genera imagen(es) con gpt-image-1 vía SSE."""
    try:
        validate_prompt(req.prompt)
        validate_image_size(req.size)
        validate_image_quality(req.quality)
        validate_image_n(req.n)
        validate_image_output_format(req.output_format)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    async def event_stream():
        try:
            bridge = ImageBridge()
        except Exception as e:
            yield BridgeEvent(EventType.ERROR, {"message": str(e)}).to_sse()
            return

        config = ImageConfig(
            size=req.size,
            quality=req.quality,
            n=req.n,
            output_format=req.output_format,
        )

        async for event in bridge.generate_stream(req.prompt, config):
            yield event.to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "agent": _DEFAULT_AGENT,
        "image_ready": bool(os.environ.get("OPENAI_API_KEY")),
    }
