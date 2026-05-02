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

---

## API Endpoints 🌐

| Endpoint | Qué hace | Cuándo usarlo |
|----------|---------|--------------|
| `POST /chat/simple` | Solo pasá el prompt | Para algo rápido y fácil ✅ |
| `POST /chat` | Con más opciones | Cuando necesitás más control (ej. cwd, timeouts) |
| `GET /agents` | Lista agentes instalados | Para ver qué tenés disponible |
| `GET /models` | Lista modelos del agente | Para elegir un modelo específico |
| `GET /health` | ¿El servidor está vivo? | Check rápido de estado |
| `POST /interrupt` | Cancela lo que está corriendo | Para parar una sesión o todas |

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