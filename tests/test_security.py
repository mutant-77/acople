"""
Tests de Seguridad — Validación de input y autenticación
Cubre: security.py (validate_prompt, validate_cwd, validate_agent_name)
       server.py (verify_api_key middleware)
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest


class TestPromptValidation:
    """Tests de validate_prompt"""

    def test_prompt_empty_rejected(self):
        from acople.security import ValidationError, validate_prompt

        with pytest.raises(ValidationError, match="empty"):
            validate_prompt("")

    def test_prompt_whitespace_only_rejected(self):
        from acople.security import ValidationError, validate_prompt

        with pytest.raises(ValidationError, match="empty"):
            validate_prompt("   \t\n  ")

    def test_prompt_too_long_rejected(self):
        from acople.security import MAX_PROMPT_LENGTH, ValidationError, validate_prompt

        with pytest.raises(ValidationError, match="exceeds"):
            validate_prompt("x" * (MAX_PROMPT_LENGTH + 1))

    def test_prompt_at_max_length_passes(self):
        from acople.security import MAX_PROMPT_LENGTH, validate_prompt

        result = validate_prompt("x" * MAX_PROMPT_LENGTH)
        assert len(result) == MAX_PROMPT_LENGTH

    def test_prompt_valid_passes(self):
        from acople.security import validate_prompt

        assert validate_prompt("hello world") == "hello world"

    def test_prompt_strips_whitespace(self):
        from acople.security import validate_prompt

        assert validate_prompt("  hello  ") == "hello"


class TestCwdValidation:
    """Tests de validate_cwd"""

    def test_cwd_traversal_blocked(self):
        from acople.security import ValidationError, validate_cwd

        with pytest.raises(ValidationError, match="\\.\\."):
            validate_cwd("../../etc")

    def test_cwd_double_dot_in_middle_blocked(self):
        from acople.security import ValidationError, validate_cwd

        with pytest.raises(ValidationError, match="\\.\\."):
            validate_cwd("/home/user/../root")

    def test_cwd_nonexistent_blocked(self):
        from acople.security import ValidationError, validate_cwd

        with pytest.raises(ValidationError, match="does not exist"):
            validate_cwd("/absolutely/nonexistent/path/12345")

    def test_cwd_none_passes(self):
        from acople.security import validate_cwd

        assert validate_cwd(None) is None

    def test_cwd_valid_passes(self):
        from acople.security import validate_cwd

        result = validate_cwd(os.getcwd())
        assert isinstance(result, Path)
        assert result.is_dir()


class TestAgentNameValidation:
    """Tests de validate_agent_name"""

    def test_agent_name_valid(self):
        from acople.security import validate_agent_name

        assert validate_agent_name("claude") == "claude"

    def test_agent_name_with_hyphens_and_underscores(self):
        from acople.security import validate_agent_name

        assert validate_agent_name("my-agent_v2") == "my-agent_v2"

    def test_agent_name_injection_blocked(self):
        from acople.security import ValidationError, validate_agent_name

        with pytest.raises(ValidationError):
            validate_agent_name("claude;rm -rf /")

    def test_agent_name_with_spaces_blocked(self):
        from acople.security import ValidationError, validate_agent_name

        with pytest.raises(ValidationError):
            validate_agent_name("claude code")

    def test_agent_name_too_long_blocked(self):
        from acople.security import ValidationError, validate_agent_name

        with pytest.raises(ValidationError):
            validate_agent_name("x" * 51)

    def test_agent_name_none_passes(self):
        from acople.security import validate_agent_name

        assert validate_agent_name(None) is None

    def test_agent_name_empty_blocked(self):
        from acople.security import ValidationError, validate_agent_name

        with pytest.raises(ValidationError):
            validate_agent_name("")


class TestAuthentication:
    """Tests del middleware verify_api_key"""

    def test_no_key_configured_allows_all(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        with patch.dict(os.environ, {}, clear=False):
            with patch("acople.server.API_KEY", None):
                client = TestClient(app)
                response = client.get("/health")
                assert response.status_code == 200

    def test_valid_key_accepted(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        with patch("acople.server.API_KEY", "test_secret_key"):
            client = TestClient(app)
            response = client.get("/health", headers={"X-API-Key": "test_secret_key"})
            assert response.status_code == 200

    def test_invalid_key_rejected(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        with patch("acople.server.API_KEY", "test_secret_key"):
            client = TestClient(app)
            response = client.get("/health", headers={"X-API-Key": "wrong_key"})
            assert response.status_code == 401

    def test_missing_key_rejected(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        with patch("acople.server.API_KEY", "test_secret_key"):
            client = TestClient(app)
            response = client.get("/health")
            assert response.status_code == 401

    def test_key_via_query_param(self):
        from fastapi.testclient import TestClient

        from acople.server import app

        with patch("acople.server.API_KEY", "test_secret_key"):
            client = TestClient(app)
            response = client.get("/health?api_key=test_secret_key")
            assert response.status_code == 200
