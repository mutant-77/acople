"""
Fase 1: Setup Tests
Tests para pyproject.toml, CLI entry points, dependencies
"""

import subprocess
import sys
from pathlib import Path


class TestFase1Setup:
    """Fase 1: pyproject.toml, CLI, dependencies"""

    def test_pyproject_exists(self):
        """1.1 pyproject.toml existe"""
        root = Path(__file__).parent.parent
        assert (root / "pyproject.toml").exists(), "pyproject.toml debe existir"

    def test_pyproject_toml_valid(self):
        """1.1 pyproject.toml es válido"""
        root = Path(__file__).parent.parent
        pyproject = root / "pyproject.toml"
        content = pyproject.read_text()
        assert "[project]" in content
        assert 'name = "acople"' in content

    def test_dependencies_core(self):
        """1.3 Deps core definidas"""
        root = Path(__file__).parent.parent
        content = (root / "pyproject.toml").read_text()
        assert "httpx" in content

    def test_dependencies_server(self):
        """1.3 Deps server separadas"""
        root = Path(__file__).parent.parent
        content = (root / "pyproject.toml").read_text()
        assert "[project.optional-dependencies]" in content
        assert "server" in content

    def test_entry_point(self):
        """1.2 Entry point definido"""
        root = Path(__file__).parent.parent
        content = (root / "pyproject.toml").read_text()
        assert "acople = " in content

    def test_package_structure(self):
        """Paquete en formato correcto"""
        root = Path(__file__).parent.parent
        assert (root / "acople").exists()
        assert (root / "acople" / "__init__.py").exists()
        assert (root / "acople" / "bridge.py").exists()

    def test_cli_module_exists(self):
        """1.4 CLI module existe"""
        root = Path(__file__).parent.parent
        assert (root / "acople" / "cli.py").exists()

    def test_server_module_exists(self):
        """1.5 Server module existe"""
        root = Path(__file__).parent.parent
        assert (root / "acople" / "server.py").exists()

    def test_imports_work(self):
        """Importaciones funcionan"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from acople import Acople, detect_all_agents
            assert Acople is not None
            assert detect_all_agents is not None
        finally:
            sys.path.pop(0)

    def test_version_in_package(self):
        """Versión definida"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            import acople
            assert hasattr(acople, "__version__")
            assert acople.__version__ == "1.0.0"
        finally:
            sys.path.pop(0)


class TestFase1CLI:
    """Tests de CLI commands"""

    def test_cli_doctor_command(self):
        """1.4 acople doctor funciona"""
        root = Path(__file__).parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "acople.cli", "doctor"],
            cwd=root,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode in [0, 1]  # OK o no agent

    def test_cli_agents_command(self):
        """acople agents funciona"""
        root = Path(__file__).parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "acople.cli", "agents"],
            cwd=root,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert b"claude" in result.stdout or b"gemini" in result.stdout
