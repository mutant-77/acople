"""
Acople CLI - Commands for terminal usage

Usage:
    acople run "prompt"         # Ejecuta un prompt
    acople doctor             # Verifica el setup
    acople agents           # Lista agentes disponibles
    acople detect          # Auto-detecta setup completo
"""

import asyncio
import sys

import httpx

from acople import (
    Acople,
    AgentNotFoundError,
    EventType,
    detect_all_agents,
)


def main():
    """Entry point for CLI."""
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "run":
        asyncio.run(cmd_run())
    elif cmd == "doctor":
        asyncio.run(cmd_doctor())
    elif cmd == "agents":
        cmd_agents()
    elif cmd == "detect":
        asyncio.run(cmd_detect())
    elif cmd in ("-h", "--help"):
        print_usage()
    else:
        print(f"Unknown command: {cmd}")
        print_usage()
        sys.exit(1)


def print_usage():
    print("""
Acople CLI

Usage:
    acople run "prompt"         # Ejecuta un prompt
    acople doctor             # Verifica el setup
    acople agents           # Lista agentes disponibles
    acople detect          # Auto-detecta setup completo

Examples:
    acople run "hola mundo"
    acople run "explicate asyncio" --agent claude
    acople run "que puedes hacer?" --cwd /mi/proyecto
""")


async def cmd_run():
    """Ejecuta un prompt."""
    args = sys.argv[2:]
    prompt = None
    agent = None
    cwd = None

    i = 0
    while i < len(args):
        if args[i] == "--agent" and i + 1 < len(args):
            agent = args[i + 1]
            i += 2
        elif args[i] == "--cwd" and i + 1 < len(args):
            cwd = args[i + 1]
            i += 2
        elif not args[i].startswith("-"):
            prompt = " ".join(args[i:])
            break
        else:
            i += 1

    if not prompt:
        print("Error: se requiere un prompt")
        sys.exit(1)

    try:
        bridge = Acople(agent)
    except AgentNotFoundError as e:
        print(f"Error: {e}")
        print("\nSolucion: instala un agente CLI (claude, gemini, opencode, kilo, codex, qwen)")
        sys.exit(1)

    try:
        async for event in bridge.run(prompt, cwd=cwd):
            if event.type == EventType.TOKEN:
                print(event.data.get("text"), end="", flush=True)
            elif event.type == EventType.DONE:
                print("\n[OK]")
            elif event.type == EventType.ERROR:
                print(f"\n[ERROR] {event.data.get('message')}")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)


async def cmd_doctor():
    """Verifica el setup del sistema."""
    print("[*] Acople Doctor")
    print("=" * 40)

    # Check Python
    print(f"\n[Python] {sys.version.split()[0]}")

    # Check agents
    print("\n[Agentes] Buscando en PATH...")
    agents = detect_all_agents()

    installed = [a for a, ok in agents.items() if ok]
    missing = [a for a, ok in agents.items() if not ok]

    if installed:
        print(f"  [OK] Instalados: {', '.join(installed)}")
    if missing:
        print(f"  [--] No instalados: {', '.join(missing)}")

    if not installed:
        print("\n[!] No se encontro ningun agente.")
        print("\n  Instala uno de:")
        print("    - Claude Code: npm i -g @anthropic-ai/claude-code")
        print("    - Gemini CLI: npm i -g @google/gemini-cli")
        print("    - Codex CLI: npm i -g @openai/codex")
        print("    - OpenCode: npm i -g opencode")
        print("    - Kilo: npm i -g kilo")

    # Try to create bridge
    print("\n[Bridge] Validando...")
    try:
        bridge = Acople()
        print(f"  [OK] Listo con: {bridge.agent}")
    except AgentNotFoundError as e:
        print(f"  [--] Error: {e}")

    # Check server (optional)
    print("\n[Server] Verificando...")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://localhost:8000/health", timeout=2.0)
            if r.status_code == 200:
                print("  [OK] Servidor corriendo en localhost:8000")
            else:
                print(f"  [!] Servidor respondio: {r.status_code}")
    except httpx.ConnectError:
        print("  [--] No hay servidor corriendo (opcional)")
    except Exception as e:
        print(f"  [!] Error conectando: {e}")

    print("\n" + "=" * 40)
    if installed:
        print("[OK] Setup listo!")
    else:
        print("[!] Instala al menos un agente para usar Acople")


def cmd_agents():
    """Lista agentes disponibles."""
    agents = detect_all_agents()
    print("Agentes disponibles:")
    for name, ok in agents.items():
        status = "[OK]" if ok else "[--]"
        print(f"  {status} {name}")


async def cmd_detect():
    """Auto-detecta setup completo."""
    print("[*] Detectando setup completo...")

    agents = detect_all_agents()
    for name, ok in agents.items():
        if ok:
            print(f"\n[OK] {name} disponible")
            # Try to get models
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(f"http://localhost:8000/models?agent={name}", timeout=5.0)
                    if r.status_code == 200:
                        data = r.json()
                        if data.get("models"):
                            print(f"  Modelos: {', '.join(data['models'])}")
            except Exception:
                pass

    # Check server
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://localhost:8000/health", timeout=2.0)
            if r.status_code == 200:
                print("\n[OK] Servidor activo en localhost:8000")
    except Exception:
        print("\n[!] Servidor no activo (ejecuta 'uvicorn acople.server:app' para iniciar)")


if __name__ == "__main__":
    main()
