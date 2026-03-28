"""Tests for the pdf2presentation plugin.

All external calls (LLM, TTS, system commands, Prax services) are mocked.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add the repo root to sys.path so we can import plugins directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# We need to mock Prax imports before importing the plugin module,
# because it tries `from prax.utils.shell import ...` at module level.
sys.modules["prax"] = MagicMock()
sys.modules["prax.utils"] = MagicMock()
sys.modules["prax.utils.shell"] = MagicMock()
sys.modules["prax.services"] = MagicMock()
sys.modules["prax.services.pdf_service"] = MagicMock()
sys.modules["prax.agent"] = MagicMock()
sys.modules["prax.agent.llm_factory"] = MagicMock()
sys.modules["prax.agent.user_context"] = MagicMock()
sys.modules["prax.services.workspace_service"] = MagicMock()
sys.modules["prax.settings"] = MagicMock()

from pdf2presentation import plugin  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_work_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture()
def fake_pdf(tmp_path):
    """Create a minimal valid PDF file."""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake pdf content for testing")
    return str(pdf)


@pytest.fixture()
def fake_html(tmp_path):
    """Create a file with HTML content (not a PDF)."""
    html = tmp_path / "not_a_pdf.pdf"
    html.write_bytes(b"<!DOCTYPE html><html><body>Not a PDF</body></html>")
    return str(html)


@pytest.fixture()
def fake_json_file(tmp_path):
    """Create a file with JSON content (not a PDF)."""
    j = tmp_path / "response.pdf"
    j.write_bytes(b'{"error": "not found"}')
    return str(j)


# ---------------------------------------------------------------------------
# _validate_pdf tests
# ---------------------------------------------------------------------------

class TestValidatePdf:
    def test_valid_pdf_passes(self, fake_pdf):
        # Should not raise.
        plugin._validate_pdf(fake_pdf)

    def test_html_file_raises(self, fake_html):
        with pytest.raises(ValueError, match="HTML page"):
            plugin._validate_pdf(fake_html)

    def test_json_file_raises(self, fake_json_file):
        with pytest.raises(ValueError, match="JSON"):
            plugin._validate_pdf(fake_json_file)

    def test_unknown_binary_raises(self, tmp_path):
        f = tmp_path / "garbage.pdf"
        f.write_bytes(b"\x00\x01\x02\x03\x04\x05\x06\x07")
        with pytest.raises(ValueError, match="does not start with %PDF"):
            plugin._validate_pdf(str(f))

    def test_includes_url_in_error(self, fake_html):
        with pytest.raises(ValueError, match="example.com"):
            plugin._validate_pdf(str(fake_html), source_url="https://example.com/page")

    def test_nonexistent_file_does_not_raise(self):
        # Can't read — let downstream handle it.
        plugin._validate_pdf("/nonexistent/file.pdf")

    def test_empty_file_raises(self, tmp_path):
        f = tmp_path / "empty.pdf"
        f.write_bytes(b"")
        with pytest.raises(ValueError, match="does not start with %PDF"):
            plugin._validate_pdf(str(f))


# ---------------------------------------------------------------------------
# _download_pdf tests
# ---------------------------------------------------------------------------

class TestDownloadPdf:
    """Test the requests-based download path.

    The Prax PDF service is mocked at module level, so we force it to raise
    ImportError to exercise the requests fallback.
    """

    @staticmethod
    def _make_response(content_type: str, content: bytes):
        resp = MagicMock()
        resp.headers = {"Content-Type": content_type}
        resp.status_code = 200
        resp.content = content
        resp.raise_for_status = MagicMock()
        return resp

    @staticmethod
    def _patch_prax_unavailable():
        """Make the Prax PDF service raise ImportError so requests path is used."""
        mock_mod = MagicMock()
        mock_mod.download_pdf.side_effect = ImportError("no prax")
        return patch.dict(sys.modules, {"prax.services.pdf_service": mock_mod})

    def test_rejects_html_response(self, tmp_work_dir):
        """A URL returning HTML should fail with a clear error."""
        import requests as _req_mod
        fake_resp = self._make_response("text/html; charset=utf-8", b"<html>Not a PDF</html>")

        with self._patch_prax_unavailable(), \
             patch.object(_req_mod, "get", return_value=fake_resp):
            with pytest.raises(ValueError, match="HTML"):
                plugin._download_pdf("https://example.com/page.html", tmp_work_dir)

    def test_accepts_pdf_response(self, tmp_work_dir):
        """A URL returning application/pdf should succeed."""
        import requests as _req_mod
        fake_resp = self._make_response("application/pdf", b"%PDF-1.4 test content")

        with self._patch_prax_unavailable(), \
             patch.object(_req_mod, "get", return_value=fake_resp):
            path = plugin._download_pdf("https://example.com/paper.pdf", tmp_work_dir)
            assert os.path.isfile(path)
            with open(path, "rb") as f:
                assert f.read().startswith(b"%PDF")

    def test_accepts_octet_stream(self, tmp_work_dir):
        """application/octet-stream with valid PDF content should succeed."""
        import requests as _req_mod
        fake_resp = self._make_response("application/octet-stream", b"%PDF-1.4 binary pdf")

        with self._patch_prax_unavailable(), \
             patch.object(_req_mod, "get", return_value=fake_resp):
            path = plugin._download_pdf("https://example.com/file", tmp_work_dir)
            assert os.path.isfile(path)

    def test_rejects_html_body_even_with_pdf_content_type(self, tmp_work_dir):
        """Even if content-type says PDF, validate magic bytes."""
        import requests as _req_mod
        fake_resp = self._make_response("application/pdf", b"<!DOCTYPE html><html></html>")

        with self._patch_prax_unavailable(), \
             patch.object(_req_mod, "get", return_value=fake_resp):
            with pytest.raises(ValueError, match="HTML"):
                plugin._download_pdf("https://example.com/fake.pdf", tmp_work_dir)


# ---------------------------------------------------------------------------
# _resolve_pdf tests
# ---------------------------------------------------------------------------

class TestResolvePdf:
    def test_local_file(self, fake_pdf):
        path = plugin._resolve_pdf(fake_pdf, "/tmp")
        assert path == fake_pdf

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="not found"):
            plugin._resolve_pdf("nonexistent_file.pdf", "/tmp")

    def test_url_triggers_download(self, tmp_work_dir):
        """HTTP URLs should go through _download_pdf."""
        with patch.object(plugin, "_download_pdf", return_value="/tmp/downloaded.pdf") as mock_dl:
            path = plugin._resolve_pdf("https://example.com/paper.pdf", tmp_work_dir)
            mock_dl.assert_called_once_with("https://example.com/paper.pdf", tmp_work_dir)
            assert path == "/tmp/downloaded.pdf"


# ---------------------------------------------------------------------------
# _generate_beamer_and_notes tests
# ---------------------------------------------------------------------------

class TestGenerateBeamerAndNotes:
    VALID_LLM_RESPONSE = json.dumps({
        "title": "Test Presentation",
        "author": "Test Author",
        "latex": "\\documentclass{beamer}\n\\begin{document}\n\\end{document}",
        "slides": [
            {"title": "Intro", "notes": "Welcome to the presentation."},
            {"title": "Main Point", "notes": "Here is the key insight."},
        ],
    })

    @staticmethod
    def _patch_llm(response_content: str):
        """Patch the LLM factory to return a mock that produces the given content."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=response_content)
        mock_factory = MagicMock()
        mock_factory.build_llm.return_value = mock_llm
        return patch.dict(sys.modules, {"prax.agent.llm_factory": mock_factory})

    def test_parses_valid_json(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=self.VALID_LLM_RESPONSE)
        mock_factory = MagicMock()
        mock_factory.build_llm.return_value = mock_llm

        with patch.dict(sys.modules, {"prax.agent.llm_factory": mock_factory}):
            result = plugin._generate_beamer_and_notes("sample text", "Test", "academic")

        assert result["title"] == "Test Presentation"
        assert len(result["slides"]) == 2
        assert "beamer" in result["latex"]

    def test_strips_markdown_fences(self):
        fenced = f"```json\n{self.VALID_LLM_RESPONSE}\n```"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=fenced)
        mock_factory = MagicMock()
        mock_factory.build_llm.return_value = mock_llm

        with patch.dict(sys.modules, {"prax.agent.llm_factory": mock_factory}):
            result = plugin._generate_beamer_and_notes("text", "", "academic")

        assert result["title"] == "Test Presentation"

    def test_strips_surrounding_text(self):
        wrapped = f"Here is the JSON:\n{self.VALID_LLM_RESPONSE}\nHope this helps!"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=wrapped)
        mock_factory = MagicMock()
        mock_factory.build_llm.return_value = mock_llm

        with patch.dict(sys.modules, {"prax.agent.llm_factory": mock_factory}):
            result = plugin._generate_beamer_and_notes("text", "", "academic")

        assert result["title"] == "Test Presentation"

    def test_rejects_missing_keys(self):
        bad_json = json.dumps({"title": "No slides key"})
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=bad_json)
        mock_factory = MagicMock()
        mock_factory.build_llm.return_value = mock_llm

        with patch.dict(sys.modules, {"prax.agent.llm_factory": mock_factory}):
            with pytest.raises(ValueError, match="missing"):
                plugin._generate_beamer_and_notes("text", "", "academic")


