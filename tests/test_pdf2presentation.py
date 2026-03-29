"""Tests for the pdf2presentation plugin.

All external calls (LLM, TTS, system commands, Prax services) are mocked.
The plugin uses the PluginCapabilities gateway — tests provide a mock caps object.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add the repo root to sys.path so we can import plugins directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock Prax imports that may be referenced transitively.
sys.modules.setdefault("prax", MagicMock())
sys.modules.setdefault("prax.settings", MagicMock())

from pdf2presentation import plugin  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_caps():
    """Create a mock PluginCapabilities instance."""
    caps = MagicMock()
    caps.get_config.return_value = None
    caps.get_user_id.return_value = "test-user"
    caps.shared_tempdir.return_value = "/tmp/prax_test_"
    caps.run_command.return_value = MagicMock(returncode=0, stdout="", stderr="")
    caps.build_llm.return_value = MagicMock()
    caps.tts_synthesize.return_value = "/tmp/out.mp3"
    caps.save_file.return_value = "/workspace/test/file"
    caps.workspace_path.return_value = "/workspace/test/active"
    return caps


@pytest.fixture(autouse=True)
def _register_caps(mock_caps):
    """Register mock caps before each test, clean up after."""
    plugin.register(mock_caps)
    yield
    plugin._caps = None


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
    """Test the capabilities-based download path."""

    @staticmethod
    def _make_response(content_type: str, content: bytes):
        resp = MagicMock()
        resp.headers = {"Content-Type": content_type}
        resp.status_code = 200
        resp.content = content
        resp.raise_for_status = MagicMock()
        return resp

    def test_rejects_html_response(self, tmp_work_dir, mock_caps):
        fake_resp = self._make_response("text/html; charset=utf-8", b"<html>Not a PDF</html>")
        mock_caps.http_get.return_value = fake_resp

        with pytest.raises(ValueError, match="HTML"):
            plugin._download_pdf("https://example.com/page.html", tmp_work_dir)

    def test_accepts_pdf_response(self, tmp_work_dir, mock_caps):
        fake_resp = self._make_response("application/pdf", b"%PDF-1.4 test content")
        mock_caps.http_get.return_value = fake_resp

        path = plugin._download_pdf("https://example.com/paper.pdf", tmp_work_dir)
        assert os.path.isfile(path)
        with open(path, "rb") as f:
            assert f.read().startswith(b"%PDF")

    def test_accepts_octet_stream(self, tmp_work_dir, mock_caps):
        fake_resp = self._make_response("application/octet-stream", b"%PDF-1.4 binary pdf")
        mock_caps.http_get.return_value = fake_resp

        path = plugin._download_pdf("https://example.com/file", tmp_work_dir)
        assert os.path.isfile(path)

    def test_rejects_html_body_even_with_pdf_content_type(self, tmp_work_dir, mock_caps):
        fake_resp = self._make_response("application/pdf", b"<!DOCTYPE html><html></html>")
        mock_caps.http_get.return_value = fake_resp

        with pytest.raises(ValueError, match="HTML"):
            plugin._download_pdf("https://example.com/fake.pdf", tmp_work_dir)

    def test_uses_caps_http_get(self, tmp_work_dir, mock_caps):
        """Verify download goes through the capabilities gateway."""
        fake_resp = self._make_response("application/pdf", b"%PDF-1.4 test")
        mock_caps.http_get.return_value = fake_resp

        plugin._download_pdf("https://example.com/paper.pdf", tmp_work_dir)
        mock_caps.http_get.assert_called_once_with(
            "https://example.com/paper.pdf", timeout=60, allow_redirects=True,
        )


# ---------------------------------------------------------------------------
# _resolve_pdf tests
# ---------------------------------------------------------------------------

class TestResolvePdf:
    def test_local_file(self, fake_pdf):
        path = plugin._resolve_pdf(fake_pdf, "/tmp")
        assert path == fake_pdf

    def test_file_not_found(self, mock_caps):
        mock_caps.get_user_id.return_value = None
        with pytest.raises(FileNotFoundError, match="not found"):
            plugin._resolve_pdf("nonexistent_file.pdf", "/tmp")

    def test_url_triggers_download(self, tmp_work_dir):
        with patch.object(plugin, "_download_pdf", return_value="/tmp/downloaded.pdf") as mock_dl:
            path = plugin._resolve_pdf("https://example.com/paper.pdf", tmp_work_dir)
            mock_dl.assert_called_once_with("https://example.com/paper.pdf", tmp_work_dir)
            assert path == "/tmp/downloaded.pdf"

    def test_workspace_file_resolution(self, fake_pdf, mock_caps):
        """If caps.workspace_path resolves to a real file, use it."""
        mock_caps.workspace_path.return_value = fake_pdf
        path = plugin._resolve_pdf("test.pdf", "/tmp")
        assert path == fake_pdf
        mock_caps.workspace_path.assert_called_with("test.pdf")


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

    def test_parses_valid_json(self, mock_caps):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=self.VALID_LLM_RESPONSE)
        mock_caps.build_llm.return_value = mock_llm

        result = plugin._generate_beamer_and_notes("sample text", "Test", "academic")

        assert result["title"] == "Test Presentation"
        assert len(result["slides"]) == 2
        assert "beamer" in result["latex"]

    def test_strips_markdown_fences(self, mock_caps):
        fenced = f"```json\n{self.VALID_LLM_RESPONSE}\n```"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=fenced)
        mock_caps.build_llm.return_value = mock_llm

        result = plugin._generate_beamer_and_notes("text", "", "academic")
        assert result["title"] == "Test Presentation"

    def test_strips_surrounding_text(self, mock_caps):
        wrapped = f"Here is the JSON:\n{self.VALID_LLM_RESPONSE}\nHope this helps!"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=wrapped)
        mock_caps.build_llm.return_value = mock_llm

        result = plugin._generate_beamer_and_notes("text", "", "academic")
        assert result["title"] == "Test Presentation"

    def test_rejects_missing_keys(self, mock_caps):
        bad_json = json.dumps({"title": "No slides key"})
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=bad_json)
        mock_caps.build_llm.return_value = mock_llm

        with pytest.raises(ValueError, match="missing"):
            plugin._generate_beamer_and_notes("text", "", "academic")

    def test_uses_caps_build_llm(self, mock_caps):
        """Verify LLM is obtained through the capabilities gateway."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=self.VALID_LLM_RESPONSE)
        mock_caps.build_llm.return_value = mock_llm

        plugin._generate_beamer_and_notes("text", "", "academic")
        mock_caps.build_llm.assert_called_once()


