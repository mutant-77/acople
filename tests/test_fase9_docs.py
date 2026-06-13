"""
Fase 9 — Validación E2E real + contrato documentado.

AC9.2: docs/openai-compat.md existe y cubre los temas requeridos por el plan:
  - finish_reason (tool_calls / stop)
  - error shape I9 (message, type, param, code)
  - Tool-Proxy mode (force-terminate, --tools "")
  - Limitations (parallel intra-turn, agent degradation)
  - loop-guard + X-Session-ID
  - usage (estimated)
  - security link
"""

from pathlib import Path


class TestOpenAICompatDoc:
    """AC9.2: docs/openai-compat.md existe y cubre el contrato del endpoint."""

    _ROOT = Path(__file__).parent.parent

    def _doc(self) -> str:
        path = self._ROOT / "docs" / "openai-compat.md"
        assert path.exists(), "docs/openai-compat.md debe existir (AC9.2)"
        return path.read_text(encoding="utf-8")

    def test_doc_exists(self):
        """AC9.2: el archivo de contrato existe."""
        self._doc()

    def test_covers_finish_reason(self):
        """Documenta los valores de finish_reason."""
        doc = self._doc()
        assert "finish_reason" in doc
        assert "tool_calls" in doc
        assert '"stop"' in doc or "'stop'" in doc or "stop" in doc

    def test_covers_error_shape_i9(self):
        """Documenta la forma de error I9: message, type, param, code."""
        doc = self._doc()
        assert '"message"' in doc or "message" in doc
        assert "server_error" in doc
        assert "invalid_request_error" in doc
        assert "param" in doc
        assert '"code"' in doc or "code" in doc

    def test_covers_tool_proxy_mode(self):
        """Documenta el Tool-Proxy mode con terminación forzada y --tools."""
        doc = self._doc()
        assert "tool" in doc.lower()
        # Termination enforcement
        assert "force" in doc.lower() or "terminat" in doc.lower()
        # Native tools disabled via --tools
        assert '--tools ""' in doc or "--tools" in doc

    def test_covers_limitations_parallel(self):
        """Documenta que las tool calls paralelas son solo intra-turno."""
        doc = self._doc()
        assert "parallel" in doc.lower() or "intra-turn" in doc.lower()
        assert "limitation" in doc.lower()

    def test_covers_agent_degradation(self):
        """Documenta la degradación D3: claude first-class, resto best-effort."""
        doc = self._doc()
        assert "claude" in doc
        assert "best-effort" in doc.lower() or "degradat" in doc.lower()

    def test_covers_loop_guard(self):
        """Documenta el loop-guard y la necesidad de X-Session-ID."""
        doc = self._doc()
        assert "loop" in doc.lower()
        assert "X-Session-ID" in doc

    def test_covers_usage(self):
        """Documenta usage estimado."""
        doc = self._doc()
        assert "usage" in doc
        assert "estimat" in doc.lower() or "approximat" in doc.lower()

    def test_covers_stream_options(self):
        """Documenta stream_options include_usage."""
        doc = self._doc()
        assert "stream_options" in doc
        assert "include_usage" in doc

    def test_links_to_security_doc(self):
        """Enlaza al documento de seguridad."""
        doc = self._doc()
        assert "security" in doc.lower()
        assert "security.md" in doc

    def test_doc_not_trivially_short(self):
        """El documento tiene contenido sustancial (no un placeholder)."""
        doc = self._doc()
        assert len(doc) > 1000, "openai-compat.md parece demasiado corto"
