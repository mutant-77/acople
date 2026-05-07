# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-05-07

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
