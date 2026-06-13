"""
Smoke E2E — /v1/chat/completions contra claude real.

Requiere: servidor Acople corriendo en http://localhost:47334
y el binario `claude` disponible en PATH.

Verifica los 4 puntos de AC9.1:
  (a) Turno 1 con tools → finish_reason=tool_calls, id/nombre/args correctos
  (b) Turno 2 con tool_result → respuesta final usa el resultado
  (c) Request sin tools → finish_reason=stop
  (d) Marcador <acople-tool> nunca visible en content (I3)
"""

import json
import sys
import time
import urllib.request

BASE = "http://localhost:47334"
TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a location.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City and country, e.g. 'Madrid, Spain'"}
            },
            "required": ["location"],
        },
    },
}


def post(path: str, body: dict) -> dict | list:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def check_health():
    req = urllib.request.Request(BASE + "/health")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200, f"Health check failed: {resp.status}"
    print("[OK] Server is up")


def smoke_a_b():
    """(a) Turno 1 con tools + (b) Turno 2 con tool_result."""
    print("\n--- (a) Turno 1: request con tools ---")
    t1 = post("/v1/chat/completions", {
        "model": "claude",
        "stream": False,
        "tools": [TOOL],
        "messages": [
            {"role": "user", "content": "What is the weather in Tokyo, Japan?"}
        ],
    })

    print(f"    finish_reason = {t1['choices'][0]['finish_reason']}")
    tool_calls = t1["choices"][0]["message"].get("tool_calls", [])
    print(f"    tool_calls count = {len(tool_calls)}")

    assert t1["choices"][0]["finish_reason"] == "tool_calls", \
        f"(a) FAIL: finish_reason={t1['choices'][0]['finish_reason']!r}, expected 'tool_calls'"
    assert len(tool_calls) >= 1, "(a) FAIL: no tool_calls in response"

    tc = tool_calls[0]
    assert tc.get("id"), "(a) FAIL: tool_call has no id"
    assert tc["function"]["name"] == "get_weather", \
        f"(a) FAIL: wrong tool name {tc['function']['name']!r}"
    args = json.loads(tc["function"]["arguments"])
    assert "location" in args, f"(a) FAIL: missing 'location' in args: {args}"
    print(f"    id={tc['id']!r} name={tc['function']['name']!r} location={args['location']!r}")
    print("[OK] (a) finish_reason=tool_calls, tool call correct")

    # Check (d): no raw marker in content
    content = t1["choices"][0]["message"].get("content") or ""
    assert "<acople-tool>" not in content, "(d) FAIL: raw marker visible in content"
    print("[OK] (d) No raw <acople-tool> marker in content")

    # Turn 2
    print("\n--- (b) Turno 2: tool_result → respuesta final ---")
    t2 = post("/v1/chat/completions", {
        "model": "claude",
        "stream": False,
        "tools": [TOOL],
        "messages": [
            {"role": "user", "content": "What is the weather in Tokyo, Japan?"},
            {"role": "assistant", "tool_calls": tool_calls},
            {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": '{"temperature": "22°C", "condition": "Sunny"}',
            },
        ],
    })

    fr2 = t2["choices"][0]["finish_reason"]
    content2 = t2["choices"][0]["message"].get("content") or ""
    print(f"    finish_reason = {fr2}")
    print(f"    content snippet = {content2[:120]!r}")

    assert fr2 in ("stop", "tool_calls"), f"(b) FAIL: unexpected finish_reason={fr2!r}"
    assert "22" in content2 or "sunny" in content2.lower() or "Tokyo" in content2, \
        f"(b) FAIL: final response doesn't seem to use tool result: {content2[:200]!r}"
    assert "<acople-tool>" not in content2, "(d) FAIL: raw marker in turno 2 content"
    print("[OK] (b) Turno 2 uses tool result; (d) no raw marker")


def smoke_c():
    """(c) Request sin tools → finish_reason=stop."""
    print("\n--- (c) Request sin tools: stop normal ---")
    t = post("/v1/chat/completions", {
        "model": "claude",
        "stream": False,
        "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
    })
    fr = t["choices"][0]["finish_reason"]
    content = t["choices"][0]["message"].get("content") or ""
    print(f"    finish_reason={fr!r}  content={content[:80]!r}")
    assert fr == "stop", f"(c) FAIL: finish_reason={fr!r}, expected 'stop'"
    print("[OK] (c) finish_reason=stop")


def main():
    print("=== Acople Smoke E2E ===")
    try:
        check_health()
    except Exception as e:
        print(f"[FAIL] Cannot reach server: {e}")
        sys.exit(1)

    results = {}
    for name, fn in [("a_b", smoke_a_b), ("c", smoke_c)]:
        try:
            fn()
            results[name] = "PASS"
        except AssertionError as e:
            print(f"[FAIL] {e}")
            results[name] = f"FAIL: {e}"
        except Exception as e:
            print(f"[ERROR] {e}")
            results[name] = f"ERROR: {e}"

    print("\n=== Results ===")
    for k, v in results.items():
        print(f"  {k}: {v}")

    failed = [k for k, v in results.items() if not v.startswith("PASS")]
    if failed:
        print(f"\nFailed: {failed}")
        sys.exit(1)
    else:
        print("\nAll smoke checks passed.")


if __name__ == "__main__":
    main()
