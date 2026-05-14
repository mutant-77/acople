"""
Acople - Universal bridge to IDE AI agents

Soporta: Claude Code, Gemini CLI, Codex, OpenCode, Qwen, y cualquier CLI de agente.

Usage:
    from acople import Acople
\n    bridge = Acople()  # auto-detecta el agente
    async for event in bridge.run("tu prompt"):
        print(event.data.get("text"), end="")

O como CLI:
    acople run "tu prompt"
    acople doctor
"""

from .bridge import (
    AGENT_CONFIGS,
    Acople,
    AcopleError,
    AgentConfig,
    AgentNotFoundError,
    BridgeEvent,
    EventType,
    detect_agent,
    detect_all_agents,
    detect_models,
    from_env,
    get_config,
)
from .image_bridge import (
    ImageBridge,
    ImageConfig,
    ImageResult,
)
_session_names = [
    "SessionManager",
    "resolve_session_id",
    "validate_session_id",
    "process_system_messages",
]

try:
    from .session import (  # noqa: F401
        SessionManager,
        process_system_messages,
        resolve_session_id,
        validate_session_id,
    )
    _HAS_SESSION = True
except ImportError:
    _HAS_SESSION = False

__version__ = "1.3.0"

__all__ = [
    "Acople",
    "AcopleError",
    "AgentNotFoundError",
    "BridgeEvent",
    "EventType",
    "AgentConfig",
    "AGENT_CONFIGS",
    "ImageBridge",
    "ImageConfig",
    "ImageResult",
    "detect_agent",
    "detect_all_agents",
    "detect_models",
    "from_env",
    "get_config",
]

if _HAS_SESSION:
    __all__.extend(_session_names)
