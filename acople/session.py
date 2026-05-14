"""
COMPACTOR — Módulo de sesiones persistente para Acople.

Framework-agnostic. Reutilizable en cualquier proyecto Python.
Zero dependencias externas (solo stdlib: sqlite3, hashlib, re, threading, time, pathlib).
Zero imports de FastAPI, aiohttp, asyncio.

Uso:
    from acople.session import SessionManager, resolve_session_id

    mgr = SessionManager("./sessions.db")
    mgr.get_or_create("session-123")
    mgr.add_message("session-123", "user", "hola")
    prompt = mgr.compile("session-123", incoming=[...])
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger("acople.session")

SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
MAX_TOOL_OUTPUT_CHARS = 2000
_LONG_QUERY_MS = 100

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    cwd TEXT DEFAULT '',
    agent TEXT DEFAULT '',
    model TEXT DEFAULT '',
    project_hash TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    msg_hash TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    UNIQUE(session_id, msg_hash)
);

CREATE INDEX IF NOT EXISTS idx_messages_session_position
    ON messages(session_id, position);

CREATE INDEX IF NOT EXISTS idx_messages_session_role
    ON messages(session_id, role);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    session_id,
    role UNINDEXED,
    content,
    tokenize='unicode61'
);
"""

ALLOWED_META_KEYS = frozenset({"cwd", "agent", "model", "project_hash"})
VALID_ROLES = frozenset({"system", "user", "assistant", "tool_use", "tool_result"})


def validate_session_id(session_id: str) -> str:
    if not SESSION_ID_RE.match(session_id):
        raise ValueError(f"Invalid session_id: {session_id!r}")
    return session_id


def _msg_hash(role: str, content: str) -> str:
    raw = f"{role}|{content}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _truncate_tool_output(content: str) -> str:
    # Python str slicing respeta character boundaries (Unicode, no bytes)
    if len(content) <= MAX_TOOL_OUTPUT_CHARS:
        return content
    return (
        content[:MAX_TOOL_OUTPUT_CHARS]
        + f"\n[... truncated: {len(content)} chars total]"
    )


def _escape_fts5_term(term: str) -> str:
    escaped = term.replace('"', '""')
    return f'"{escaped}"'


def _log_slow(label: str, start: float, threshold_ms: int = _LONG_QUERY_MS):
    elapsed = (time.time() - start) * 1000
    if elapsed > threshold_ms:
        logger.warning("SLOW %s: %.0fms", label, elapsed)
    else:
        logger.log(logging.DEBUG, "%s: %.0fms", label, elapsed)


def _tokenize_terms(text: str, min_len: int = 4) -> str:
    terms = []
    current = []
    for ch in text:
        if ch.isalnum():
            current.append(ch)
        else:
            word = "".join(current)
            if len(word) >= min_len:
                terms.append(_escape_fts5_term(word))
            current = []
    word = "".join(current)
    if len(word) >= min_len:
        terms.append(_escape_fts5_term(word))
    return " OR ".join(terms) if terms else ""


def _normalize_content(content) -> str:
    if isinstance(content, list):
        text = ""
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text += block.get("text", "") + "\n"
                elif block.get("type") == "image_url":
                    text += "[image]\n"
                else:
                    text += json.dumps(block, ensure_ascii=False) + "\n"
            elif isinstance(block, str):
                text += block + "\n"
        return text.strip()
    
    # Normalización agresiva para evitar duplicados por espacios o saltos de línea
    # Si es un string, quitamos espacios extra y normalizamos saltos de línea
    s = str(content).strip()
    s = s.replace("\r\n", "\n")
    return s


