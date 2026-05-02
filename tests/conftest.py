"""
Pytest configuration y fixtures para Acople tests
"""

import os
import sys
from pathlib import Path

import pytest

# Agregar el proyecto al path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture
def clean_env():
    """Limpiar entorno antes de cada test"""
    old_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(old_env)


@pytest.fixture
def mock_which():
    """Mock shutil.which para testing"""
    from unittest.mock import patch
    return patch('acople.bridge.shutil.which')


# Configure asyncio para pytest
def pytest_configure(config):
    config.option.asyncio_mode = "auto"
