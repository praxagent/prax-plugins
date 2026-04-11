"""Tests for the imagegen plugin.

All OpenAI API calls are mocked.
The plugin uses the PluginCapabilities gateway — tests provide a mock caps object.
"""
from __future__ import annotations

import base64
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock Prax imports.
sys.modules.setdefault("prax", MagicMock())
sys.modules.setdefault("prax.settings", MagicMock())

from imagegen import plugin  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_b64_image(size: int = 5000) -> str:
    """Create a fake base64-encoded image payload."""
    return base64.b64encode(b"\x89PNG" + b"\x00" * size).decode()


def _make_generation_response(b64_data: str | None = None) -> MagicMock:
    """Create a mock HTTP response for the generations endpoint."""
    if b64_data is None:
        b64_data = _make_b64_image()
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "data": [{"b64_json": b64_data}],
    }
    return resp


def _make_edit_response(b64_data: str | None = None) -> MagicMock:
    """Create a mock HTTP response for the edits endpoint."""
    if b64_data is None:
        b64_data = _make_b64_image()
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "data": [{"b64_json": b64_data}],
    }
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_caps():
    """Create a mock PluginCapabilities instance."""
    caps = MagicMock()
    caps.get_approved_secret.return_value = "sk-test-openai-key"
    caps.get_user_id.return_value = "test-user"
    caps.save_file.return_value = "/workspace/test-user/active/image_123.png"
    caps.read_file.return_value = b"\x89PNG" + b"\x00" * 5000
    caps.workspace_path.return_value = "/workspace/test-user/active/source.png"
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
        assert len(tools) == 2
        tool_names = {t.name for t in tools}
        assert "generate_image" in tool_names
        assert "edit_image" in tool_names

    def test_plugin_version(self):
        assert plugin.PLUGIN_VERSION == "1"

    def test_plugin_description(self):
        assert plugin.PLUGIN_DESCRIPTION
        assert "gpt-image-1" in plugin.PLUGIN_DESCRIPTION

    def test_plugin_permissions_declared(self):
        assert hasattr(plugin, "PLUGIN_PERMISSIONS")
        perms = plugin.PLUGIN_PERMISSIONS
        assert isinstance(perms, list)
        assert len(perms) >= 1
        keys = [p["key"] for p in perms]
        assert "OPENAI_KEY" in keys

    def test_plugin_permissions_have_reasons(self):
        for perm in plugin.PLUGIN_PERMISSIONS:
            assert "key" in perm
            assert "reason" in perm
            assert len(perm["reason"]) > 10


# ---------------------------------------------------------------------------
# Secret access
# ---------------------------------------------------------------------------

class TestSecretAccess:
    """Verify the plugin accesses secrets through the gateway."""

    def test_get_api_key_calls_approved_secret(self, mock_caps):
        key = plugin._get_api_key()
        mock_caps.get_approved_secret.assert_called_with("OPENAI_KEY")
        assert key == "sk-test-openai-key"

    def test_get_api_key_raises_when_not_configured(self, mock_caps):
        mock_caps.get_approved_secret.return_value = None
        with pytest.raises(RuntimeError, match="not configured"):
            plugin._get_api_key()

    def test_get_api_key_raises_when_not_registered(self):
        plugin._caps = None
        with pytest.raises(RuntimeError, match="not registered"):
            plugin._get_api_key()


# ---------------------------------------------------------------------------
# generate_image
# ---------------------------------------------------------------------------

