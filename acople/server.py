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
from fastapi.responses import HTMLResponse, StreamingResponse
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


_DEFAULT_AGENT: str | None = None
_session_manager = None
ACTIVE_PROCESSES: dict[str, asyncio.subprocess.Process] = {}
MAX_CONCURRENT = int(os.environ.get("ACOPLE_MAX_CONCURRENT", "5"))


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

_cors_origins = os.environ.get("ACOPLE_CORS_ORIGINS", "http://localhost:*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
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
) -> AsyncIterator[BridgeEvent]:
    """
    Workflow unificado (Pipeline Senior) para el manejo de chats.
    Centraliza: Normalización, Identidad, Memoria, Ejecución y Persistencia.
    """
    # 0. Normalización agnóstica: OpenAI/Anthropic/Ollama → formato interno.
    messages = normalize_incoming_messages(messages)

    # 1. Normalización de Identidad y CWD
    sys_prompt_text, extracted_cwd = process_system_messages(messages)
    effective_cwd = cwd or extracted_cwd
    
    # 2. Resolución de Sesión (si el manager está activo)
    final_session_id = session_id
    compiled_prompt = ""
    
    if _session_manager:
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
        # Modo Legacy (sin sesiones activas) — messages ya está normalizado
        history_parts = []
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
    process_pid = final_session_id

    # Para agentes plain-text (opencode, gemini, codex…) el compiled_prompt
    # incluye prefijos "User:"/"Assistant:" y todo el historial, lo que confunde
    # al modelo subyacente (responde al contexto histórico en vez de a la tarea
    # actual). Estos agentes tienen su propio manejo de contexto; les enviamos
    # solo el último mensaje del usuario como prompt limpio.
    if active.config.stream_format != "json":
        raw_last = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            compiled_prompt,
        )
        agent_prompt = f"{tool_catalog}{raw_last}" if tool_catalog else raw_last
    else:
        agent_prompt = compiled_prompt

    def register_proc(p):
        ACTIVE_PROCESSES[process_pid] = p

    response_content = ""
    captured_tool_uses: list[dict] = []
    try:
        async for event in active.run(
            prompt=agent_prompt,
            cwd=effective_cwd,
            on_start=register_proc
        ):
            if event.type == EventType.TOKEN:
                response_content += event.data.get("text", "")
            elif event.type == EventType.TOOL_USE:
                captured_tool_uses.append(dict(event.data))
            yield event

    except Exception as e:
        logger.error(f"Unified workflow error for {agent_name}: {e}")
        yield BridgeEvent(EventType.ERROR, {"message": str(e)})
    finally:
        ACTIVE_PROCESSES.pop(process_pid, None)
        # 4. Persistencia final: texto del assistant y cada tool_use
        if _session_manager and final_session_id:
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

    # response_format (estándar OpenAI): si se pide JSON, debemos garantizar
    # que la respuesta sea JSON limpio (sin fences ni prosa alrededor).
    response_format = body.get("response_format")
    rf_type = response_format.get("type") if isinstance(response_format, dict) else None
    json_mode = rf_type in ("json_object", "json_schema")
    if json_mode:
        # Reforzar la instrucción al agente además del saneado posterior.
        messages = [
            {
                "role": "system",
                "content": (
                    "Devuelve la respuesta como un único objeto JSON válido "
                    "DIRECTAMENTE en tu mensaje de texto. Reglas estrictas: "
                    "(1) NO uses herramientas, NO crees ni escribas archivos, "
                    "NO ejecutes comandos; el JSON va en el texto de la respuesta. "
                    "(2) NO incluyas explicaciones, comentarios ni fences de "
                    "código (```). (3) Tu respuesta completa debe poder parsearse "
                    "directamente con un parser JSON."
                ),
            },
            *messages,
        ]

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

    workflow = _unified_chat_workflow(
        messages=messages,
        agent_name=agent_name,
        max_history=max_history,
        model=full_model,
        tools=tools,
        tool_choice=tool_choice,
    )

    if stream:
        async def sse_adapter():
            tool_index = 0
            has_tool_calls = False
            json_buffer = ""  # en json_mode acumulamos tokens para limpiarlos al final
            try:
                async for event in workflow:
                    if event.type == EventType.TOKEN:
                        text = event.data.get("text", "")
                        if json_mode:
                            # No emitir tokens crudos: el JSON puede llegar con
                            # fences/prosa parciales. Se sanea y emite en DONE.
                            json_buffer += text
                            continue
                        chunk = {
                            "id": "chatcmpl-" + uuid.uuid4().hex[:12],
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": full_model,
                            "choices": [{"delta": {"content": text}, "index": 0, "finish_reason": None}]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
                    elif event.type == EventType.TOOL_USE:
                        has_tool_calls = True
                        tool_call = _tool_use_to_openai(event.data, tool_index)
                        tool_index += 1
                        chunk = {
                            "id": "chatcmpl-" + uuid.uuid4().hex[:12],
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": full_model,
                            "choices": [{"delta": {"tool_calls": [tool_call]}, "index": 0, "finish_reason": None}]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
                    elif event.type == EventType.DONE:
                        if json_mode and json_buffer:
                            cleaned = _extract_json_payload(json_buffer)
                            content_chunk = {
                                "id": "chatcmpl-" + uuid.uuid4().hex[:12],
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": full_model,
                                "choices": [{"delta": {"content": cleaned}, "index": 0, "finish_reason": None}]
                            }
                            yield f"data: {json.dumps(content_chunk)}\n\n"
                        finish_reason = "tool_calls" if has_tool_calls else "stop"
                        chunk = {
                            "id": "chatcmpl-" + uuid.uuid4().hex[:12],
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": full_model,
                            "choices": [{"delta": {}, "index": 0, "finish_reason": finish_reason}]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                    elif event.type == EventType.ERROR:
                        logger.error("SSE adapter received error event: %s", event.data.get("message", ""))
                        yield f"data: {json.dumps({'error': event.data})}\n\n"
                    else:
                        logger.debug("SSE adapter ignoring unhandled event: %s", event.type)
            except Exception as e:
                logger.error(f"SSE Adapter error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                yield "data: [DONE]\n\n"
        
        return StreamingResponse(
            sse_adapter(), 
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
    else:
        full_response = ""
        collected_tool_calls: list[dict] = []
        error_message: str | None = None
        async for event in workflow:
            if event.type == EventType.TOKEN:
                full_response += event.data.get("text", "")
            elif event.type == EventType.TOOL_USE:
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

        if error_message:
            raise HTTPException(status_code=502, detail=error_message)

        content = full_response or None
        if json_mode and full_response:
            content = _extract_json_payload(full_response)

        message: dict = {"role": "assistant", "content": content}
        if collected_tool_calls:
            message["tool_calls"] = collected_tool_calls

        return {
            "id": "chatcmpl-" + uuid.uuid4().hex[:12],
            "object": "chat.completion",
            "created": int(time.time()),
            "model": full_model,
            "choices": [{
                "message": message,
                "index": 0,
                "finish_reason": "tool_calls" if collected_tool_calls else "stop"
            }]
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
        proc = ACTIVE_PROCESSES.get(session_id)
        if not proc:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        try:
            if proc.returncode is None:
                if sys.platform == "win32":
                    proc.terminate()
                else:
                    import signal as _signal
                    proc.send_signal(_signal.SIGINT)
            return {"ok": True, "interrupted": 1}
        except Exception as e:
            logger.error(f"Error interrumpiendo {session_id}: {e}")
            raise HTTPException(status_code=500, detail="Error interrumpiendo proceso")

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
