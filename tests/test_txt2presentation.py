"""Tests for the txt2presentation plugin.

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

from txt2presentation import plugin  # noqa: E402


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
    caps.transcribe_audio.return_value = "This is a transcribed audio file with enough text to be useful."
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


@pytest.fixture()
def fake_text_file(tmp_path):
    """Create a plain text file with enough content."""
    txt = tmp_path / "article.txt"
    txt.write_text("This is a long article about technology. " * 50, encoding="utf-8")
    return str(txt)


@pytest.fixture()
def fake_html_file(tmp_path):
    """Create an HTML file."""
    html = tmp_path / "page.html"
    html.write_text(
        "<html><body><h1>Title</h1><p>This is a paragraph of content.</p>"
        "<script>var x = 1;</script><p>More content here.</p></body></html>",
        encoding="utf-8",
    )
    return str(html)


# ---------------------------------------------------------------------------
# Input detection tests
# ---------------------------------------------------------------------------

class TestInputDetection:
    def test_youtube_url_detected(self):
        assert plugin._is_youtube_url("https://www.youtube.com/watch?v=abc123")
        assert plugin._is_youtube_url("https://youtu.be/abc123")
        assert plugin._is_youtube_url("https://youtube.com/shorts/abc123")

    def test_non_youtube_not_detected(self):
        assert not plugin._is_youtube_url("https://example.com/video")
        assert not plugin._is_youtube_url("https://vimeo.com/12345")
        assert not plugin._is_youtube_url("some text about youtube")

    def test_is_url(self):
        assert plugin._is_url("https://example.com")
        assert plugin._is_url("http://example.com")
        assert not plugin._is_url("just some text")
        assert not plugin._is_url("/path/to/file.pdf")


# ---------------------------------------------------------------------------
# HTML stripping tests
# ---------------------------------------------------------------------------

class TestStripHtml:
    def test_basic_stripping(self):
        html = "<p>Hello <b>world</b></p>"
        text = plugin._strip_html(html)
        assert "Hello world" in text
        assert "<p>" not in text
        assert "<b>" not in text

    def test_script_removal(self):
        html = "<p>Before</p><script>alert('xss')</script><p>After</p>"
        text = plugin._strip_html(html)
        assert "Before" in text
        assert "After" in text
        assert "alert" not in text

    def test_entity_decoding(self):
        html = "<p>A &amp; B &lt; C</p>"
        text = plugin._strip_html(html)
        assert "A & B < C" in text


# ---------------------------------------------------------------------------
# VTT parsing tests
# ---------------------------------------------------------------------------

class TestParseVtt:
    def test_basic_vtt(self):
        vtt = """WEBVTT

1
00:00:01.000 --> 00:00:03.000
Hello, welcome to the video.

2
00:00:03.500 --> 00:00:06.000
Today we will discuss testing."""
        text = plugin._parse_vtt(vtt)
        assert "Hello, welcome to the video." in text
        assert "Today we will discuss testing." in text
        assert "-->" not in text

    def test_strips_inline_tags(self):
        vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
<c>Hello</c> world"""
        text = plugin._parse_vtt(vtt)
        assert "Hello world" in text
        assert "<c>" not in text


# ---------------------------------------------------------------------------
# _validate_pdf tests
# ---------------------------------------------------------------------------

class TestValidatePdf:
    def test_valid_pdf_passes(self, fake_pdf):
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
        plugin._validate_pdf("/nonexistent/file.pdf")


# ---------------------------------------------------------------------------
# _resolve_source tests
# ---------------------------------------------------------------------------

class TestResolveSource:
    def test_local_text_file(self, fake_text_file, tmp_work_dir):
        text = plugin._resolve_source(fake_text_file, tmp_work_dir)
        assert "technology" in text
        assert len(text) > 100

    def test_local_html_file(self, fake_html_file, tmp_work_dir):
        text = plugin._resolve_source(fake_html_file, tmp_work_dir)
        assert "Title" in text
        assert "paragraph" in text
        assert "<html>" not in text  # HTML should be stripped

    def test_raw_text_passthrough(self, tmp_work_dir):
        raw = "This is a long piece of text about testing. " * 20
        text = plugin._resolve_source(raw, tmp_work_dir)
        assert text == raw.strip()

    def test_short_text_raises(self, tmp_work_dir, mock_caps):
        mock_caps.get_user_id.return_value = None
        with pytest.raises(ValueError, match="Could not resolve"):
            plugin._resolve_source("too short", tmp_work_dir)

    def test_youtube_url_dispatches(self, tmp_work_dir, mock_caps):
        with patch.object(plugin, "_extract_text_from_youtube", return_value="transcript") as mock_yt:
            text = plugin._resolve_source("https://www.youtube.com/watch?v=abc", tmp_work_dir)
            mock_yt.assert_called_once()
            assert text == "transcript"

    def test_regular_url_dispatches(self, tmp_work_dir, mock_caps):
        with patch.object(plugin, "_extract_text_from_url", return_value="web content") as mock_url:
            text = plugin._resolve_source("https://example.com/article", tmp_work_dir)
            mock_url.assert_called_once()
            assert text == "web content"

    def test_workspace_pdf_dispatches(self, fake_pdf, tmp_work_dir, mock_caps):
        mock_caps.workspace_path.return_value = fake_pdf
        with patch.object(plugin, "_extract_text_from_pdf", return_value="pdf text") as mock_pdf:
            text = plugin._resolve_source("test.pdf", tmp_work_dir)
            mock_pdf.assert_called_once_with(fake_pdf)
            assert text == "pdf text"


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


# ---------------------------------------------------------------------------
# TTS audio generation tests
# ---------------------------------------------------------------------------

class TestGenerateAudio:
    def test_calls_caps_tts_synthesize(self, mock_caps):
        mock_caps.get_config.return_value = None
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
        assert "text_to_presentation" in names
        assert "text_to_slides" in names

    def test_register_sets_caps(self, mock_caps):
        plugin.register(mock_caps)
        assert plugin._caps is mock_caps

    def test_plugin_version(self):
        assert plugin.PLUGIN_VERSION == "1"

    def test_plugin_description(self):
        assert plugin.PLUGIN_DESCRIPTION
        assert "text" in plugin.PLUGIN_DESCRIPTION.lower() or "presentation" in plugin.PLUGIN_DESCRIPTION.lower()