class TestGenerateImage:
    """Test the generate_image tool."""

    def test_empty_prompt_rejected(self):
        result = plugin.generate_image.invoke({"prompt": ""})
        assert "provide a prompt" in result.lower()

    def test_whitespace_prompt_rejected(self):
        result = plugin.generate_image.invoke({"prompt": "   "})
        assert "provide a prompt" in result.lower()

    def test_successful_generation(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()

        result = plugin.generate_image.invoke({
            "prompt": "A cat sitting on a rainbow",
        })

        assert "successfully" in result.lower()
        assert "image_123.png" in result
        mock_caps.http_post.assert_called_once()

    def test_calls_correct_endpoint(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()

        plugin.generate_image.invoke({"prompt": "test image"})

        call_args = mock_caps.http_post.call_args
        url = call_args[0][0]
        assert url == "https://api.openai.com/v1/images/generations"

    def test_sends_correct_payload(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()

        plugin.generate_image.invoke({
            "prompt": "A sunset over mountains",
            "size": "1536x1024",
            "quality": "high",
        })

        call_kwargs = mock_caps.http_post.call_args[1]
        payload = call_kwargs["json"]
        assert payload["model"] == "gpt-image-1"
        assert payload["prompt"] == "A sunset over mountains"
        assert payload["n"] == 1
        assert payload["size"] == "1536x1024"
        assert payload["quality"] == "high"
        assert payload["response_format"] == "b64_json"

    def test_sends_auth_header(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()

        plugin.generate_image.invoke({"prompt": "test"})

        call_kwargs = mock_caps.http_post.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer sk-test-openai-key"

    def test_style_included_when_not_auto(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()

        plugin.generate_image.invoke({
            "prompt": "A photorealistic landscape",
            "style": "natural",
        })

        call_kwargs = mock_caps.http_post.call_args[1]
        assert call_kwargs["json"]["style"] == "natural"

    def test_style_omitted_when_auto(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()

        plugin.generate_image.invoke({
            "prompt": "test",
            "style": "auto",
        })

        call_kwargs = mock_caps.http_post.call_args[1]
        assert "style" not in call_kwargs["json"]

    def test_invalid_size_defaults(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()

        plugin.generate_image.invoke({
            "prompt": "test",
            "size": "999x999",
        })

        call_kwargs = mock_caps.http_post.call_args[1]
        assert call_kwargs["json"]["size"] == "1024x1024"

    def test_invalid_quality_defaults(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()

        plugin.generate_image.invoke({
            "prompt": "test",
            "quality": "ultra",
        })

        call_kwargs = mock_caps.http_post.call_args[1]
        assert call_kwargs["json"]["quality"] == "auto"

    def test_base64_decoding(self, mock_caps):
        raw_bytes = b"\x89PNG\r\n\x1a\n" + b"\xab\xcd" * 2000
        b64_data = base64.b64encode(raw_bytes).decode()
        mock_caps.http_post.return_value = _make_generation_response(b64_data)

        plugin.generate_image.invoke({"prompt": "test decode"})

        saved_bytes = mock_caps.save_file.call_args[0][1]
        assert saved_bytes == raw_bytes

    def test_file_saved_with_png_extension(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()

        plugin.generate_image.invoke({"prompt": "test save"})

        saved_filename = mock_caps.save_file.call_args[0][0]
        assert saved_filename.endswith(".png")

    def test_filename_derived_from_prompt(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()

        plugin.generate_image.invoke({"prompt": "A Beautiful Sunset"})

        saved_filename = mock_caps.save_file.call_args[0][0]
        assert "a_beautiful_sunset" in saved_filename.lower()

    def test_api_error_returns_message(self, mock_caps):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("API rate limit exceeded")
        mock_caps.http_post.return_value = mock_response

        result = plugin.generate_image.invoke({"prompt": "test"})
        assert "failed" in result.lower() or "rate limit" in result.lower()

    def test_save_failure_handled(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()
        mock_caps.save_file.side_effect = OSError("Disk full")

        result = plugin.generate_image.invoke({"prompt": "test"})
        assert "saving failed" in result.lower()

    def test_timeout_is_generous(self, mock_caps):
        mock_caps.http_post.return_value = _make_generation_response()

        plugin.generate_image.invoke({"prompt": "test"})

        call_kwargs = mock_caps.http_post.call_args[1]
        assert call_kwargs["timeout"] >= 60


# ---------------------------------------------------------------------------
# edit_image
# ---------------------------------------------------------------------------

class TestEditImage:
    """Test the edit_image tool."""

    def test_empty_prompt_rejected(self):
        result = plugin.edit_image.invoke({
            "image_path": "source.png",
            "prompt": "",
        })
        assert "provide a prompt" in result.lower()

    def test_empty_image_path_rejected(self):
        result = plugin.edit_image.invoke({
            "image_path": "",
            "prompt": "add a hat",
        })
        assert "provide the path" in result.lower()

    def test_successful_edit(self, mock_caps):
        mock_caps.http_post.return_value = _make_edit_response()

        result = plugin.edit_image.invoke({
            "image_path": "source.png",
            "prompt": "Add a red hat to the person",
        })

        assert "successfully" in result.lower()
        assert "image_123.png" in result

    def test_calls_correct_endpoint(self, mock_caps):
        mock_caps.http_post.return_value = _make_edit_response()

        plugin.edit_image.invoke({
            "image_path": "source.png",
            "prompt": "edit test",
        })

        call_args = mock_caps.http_post.call_args
        url = call_args[0][0]
        assert url == "https://api.openai.com/v1/images/edits"

    def test_sends_multipart_form_data(self, mock_caps):
        mock_caps.http_post.return_value = _make_edit_response()

        plugin.edit_image.invoke({
            "image_path": "source.png",
            "prompt": "add sunglasses",
            "size": "1024x1024",
        })

        call_kwargs = mock_caps.http_post.call_args[1]

        # Should send files (multipart), not json.
        assert "files" in call_kwargs
        assert "data" in call_kwargs

        # Verify form data fields.
        form_data = call_kwargs["data"]
        assert form_data["model"] == "gpt-image-1"
        assert form_data["prompt"] == "add sunglasses"
        assert form_data["size"] == "1024x1024"
        assert form_data["response_format"] == "b64_json"

    def test_sends_image_in_files(self, mock_caps):
        source_bytes = b"\x89PNG" + b"\x00" * 5000
        mock_caps.read_file.return_value = source_bytes
        mock_caps.http_post.return_value = _make_edit_response()

        plugin.edit_image.invoke({
            "image_path": "source.png",
            "prompt": "edit it",
        })

        call_kwargs = mock_caps.http_post.call_args[1]
        files = call_kwargs["files"]
        assert "image" in files
        # files["image"] is a tuple: (filename, bytes, content_type)
        file_tuple = files["image"]
        assert file_tuple[0] == "source.png"  # filename
        assert file_tuple[1] == source_bytes  # image bytes
        assert file_tuple[2] == "image/png"  # content type

    def test_sends_auth_header(self, mock_caps):
        mock_caps.http_post.return_value = _make_edit_response()

        plugin.edit_image.invoke({
            "image_path": "source.png",
            "prompt": "test",
        })

        call_kwargs = mock_caps.http_post.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer sk-test-openai-key"

    def test_read_file_fallback_to_workspace_path(self, mock_caps):
        """If read_file raises, fall back to workspace_path + open()."""
        mock_caps.read_file.side_effect = Exception("Not found")
        mock_caps.http_post.return_value = _make_edit_response()

        # workspace_path returns a file path; we need to mock open() too.
        source_bytes = b"\x89PNG" + b"\x00" * 3000
        mock_caps.workspace_path.return_value = "/workspace/test-user/active/source.png"

        with patch("builtins.open", create=True) as mock_open:
            mock_file = MagicMock()
            mock_file.read.return_value = source_bytes
            mock_file.__enter__ = MagicMock(return_value=mock_file)
            mock_file.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_file

            result = plugin.edit_image.invoke({
                "image_path": "source.png",
                "prompt": "test fallback",
            })

        assert "successfully" in result.lower()

    def test_missing_source_image_returns_error(self, mock_caps):
        mock_caps.read_file.side_effect = Exception("Not found")
        mock_caps.workspace_path.side_effect = Exception("Not found")

        result = plugin.edit_image.invoke({
            "image_path": "nonexistent.png",
            "prompt": "test",
        })

        assert "could not read" in result.lower()

    def test_api_error_returns_message(self, mock_caps):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("Server error")
        mock_caps.http_post.return_value = mock_response

        result = plugin.edit_image.invoke({
            "image_path": "source.png",
            "prompt": "test",
        })
        assert "failed" in result.lower() or "error" in result.lower()

    def test_save_failure_handled(self, mock_caps):
        mock_caps.http_post.return_value = _make_edit_response()
        mock_caps.save_file.side_effect = OSError("Disk full")

        result = plugin.edit_image.invoke({
            "image_path": "source.png",
            "prompt": "test",
        })
        assert "saving failed" in result.lower()

    def test_edited_file_saved_with_png_extension(self, mock_caps):
        mock_caps.http_post.return_value = _make_edit_response()

        plugin.edit_image.invoke({
            "image_path": "source.png",
            "prompt": "add a border",
        })

        saved_filename = mock_caps.save_file.call_args[0][0]
        assert saved_filename.endswith(".png")

    def test_edited_filename_includes_edited_suffix(self, mock_caps):
        mock_caps.http_post.return_value = _make_edit_response()

        plugin.edit_image.invoke({
            "image_path": "source.png",
            "prompt": "add a border",
        })

        saved_filename = mock_caps.save_file.call_args[0][0]
        assert "edited" in saved_filename

    def test_base64_decoding_edit(self, mock_caps):
        raw_bytes = b"\x89PNG\r\n\x1a\n" + b"\xef\xbe" * 2000
        b64_data = base64.b64encode(raw_bytes).decode()
        mock_caps.http_post.return_value = _make_edit_response(b64_data)

        plugin.edit_image.invoke({
            "image_path": "source.png",
            "prompt": "decode test",
        })

        saved_bytes = mock_caps.save_file.call_args[0][1]
        assert saved_bytes == raw_bytes
