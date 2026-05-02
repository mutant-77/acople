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

__version__ = "1.0.0"

__all__ = [
    "Acople",
    "AcopleError",
    "AgentNotFoundError",
    "BridgeEvent",
    "EventType",
    "AgentConfig",
    "AGENT_CONFIGS",
    "detect_agent",
    "detect_all_agents",
    "detect_models",
    "from_env",
    "get_config",
]