# ---------------------------------------------------------------------------
# _check_system_deps tests
# ---------------------------------------------------------------------------

class TestCheckSystemDeps:
    def test_all_present(self):
        with patch.object(plugin, "_which_cmd", return_value=True):
            assert plugin._check_system_deps(need_ffmpeg=True) == []

    def test_missing_pdflatex(self):
        def fake_which(name):
            return name != "pdflatex"

        with patch.object(plugin, "_which_cmd", side_effect=fake_which):
            missing = plugin._check_system_deps(need_ffmpeg=False)
            assert "pdflatex" in missing
            assert "ffmpeg" not in missing

    def test_skip_ffmpeg_check(self):
        def fake_which(name):
            return name not in ("ffmpeg", "ffprobe")

        with patch.object(plugin, "_which_cmd", side_effect=fake_which):
            missing = plugin._check_system_deps(need_ffmpeg=False)
            assert "ffmpeg" not in missing


# ---------------------------------------------------------------------------
# TTS config tests
# ---------------------------------------------------------------------------

class TestTtsConfig:
    def test_default_openai(self, monkeypatch):
        monkeypatch.delenv("PRESENTATION_TTS_PROVIDER", raising=False)
        monkeypatch.delenv("PRESENTATION_TTS_VOICE", raising=False)
        monkeypatch.setenv("OPENAI_KEY", "sk-test")
        cfg = plugin._get_tts_config()
        assert cfg["provider"] == "openai"
        assert cfg["voice"] == "nova"
        assert cfg["api_key"] == "sk-test"

    def test_elevenlabs(self, monkeypatch):
        monkeypatch.setenv("PRESENTATION_TTS_PROVIDER", "elevenlabs")
        monkeypatch.setenv("PRESENTATION_TTS_VOICE", "Adam")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
        cfg = plugin._get_tts_config()
        assert cfg["provider"] == "elevenlabs"
        assert cfg["voice"] == "Adam"
        assert cfg["api_key"] == "el-test"


# ---------------------------------------------------------------------------
# Plugin registration tests
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_returns_tools(self):
        tools = plugin.register()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "pdf_to_presentation" in names
        assert "pdf_to_slides" in names

    def test_plugin_version(self):
        assert plugin.PLUGIN_VERSION == "4"

    def test_plugin_description(self):
        assert plugin.PLUGIN_DESCRIPTION
        assert isinstance(plugin.PLUGIN_DESCRIPTION, str)
