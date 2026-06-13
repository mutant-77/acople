# Acople — OpenAI-Compatible Endpoint

`POST /v1/chat/completions` is a drop-in replacement for the OpenAI Chat Completions API.
Target client: **openclaw** (function calling over OpenAI-compat layer).

## Quick start

```bash
curl http://localhost:47334/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ACOPLE_API_KEY" \
  -d '{"model": "claude", "messages": [{"role": "user", "content": "hello"}], "stream": true}'
```

## Request parameters

| Parameter | Type | Notes |
|---|---|---|
| `model` | string | Maps to agent binary. `claude` → Claude Code CLI. Unknown model → falls back to `$ACOPLE_AGENT` or `claude`. |
| `messages` | array | Full conversation history (stateless by default). |
| `stream` | bool | `true` for SSE chunks, `false` for a single blocking JSON object. |
| `tools` | array | Triggers Tool-Proxy mode (see below). |
| `tool_choice` | object/string | Forwarded as a hint to the agent prompt. |
| `stream_options` | object | `{"include_usage": true}` to receive `usage` fields on chunks. |
| `response_format` | object | `{"type": "json_object"}` or `{"type": "json_schema"}`. **Ignored when `tools` are present** (tools take precedence — F7). |

## Response contract

### `finish_reason`

| Value | Condition |
|---|---|
| `"stop"` | Agent produced a text response with no client-registered tool calls. |
| `"tool_calls"` | Agent emitted ≥1 `<acople-tool>` marker; agent process was force-terminated after the last tool. |

No other `finish_reason` values are emitted.

### `usage`

Token counts are **estimated** (`len(text) / 4`). They are not exact and must not be used for billing or quota tracking. When `stream_options: {include_usage: true}`, usage fields are attached to each chunk (`null` on intermediate chunks, populated on the final `finish_reason` chunk).

### Error shape (I9)

All errors that reach the wire — stream or non-stream — have this exact shape:

```json
{
  "error": {
    "message": "human-readable description",
    "type": "server_error",
    "param": null,
    "code": null
  }
}
```

| `type` | When |
|---|---|
| `"server_error"` | Agent failure, bridge error, idle/duration timeout. |
| `"invalid_request_error"` | Missing `messages`, invalid JSON body. |

**Streaming:** an error chunk is always followed by a final `finish_reason` chunk and `data: [DONE]`. The stream never hangs open.

**Non-streaming:** HTTP 502 with the error body above. There is no `"detail"` key (that is FastAPI's default shape, not used here).

## Tool-Proxy mode

Triggered when the request includes a non-empty `tools` array.

### Pipeline

1. Agent launched with **native tools disabled** (`--tools ""` for claude; other agents: enforced via early-terminate).
2. Tool catalog injected as text into the prompt; agent instructed to emit `<acople-tool>{...}</acople-tool>` and stop.
3. Server parses `<acople-tool>` markers from the agent output stream.
4. After ≥1 tool call: agent process is **force-terminated** and the turn closes with `finish_reason="tool_calls"`.
5. Parallel tool calls within a single assistant turn are supported: consecutive markers before termination are all emitted as separate `tool_calls` entries.

### Loop-guard

Requires a stable `X-Session-ID`. When the agent repeats the **identical** tool call (same name + same arguments) in two consecutive turns under the same session, the server intercepts the second call, emits a `[acople]` note as content, and closes the turn with `finish_reason="stop"`. The guard is **one-shot**: it fires once and resets — a legitimate repeated call on the following turn passes through.

## Limitations

| Limitation | Detail |
|---|---|
| **Parallel tool calls are intra-turn only** | Multiple tool calls within one assistant turn (F6) are supported. Cross-message parallel execution (tool calls that span multiple HTTP requests) is not. |
| **Agent degradation for non-claude backends** | Only `claude` (Claude Code CLI) guarantees reliable marker emission with native tools disabled (D3). Other agents (`gemini`, `codex`, `opencode`, `kilo`, `qwen`) are best-effort: early-terminate enforces the turn boundary but marker compliance varies. Use `claude` for function calling. |
| **MCP tools escape** | `--tools ""` disables all of claude's native tools, including MCP-configured ones. This is intentional: the client's `tools` array is the sole source of tool definitions in Tool-Proxy mode. Individual native tools cannot be selectively preserved — keep those requests out of Tool-Proxy mode. |
| **`usage` is estimated** | Approximation only; do not use for billing. |
| **`response_format` ignored with tools** | JSON-mode directive contradicts the tool catalog; tools take precedence. |

## Session management

By default the endpoint is **stateless**: every request is independent and the client sends the full conversation history. No server-side memory is used.

To opt into server-side memory, send a stable identifier in the `X-Session-ID` header:

```
X-Session-ID: my-stable-session-id
```

With a stable session ID the server stores assistant responses and tool uses in an ephemeral SQLite database. The database is wiped on server restart. The loop-guard (above) requires a stable session ID to be active.

## Model → agent mapping

The `model` field is mapped to a local agent binary. The mapping is prefix-based:

| `model` value | Agent used |
|---|---|
| `claude`, `claude-*` | `claude` (Claude Code CLI) |
| `gemini`, `gemini-*` | `gemini` |
| `codex`, `codex-*` | `codex` |
| `opencode` | `opencode` |
| `kilo` | `kilo` |
| `qwen` | `qwen` |
| anything else | Falls back to `$ACOPLE_AGENT` or `claude` |

## Security

See [security.md](security.md) for the full risk profile and deployment recommendations.

Key rules:
- Always set `ACOPLE_API_KEY`.
- Always set `ACOPLE_DEFAULT_CWD` to an isolated directory (the agent runs there with full filesystem access for the OS user).
- Tool-Proxy mode reduces risk from critical to low: the agent cannot execute anything — it only describes intended actions as `tool_calls`.
