"""
Tests de Concurrencia — Límites y interrupciones por sesión
Cubre: server.py (MAX_CONCURRENT, ACTIVE_PROCESSES, /interrupt con session_id)
"""

from unittest.mock import MagicMock


class TestConcurrencyLimit:
    """Tests del límite MAX_CONCURRENT"""

    def test_max_concurrency_returns_429(self):
        """Cuando ACTIVE_PROCESSES >= MAX_CONCURRENT, devuelve 429."""
        from fastapi.testclient import TestClient

        from acople import server
        from acople.server import app

        # Fill ACTIVE_PROCESSES to max
        original = server.ACTIVE_PROCESSES.copy()
        original_max = server.MAX_CONCURRENT
        try:
            server.MAX_CONCURRENT = 2
            server.ACTIVE_PROCESSES["fake-1"] = MagicMock()
            server.ACTIVE_PROCESSES["fake-2"] = MagicMock()

            client = TestClient(app)
            response = client.post("/chat/simple", json={"prompt": "test"})

            assert response.status_code == 429
            assert "Max" in response.json()["detail"]
        finally:
            server.ACTIVE_PROCESSES.clear()
            server.ACTIVE_PROCESSES.update(original)
            server.MAX_CONCURRENT = original_max

    def test_max_concurrency_on_chat_full(self):
        """También se aplica a /chat."""
        from fastapi.testclient import TestClient

        from acople import server
        from acople.server import app

        original = server.ACTIVE_PROCESSES.copy()
        original_max = server.MAX_CONCURRENT
        try:
            server.MAX_CONCURRENT = 1
            server.ACTIVE_PROCESSES["fake-1"] = MagicMock()

            client = TestClient(app)
            response = client.post("/chat", json={"prompt": "test"})

            assert response.status_code == 429
        finally:
            server.ACTIVE_PROCESSES.clear()
            server.ACTIVE_PROCESSES.update(original)
            server.MAX_CONCURRENT = original_max

    def test_under_limit_allows_request(self):
        """Bajo el límite, la request pasa (puede fallar por otro motivo pero no 429)."""
        from fastapi.testclient import TestClient

        from acople import server
        from acople.server import app

        original_max = server.MAX_CONCURRENT
        try:
            server.MAX_CONCURRENT = 10
            client = TestClient(app)
            response = client.post("/chat/simple", json={"prompt": "hello"})

            # Should not be 429 (might be another error like agent not found)
            assert response.status_code != 429
        finally:
            server.MAX_CONCURRENT = original_max


class TestTargetedInterrupt:
    """Tests de /interrupt con session_id"""

    def test_interrupt_no_active_processes(self):
        """Sin procesos activos, devuelve ok con mensaje."""
        from fastapi.testclient import TestClient

        from acople import server
        from acople.server import app

        original = server.ACTIVE_PROCESSES.copy()
        try:
            server.ACTIVE_PROCESSES.clear()

            client = TestClient(app)
            response = client.post("/interrupt")

            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert "message" in data
        finally:
            server.ACTIVE_PROCESSES.clear()
            server.ACTIVE_PROCESSES.update(original)

    def test_interrupt_nonexistent_session_returns_404(self):
        """session_id inválido devuelve 404."""
        from fastapi.testclient import TestClient

        from acople import server
        from acople.server import app

        original = server.ACTIVE_PROCESSES.copy()
        try:
            # Need at least one active process for the code to not hit the "no active" branch
            server.ACTIVE_PROCESSES["real-session"] = MagicMock()

            client = TestClient(app)
            response = client.post("/interrupt?session_id=nonexistent-uuid")

            assert response.status_code == 404
        finally:
            server.ACTIVE_PROCESSES.clear()
            server.ACTIVE_PROCESSES.update(original)

    def test_interrupt_specific_session(self):
        """Interrupt de sesión específica llama terminate."""
        from fastapi.testclient import TestClient

        from acople import server
        from acople.server import app

        original = server.ACTIVE_PROCESSES.copy()
        try:
            mock_proc = MagicMock()
            mock_proc.returncode = None
            server.ACTIVE_PROCESSES["target-session"] = mock_proc

            client = TestClient(app)
            response = client.post("/interrupt?session_id=target-session")

            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert data["interrupted"] == 1
        finally:
            server.ACTIVE_PROCESSES.clear()
            server.ACTIVE_PROCESSES.update(original)

    def test_interrupt_all_processes(self):
        """Sin session_id, interrumpe todos los procesos activos."""
        from fastapi.testclient import TestClient

        from acople import server
        from acople.server import app

        original = server.ACTIVE_PROCESSES.copy()
        try:
            proc1 = MagicMock()
            proc1.returncode = None
            proc2 = MagicMock()
            proc2.returncode = None

            server.ACTIVE_PROCESSES["session-1"] = proc1
            server.ACTIVE_PROCESSES["session-2"] = proc2

            client = TestClient(app)
            response = client.post("/interrupt")

            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert data["interrupted"] == 2
        finally:
            server.ACTIVE_PROCESSES.clear()
            server.ACTIVE_PROCESSES.update(original)

    def test_interrupt_skips_dead_processes(self):
        """No cuenta procesos ya terminados."""
        from fastapi.testclient import TestClient

        from acople import server
        from acople.server import app

        original = server.ACTIVE_PROCESSES.copy()
        try:
            alive = MagicMock()
            alive.returncode = None
            dead = MagicMock()
            dead.returncode = 0

            server.ACTIVE_PROCESSES["alive"] = alive
            server.ACTIVE_PROCESSES["dead"] = dead

            client = TestClient(app)
            response = client.post("/interrupt")

            data = response.json()
            assert data["interrupted"] == 1
        finally:
            server.ACTIVE_PROCESSES.clear()
            server.ACTIVE_PROCESSES.update(original)
