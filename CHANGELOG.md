# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-05-13

### Added
- **COMPACTOR module hardening**: 25 bug fixes across `session.py`, `__init__.py`, and `server.py` from unified audit plan.
- Thread-safe `list_sessions()` with `self._lock`.
- `close()`, `__del__()`, `__enter__/__exit__` for proper SQLite connection lifecycle.
- `_hydrate_cache()` to pre-populate hash cache from existing database on startup.
- `_escape_fts5_term()` to prevent FTS5 `OperationalError` on reserved words (`near`, `table`, `rank`).
- `VALID_ROLES` validation with `ValueError` for invalid roles in `add_message`.
- Batch DELETE optimization in `cleanup_expired()`.
- 15 new tests covering thread safety, cache hydration, role validation, FTS edge cases, and context manager.

### Changed
- Version bump to `1.3.0`.
- Hash computation now runs on original content (before truncation) for accurate deduplication.
- `sync_new_messages` normalizes multimodal content and uses `_add_message_no_commit` for N+1 commit reduction.
- `_normalize_content` serializes non-text blocks (`image_url`, `tool_use`, `tool_result`) instead of dropping them.
- `compile()` now validates `session_id` and shows warning for nonexistent sessions.
- Prompt truncation respects newline boundaries instead of cutting mid-line.
- `get_max_chars_for_agent` catches only specific exceptions with logging.
- `_known_hashes.pop()` moved inside lock in `delete()`.
- `server.py`: imports `process_system_messages`/`resolve_session_id` at top level; calls `close()` on shutdown.
- `__init__.py`: conditional `__all__` based on session module availability.

### Fixed
- FTS5 `OperationalError` on reserved word queries.
- Cache inconsistency in `sync_new_messages` on cold start.
- `database is locked` race condition in `list_sessions()`.
- Silent data loss of multimodal blocks in content normalization.
- Generic `except Exception` swallowing import errors.
- `_known_hashes` never hydrated from existing database.
- Session deletion race condition (cache pop outside lock).

### Added
- **Image Generation**: Support for OpenAI `gpt-image-1` via `POST /image/generate` and `POST /image/generate/stream` endpoints.
- New `ImageBridge`, `ImageConfig`, `ImageResult` classes for programmatic image generation.
- Image Generation tab in the built-in web UI (`/ui`) with gallery, size/quality/count controls, and download buttons.
- `image_ready` field in `/health` endpoint to indicate if `OPENAI_API_KEY` is configured.
- Image parameter validation in `security.py`.

### Changed
- Version bump to `1.2.0`.

## [1.1.0] - 2026-05-03

### Added
- **Built-in Web UI**: Added a modern, self-contained HTML test interface served directly from the FastAPI server. Available at `GET /ui`.
- Endpoints documented in all READMEs (English, Spanish, French).

### Changed
- Version bump to `1.1.0`.

## [1.0.0] - 2026-05-02

### Added
- Initial stable release.
- Production-ready features including security (API Keys), concurrency control, and error handling.
- Rebranded to Acople.
- Multi-language README support (ES, EN, FR).
