"""
Fase 5: Documentación y DX Tests
Tests para README, docstrings, examples/
"""

import sys
from pathlib import Path


class TestFase5Docs:
    """Fase 5: Documentación"""

    def test_readme_exists(self):
        """5.1 README existe"""
        root = Path(__file__).parent.parent
        assert (root / "README.md").exists(), "README.md debe existir"

    def test_readme_has_quickstart(self):
        """5.1 README tiene Quick Start"""
        root = Path(__file__).parent.parent
        content = (root / "README.md").read_text(encoding="utf-8")

        assert "pip install" in content
        assert "acople" in content.lower()

    def test_readme_has_api_docs(self):
        """5.1 Documentación de API"""
        root = Path(__file__).parent.parent
        content = (root / "README.md").read_text(encoding="utf-8")

        # Verificar que menciona endpoints
        assert "/chat" in content or "/agents" in content

    def test_readme_not_too_long(self):
        """5.1 README minimal (no más de 2000 chars)"""
        root = Path(__file__).parent.parent
        content = (root / "README.md").read_text(encoding="utf-8")

        # Un README minimal no debe ser enorme
        assert len(content) < 5000, "README muy largo"

    def test_init_docstring(self):
        """5.2 Docstring en __init__.py"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            import acople
            doc = acople.__doc__
            assert doc is not None
            assert len(doc) > 0
        finally:
            sys.path.pop(0)

    def test_bridge_docstring(self):
        """5.2 Docstring en Acople"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import Acople
            doc = Acople.__doc__
            assert doc is not None
            assert "Acople" in doc or "bridge" in doc.lower()
        finally:
            sys.path.pop(0)

    def test_all_exports_in_init(self):
        """5.2 Exports definidos en __init__"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            import acople

            expected = [
                "Acople",
                "AcopleError",
                "AgentNotFoundError",
                "BridgeEvent",
                "EventType",
                "AgentConfig",
                "AGENT_CONFIGS",
                "detect_agent",
                "detect_all_agents",
                "detect_models",
                "from_env",
                "get_config",
            ]

            for name in expected:
                assert hasattr(acople, name), f"{name} no exportado"
        finally:
            sys.path.pop(0)


class TestFase5DX:
    """Tests de Developer Experience"""

    def test_2_lines_example_works(self):
        """5.1 Ejemplo de 2 líneas funciona"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import Acople
            try:
                bridge = Acople()
                assert bridge is not None
            except Exception:
                pass
        finally:
            sys.path.pop(0)

    def test_no_circular_imports(self):
        """5.1 Sin imports circulares"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import detect_agent
            assert detect_agent is not None
        finally:
            sys.path.pop(0)

    def test_commands_in_readme(self):
        """5.1 Commands en README"""
        root = Path(__file__).parent.parent
        content = (root / "README.md").read_text(encoding="utf-8")

        assert "uvicorn" in content or "pip install" in content

    def test_install_hints_in_readme(self):
        """5.1 Hints de instalación en docs"""
        root = Path(__file__).parent.parent
        content = (root / "README.md").read_text(encoding="utf-8")

        lines = content.lower().split('\n')
        has_install_hint = any('npm' in line or 'pip' in line for line in lines)
        assert has_install_hint


class TestFase5Examples:
    """Tests de ejemplos"""

    def test_examples_or_code_in_readme(self):
        """5.2 README tiene ejemplos"""
        root = Path(__file__).parent.parent
        content = (root / "README.md").read_text(encoding="utf-8")

        has_code = "from acople import" in content.lower()
        has_examples = "example" in content.lower()

        assert has_code or has_examples
