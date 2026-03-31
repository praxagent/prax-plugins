"""Tests for the elevenmusic plugin.

All ElevenLabs API calls are mocked.
The plugin uses the PluginCapabilities gateway — tests provide a mock caps object.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock Prax imports.
sys.modules.setdefault("prax", MagicMock())
sys.modules.setdefault("prax.settings", MagicMock())

from elevenmusic import plugin  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_caps():
    """Create a mock PluginCapabilities instance."""
    caps = MagicMock()
    caps.get_approved_secret.return_value = "sk-test-elevenlabs-key"
    caps.get_user_id.return_value = "test-user"
    caps.save_file.return_value = "/workspace/test-user/active/song_123.mp3"
    return caps


@pytest.fixture(autouse=True)
def _register_and_clear(mock_caps):
    """Register mock caps before each test, clean up after."""
    plugin.register(mock_caps)
    yield
    plugin._caps = None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    """Plugin registration and metadata."""

    def test_register_returns_tools(self, mock_caps):
        tools = plugin.register(mock_caps)
        assert isinstance(tools, list)
        assert len(tools) == 1
        assert tools[0].name == "generate_song"

    def test_plugin_version(self):
        assert plugin.PLUGIN_VERSION == "1"

    def test_plugin_description(self):
        assert plugin.PLUGIN_DESCRIPTION

    def test_plugin_permissions_declared(self):
        assert hasattr(plugin, "PLUGIN_PERMISSIONS")
        perms = plugin.PLUGIN_PERMISSIONS
        assert isinstance(perms, list)
        assert len(perms) >= 1
        keys = [p["key"] for p in perms]
        assert "ELEVENLABS_API_KEY" in keys

    def test_plugin_permissions_have_reasons(self):
        for perm in plugin.PLUGIN_PERMISSIONS:
            assert "key" in perm
            assert "reason" in perm
            assert len(perm["reason"]) > 10  # Non-trivial reason


# ---------------------------------------------------------------------------
# Secret access
# ---------------------------------------------------------------------------

class TestSecretAccess:
    """Verify the plugin accesses secrets through the gateway."""

    def test_get_api_key_calls_approved_secret(self, mock_caps):
        key = plugin._get_api_key()
        mock_caps.get_approved_secret.assert_called_with("ELEVENLABS_API_KEY")
        assert key == "sk-test-elevenlabs-key"

    def test_get_api_key_raises_when_not_configured(self, mock_caps):
        mock_caps.get_approved_secret.return_value = None
        with pytest.raises(RuntimeError, match="not configured"):
            plugin._get_api_key()

    def test_get_api_key_raises_when_not_registered(self):
        plugin._caps = None
        with pytest.raises(RuntimeError, match="not registered"):
            plugin._get_api_key()


# ---------------------------------------------------------------------------
# Song generation
# ---------------------------------------------------------------------------

class TestGenerateSong:
    """Test the generate_song tool."""

    def test_empty_prompt_rejected(self):
        result = plugin.generate_song.invoke({"prompt": ""})
        assert "provide a prompt" in result.lower()

    def test_whitespace_prompt_rejected(self):
        result = plugin.generate_song.invoke({"prompt": "   "})
        assert "provide a prompt" in result.lower()

    def test_successful_generation(self, mock_caps):
        # Mock the HTTP response.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\xff" * 5000  # Fake audio bytes
        mock_response.raise_for_status.return_value = None
        mock_caps.http_post.return_value = mock_response

        result = plugin.generate_song.invoke({
            "prompt": "A chill lo-fi beat",
            "duration_seconds": 30,
        })

        assert "successfully" in result.lower()
        assert "song_123.mp3" in result
        mock_caps.http_post.assert_called_once()

        # Verify the API call.
        call_args = mock_caps.http_post.call_args
        assert "api.elevenlabs.io" in call_args[0][0]
        assert call_args[1]["headers"]["xi-api-key"] == "sk-test-elevenlabs-key"
        assert call_args[1]["json"]["prompt"] == "A chill lo-fi beat"
        assert call_args[1]["json"]["music_length_ms"] == 30000

    def test_instrumental_flag(self, mock_caps):
        mock_response = MagicMock()
        mock_response.content = b"\xff" * 5000
        mock_response.raise_for_status.return_value = None
        mock_caps.http_post.return_value = mock_response

        plugin.generate_song.invoke({
            "prompt": "Jazz piano",
            "instrumental": True,
        })

        call_json = mock_caps.http_post.call_args[1]["json"]
        assert call_json["force_instrumental"] is True

    def test_duration_clamped_min(self, mock_caps):
        mock_response = MagicMock()
        mock_response.content = b"\xff" * 5000
        mock_response.raise_for_status.return_value = None
        mock_caps.http_post.return_value = mock_response

        plugin.generate_song.invoke({
            "prompt": "Short jingle",
            "duration_seconds": 1,
        })

        call_json = mock_caps.http_post.call_args[1]["json"]
        assert call_json["music_length_ms"] == 3000  # Clamped to min

    def test_duration_clamped_max(self, mock_caps):
        mock_response = MagicMock()
        mock_response.content = b"\xff" * 5000
        mock_response.raise_for_status.return_value = None
        mock_caps.http_post.return_value = mock_response

        plugin.generate_song.invoke({
            "prompt": "Epic symphony",
            "duration_seconds": 9999,
        })

        call_json = mock_caps.http_post.call_args[1]["json"]
        assert call_json["music_length_ms"] == 600000  # Clamped to max

    def test_api_error_returns_message(self, mock_caps):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("API rate limit exceeded")
        mock_caps.http_post.return_value = mock_response

        result = plugin.generate_song.invoke({"prompt": "test"})
        assert "failed" in result.lower() or "rate limit" in result.lower()

    def test_small_response_rejected(self, mock_caps):
        mock_response = MagicMock()
        mock_response.content = b"\xff" * 100  # Too small
        mock_response.raise_for_status.return_value = None
        mock_caps.http_post.return_value = mock_response

        result = plugin.generate_song.invoke({"prompt": "test song"})
        assert "failed" in result.lower() or "small" in result.lower()

    def test_save_failure_handled(self, mock_caps):
        mock_response = MagicMock()
        mock_response.content = b"\xff" * 5000
        mock_response.raise_for_status.return_value = None
        mock_caps.http_post.return_value = mock_response
        mock_caps.save_file.side_effect = OSError("Disk full")

        result = plugin.generate_song.invoke({"prompt": "test song"})
        assert "saving failed" in result.lower()

    def test_timeout_is_generous(self, mock_caps):
        """ElevenLabs music generation can be slow — verify timeout >= 120s."""
        mock_response = MagicMock()
        mock_response.content = b"\xff" * 5000
        mock_response.raise_for_status.return_value = None
        mock_caps.http_post.return_value = mock_response

        plugin.generate_song.invoke({"prompt": "test"})

        call_kwargs = mock_caps.http_post.call_args[1]
        assert call_kwargs["timeout"] >= 120


# ---------------------------------------------------------------------------
# File saving
# ---------------------------------------------------------------------------

class TestFileSaving:
    """Verify files are saved via the capabilities gateway."""

    def test_save_file_called_with_audio_bytes(self, mock_caps):
        audio_bytes = b"\xff\xfb\x90" * 2000
        mock_response = MagicMock()
        mock_response.content = audio_bytes
        mock_response.raise_for_status.return_value = None
        mock_caps.http_post.return_value = mock_response

        plugin.generate_song.invoke({"prompt": "saving test"})

        mock_caps.save_file.assert_called_once()
        saved_filename = mock_caps.save_file.call_args[0][0]
        saved_bytes = mock_caps.save_file.call_args[0][1]
        assert saved_filename.endswith(".mp3")
        assert saved_bytes == audio_bytes

    def test_filename_derived_from_prompt(self, mock_caps):
        mock_response = MagicMock()
        mock_response.content = b"\xff" * 5000
        mock_response.raise_for_status.return_value = None
        mock_caps.http_post.return_value = mock_response

        plugin.generate_song.invoke({"prompt": "My Cool Song"})

        saved_filename = mock_caps.save_file.call_args[0][0]
        assert "my_cool_song" in saved_filename.lower()
        assert saved_filename.endswith(".mp3")
