"""
Acople — Universal bridge to IDE AI agents

Soporta: claude, gemini, codex, opencode, kilo, qwen, y cualquier CLI personalizado.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

_ANSI_RE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

logger = logging.getLogger("acople")


# ---------------------------------------------------------------------------
# Tipos de eventos normalizados
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    TOKEN = "token"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    IMAGE = "image"
    DONE = "done"
    ERROR = "error"
    SYSTEM = "system"


@dataclass
class BridgeEvent:
    type: EventType
    data: dict = field(default_factory=dict)

    def to_sse(self) -> str:
        payload = json.dumps({**self.data, "type": self.type.value})
        return f"data: {payload}\n\n"


# ---------------------------------------------------------------------------
# Configuración por agente
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    bin: str
    args: list[str]
    prompt_flag: str
    stream_format: str
    extra_env: dict = field(default_factory=dict)
    max_chars: int = 50_000


AGENT_CONFIGS: dict[str, AgentConfig] = {
    "claude": AgentConfig(
        bin="claude",
        args=["--output-format", "stream-json", "--verbose", "--no-session-persistence"],
        prompt_flag="--print",
        stream_format="json",
        max_chars=20_000,
    ),
    "gemini": AgentConfig(
        bin="gemini",
        args=["--skip-trust"],
        prompt_flag=None,   # None = use stdin
        stream_format="plain",
    ),
    "codex": AgentConfig(
        bin="codex",
        args=["exec", "--skip-git-repo-check"],
        prompt_flag=None,   # None = use stdin
        stream_format="plain",
    ),
    "opencode": AgentConfig(
        bin="opencode",
        args=["run", "--format", "json", "--dangerously-skip-permissions"],
        prompt_flag=None,   # None = use stdin
        stream_format="opencode-json",
    ),
    "kilo": AgentConfig(
        bin="kilo",
        args=["run", "--format", "json", "--auto"],
        prompt_flag=None,   # None = use stdin (fork of opencode, same behavior)
        stream_format="opencode-json",
    ),
    "qwen": AgentConfig(
        bin="qwen",
        args=[],
        prompt_flag="-p",
        stream_format="plain",
        max_chars=15_000,
    ),
}


# ---------------------------------------------------------------------------
# Detección automática
# ---------------------------------------------------------------------------

def detect_agent() -> str | None:
    """Escanea el entorno y PATH y devuelve el agente disponible."""
    # 1. Prioridad: Variable de entorno
    env_agent = os.environ.get("ACOPLE_AGENT")
    if env_agent and shutil.which(env_agent):
        return env_agent

    # 2. PATH scanning basado en configuración conocida
    for name in AGENT_CONFIGS.keys():
        if shutil.which(name):
            return name
    return None


def detect_all_agents() -> dict[str, bool]:
    """Escanea PATH y devuelve todos los agentes disponibles."""
    return {name: bool(shutil.which(name)) for name in AGENT_CONFIGS}


async def detect_models(agent: str) -> list[str]:
    """Intenta detectar modelos disponibles del agente."""
    if agent not in AGENT_CONFIGS:
        return []

    bin_cmd = shutil.which(agent)
    if not bin_cmd:
        return []

    try:
        proc = await asyncio.create_subprocess_exec(
            bin_cmd, "--list-models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        output = stdout.decode("utf-8", errors="replace")
        models = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("-"):
                models.append(line)
        return models
    except Exception:
        return []


def from_env() -> "Acople":
    """Crea un Acople usando variables de entorno."""
    return Acople()


def get_config(agent_name: str) -> AgentConfig:
    if agent_name in AGENT_CONFIGS:
        return AGENT_CONFIGS[agent_name]
    return AgentConfig(
        bin=agent_name,
        args=[],
        prompt_flag="-p",
        stream_format="plain",
    )


# ---------------------------------------------------------------------------
# Extractor de objetos JSON multilínea por profundidad de llaves
# ---------------------------------------------------------------------------

def _extract_json_objects(text: str) -> tuple[list[str], str]:
    """Extrae objetos JSON completos del texto, manejando saltos de línea
    dentro de strings. Retorna (objetos_como_strings, resto).

    Esto reemplaza el split ingenuo por ``\\n`` que rompe JSON multilínea
    (ej: tool input con texto con saltos de línea).
    """
    objects: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                objects.append(text[start:i+1])
                start = -1

    remainder = text[start:] if start >= 0 else ""
    return objects, remainder


# ---------------------------------------------------------------------------
# Parsers de stream JSON
# ---------------------------------------------------------------------------

def parse_opencode_json_line(line: str) -> BridgeEvent | None:
    """Parser para opencode --format json.

    Schema confirmado (v1.15.6):
      {"type":"step_start", ...}
      {"type":"tool_use", "part":{"tool":"read", "state":{"status":"completed","input":{...},"output":"..."}}}
      {"type":"step_finish", "part":{"reason":"tool-calls"|"stop"}}
      {"type":"text", "part":{"text":"..."}}
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        logger.debug("parse_opencode_json_line: invalid JSON (%d bytes)", len(line))
        return None

    t = event.get("type", "")
    part = event.get("part") or {}

    if t == "text":
        text = part.get("text", "")
        if text:
            return BridgeEvent(EventType.TOKEN, {"text": text})

    elif t == "tool_use":
        state = part.get("state") or {}
        return BridgeEvent(EventType.TOOL_USE, {
            "tool": part.get("tool", "unknown"),
            "input": state.get("input") or {},
        })

    elif t == "step_finish":
        if part.get("reason") == "stop":
            return BridgeEvent(EventType.DONE, {})

    if t:
        logger.debug("parse_opencode_json_line: unhandled event type %r", t)
    return None


