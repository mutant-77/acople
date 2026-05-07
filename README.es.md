<p align="center">
  <img src="img/header.png" alt="Header">
</p>

<h1 align="center">Acople</h1>

<p align="center">
  La forma más fácil de conectar tu app a un Agente IDE y usarlo de motor.
</p>

Languages: [Español](README.es.md) | [English](README.md) | [Français](README.fr.md)
---

## ¿Para quién es esto? 👀

Para **vos** que:
- Querés usar Claude Code, Gemini, OpenCode o cualquier agente desde tu app.
- No querés romperte la cabeza con configuraciones complejas.
- Querés algo que simplemente **funcione**.

---

## ¿Por qué Acople? 🚀

### Agente vs Modelo
Acople no solo llama a una API de texto; llama a un **Agente** con "manos" (herramientas, navegación web, acceso al sistema de archivos). Mientras que una API de LLM es solo un "cerebro" en un sandbox, Acople le da a ese cerebro la capacidad de actuar en tu entorno local.

### Puente y Normalización
Dejá de pelear con diferentes flags de CLI y formatos de salida inconsistentes. Acople ofrece una **interfaz unificada** para Claude Code, Gemini y otros. Un solo formato para controlarlos a todos.

### Tu terminal como una API
Como no podés ejecutar comandos de terminal desde un navegador web o una app móvil, el servidor de Acople actúa como un **puente seguro**, exponiendo tus agentes locales vía HTTP/SSE.

### Streaming en Tiempo Real
Acople se encarga del complejo parsing de los streams de la terminal, entregándote tokens limpios en tiempo real. Es la diferencia entre una app congelada y una experiencia fluida.

### Listo para Producción
Incluye **control de concurrencia** nativo, gestión del ciclo de vida de procesos y autenticación mediante API Key.

---

## Quick Start ⚡ (en 30 segundos)

```bash
# 1. Instalá
pip install acople[server]

# 2. Arrancá el servidor
uvicorn acople.server:app --port 8000
```

¡Listo! Ya podés usar el agente desde tu app.

---

## Uso básico 📦

```python
from acople import Acople

# Auto-detecta tu agente - no tenés que configurar nada
bridge = Acople()

# Mandá un prompt y recibís la respuesta
async for event in bridge.run("Hola, ¿quién sos?"):
    print(event.data.get("text"), end="")
```

O si preferís usar el servidor HTTP:

```bash
# La forma más fácil
curl -X POST http://localhost:8000/chat/simple \
  -H "Content-Type: application/json" \
  -d '{"prompt": "creá un hello world en python"}'
```

---

## Seguridad y Concurrencia 🛡️ (NUEVO)

Ahora Acople es apto para producción. Podés configurar estas variables de entorno:

- `ACOPLE_API_KEY`: Definí una clave secreta para proteger tus endpoints (ej. `export ACOPLE_API_KEY="mi_secreto"`). Luego pasala en el header `X-API-Key`.
- `ACOPLE_MAX_CONCURRENT`: Límite de sesiones simultáneas para no saturar tu compu (por defecto es `5`).
- `ACOPLE_CORS_ORIGINS`: Controlá quién puede pegarle a tu API (por defecto `http://localhost:*`).
- `OPENAI_API_KEY`: Requerida para generación de imágenes con `gpt-image-1`.

---

## API Endpoints 🌐

| Endpoint | Qué hace | Cuándo usarlo |
|----------|---------|--------------|
| `POST /chat/simple` | Solo pasá el prompt | Para algo rápido y fácil ✅ |
| `POST /chat` | Con más opciones | Cuando necesitás más control (ej. cwd, timeouts) |
| `POST /image/generate` | Genera imágenes | Generación de imágenes con gpt-image-1 🎨 |
| `GET /agents` | Lista agentes instalados | Para ver qué tenés disponible |
| `GET /models` | Lista modelos del agente | Para elegir un modelo específico |
| `GET /health` | ¿El servidor está vivo? | Check rápido de estado |
| `GET /ui` | Interfaz web integrada | Para probar tus agentes directo desde el navegador 🖥️ |
| `POST /interrupt` | Cancela lo que está corriendo | Para parar una sesión o todas |

---

## Interfaz de Pruebas Integrada 🖥️

Acople viene con una interfaz web moderna y lista para usar para probar tus agentes, chequear el estado de conexión y correr diagnósticos.

Solo iniciá tu servidor:
```bash
uvicorn acople.server:app --port 8000
```
Y abrí en tu navegador: **`http://localhost:8000/ui`**

---

## ¿No tenés un agente instalado? 🤔

Tranqui, instalá uno y ya:

```bash
# Elegí el que quieras:

# Claude Code (el más popular)
npm i -g @anthropic-ai/claude-code

# Gemini CLI (gratis)
npm i -g @google/gemini-cli

# OpenCode (open source)
npm i -g opencode

# Codex CLI
npm i -g @openai/codex
```

---

## Verificá que todo esté bien 🛠️

```bash
python -m acople.cli doctor
```

Te dice si tenés todo instalado y funcional.

---

## Errores útiles 💪

Si algo falla, Acople te dice **exactamente qué hacer**:

```text
# Antes (genérico y confuso)
Error: "El agente no está en PATH"

# Después (clarito)
Error: Claude no está instalado
→ Ejecutá: npm i -g @anthropic-ai/claude-code
```

---

## Cosas que podés hacer 🎯

Con Acople podés crear:

- Tu propio **coding assistant** personal
- **Code review** automático
- Generador de **tests**
- **Debugger** inteligente
- Lo que **se te ocurra** ✨

---

## Generación de Imágenes 🎨

Acople puede generar imágenes usando `gpt-image-1` de OpenAI:

```bash
# Configurá tu API Key de OpenAI
export OPENAI_API_KEY="sk-..."

# Generá una imagen
curl -X POST http://localhost:8000/image/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Una ciudad futurista al atardecer", "size": "1024x1024", "quality": "high"}'
```

O usalo desde Python:

```python
from acople import ImageBridge, ImageConfig

bridge = ImageBridge()
results = await bridge.generate(
    "Una ciudad futurista al atardecer",
    ImageConfig(size="1024x1024", quality="high")
)
# results[0].b64_data contiene la imagen codificada en base64
```

---

## Requisitos 📋

- Python 3.10+
- Al menos 1 agente CLI instalado (cualquiera de: `claude`, `gemini`, `opencode`, `codex`, `qwen`)

---

## Ejemplo completo 💻

```python
# client.py - tu app que usa el agente
import httpx
import json

def chat(prompt):
    with httpx.Client() as client:
        # Acordate de pasar la API Key si la configuraste!
        headers = {"X-API-Key": "mi_secreto"} 
        with client.stream("POST", "http://localhost:8000/chat", json={"prompt": prompt}, headers=headers) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    if event["type"] == "token":
                        print(event.get("text", ""), end="")

# ¡Usalo!
chat("creá un botón en HTML que diga 'Click me'")
```

---

MIT License