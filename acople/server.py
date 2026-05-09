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
import logging
import os
import sys
import uuid
import json
import time
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel

from acople import (
    Acople,
    AgentNotFoundError,
    BridgeEvent,
    EventType,
    detect_agent,
    detect_all_agents,
    detect_models,
)
from acople.security import (
    ValidationError,
    validate_agent_name,
    validate_cwd,
    validate_prompt,
    validate_image_size,
    validate_image_quality,
    validate_image_n,
    validate_image_output_format,
)
from acople.image_bridge import ImageBridge, ImageConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("AcopleServer")

_DEFAULT_AGENT: str | None = None
ACTIVE_PROCESSES: dict[str, asyncio.subprocess.Process] = {}
MAX_CONCURRENT = int(os.environ.get("ACOPLE_MAX_CONCURRENT", "5"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _DEFAULT_AGENT
    try:
        _DEFAULT_AGENT = detect_agent()
        if _DEFAULT_AGENT:
            logger.info(f"[OK] Agente detectado: {_DEFAULT_AGENT}")
        else:
            logger.warning("[--] Ningun agente en PATH")
    except Exception as e:
        logger.error(f"Error inicializando: {e}", exc_info=True)
    yield


API_KEY = os.environ.get("ACOPLE_API_KEY")

async def verify_api_key(request: Request):
    if not API_KEY:
        return  # No key configured = no auth (local dev)
    key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

app = FastAPI(
    title="Acople",
    description="Universal bridge to IDE AI agents",
    version="1.2.0",
    lifespan=lifespan,
    dependencies=[Depends(verify_api_key)],
)

_cors_origins = os.environ.get("ACOPLE_CORS_ORIGINS", "http://localhost:*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


class ChatRequest(BaseModel):
    prompt: str
    system: str | None = None
    cwd: str | None = None
    agent: str | None = None
    model: str | None = None
    timeout: float | None = None


class SimpleChatRequest(BaseModel):
    prompt: str


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
    with open(ui_path, "r", encoding="utf-8") as f:
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
    """Chat full con todos los parámetros."""
    if len(ACTIVE_PROCESSES) >= MAX_CONCURRENT:
        raise HTTPException(status_code=429, detail=f"Max {MAX_CONCURRENT} concurrent sessions")

    try:
        validate_prompt(req.prompt)
        validate_cwd(req.cwd)
        validate_agent_name(req.agent)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    async def event_stream():
        agent_name = req.agent or _DEFAULT_AGENT

        if not agent_name:
            yield BridgeEvent(EventType.ERROR, {"message": "Ningún agente disponible"}).to_sse()
            return

        session_id = str(uuid.uuid4())

        def register(proc):
            ACTIVE_PROCESSES[session_id] = proc

        try:
            active = Acople(agent_name)
            # TODO: Soportar model selection cuando el CLI lo soporte
            if req.model:
                logger.info(f"Model selection no implementado aún: {req.model}")

            async for event in active.run(
                prompt=req.prompt,
                cwd=req.cwd,
                system=req.system,
                timeout=req.timeout,
                on_start=register
            ):
                yield event.to_sse()

        except AgentNotFoundError as e:
            yield BridgeEvent(EventType.ERROR, {"message": str(e)}).to_sse()
        except asyncio.CancelledError:
            yield BridgeEvent(EventType.DONE, {"reason": "cancelled"}).to_sse()
        except Exception as e:
            yield BridgeEvent(EventType.ERROR, {"message": str(e)}).to_sse()
        finally:
            ACTIVE_PROCESSES.pop(session_id, None)

    return StreamingResponse(
        event_stream(),
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


@app.post("/v1/chat/completions")
async def openai_compatibility(request: Request):
    """
    OpenAI-compatible endpoint. Turns Acople into a local AI provider.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    # Extract user prompt and system message
    prompt = ""
    system_msg = None
    
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            system_msg = content
        elif role == "user":
            prompt = content

    # Check if we should stream
    stream = body.get("stream", False)
    
    # Resolve agent name from model field (e.g. "acople/claude" -> "claude")
    full_model = body.get("model", _DEFAULT_AGENT or "claude")
    agent_name = full_model.split("/")[-1] if "/" in full_model else full_model

    async def openai_stream():
        try:
            active = Acople(agent_name)
            session_id = str(uuid.uuid4())
            
            def reg(p): ACTIVE_PROCESSES[session_id] = p
            
            # NOTE: On Windows, we avoid passing system_msg to the CLI if it's too long
            # to prevent WinError 206 (command line limit).
            effective_system = system_msg
            if sys.platform == "win32" and system_msg and len(system_msg) > 1000:
                logger.warning("System prompt too long for Windows CLI, omitting to prevent crash.")
                effective_system = None

            async for event in active.run(prompt, system=effective_system, on_start=reg):
                if event.type == EventType.TOKEN:
                    chunk = {
                        "id": session_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": agent_name,
                        "choices": [{
                            "delta": {"content": event.data.get("text", "")}, 
                            "index": 0, 
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                elif event.type == EventType.DONE:
                    chunk = {
                        "id": session_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": agent_name,
                        "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                    yield "data: [DONE]\n\n"
            
            ACTIVE_PROCESSES.pop(session_id, None)
        except Exception as e:
            logger.error(f"OpenAI Shim Error: {e}")
            yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n"
            yield "data: [DONE]\n\n"

    if stream:
        return StreamingResponse(openai_stream(), media_type="text/event-stream")
    else:
        content = ""
        try:
            active = Acople(agent_name)
            async for event in active.run(prompt, system=system_msg):
                if event.type == EventType.TOKEN:
                    content += event.data.get("text", "")
            
            return {
                "id": str(uuid.uuid4()),
                "object": "chat.completion",
                "created": int(time.time()),
                "model": agent_name,
                "choices": [{
                    "message": {"role": "assistant", "content": content},
                    "index": 0,
                    "finish_reason": "stop"
                }]
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/simple")
async def chat_simple(req: SimpleChatRequest):
    """Chat minimal - solo prompt."""
    if len(ACTIVE_PROCESSES) >= MAX_CONCURRENT:
        raise HTTPException(status_code=429, detail=f"Max {MAX_CONCURRENT} concurrent sessions")

    try:
        validate_prompt(req.prompt)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    async def event_stream():
        if not _DEFAULT_AGENT:
            yield BridgeEvent(EventType.ERROR, {"message": "Ningún agente disponible"}).to_sse()
            return

        session_id = str(uuid.uuid4())

        def register(proc):
            ACTIVE_PROCESSES[session_id] = proc

        try:
            active = Acople(_DEFAULT_AGENT)
            async for event in active.run(req.prompt, on_start=register):
                yield event.to_sse()
        except AgentNotFoundError as e:
            yield BridgeEvent(EventType.ERROR, {"message": str(e)}).to_sse()
        except Exception as e:
            yield BridgeEvent(EventType.ERROR, {"message": str(e)}).to_sse()
        finally:
            ACTIVE_PROCESSES.pop(session_id, None)

    return StreamingResponse(
        event_stream(),
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
