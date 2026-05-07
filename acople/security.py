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


# ---------------------------------------------------------------------------
# Image parameter validation (gpt-image-1)
# ---------------------------------------------------------------------------

_VALID_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}
_VALID_QUALITIES = {"auto", "low", "medium", "high"}
_VALID_OUTPUT_FORMATS = {"png", "jpeg", "webp"}

def validate_image_size(size: str) -> str:
    if size not in _VALID_SIZES:
        raise ValidationError(f"Invalid size '{size}'. Valid: {', '.join(sorted(_VALID_SIZES))}")
    return size

def validate_image_quality(quality: str) -> str:
    if quality not in _VALID_QUALITIES:
        raise ValidationError(f"Invalid quality '{quality}'. Valid: {', '.join(sorted(_VALID_QUALITIES))}")
    return quality

def validate_image_n(n: int) -> int:
    if not 1 <= n <= 10:
        raise ValidationError(f"n must be between 1 and 10, got {n}")
    return n

def validate_image_output_format(fmt: str) -> str:
    if fmt not in _VALID_OUTPUT_FORMATS:
        raise ValidationError(f"Invalid output_format '{fmt}'. Valid: {', '.join(sorted(_VALID_OUTPUT_FORMATS))}")
    return fmt

