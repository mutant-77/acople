"""Tests del módulo COMPACTOR (acople/session.py)."""

import hashlib
import os
import threading
import time

import pytest

from acople.session import (
    ALLOWED_META_KEYS,
    VALID_ROLES,
    SessionManager,
    _escape_fts5_term,
    _msg_hash,
    _normalize_content,
    _tokenize_terms,
    _truncate_tool_output,
    process_system_messages,
    resolve_session_id,
    validate_session_id,
)


class TestValidateSessionId:
    def test_valid_ids(self):
        assert validate_session_id("abc-123_def") == "abc-123_def"
        assert validate_session_id("a" * 64) == "a" * 64

    def test_invalid_ids(self):
        with pytest.raises(ValueError, match="Invalid session_id"):
            validate_session_id("../../../etc/passwd")
        with pytest.raises(ValueError, match="Invalid session_id"):
            validate_session_id("")
        with pytest.raises(ValueError, match="Invalid session_id"):
            validate_session_id("a" * 65)
        with pytest.raises(ValueError, match="Invalid session_id"):
            validate_session_id("bad char!")


class TestMsgHash:
    def test_deterministic(self):
        assert _msg_hash("user", "hola") == _msg_hash("user", "hola")

    def test_different_role_same_content(self):
        assert _msg_hash("user", "hola") != _msg_hash("assistant", "hola")


class TestTruncateToolOutput:
    def test_short_preserved(self):
        assert _truncate_tool_output("short") == "short"

    def test_long_truncated(self):
        long_str = "x" * 5000
        result = _truncate_tool_output(long_str)
        assert len(result) < 5000
        assert "[... truncated:" in result


class TestTokenizeTerms:
    def test_basic(self):
        terms = _tokenize_terms("hola mundo desde python")
        assert "hola" in terms
        assert "mundo" in terms
        assert "desde" in terms  # 5 chars >= min_len=4
        assert "python" in terms

    def test_with_accents(self):
        terms = _tokenize_terms("función búsqueda")
        assert "función" in terms
        assert "búsqueda" in terms

    def test_short_words_filtered(self):
        terms = _tokenize_terms("a de el por")
        assert terms == ""


class TestProcessSystemMessages:
    def test_extracts_system_and_cwd(self):
        msgs = [
            {"role": "system", "content": "Eres un asistente.\nworking directory: /home/user/project"},
            {"role": "user", "content": "hola"},
        ]
        system, cwd = process_system_messages(msgs)
        assert "Eres un asistente" in system
        assert cwd == "/home/user/project"

    def test_no_cwd(self):
        msgs = [{"role": "system", "content": "Eres un asistente."}]
        system, cwd = process_system_messages(msgs)
        assert cwd is None

    def test_no_system(self):
        msgs = [{"role": "user", "content": "hola"}]
        system, cwd = process_system_messages(msgs)
        assert system == ""
        assert cwd is None


class TestResolveSessionId:
    def test_from_header(self):
        sid = resolve_session_id({"X-Session-ID": "custom-id"}, [])
        assert sid == "custom-id"

    def test_system_cwd_ignored_uses_fallback(self):
        """CWD en system message ya no afecta el session_id.
        Se eliminó step 2 porque CWD puede estar ausente en algunos requests,
        causando que el session_id cambie entre turnos."""
        sid = resolve_session_id({}, [
            {"role": "system", "content": "working directory: /home/user/project"},
            {"role": "user", "content": "hola"},
        ], agent="test-agent")
        expected = hashlib.md5((os.getcwd() + "|test-agent").encode()).hexdigest()[:12]
        assert sid == expected

    def test_fallback_stable_by_cwd_and_agent(self):
        sid1 = resolve_session_id({}, [{"role": "user", "content": "hola"}], agent="test-agent")
        sid2 = resolve_session_id({}, [{"role": "user", "content": "hola"}], agent="test-agent")
        assert sid1 == sid2
        assert len(sid1) == 12

    def test_fallback_different_agent_different_id(self):
        sid1 = resolve_session_id({}, [{"role": "user", "content": "hola"}], agent="agent-a")
        sid2 = resolve_session_id({}, [{"role": "user", "content": "hola"}], agent="agent-b")
        assert sid1 != sid2

    def test_case_insensitive_header(self):
        sid = resolve_session_id({"x-session-id": "test"}, [])
        assert sid == "test"


