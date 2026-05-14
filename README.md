<p align="center">
  <img src="img/header.png" alt="Header">
</p>

<h1 align="center">Acople</h1>

<p align="center">
  Turn terminal AI agents (like Claude or Gemini) into a local API for your own apps.
</p>

Languages: [Español](README.es.md) | [English](README.md) | [Français](README.fr.md)

---

## Who is this for? 👀

For **you** who:
- Wants to use Claude Code, Gemini, OpenCode, or any agent from your app.
- Doesn't want to break your head with complex configurations.
- Wants something that just **works**.

---

## Why Acople? 🚀

### Agent vs Model
Acople doesn't just call a text API; it calls an **Agent** with "hands" (tools, web browsing, file system access). While an LLM API is just a "brain" in a sandbox, Acople gives that brain the ability to act on your local environment.

### Bridge & Normalization
Stop fighting with different CLI flags and inconsistent output formats. Acople provides a **unified interface** for Claude Code, Gemini, and others. One format to rule them all.

### Your Terminal as an API
Since you can't run CLI commands from a web browser or a mobile app, Acople's server component acts as a **secure bridge**, exposing your local agents via HTTP/SSE.

### Real-time Streaming
Acople handles the complex parsing of terminal streams, giving you clean, real-time tokens. It's the difference between a frozen app and a live experience.

### Production Ready
Built-in **concurrency control**, process lifecycle management, and API Key authentication.

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
- `OPENAI_API_KEY`: Required for image generation with `gpt-image-1`.

---

## Persistent Session Memory 🧠 (NEW)

Acople now features an intelligent persistence system (**Compactor**) enabled by default, turning your CLI agents into long-term memory assistants:

- **Automatic Folder Context:** When working on a project (CWD), Acople automatically associates and remembers previous conversations based on your file location.
- **Infinite History (Sliding Window):** Intelligently manages context to keep the most relevant messages even in very long chat sessions.
- **Cross-Agent Memory:** You can start a complex task with Kilo and then switch to Claude to finish it; the memory will persist seamlessly as it is tied to your project, not the agent.
- **Ephemeral Location:** Everything is stored locally in `./.acople/sessions.db`. This folder is **automatically deleted every time you restart the server**, ensuring a clean slate on every start.
- **Configuration:** Enabled by default. You can disable it by setting the environment variable `ACOPLE_SESSIONS="false"` if you prefer stateless mode.

---

## API Endpoints 🌐

| Endpoint | What it does | When to use it |
|----------|---------|--------------|
| `POST /chat/simple` | Just pass the prompt | For something quick and easy ✅ |
| `POST /chat` | With more options | When you need more control (e.g. cwd, timeouts) |
| `POST /image/generate` | Generate images | Image generation with gpt-image-1 🎨 |
| `GET /agents` | Lists installed agents | To see what's available |
| `GET /models` | Lists agent models | To choose a specific model |
| `GET /health` | Is the server alive? | Quick status check |
| `GET /ui` | Built-in web interface | To test your agents directly from the browser 🖥️ |
| `POST /interrupt` | Cancels what is running | To stop one session or all of them |
| `POST /v1/chat/completions` | OpenAI compatible chat | Use Acople as a backend for any AI tool 🔌 |
| `GET /v1/models` | OpenAI models list | Compatibility with OpenAI spec |

---

## Built-in Test UI 🖥️

Acople comes with a modern, ready-to-use web interface to test your agents, check connection status, and run diagnostics.

Just start your server:
```bash
uvicorn acople.server:app --port 8000
```
And open in your browser: **`http://localhost:8000/ui`**

---

## OpenAI Compatibility 🔌

Acople can act as a local **OpenAI-compatible gateway**. This means you can point any tool that supports OpenAI (like [NullClaw](https://github.com/nullclaw/nullclaw), *Continue*, *Cursor*, etc.) to your local Acople server.

**Configuration for your tools:**
- **Base URL:** `http://localhost:8000/v1`
- **API Key:** `any-string` (or your `ACOPLE_API_KEY`)
- **Model:** `acople/claude`, `acople/gemini`, etc.

Now your favorite CLI agents are available as a standard API!

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

# Kilo (fork of OpenCode)
npm i -g kilo

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

## Image Generation 🎨

Acople can generate images using OpenAI's `gpt-image-1`:

```bash
# Set your OpenAI API key
export OPENAI_API_KEY="sk-..."

# Generate an image
curl -X POST http://localhost:8000/image/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "A futuristic city at sunset", "size": "1024x1024", "quality": "high"}'
```

Or use it from Python:

```python
from acople import ImageBridge, ImageConfig

bridge = ImageBridge()
results = await bridge.generate(
    "A futuristic city at sunset",
    ImageConfig(size="1024x1024", quality="high")
)
# results[0].b64_data contains the base64-encoded image
```

---

## Requirements 📋

- Python 3.10+
- At least 1 CLI agent installed (any of: `claude`, `gemini`, `opencode`, `kilo`, `codex`, `qwen`)

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