def parse_claude_json_line(line: str) -> list[BridgeEvent]:
    """Parser para ``claude --output-format stream-json --verbose``.

    El CLI de Claude Code NO emite los eventos crudos de la API SSE
    (``content_block_delta``/``message_stop``); emite mensajes envueltos:
      {"type":"assistant","message":{"content":[{"type":"text","text":...}]}}
      {"type":"user","message":{"content":[{"type":"tool_result",...}]}}
      {"type":"result","subtype":"success","result":"..."}
    Un mismo mensaje ``assistant`` puede traer varios bloques (texto +
    tool_use), por eso se devuelve una lista de eventos.
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        logger.debug("parse_claude_json_line: invalid JSON (%d bytes)", len(line))
        return []

    t = event.get("type", "")
    events: list[BridgeEvent] = []

    # --- Formato real del CLI de Claude Code -------------------------------
    if t == "assistant":
        for block in event.get("message", {}).get("content", []):
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text" and block.get("text"):
                events.append(BridgeEvent(EventType.TOKEN, {"text": block["text"]}))
            elif bt == "tool_use":
                events.append(BridgeEvent(EventType.TOOL_USE, {
                    "tool": block.get("name", "unknown"),
                    "input": block.get("input", {}),
                }))
        return events

    if t == "user":
        content = event.get("message", {}).get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    events.append(BridgeEvent(EventType.TOOL_RESULT, {
                        "content": block.get("content", ""),
                    }))
        return events

    if t == "result":
        return [BridgeEvent(EventType.DONE, {})]

    # --- Compatibilidad con eventos crudos de la API SSE -------------------
    if t == "content_block_delta":
        text = event.get("delta", {}).get("text", "")
        if text:
            return [BridgeEvent(EventType.TOKEN, {"text": text})]
    elif t in ("tool_use", "tool_call"):
        return [BridgeEvent(EventType.TOOL_USE, {
            "tool": event.get("name", "unknown"),
            "input": event.get("input", {}),
        })]
    elif t == "tool_result":
        return [BridgeEvent(EventType.TOOL_RESULT, {"content": event.get("content", "")})]
    elif t in ("message_stop", "end"):
        return [BridgeEvent(EventType.DONE, {})]

    if t:
        logger.debug("parse_claude_json_line: unhandled event type %r", t)
    return events


# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------

class AcopleError(Exception):
    pass


class AgentNotFoundError(AcopleError):
    def __init__(self, message: str, agent: str = "", suggestion: str = ""):
        self.agent = agent
        self.suggestion = suggestion
        super().__init__(message)


AGENT_INSTALL_HINTS = {
    "claude": "npm i -g @anthropic-ai/claude-code",
    "gemini": "npm i -g @google/gemini-cli",
    "codex": "npm i -g @openai/codex",
    "opencode": "npm i -g opencode",
    "kilo": "npm i -g kilo",
    "qwen": "pip install qwen-agent",
}

class AsyncProcessProxy:
    """Fallback proxy to read subprocess streams in threads for Windows SelectorEventLoop."""
    def __init__(self, proc):
        import asyncio
        self.proc = proc
        self.pid = proc.pid
        self.returncode = None
        self.stdout_queue = asyncio.Queue()
        self.stderr_queue = asyncio.Queue()
        self.loop = asyncio.get_running_loop()

        self._t_out = threading.Thread(target=self._reader, args=(self.proc.stdout, self.stdout_queue))
        self._t_err = threading.Thread(target=self._reader, args=(self.proc.stderr, self.stderr_queue))
        self._t_out.daemon = True
        self._t_err.daemon = True
        self._t_out.start()
        self._t_err.start()

    def _reader(self, pipe, queue):
        try:
            while True:
                chunk = pipe.read1(4096)
                if not chunk:
                    break
                self.loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception:
            pass
        finally:
            self.loop.call_soon_threadsafe(queue.put_nowait, b"")

    async def wait(self):
        import asyncio
        def _wait():
            self.proc.wait()
            return self.proc.returncode
        self.returncode = await asyncio.to_thread(_wait)
        return self.returncode

    def terminate(self):
        self.proc.terminate()

    def kill(self):
        self.proc.kill()

    def send_signal(self, sig):
        self.proc.send_signal(sig)

    class _StreamProxy:
        def __init__(self, queue):
            self.queue = queue
        async def read(self, n=None):
            return await self.queue.get()

    @property
    def stdout(self):
        return self._StreamProxy(self.stdout_queue)

    @property
    def stderr(self):
        return self._StreamProxy(self.stderr_queue)


# ---------------------------------------------------------------------------
# Bridge principal
# ---------------------------------------------------------------------------

class Acople:
    """
    Uso:
        bridge = Acople()   # autodetecta
        async for event in bridge.run("tu prompt"):
            print(event.data.get("text"), end="")
    """

    def __init__(self, agent: str | None = None):
        self.agent_name = agent or detect_agent()
        if not self.agent_name:
            hint = AGENT_INSTALL_HINTS.get("claude", "")
            raise AgentNotFoundError(
                "No se encontró ningún agente CLI en PATH. "
                "Instala uno de: claude, gemini, codex, opencode, kilo, qwen.",
                suggestion=f"Ejecuta: {hint}",
            )
        self.config = get_config(self.agent_name)
        self.validate_binary()

    @property
    def agent(self) -> str:
        return self.agent_name

    def validate_binary(self):
        if not shutil.which(self.config.bin):
            hint = AGENT_INSTALL_HINTS.get(self.agent_name, "")
            raise AgentNotFoundError(
                f"El agente '{self.agent_name}' no está en PATH.",
                agent=self.agent_name,
                suggestion=f"Ejecuta: {hint}" if hint else "Verifica que el binario esté en tu PATH",
            )

    def _resolve_bin(self, bin_name: str) -> str:
        path = shutil.which(bin_name)
        if not path:
            return bin_name

        # En Windows, priorizar .cmd, .bat, .exe si están en PATHEXT
        if os.name == 'nt':
            pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
            path_lower = path.lower()
            for ext in pathext:
                if path_lower.endswith(ext.lower()):
                    return path

        return path

    def _build_cmd(self, prompt: str) -> list[str]:
        cfg = self.config
        bin_cmd = self._resolve_bin(cfg.bin)
        cmd = [bin_cmd] + cfg.args

        # prompt_flag semantics:
        #   None  → use stdin
        #   ""    → positional argument appended to cmd (e.g. opencode run "<prompt>")
        #   "-p"  → flag+value pair (e.g. qwen -p "<prompt>")
        #
        # Windows fallback: if prompt > 4000 chars, force stdin regardless of flag mode
        # to stay within the 8191-char command-line limit.
        force_stdin = os.name == "nt" and len(prompt) > 4000
        use_stdin = cfg.prompt_flag is None or force_stdin

        if not use_stdin:
            if cfg.prompt_flag:
                cmd += [cfg.prompt_flag, prompt]
            else:
                cmd += [prompt]

        # On Windows, executing .cmd or .bat directly can fail with WinError 193
        if os.name == 'nt' and (bin_cmd.lower().endswith('.cmd') or bin_cmd.lower().endswith('.bat')):
            cmd = ["cmd.exe", "/c"] + cmd

        return cmd

    async def run(
        self,
        prompt: str,
        cwd: str | None = None,
        system: str | None = None,
        timeout: float | None = None,
        on_start: Callable[[asyncio.subprocess.Process], None] | None = None,
    ) -> AsyncIterator[BridgeEvent]:
        start_time = time.time()
        proc = None
        try:
            self.validate_binary()
            full_prompt = f"{system}\n\n{prompt}" if system else prompt
            cmd = self._build_cmd(full_prompt)
            work_dir = Path(cwd) if cwd else Path.cwd()

            logger.info(f"Starting agent: {self.agent_name}")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=work_dir,
                )
            except NotImplementedError:
                # Robust fallback for Windows SelectorEventLoop
                import subprocess
                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                raw_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=work_dir,
                    creationflags=creationflags
                )
                proc = AsyncProcessProxy(raw_proc)

            if on_start:
                on_start(proc)

            # Mirror the same logic as _build_cmd to decide whether stdin was used
            use_stdin = self.config.prompt_flag is None or (os.name == "nt" and len(full_prompt) > 4000)
            if use_stdin:
                proc.stdin.write(full_prompt.encode("utf-8", errors="replace"))
                await proc.stdin.drain()
            # Cerrar stdin SIEMPRE: si lo usamos hay que terminar el flujo;
            # si no lo usamos (prompt via flag) hay que evitar que el agente
            # se quede esperando input fantasma.
            try:
                proc.stdin.close()
                if hasattr(proc.stdin, "wait_closed"):
                    await proc.stdin.wait_closed()
            except Exception:
                pass

            logger.info(f"PID: {proc.pid}")
        except Exception as e:
            import traceback
            err_details = traceback.format_exc()
            logger.error(f"Failed to start: {err_details}")
            yield BridgeEvent(EventType.ERROR, {"message": f"Failed to start: {repr(e)}"})
            return

        try:
            if timeout:
                deadline = asyncio.get_event_loop().time() + timeout
                async for event in self._read_stream(proc):
                    yield event
                    if asyncio.get_event_loop().time() > deadline:
                        raise asyncio.TimeoutError()
            else:
                async for event in self._read_stream(proc):
                    yield event
        except asyncio.TimeoutError:
            yield BridgeEvent(EventType.ERROR, {"message": f"Timeout after {timeout}s"})
        except Exception as e:
            logger.error(f"Error: {e}")
            yield BridgeEvent(EventType.ERROR, {"message": str(e)})
        finally:
            if proc:
                await self._cleanup_process(proc)
                duration = time.time() - start_time
                logger.info(f"Finished. Exit: {proc.returncode}, Duration: {duration:.2f}s")

    async def _cleanup_process(self, proc: asyncio.subprocess.Process):
        """Escalación de terminación: SIGINT -> SIGTERM -> SIGKILL."""
        if proc.returncode is not None:
            return

        try:
            logger.info(f"Cleaning up process {proc.pid}...")
            # 1. Intentar SIGINT (Graceful)
            if sys.platform == "win32":
                proc.terminate()  # Sends CTRL_BREAK on Windows
            else:
                import signal
                proc.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
                return
            except asyncio.TimeoutError:
                pass

            # 2. Intentar SIGTERM
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                    return
                except asyncio.TimeoutError:
                    pass

            # 3. SIGKILL (Force)
            if proc.returncode is None:
                logger.warning(f"Force killing process {proc.pid}")
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass  # Already dead
        except Exception as e:
            logger.error(f"Cleanup error for {proc.pid}: {e}")

    async def _read_stream(self, proc: asyncio.subprocess.Process) -> AsyncIterator[BridgeEvent]:
        from acople.normalize import parse_plain_tool_markers
        cfg = self.config
        buffer = ""
        done_emitted = False

        while True:
            try:
                # Timeout protector para evitar bloqueos infinitos si el agente se cuelga
                chunk_bytes = await asyncio.wait_for(proc.stdout.read(4096), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning(f"No output from process {proc.pid} for 30s")
                break

            if not chunk_bytes:
                break

            chunk = chunk_bytes.decode("utf-8", errors="replace")

            if cfg.stream_format in ("json", "opencode-json"):
                buffer += chunk
                if len(buffer) > 5 * 1024 * 1024:
                    yield BridgeEvent(EventType.ERROR, {"message": "Output too long"})
                    buffer = ""
                    continue

                objects, buffer = _extract_json_objects(buffer)
                for obj_str in objects:
                    if cfg.stream_format == "opencode-json":
                        ev = parse_opencode_json_line(obj_str)
                        events = [ev] if ev else []
                    else:
                        events = parse_claude_json_line(obj_str)
                    if not events:
                        logger.debug("Dropped unrecognized JSON object (%d bytes)", len(obj_str))
                        continue
                    for event in events:
                        if event.type == EventType.DONE:
                            done_emitted = True
                        yield event
            else:
                # Plain-text: buffereamos para detectar markers <acople-tool>.
                buffer += _ANSI_RE.sub("", chunk)
                if len(buffer) > 5 * 1024 * 1024:
                    yield BridgeEvent(EventType.ERROR, {"message": "Output too long"})
                    buffer = ""
                    continue
                events, buffer = parse_plain_tool_markers(buffer, final=False)
                for kind, data in events:
                    if kind == "tool_use":
                        yield BridgeEvent(EventType.TOOL_USE, data)
                    else:
                        yield BridgeEvent(EventType.TOKEN, data)

        if cfg.stream_format in ("json", "opencode-json") and buffer.strip():
            logger.warning(
                "Dropping incomplete JSON tail (%d bytes) for PID %s: %r",
                len(buffer), getattr(proc, 'pid', '?'), buffer[:200],
            )
        elif cfg.stream_format not in ("json", "opencode-json") and buffer:
            events, _ = parse_plain_tool_markers(buffer, final=True)
            for kind, data in events:
                if kind == "tool_use":
                    yield BridgeEvent(EventType.TOOL_USE, data)
                else:
                    yield BridgeEvent(EventType.TOKEN, data)

        if proc.stderr:
            try:
                stderr_bytes = await asyncio.wait_for(proc.stderr.read(), timeout=5.0)
            except asyncio.TimeoutError:
                stderr_bytes = b""
            if stderr_bytes:
                stderr = _ANSI_RE.sub("", stderr_bytes.decode("utf-8", errors="replace")).strip()
                if stderr:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
                    if proc.returncode is not None and proc.returncode != 0:
                        yield BridgeEvent(EventType.ERROR, {"message": stderr})
                    else:
                        yield BridgeEvent(EventType.SYSTEM, {"message": stderr})

        if not done_emitted:
            yield BridgeEvent(EventType.DONE, {})

    def interrupt(self, proc: asyncio.subprocess.Process):
        """Interrupt a specific process. Use ACTIVE_PROCESSES registry for lookup."""
        if proc.returncode is None:
            if sys.platform == "win32":
                proc.terminate()
            else:
                import signal
                proc.send_signal(signal.SIGINT)
