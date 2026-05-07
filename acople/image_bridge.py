"""
Acople — Image generation bridge via OpenAI gpt-image-1

Llama directamente a la API de OpenAI para generar imágenes.
Devuelve resultados en base64.
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from acople.bridge import AcopleError, BridgeEvent, EventType

logger = logging.getLogger("acople.image")

OPENAI_API_URL = "https://api.openai.com/v1/images/generations"

VALID_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}
VALID_QUALITIES = {"auto", "low", "medium", "high"}
VALID_OUTPUT_FORMATS = {"png", "jpeg", "webp"}


@dataclass
class ImageConfig:
    size: str = "auto"
    quality: str = "auto"
    n: int = 1
    output_format: str = "png"


@dataclass
class ImageResult:
    b64_data: str
    format: str
    revised_prompt: str | None = None


class ImageBridge:
    """Bridge para generación de imágenes con gpt-image-1."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise AcopleError(
                "OPENAI_API_KEY no está configurada. "
                "Ejecuta: export OPENAI_API_KEY=\"tu-key\""
            )

    async def generate(
        self,
        prompt: str,
        config: ImageConfig | None = None,
    ) -> list[ImageResult]:
        """Genera imagen(es) y devuelve resultados en base64."""
        cfg = config or ImageConfig()

        payload = {
            "model": "gpt-image-1",
            "prompt": prompt,
            "n": cfg.n,
            "size": cfg.size,
            "quality": cfg.quality,
            "output_format": cfg.output_format,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                response = await client.post(
                    OPENAI_API_URL,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
            except httpx.TimeoutException:
                raise AcopleError("Timeout: OpenAI tardó demasiado en responder (120s)")
            except httpx.HTTPStatusError as e:
                body = e.response.text
                raise AcopleError(f"OpenAI API error {e.response.status_code}: {body}")
            except httpx.RequestError as e:
                raise AcopleError(f"Error de conexión con OpenAI: {e}")

        data = response.json()
        results = []
        for item in data.get("data", []):
            results.append(ImageResult(
                b64_data=item.get("b64_json", ""),
                format=cfg.output_format,
                revised_prompt=item.get("revised_prompt"),
            ))

        return results

    async def generate_stream(
        self,
        prompt: str,
        config: ImageConfig | None = None,
    ) -> AsyncIterator[BridgeEvent]:
        """Genera imagen(es) emitiendo BridgeEvents para SSE."""
        yield BridgeEvent(EventType.SYSTEM, {"message": "Generating image..."})

        try:
            results = await self.generate(prompt, config)
            for i, result in enumerate(results):
                yield BridgeEvent(EventType.IMAGE, {
                    "b64": result.b64_data,
                    "format": result.format,
                    "index": i,
                    "total": len(results),
                    "revised_prompt": result.revised_prompt,
                })
        except AcopleError as e:
            yield BridgeEvent(EventType.ERROR, {"message": str(e)})

        yield BridgeEvent(EventType.DONE, {})
