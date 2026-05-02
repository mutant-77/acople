<p align="center">
  <img src="img/header.png" alt="Header">
</p>

<h1 align="center">Acople</h1>

<p align="center">
  The easiest way to connect your app to an IDE Agent and use it as an engine.
</p>

Languages: [Español](README.es.md) | [English](README.md) | [Français](README.fr.md)

---

## Who is this for? 👀

For **you** who:
- Wants to use Claude Code, Gemini, OpenCode, or any agent from your app.
- Doesn't want to break your head with complex configurations.
- Wants something that just **works**.

---

## Quick Start ⚡ (in 30 seconds)

```bash
# 1. Install
pip install acople[server]

# 2. Start the server
uvicorn acople.server:app --port 8000
```

Done! You can now use the agent from your app.

---

## Basic Usage 📦

```python
from acople import Acople

# Auto-detects your agent - no configuration needed
bridge = Acople()

# Send a prompt and receive the response
async for event in bridge.run("Hello, who are you?"):
    print(event.data.get("text"), end="")
```

Or if you prefer using the HTTP server:

```bash
# The easiest way
curl -X POST http://localhost:8000/chat/simple \
  -H "Content-Type: application/json" \
  -d '{"prompt": "create a python hello world"}'
```

---

## Security and Concurrency 🛡️ (NEW)

Acople is now production-ready. You can configure these environment variables:

- `ACOPLE_API_KEY`: Define a secret key to protect your endpoints (e.g. `export ACOPLE_API_KEY="my_secret"`). Then pass it in the `X-API-Key` header.
- `ACOPLE_MAX_CONCURRENT`: Limit of simultaneous sessions to avoid saturating your computer (default is `5`).
- `ACOPLE_CORS_ORIGINS`: Control who can hit your API (default `http://localhost:*`).

---

## API Endpoints 🌐

| Endpoint | What it does | When to use it |
|----------|---------|--------------|
| `POST /chat/simple` | Just pass the prompt | For something quick and easy ✅ |
| `POST /chat` | With more options | When you need more control (e.g. cwd, timeouts) |
| `GET /agents` | Lists installed agents | To see what's available |
| `GET /models` | Lists agent models | To choose a specific model |
| `GET /health` | Is the server alive? | Quick status check |
| `POST /interrupt` | Cancels what is running | To stop one session or all of them |

---

## Don't have an agent installed? 🤔

Don't worry, just install one:

```bash
# Choose the one you want:

# Claude Code (most popular)
npm i -g @anthropic-ai/claude-code

# Gemini CLI (free)
npm i -g @google/gemini-cli

# OpenCode (open source)
npm i -g opencode

# Codex CLI
npm i -g @openai/codex
```

---

## Verify everything is fine 🛠️

```bash
python -m acople.cli doctor
```

It tells you if everything is installed and functional.

---

## Useful Errors 💪

If something fails, Acople tells you **exactly what to do**:

```text
# Before (generic and confusing)
Error: "Agent is not in PATH"

# After (clear)
Error: Claude is not installed
→ Run: npm i -g @anthropic-ai/claude-code
```

---

## Things you can do 🎯

With Acople you can create:

- Your own personal **coding assistant**
- Automatic **code review**
- **Tests** generator
- Smart **debugger**
- Whatever **comes to your mind** ✨

---

## Requirements 📋

- Python 3.10+
- At least 1 CLI agent installed (any of: `claude`, `gemini`, `opencode`, `codex`, `qwen`)

---

## Complete Example 💻

```python
# client.py - your app that uses the agent
import httpx
import json

def chat(prompt):
    with httpx.Client() as client:
        # Remember to pass the API Key if you configured it!
        headers = {"X-API-Key": "my_secret"} 
        with client.stream("POST", "http://localhost:8000/chat", json={"prompt": prompt}, headers=headers) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    if event["type"] == "token":
                        print(event.get("text", ""), end="")

# Use it!
chat("create an HTML button that says 'Click me'")
```

---

MIT License