class TestSessionManager:
    def test_create_and_persist(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-1")

        mgr2 = SessionManager(db_path)
        row = mgr2.get_or_create("test-1")
        assert row["id"] == "test-1"

    def test_add_and_compile(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-1")
        mgr.add_message("test-1", "user", "hola")
        mgr.add_message("test-1", "assistant", "mundo")

        prompt = mgr.compile("test-1", enable_fts=False)
        assert "User: hola" in prompt
        assert "Assistant: mundo" in prompt

    def test_dedup_by_hash(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-1")
        assert mgr.add_message("test-1", "user", "hola") is True
        assert mgr.add_message("test-1", "user", "hola") is False

    def test_system_upsert(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-1")

        mgr.sync_new_messages("test-1", [{"role": "system", "content": "v1"}])
        mgr.sync_new_messages("test-1", [{"role": "system", "content": "v2"}])

        prompt = mgr.compile("test-1", enable_fts=False)
        assert "v1" not in prompt
        assert "v2" in prompt

    def test_tool_output_truncation(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-1")
        long_output = "x" * 5000
        mgr.add_message("test-1", "tool_result", long_output)

        prompt = mgr.compile("test-1", enable_fts=False)
        assert "[... truncated:" in prompt

    def test_sliding_window_preserves_first(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-1")
        mgr.add_message("test-1", "user", "PRIMER_MENSAJE")
        for i in range(15):
            mgr.add_message("test-1", "user", f"msg {i}")
            mgr.add_message("test-1", "assistant", f"resp {i}")

        prompt = mgr.compile("test-1", max_history=5, enable_fts=False)
        assert "PRIMER_MENSAJE" in prompt
        assert "omitted" in prompt

    def test_omitted_count_is_number(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-1")
        mgr.add_message("test-1", "user", "first")
        for i in range(15):
            mgr.add_message("test-1", "user", f"msg {i}")
            mgr.add_message("test-1", "assistant", f"resp {i}")

        prompt = mgr.compile("test-1", max_history=5, enable_fts=False)
        assert "[True" not in prompt

    def test_session_id_sanitization(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        with pytest.raises(ValueError, match="Invalid session_id"):
            mgr.get_or_create("../../../etc/passwd")

    def test_cleanup_expired(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("old-session")
        conn = mgr._conn
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = 'old-session'",
            (time.time() - 999999,),
        )
        conn.commit()

        mgr.cleanup_expired(max_age_days=1)
        assert "old-session" not in mgr.list_sessions()

    def test_fts_retrieval(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-1")
        mgr.add_message("test-1", "user", "Me gusta programar en Python")
        mgr.add_message("test-1", "assistant", "Python es genial")
        mgr.add_message("test-1", "user", "¿Qué opina de TypeScript?")
        mgr.add_message("test-1", "assistant", "TypeScript tiene tipado estático")

        prompt = mgr.compile("test-1", max_history=2, enable_fts=True)
        assert "Python" in prompt

    def test_fts_with_spanish_accents(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-1")
        mgr.add_message("test-1", "user", "La función de búsqueda en español")
        mgr.add_message("test-1", "assistant", "Funciona correctamente")
        mgr.add_message("test-1", "user", "¿Cómo funciona la función?")

        prompt = mgr.compile("test-1", max_history=2, enable_fts=True)
        assert "función" in prompt

    def test_single_transaction_rowcount(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-1")
        assert mgr.add_message("test-1", "user", "único") is True
        assert mgr.add_message("test-1", "user", "único") is False

    def test_compile_with_incoming(self, tmp_path):
        """compile() acepta incoming messages y los sincroniza automáticamente."""
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-1")

        incoming = [
            {"role": "system", "content": "Eres un asistente útil."},
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "¡Hola! ¿En qué puedo ayudarte?"},
        ]
        prompt = mgr.compile("test-1", incoming=incoming, max_history=10, enable_fts=False)
        assert "Eres un asistente útil." in prompt
        assert "User: Hola" in prompt
        assert "Assistant: ¡Hola!" in prompt

    def test_delete_session(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("to-delete")
        mgr.add_message("to-delete", "user", "msg")

        mgr.delete("to-delete")
        assert "to-delete" not in mgr.list_sessions()

    def test_persistent_connection_reused(self, tmp_path):
        """Verifica que la conexión SQLite es persistente (no open/close)."""
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        conn1 = mgr._conn
        conn2 = mgr._conn
        assert conn1 is conn2  # misma conexión

    def test_update_metadata(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("meta-test")
        mgr.update_metadata("meta-test", agent="claude", cwd="/tmp")

        row = mgr.get_or_create("meta-test")
        assert row["agent"] == "claude"
        assert row["cwd"] == "/tmp"

    def test_list_sessions_empty(self, tmp_path):
        mgr = SessionManager(tmp_path / "empty.db")
        assert mgr.list_sessions() == []

    def test_list_sessions_with_data(self, tmp_path):
        mgr = SessionManager(tmp_path / "data.db")
        mgr.get_or_create("sess-a")
        mgr.get_or_create("sess-b")
        sessions = mgr.list_sessions()
        assert "sess-a" in sessions
        assert "sess-b" in sessions


class TestUpdatedAt:
    """FASE 5.1 — Verifica que updated_at se actualiza al agregar mensajes."""

    def test_updated_at_after_add_message(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-session")

        row = mgr.get_or_create("test-session")
        before = row["updated_at"]

        time.sleep(0.01)
        mgr.add_message("test-session", "user", "hello")

        row = mgr.get_or_create("test-session")
        assert row["updated_at"] > before

    def test_updated_at_after_sync_new_messages(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-session")

        row = mgr.get_or_create("test-session")
        before = row["updated_at"]

        time.sleep(0.01)
        mgr.sync_new_messages("test-session", [{"role": "user", "content": "hello"}])

        row = mgr.get_or_create("test-session")
        assert row["updated_at"] > before


class TestConcurrency:
    """FASE 5.2 — Prueba básica de concurrencia con threads."""

    def test_concurrent_add_messages(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("conc-session")

        n_threads = 10
        msgs_per_thread = 10
        errors = []

        def worker(worker_id):
            try:
                for i in range(msgs_per_thread):
                    mgr.add_message("conc-session", "user", f"msg from {worker_id}-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent errors: {errors}"
        prompt = mgr.compile("conc-session", enable_fts=False, max_history=1000)
        for i in range(n_threads):
            for j in range(msgs_per_thread):
                assert f"msg from {i}-{j}" in prompt, f"Missing: msg from {i}-{j}"


class TestUpdateMetadataInvalidKeys:
    """FASE 5.3 — update_metadata con keys inválidas."""

    def test_invalid_key_raises_value_error(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("meta-test")
        with pytest.raises(ValueError, match="Invalid metadata keys"):
            mgr.update_metadata("meta-test", foo="bar")

    def test_invalid_keys_in_message(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("meta-test")
        with pytest.raises(ValueError, match="Invalid metadata keys"):
            mgr.update_metadata("meta-test", cwd="/tmp", agent="claude", hacker="true")

    def test_valid_keys_work(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("meta-test")
        mgr.update_metadata("meta-test", cwd="/tmp", agent="claude")
        row = mgr.get_or_create("meta-test")
        assert row["cwd"] == "/tmp"
        assert row["agent"] == "claude"

    def test_allowed_meta_keys_defined(self):
        assert ALLOWED_META_KEYS == {"cwd", "agent", "model", "project_hash"}


class TestFirstMessageByPosition:
    """FASE 5.4 — El primer mensaje se detecta por position, no por content."""

    def test_duplicate_content_first_message_preserved(self, tmp_path):
        db_path = tmp_path / "test.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-session")

        mgr.add_message("test-session", "user", "start")
        mgr.add_message("test-session", "assistant", "ok")
        mgr.add_message("test-session", "assistant", "more")
        mgr.add_message("test-session", "user", "start")

        prompt = mgr.compile("test-session", max_history=2, enable_fts=False)

        assert prompt.startswith("User: start"), \
            "First user message should appear at the start of the prompt"


class TestNewFixes:

    def test_compound_role_validation(self, tmp_path):
        mgr = SessionManager(tmp_path / "m5.db")
        mgr.get_or_create("s")
        with pytest.raises(ValueError, match="Invalid role"):
            mgr.add_message("s", "invalid_role", "content")

    def test_delete_cache_consistency(self, tmp_path):
        mgr = SessionManager(tmp_path / "a1.db")
        mgr.get_or_create("s")
        mgr.add_message("s", "user", "msg")
        mgr.delete("s")
        assert "s" not in mgr.list_sessions()

    def test_compile_invalid_session_id(self, tmp_path):
        mgr = SessionManager(tmp_path / "g2.db")
        with pytest.raises(ValueError, match="Invalid session_id"):
            mgr.compile("")

    def test_fts_reserved_words(self, tmp_path):
        for word in ["near", "table", "rank"]:
            assert _escape_fts5_term(word) == f'"{word}"'

    def test_messages_with_image_blocks(self, tmp_path):
        mgr = SessionManager(tmp_path / "g3.db")
        mgr.get_or_create("s")
        content = [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]
        mgr.sync_new_messages("s", [{"role": "user", "content": content}])
        prompt = mgr.compile("s", enable_fts=False)
        assert "describe" in prompt
        assert "[image]" in prompt

    def test_cache_hydration(self, tmp_path):
        db_path = tmp_path / "c5.db"
        mgr1 = SessionManager(db_path)
        mgr1.get_or_create("s")
        mgr1.add_message("s", "user", "hello")
        mgr1.close()

        mgr2 = SessionManager(db_path)
        assert mgr2.add_message("s", "user", "hello") is False

    def test_context_manager(self, tmp_path):
        with SessionManager(tmp_path / "c2.db") as mgr:
            mgr.get_or_create("s")
            mgr.add_message("s", "user", "hi")
            assert "User: hi" in mgr.compile("s", enable_fts=False)

    def test_tool_dedup_different_content(self, tmp_path):
        mgr = SessionManager(tmp_path / "c4.db")
        mgr.get_or_create("s")
        content_a = "x" * 2000 + "AAAA"
        content_b = "x" * 2000 + "BBBB"
        assert mgr.add_message("s", "tool_result", content_a) is True
        assert mgr.add_message("s", "tool_result", content_b) is True

    def test_session_not_found_warning(self, tmp_path, caplog):
        mgr = SessionManager(tmp_path / "o2.db")
        caplog.clear()
        prompt = mgr.compile("nonexistent", enable_fts=False)
        assert prompt == ""
        assert "not found" in caplog.text

    def test_compile_prompt_truncation(self, tmp_path):
        mgr = SessionManager(tmp_path / "u3.db")
        mgr.get_or_create("s")
        for i in range(500):
            mgr.add_message("s", "user", f"long message number {i}" * 10)
            mgr.add_message("s", "assistant", f"response to message {i}" * 10)
        prompt = mgr.compile("s", max_history=500, max_chars=1000, enable_fts=False)
        assert "[...]" in prompt

    def test_cleanup_expired_batch(self, tmp_path):
        mgr = SessionManager(tmp_path / "a2.db")
        mgr.get_or_create("old1")
        mgr.get_or_create("old2")
        conn = mgr._conn
        conn.execute("UPDATE sessions SET updated_at = 0 WHERE id IN ('old1', 'old2')")
        conn.commit()
        mgr.cleanup_expired(max_age_days=1)
        assert "old1" not in mgr.list_sessions()
        assert "old2" not in mgr.list_sessions()

    def test_list_sessions_thread_safety(self, tmp_path):
        mgr = SessionManager(tmp_path / "c1.db")
        mgr.get_or_create("s")
        errors = []

        def writer():
            try:
                for i in range(50):
                    mgr.add_message("s", "user", f"msg {i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(50):
                    mgr.list_sessions()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_close_then_operations_fail(self, tmp_path):
        mgr = SessionManager(tmp_path / "close.db")
        mgr.get_or_create("s")
        mgr.close()
        with pytest.raises(Exception):
            mgr.list_sessions()

    def test_valid_roles_defined(self):
        assert "system" in VALID_ROLES
        assert "user" in VALID_ROLES
        assert "assistant" in VALID_ROLES
        assert "tool_use" in VALID_ROLES
        assert "tool_result" in VALID_ROLES

    def test_normalize_content_multimodal(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}},
        ]
        result = _normalize_content(content)
        assert "hello" in result
        assert "[image]" in result
        assert "tool_use" in result

    def test_multi_turn_memory(self, tmp_path):
        """Simula el flujo real del server: múltiples turnos con contexto.

        Este test reproduce el escenario exacto de PROBLEMA_session.py.md
        donde el agente perdió el contexto entre turnos.
        """
        db_path = tmp_path / "multi_turn.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("test-session")

        # ── Turno 1 ──
        incoming_1 = [
            {"role": "system", "content": "Eres un asistente útil."},
            {"role": "user", "content": "hola estas ahi?"},
        ]
        prompt_1 = mgr.compile("test-session", incoming=incoming_1, max_history=10, enable_fts=False)
        assert "hola estas ahi?" in prompt_1

        mgr.add_message("test-session", "assistant", "Yes, I'm here. How can I help you?")

        # ── Turno 2 ──
        incoming_2 = [
            {"role": "system", "content": "Eres un asistente útil."},
            {"role": "user", "content": "dime que puedo construir con vite?"},
        ]
        prompt_2 = mgr.compile("test-session", incoming=incoming_2, max_history=10, enable_fts=False)

        assert "hola estas ahi?" in prompt_2, "Turn 1 user should be in Turn 2 prompt"
        assert "Yes, I'm here" in prompt_2, "Turn 1 assistant should be in Turn 2 prompt"
        assert "dime que puedo construir con vite?" in prompt_2, "Turn 2 user should be in prompt"

        mgr.add_message("test-session", "assistant", "Con Vite puedes construir SPAs, SSR, etc.")

        # ── Turno 3 ──
        incoming_3 = [
            {"role": "system", "content": "Eres un asistente útil."},
            {"role": "user", "content": "que más, algo util para medicina?"},
        ]
        prompt_3 = mgr.compile("test-session", incoming=incoming_3, max_history=10, enable_fts=False)

        assert "hola estas ahi?" in prompt_3
        assert "Con Vite puedes construir" in prompt_3
        assert "que más, algo util para medicina?" in prompt_3

        mgr.add_message("test-session", "assistant", "Para medicina: DICOM, data processing, etc.")

        # ── Turno 4 (el problemático) ──
        incoming_4 = [
            {"role": "system", "content": "Eres un asistente útil."},
            {"role": "user", "content": "que es lo que mas se usa de esa lista?"},
        ]
        prompt_4 = mgr.compile("test-session", incoming=incoming_4, max_history=10, enable_fts=False)

        assert "Con Vite puedes construir" in prompt_4, "Vite list should be in Turn 4 prompt"
        assert "Para medicina: DICOM" in prompt_4, "Medicine list should be in Turn 4 prompt"
        assert "que es lo que mas se usa de esa lista?" in prompt_4, "Turn 4 question should be in prompt"

    def test_multi_turn_accumulated_history(self, tmp_path):
        """Cliente envía historial acumulado (como hace Claude Code)."""
        db_path = tmp_path / "accumulated.db"
        mgr = SessionManager(db_path)
        mgr.get_or_create("s")

        # Turno 1
        mgr.compile("s", incoming=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "msg1"},
        ], enable_fts=False)
        mgr.add_message("s", "assistant", "resp1")

        # Turno 2 — el cliente re-envía todo el historial
        prompt = mgr.compile("s", incoming=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
        ], enable_fts=False)

        assert "msg1" in prompt
        assert "resp1" in prompt
        assert "msg2" in prompt

    def test_resolve_session_id_stable_without_cwd(self):
        """Sin X-Session-ID y sin CWD en system, el fallback debe ser estable."""
        sid1 = resolve_session_id({}, [{"role": "user", "content": "hola"}], agent="kilo")
        sid2 = resolve_session_id({}, [{"role": "user", "content": "hola"}], agent="kilo")
        assert sid1 == sid2
        assert len(sid1) == 12

    def test_resolve_session_id_no_cwd_in_system(self, tmp_path):
        """System message sin 'working directory:' debe usar fallback."""
        sid = resolve_session_id({}, [
            {"role": "system", "content": "Eres un asistente útil."},
            {"role": "user", "content": "hola"},
        ], agent="kilo")
        expected = hashlib.md5((os.getcwd() + "|kilo").encode()).hexdigest()[:12]
        assert sid == expected