# ---------------------------------------------------------------------------
# _check_system_deps tests
# ---------------------------------------------------------------------------

class TestCheckSystemDeps:
    def test_all_present(self, mock_caps):
        mock_caps.run_command.return_value = MagicMock(returncode=0)
        assert plugin._check_system_deps(need_ffmpeg=True) == []

    def test_missing_pdflatex(self, mock_caps):
        def fake_which(cmd, **kw):
            result = MagicMock()
            result.returncode = 1 if cmd == ["which", "pdflatex"] else 0
            return result

        mock_caps.run_command.side_effect = fake_which
        missing = plugin._check_system_deps(need_ffmpeg=False)
        assert "pdflatex" in missing
        assert "ffmpeg" not in missing

    def test_skip_ffmpeg_check(self, mock_caps):
        def fake_which(cmd, **kw):
            result = MagicMock()
            result.returncode = 1 if cmd[1] in ("ffmpeg", "ffprobe") else 0
            return result

        mock_caps.run_command.side_effect = fake_which
        missing = plugin._check_system_deps(need_ffmpeg=False)
        assert "ffmpeg" not in missing


# ---------------------------------------------------------------------------
# TTS config tests
# ---------------------------------------------------------------------------

class TestTtsConfig:
    def test_default_openai(self, mock_caps):
        mock_caps.get_config.return_value = None
        cfg = plugin._get_tts_config()
        assert cfg["provider"] == "openai"
        assert cfg["voice"] == "nova"

    def test_elevenlabs(self, mock_caps):
        def config_lookup(key):
            return {"presentation_tts_provider": "elevenlabs",
                    "presentation_tts_voice": "Adam"}.get(key)

        mock_caps.get_config.side_effect = config_lookup
        cfg = plugin._get_tts_config()
        assert cfg["provider"] == "elevenlabs"
        assert cfg["voice"] == "Adam"

    def test_custom_openai_voice(self, mock_caps):
        def config_lookup(key):
            return {"presentation_tts_provider": "openai",
                    "presentation_tts_voice": "shimmer"}.get(key)

        mock_caps.get_config.side_effect = config_lookup
        cfg = plugin._get_tts_config()
        assert cfg["provider"] == "openai"
        assert cfg["voice"] == "shimmer"


# ---------------------------------------------------------------------------
# TTS audio generation tests
# ---------------------------------------------------------------------------

class TestGenerateAudio:
    def test_calls_caps_tts_synthesize(self, mock_caps):
        mock_caps.get_config.return_value = None  # defaults
        plugin._generate_audio("Hello world", "/tmp/out.mp3")
        mock_caps.tts_synthesize.assert_called_once_with(
            text="Hello world",
            output_path="/tmp/out.mp3",
            voice="nova",
            provider="openai",
        )


# ---------------------------------------------------------------------------
# _save_to_workspace tests
# ---------------------------------------------------------------------------

class TestSaveToWorkspace:
    def test_saves_via_caps(self, mock_caps, fake_pdf):
        result = plugin._save_to_workspace(fake_pdf, "output.pdf")
        mock_caps.save_file.assert_called_once()
        assert result is not None

    def test_returns_none_without_user(self, mock_caps, fake_pdf):
        mock_caps.get_user_id.return_value = None
        result = plugin._save_to_workspace(fake_pdf, "output.pdf")
        assert result is None

    def test_returns_none_on_error(self, mock_caps, fake_pdf):
        mock_caps.save_file.side_effect = RuntimeError("save failed")
        result = plugin._save_to_workspace(fake_pdf, "output.pdf")
        assert result is None


# ---------------------------------------------------------------------------
# Plugin registration tests
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_returns_tools(self, mock_caps):
        tools = plugin.register(mock_caps)
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "pdf_to_presentation" in names
        assert "pdf_to_slides" in names

    def test_register_sets_caps(self, mock_caps):
        plugin.register(mock_caps)
        assert plugin._caps is mock_caps

    def test_plugin_version(self):
        assert plugin.PLUGIN_VERSION == "5"

    def test_plugin_description(self):
        assert plugin.PLUGIN_DESCRIPTION
        assert isinstance(plugin.PLUGIN_DESCRIPTION, str)
