# Acople Security Guide

## Overview

Acople is a bridge that exposes CLI agents (Claude Code, OpenCode, Gemini, Codex,
Kilo, Qwen) via an HTTP/SSE API. Each agent has full access to the filesystem and
can execute arbitrary commands on the host machine. This document outlines the
security model, risks, and recommended mitigations.

## Risk profile

| Risk | Severity | Description |
|---|---|---|
| Arbitrary code execution | **Critical** | Agents run bash/read/write on the host. A malicious prompt can execute arbitrary commands. |
| Data exfiltration | **High** | An agent can read any file the server process has access to and return its contents. |
| Property file changes | **High** | Agents can create, modify, or delete files. |
| SSRF / network access | **Medium** | Agents can make outbound network calls from the host. |
| Resource exhaustion | **Medium** | An agent can consume CPU, RAM, or disk indefinitely. |

## Mitigations

### 1. API Key authentication

Set `ACOPLE_API_KEY` in the environment. All endpoints require this key via
`X-API-Key` header, `Authorization: Bearer <key>`, or `api_key` query parameter.

Without this, the server is open to anyone who can reach the port.

### 2. Isolated working directory

Set `ACOPLE_DEFAULT_CWD` to a dedicated, isolated directory. The agent runs
inside this working directory and its filesystem access is constrained by the
OS user's permissions.

```env
ACOPLE_DEFAULT_CWD=/home/acople/sandbox
```

### 3. Tool-Proxy mode (function calling)

When the client registers `tools` in the request, Acople enters **Tool-Proxy
mode** (see `PLAN.md` §3). In this mode:

- The agent's native tools (bash, read, write, edit) are **disabled** (Claude:
  `--tools ""`; other agents: enforced via early-terminate).
- The agent can only emit `<acople-tool>` markers; the server terminates the
  process as soon as a client-registered tool is emitted.
- The agent never executes anything on the host — it only describes what should
  be done.

This reduces risk from **critical** to **low** when the client is the one
executing the recommended actions.

### 4. Resource limits

- `ACOPLE_MAX_CONCURRENT`: limits concurrent agent processes (default 5).
- `ACOPLE_STREAM_IDLE_TIMEOUT`: kills streams with no output (default 300s).
- `ACOPLE_STREAM_MAX_DURATION`: absolute stream deadline (default 1800s).

### 5. CORS

The server uses an origin regex to validate CORS requests. Default:
`^https?://localhost(:\d+)?$`. Override via `ACOPLE_CORS_ORIGINS` environment
variable (set to a regex pattern).

### 6. Process isolation

Each request spawns a separate subprocess. There is no shared state between
processes. The `_cleanup_process` escalation (`SIGINT → SIGTERM → SIGKILL`)
ensures no orphan processes survive a request.

### 7. Session isolation

Session state is stored in an ephemeral SQLite database under `.acople/`.
The database is wiped on server restart. Use `X-Session-ID` for explicit
session continuity — without it, every request is stateless.

### 8. Known risky flags

Some agents accept flags that bypass security checks. These are **not**
recommended for production:

| Agent | Flags | Risk |
|---|---|---|
| opencode | `--dangerously-skip-permissions` | No system prompt permission check |
| kilo | `--auto` | Auto-approves all actions |
| gemini | `--skip-trust` | Skips trust validation |
| codex | `--skip-git-repo-check` | Bypasses git repo validation |

Tool-Proxy mode (when `tools` are registered) mitigates these flags because
the agent's native tools are disabled.

## Recommendations for production

1. **Always** set `ACOPLE_API_KEY`.
2. **Always** set `ACOPLE_DEFAULT_CWD` to an isolated directory.
3. Use `ACOPLE_CORS_ORIGINS` to restrict to known client origins.
4. Set `ACOPLE_MAX_CONCURRENT` to a reasonable limit.
5. Monitor agent output via logs (configure `LOG_LEVEL=INFO`).
6. **When using function calling (openclaw):** Tool-Proxy mode is active by
   default when `tools` are registered. This is the safest mode.
7. Run the server as a dedicated, unprivileged OS user.
