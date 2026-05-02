"""Input validation and security utilities."""
import re
from pathlib import Path

MAX_PROMPT_LENGTH = 100_000  # 100KB
MAX_CWD_DEPTH = 10
FORBIDDEN_CWD_PATTERNS = [".."]

class ValidationError(Exception):
    pass

def validate_prompt(prompt: str) -> str:
    if not prompt or not prompt.strip():
        raise ValidationError("Prompt cannot be empty")
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ValidationError(f"Prompt exceeds {MAX_PROMPT_LENGTH} characters")
    return prompt.strip()

def validate_cwd(cwd: str | None) -> Path | None:
    if cwd is None:
        return None
    for pattern in FORBIDDEN_CWD_PATTERNS:
        if pattern in cwd:
            raise ValidationError(f"Invalid cwd: contains '{pattern}'")
    resolved = Path(cwd).resolve()
    if not resolved.is_dir():
        raise ValidationError(f"cwd does not exist: {cwd}")
    return resolved

def validate_agent_name(name: str | None) -> str | None:
    if name is None:
        return None
    if not re.match(r"^[a-zA-Z0-9_-]{1,50}$", name):
        raise ValidationError(f"Invalid agent name: {name}")
    return name