def _get_header(headers: dict, key: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == key.lower():
            return v
    return None


def resolve_session_id(
    headers: dict,
    messages: list[dict],
    agent: str | None = None,
    cwd: str | None = None,
) -> str:
    """Resuelve session_id. headers es un dict plano (framework-agnostic).

    Prioridad:
    1. headers['X-Session-ID']
    2. Hash estable de CWD real + nombre del agente

    NOTA: Se eliminó la estrategia de extraer CWD de system messages porque
    su presencia puede variar entre requests del mismo cliente, causando
    que el session_id cambie entre turnos y se pierda el historial.
    """
    sid = _get_header(headers, "X-Session-ID")
    if sid:
        validated = validate_session_id(sid)
        logger.debug("resolve_session_id: from header -> %s", validated)
        return validated

    # Prioridad 2: Hash estable del CWD real del proyecto
    # Si no se provee, usamos el CWD del servidor como fallback
    stable_key = cwd or os.getcwd()
    sid = hashlib.md5(stable_key.encode()).hexdigest()[:12]
    logger.debug("resolve_session_id: from CWD stable key (%s) -> %s", stable_key, sid)
    return sid


def process_system_messages(messages: list[dict]) -> tuple[str, str | None]:
    """Extrae system text y CWD de los mensajes.

    Semántica "last wins" para CWD: si hay múltiples system messages
    con 'working directory:', el último prevalece.
    """
    system_msg = ""
    extracted_cwd = None

    for m in messages:
        if m.get("role") == "system":
            content_text = _normalize_content(m.get("content", ""))
            system_msg += content_text + "\n"

            if "working directory: " in content_text:
                try:
                    parts = content_text.split("working directory: ", 1)
                    path_part = parts[1].split("\n", 1)[0].strip()
                    path_part = path_part.rstrip('."\' ')
                    if path_part:
                        extracted_cwd = path_part
                except Exception as e:
                    logger.warning("CWD extraction failed: %s", e)

    return system_msg.strip(), extracted_cwd


def get_max_chars_for_agent(agent_name: str | None) -> int:
    if not agent_name:
        return 50_000

    try:
        from acople.bridge import get_config
        return get_config(agent_name).max_chars
    except (ImportError, AttributeError) as e:
        logger.warning("get_max_chars_for_agent(%r) failed: %s", agent_name, e)
        return 50_000


class SessionManager:

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_dir = Path.home() / ".acople"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / "sessions.db"

        self._db_path = Path(db_path)
        self._lock = threading.Lock()

        # Conexión SQLite PERSISTENTE — se abre una vez, se reusa siempre
        # check_same_thread=False es seguro porque threading.Lock serializa todo
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        except sqlite3.OperationalError as e:
            logger.error("DB pragma failed (%s): %s", self._db_path, e)
            raise
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()
        except sqlite3.OperationalError as e:
            logger.error("Schema init failed (%s): %s", self._db_path, e)
            raise

        self._known_hashes: dict[str, set[str]] = {}
        self._hydrate_cache()
        logger.info("SessionManager opened: %s", self._db_path)

    def _hydrate_cache(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT session_id, msg_hash FROM messages"
            ).fetchall()
        except sqlite3.OperationalError as e:
            logger.error("Cache hydration failed: %s", e)
            return
        count = 0
        for row in rows:
            self._known_hashes.setdefault(row["session_id"], set()).add(row["msg_hash"])
            count += 1
        if count:
            logger.info("Hydrated %d hashes from DB (%d sessions)", count, len(self._known_hashes))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
            self._known_hashes.clear()
            logger.info("SessionManager closed: %s", self._db_path)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception as e:
            logger.warning("SessionManager.__del__ error: %s", e)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ── Sesiones ──

    def get_or_create(self, session_id: str) -> dict:
        validate_session_id(session_id)
        with self._lock:
            t0 = time.time()
            conn = self._conn
            now = time.time()
            conn.execute(
                """INSERT INTO sessions (id, created_at, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET updated_at = ?""",
                (session_id, now, now, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            result = dict(row)
            _log_slow(f"get_or_create({session_id[:16]})", t0)
            return result

    def delete(self, session_id: str) -> None:
        validate_session_id(session_id)
        with self._lock:
            self._known_hashes.pop(session_id, None)
            conn = self._conn
            conn.execute(
                "DELETE FROM messages_fts WHERE session_id = ?", (session_id,)
            )
            conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
            conn.commit()
            logger.info("Deleted session: %s", session_id)

    def list_sessions(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [r["id"] for r in rows]

    def update_metadata(self, session_id: str, **kwargs):
        invalid = set(kwargs) - ALLOWED_META_KEYS
        if invalid:
            raise ValueError(f"Invalid metadata keys: {invalid}")
        with self._lock:
            conn = self._conn
            now = time.time()
            updates = ", ".join(f"{k} = ?" for k in kwargs)
            values = list(kwargs.values()) + [now, session_id]
            conn.execute(
                f"UPDATE sessions SET {updates}, updated_at = ? WHERE id = ?",
                values,
            )
            conn.commit()

    # ── Mensajes ──

    def add_message(self, session_id: str, role: str, content: str) -> bool:
        if role not in VALID_ROLES:
            logger.error("Invalid role %r from session %s", role, session_id)
            raise ValueError(f"Invalid role: {role!r}. Expected one of {sorted(VALID_ROLES)}")

        if role in ("tool_use", "tool_result"):
            content_stored = _truncate_tool_output(content)
        else:
            content_stored = content

        msg_hash = _msg_hash(role, content)

        with self._lock:
            t0 = time.time()
            known = self._known_hashes.get(session_id)
            if known and msg_hash in known:
                return False

            conn = self._conn
            now = time.time()
            pos = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]

            cursor = conn.execute(
                """INSERT OR IGNORE INTO messages
                   (session_id, role, content, msg_hash, position, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, role, content_stored, msg_hash, pos, now),
            )
            was_inserted = cursor.rowcount > 0

            conn.execute(
                """INSERT OR IGNORE INTO messages_fts (rowid, session_id, role, content)
                   SELECT id, session_id, role, content FROM messages
                   WHERE msg_hash = ? AND session_id = ?""",
                (msg_hash, session_id),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            conn.commit()

            if was_inserted:
                self._known_hashes.setdefault(session_id, set()).add(msg_hash)

            _log_slow(f"add_message({session_id[:16]}, {role})", t0)
            return was_inserted

    def _add_message_no_commit(self, session_id: str, role: str, content: str) -> bool:
        content_stored = _truncate_tool_output(content) if role in ("tool_use", "tool_result") else content
        msg_hash = _msg_hash(role, content)

        known = self._known_hashes.get(session_id)
        if known and msg_hash in known:
            return False

        conn = self._conn
        now = time.time()
        pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]

        cursor = conn.execute(
            "INSERT OR IGNORE INTO messages (session_id, role, content, msg_hash, position, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content_stored, msg_hash, pos, now),
        )
        was_inserted = cursor.rowcount > 0
        conn.execute(
            "INSERT OR IGNORE INTO messages_fts (rowid, session_id, role, content) SELECT id, session_id, role, content FROM messages WHERE msg_hash = ? AND session_id = ?",
            (msg_hash, session_id),
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        if was_inserted:
            self._known_hashes.setdefault(session_id, set()).add(msg_hash)
        return was_inserted

    def sync_new_messages(self, session_id: str, incoming: list[dict]) -> int:
        count = 0
        if not incoming:
            return 0
        for msg in incoming:
            role = msg.get("role", "")
            if not role:
                logger.warning("sync_new_messages: msg without role in session %s", session_id)
            content = _normalize_content(msg.get("content", ""))

            if role == "system":
                with self._lock:
                    old_hashes = {
                        _msg_hash("system", row["content"])
                        for row in self._conn.execute(
                            "SELECT content FROM messages WHERE session_id = ? AND role = 'system'",
                            (session_id,),
                        ).fetchall()
                    }
                    self._conn.execute(
                        "DELETE FROM messages WHERE session_id = ? AND role = 'system'",
                        (session_id,),
                    )
                    self._conn.execute(
                        "DELETE FROM messages_fts WHERE session_id = ? AND role = 'system'",
                        (session_id,),
                    )
                    if session_id in self._known_hashes:
                        self._known_hashes[session_id] -= old_hashes
                        if not self._known_hashes[session_id]:
                            del self._known_hashes[session_id]
                    if self._add_message_no_commit(session_id, role, content):
                        count += 1
                    self._conn.commit()
            else:
                with self._lock:
                    if self._add_message_no_commit(session_id, role, content):
                        count += 1
                    self._conn.commit()

        logger.debug("sync_new_messages(%s): %d new of %d incoming", session_id[:16], count, len(incoming))
        return count

    # ── Compilación de prompt ──

    def compile(
        self,
        session_id: str,
        incoming: list[dict] | None = None,
        agent: str | None = None,
        max_history: int = 10,
        max_chars: int | None = None,
        enable_fts: bool = True,
    ) -> str:
        """Interfaz de alto nivel: sync + compile en un solo paso.

        Es el método principal que usa el server (vía SESSION_MODULAR_APPROACH.md).
        - Sincroniza mensajes entrantes
        - Compila el prompt narrativo con sliding window + FTS5
        """
        validate_session_id(session_id)
        if incoming:
            self.sync_new_messages(session_id, incoming)

        if max_chars is None:
            max_chars = get_max_chars_for_agent(agent)

        return self._compile_prompt(
            session_id=session_id,
            max_history=max_history,
            max_chars=max_chars,
            enable_fts=enable_fts,
        )

    def _compile_prompt(
        self,
        session_id: str,
        max_history: int = 10,
        max_chars: int = 50_000,
        enable_fts: bool = True,
    ) -> str:
        with self._lock:
            t0 = time.time()
            conn = self._conn

            session_exists = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not session_exists:
                logger.warning("Session %s not found, returning empty prompt", session_id)
                return ""

            system_row = conn.execute(
                """SELECT content FROM messages
                   WHERE session_id = ? AND role = 'system'
                   ORDER BY position DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
            system_text = system_row["content"] if system_row else ""

            # Ventana deslizante: tomamos una cantidad generosa de mensajes
            # El límite real lo impondrá max_chars durante la compilación.
            window_rows = conn.execute(
                """SELECT role, content, position FROM messages
                   WHERE session_id = ? AND role != 'system'
                   ORDER BY position DESC LIMIT ?""",
                (session_id, max(max_history + 1, 100)),
            ).fetchall()
            window_rows.reverse()

            first_user_row = conn.execute(
                """SELECT role, content, position FROM messages
                   WHERE session_id = ? AND role = 'user'
                   ORDER BY position ASC LIMIT 1""",
                (session_id,),
            ).fetchone()

            if first_user_row:
                already_included = any(
                    r["position"] == first_user_row["position"] for r in window_rows
                )
                if not already_included:
                    window_rows.insert(0, first_user_row)

            total_non_system = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role != 'system'",
                (session_id,),
            ).fetchone()[0]
            omitted_count = total_non_system - len(window_rows)

            last_user = None
            if enable_fts and window_rows:
                for r in reversed(window_rows):
                    if r["role"] == "user":
                        last_user = r["content"]
                        break

            fts_matches = []
            if last_user:
                terms = _tokenize_terms(last_user)
                if terms:
                    try:
                        fts_rows = conn.execute(
                            """SELECT content, role, rank FROM messages_fts
                               WHERE messages_fts MATCH ?
                               AND session_id = ?
                               ORDER BY rank LIMIT 3""",
                            (terms, session_id),
                        ).fetchall()
                    except sqlite3.OperationalError as e:
                        logger.error("FTS query failed for session %s: %s", session_id, e)
                        fts_rows = []
                    fts_matches = [
                        r
                        for r in fts_rows
                        if not any(
                            ex["content"] == r["content"] for ex in window_rows
                        )
                    ]

            parts = []
            if system_text:
                parts.append(system_text)
                parts.append("")

            if omitted_count > 0:
                parts.append(f"[{omitted_count} previous messages omitted]")
                parts.append("")

            for m in window_rows:
                role_label = m["role"]
                content = m["content"]
                if role_label == "user":
                    parts.append(f"User: {content}")
                elif role_label == "assistant":
                    parts.append(f"Assistant: {content}")
                elif role_label == "tool_use":
                    parts.append(f"[Tool: {content}]")
                elif role_label == "tool_result":
                    parts.append(f"[Tool Result: {content}]")
                else:
                    logger.warning("Unknown role %r in session %s", role_label, session_id)
                    parts.append(f"[{role_label}: {content}]")

            if fts_matches:
                parts.append("")
                parts.append("[Relevant context from history:]")
                for m in fts_matches:
                    label = "User" if m["role"] == "user" else "Assistant"
                    parts.append(f"[{label}: {m['content'][:500]}]")

            compiled = "\n\n".join(parts)

            if len(compiled) > max_chars:
                # TRUNCACIÓN INTELIGENTE (Sliding Window):
                # 1. Preservar System Prompt (siempre).
                # 2. Preservar la mayor cantidad de mensajes RECIENTES posibles.
                
                header_text = system_text + "\n\n" if system_text else ""
                footer_text = ""
                if fts_matches:
                    footer_text = "\n\n[Relevant context from history:]\n" + "\n".join(
                        f"[{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:200]}]"
                        for m in fts_matches
                    )

                allowed_body_chars = max_chars - len(header_text) - len(footer_text) - 50
                if allowed_body_chars < 500:
                    # Si el system prompt es demasiado grande, sacrificamos FTS
                    footer_text = ""
                    allowed_body_chars = max_chars - len(header_text) - 50

                body_parts = []
                current_chars = 0
                # Ir de atrás hacia adelante (mensajes más recientes)
                for m in reversed(window_rows):
                    role_label = m["role"]
                    content = m["content"]
                    if role_label == "user":
                        part = f"User: {content}"
                    elif role_label == "assistant":
                        part = f"Assistant: {content}"
                    elif role_label == "tool_use":
                        part = f"[Tool: {content}]"
                    elif role_label == "tool_result":
                        part = f"[Tool Result: {content}]"
                    else:
                        part = f"[{role_label}: {content}]"
                    
                    if current_chars + len(part) + 2 > allowed_body_chars:
                        body_parts.insert(0, "[... history truncated ...]")
                        break
                    body_parts.insert(0, part)
                    current_chars += len(part) + 2

                compiled = header_text + "\n\n".join(body_parts) + footer_text
                logger.warning("Prompt sliding-window truncated for session %s: %d chars (limit %d)", session_id[:16], len(compiled), max_chars)

            _log_slow(f"_compile_prompt({session_id[:16]})", t0)
            return compiled

    # ── Cleanup ──

    def cleanup_expired(self, max_age_days: int = 7):
        cutoff = time.time() - (max_age_days * 86400)
        with self._lock:
            conn = self._conn
            old = conn.execute(
                "SELECT id FROM sessions WHERE updated_at < ?", (cutoff,)
            ).fetchall()
            if not old:
                logger.debug("cleanup_expired: no sessions to clean")
                return
            old_ids = [row["id"] for row in old]
            placeholders = ",".join("?" for _ in old_ids)

            conn.execute(
                f"DELETE FROM messages_fts WHERE session_id IN ({placeholders})",
                old_ids,
            )
            conn.execute(
                f"DELETE FROM messages WHERE session_id IN ({placeholders})",
                old_ids,
            )
            conn.execute(
                "DELETE FROM sessions WHERE updated_at < ?", (cutoff,)
            )
            for sid in old_ids:
                self._known_hashes.pop(sid, None)
            conn.commit()
            logger.info(f"Cleaned up {len(old)} expired sessions")
