"""AI image generation and editing plugin for Prax.

Generates and edits images via the OpenAI Images API (gpt-image-1).
The plugin never touches API keys directly — it requests the OPENAI_KEY
through the approved-secret gateway.

Configure:
    1. Import this plugin into Prax
    2. Approve the OPENAI_KEY permission when prompted
    3. Ensure OPENAI_KEY is set in your Prax environment

API docs: https://platform.openai.com/docs/api-reference/images
"""
from __future__ import annotations

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = (
    "AI image generation and editing via OpenAI gpt-image-1 (DALL-E). "
    "Generate images from text prompts or edit existing images."
)

PLUGIN_PERMISSIONS = [
    {
        "key": "OPENAI_KEY",
        "reason": "Required for OpenAI Images API (gpt-image-1)",
    },
]

import base64
import logging
import os
import time

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_GENERATIONS_URL = "https://api.openai.com/v1/images/generations"
_EDITS_URL = "https://api.openai.com/v1/images/edits"

_VALID_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}
_VALID_QUALITIES = {"low", "medium", "high", "auto"}
_VALID_STYLES = {"natural", "vivid", "auto"}

# Module-level caps reference, set during register().
_caps = None


def _get_api_key() -> str:
    """Retrieve the OpenAI API key through the approved-secret gateway."""
    if _caps is None:
        raise RuntimeError("Plugin not registered — no capabilities context.")
    key = _caps.get_approved_secret("OPENAI_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_KEY is not configured. "
            "Set it in your Prax environment (.env or settings)."
        )
    return key


def _slugify(text: str, max_len: int = 40) -> str:
    """Create a filesystem-safe slug from text."""
    slug = text[:max_len].lower()
    slug = "".join(c if c.isalnum() or c in " -_" else "" for c in slug)
    slug = slug.strip().replace(" ", "_") or "image"
    return slug


def _save_image_to_workspace(image_bytes: bytes, prompt: str, suffix: str = "") -> str:
    """Save image bytes to the user's workspace and return the path."""
    slug = _slugify(prompt)
    timestamp = int(time.time())
    tag = f"_{suffix}" if suffix else ""
    filename = f"{slug}{tag}_{timestamp}.png"
    path = _caps.save_file(filename, image_bytes)
    return path


# ======================================================================
# Tool: generate_image
# ======================================================================

@tool
def generate_image(
    prompt: str,
    size: str = "1024x1024",
    quality: str = "auto",
    style: str = "auto",
) -> str:
    """Generate an image from a text description using DALL-E / gpt-image-1.

    Args:
        prompt: Detailed description of the image to generate. Be specific
            about style, composition, lighting, etc.
        size: Image dimensions. Options: "1024x1024" (square),
            "1536x1024" (landscape), "1024x1536" (portrait), "auto"
        quality: "low" (faster, cheaper), "medium", "high" (best quality),
            or "auto"
        style: "natural" (photorealistic) or "vivid" (dramatic/hyper-real)
            or "auto"

    Returns:
        Path to the saved image file in the user's workspace.
    """
    if not prompt or not prompt.strip():
        return "Please provide a prompt describing the image you want to generate."

    prompt = prompt.strip()

    if size not in _VALID_SIZES:
        size = "1024x1024"
    if quality not in _VALID_QUALITIES:
        quality = "auto"
    if style not in _VALID_STYLES:
        style = "auto"

    try:
        api_key = _get_api_key()

        payload = {
            "model": "gpt-image-1",
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "b64_json",
        }
        if style != "auto":
            payload["style"] = style

        logger.info(
            "Generating image: prompt=%r, size=%s, quality=%s, style=%s",
            prompt[:80], size, quality, style,
        )

        resp = _caps.http_post(
            _GENERATIONS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()

        data = resp.json()
        b64_data = data["data"][0]["b64_json"]
        image_bytes = base64.b64decode(b64_data)

        if not image_bytes or len(image_bytes) < 100:
            raise RuntimeError(
                f"OpenAI returned unexpectedly small image ({len(image_bytes)} bytes). "
                "The generation may have failed."
            )

    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Image generation failed: {e}"

    try:
        saved_path = _save_image_to_workspace(image_bytes, prompt)
    except Exception as e:
        return f"Image was generated but saving failed: {e}"

    size_kb = len(image_bytes) / 1024
    return (
        f"Image generated successfully!\n"
        f"- **File:** {saved_path}\n"
        f"- **Size:** {size_kb:.0f} KB\n"
        f"- **Dimensions:** {size}\n"
        f"- **Quality:** {quality}\n"
        f"- **Prompt:** {prompt[:100]}"
    )


# ======================================================================
# Tool: edit_image
# ======================================================================

@tool
def edit_image(
    image_path: str,
    prompt: str,
    size: str = "1024x1024",
) -> str:
    """Edit an existing image using AI — add, remove, or modify elements.

    Args:
        image_path: Path to the source image in the workspace.
        prompt: Description of the edit to make (e.g., "add a red hat to
            the person", "remove the background").
        size: Output size. Options: "1024x1024", "1536x1024", "1024x1536",
            "auto"

    Returns:
        Path to the edited image in the workspace.
    """
    if not prompt or not prompt.strip():
        return "Please provide a prompt describing the edit to make."
    if not image_path or not image_path.strip():
        return "Please provide the path to the source image."

    prompt = prompt.strip()
    image_path = image_path.strip()

    if size not in _VALID_SIZES:
        size = "1024x1024"

    try:
        api_key = _get_api_key()

        # Read the source image from workspace.
        try:
            image_bytes = _caps.read_file(image_path)
        except Exception:
            # Try resolving via workspace_path if read_file fails.
            try:
                full_path = _caps.workspace_path(image_path)
                with open(full_path, "rb") as f:
                    image_bytes = f.read()
            except Exception:
                return (
                    f"Could not read source image: {image_path}\n"
                    f"Make sure the file exists in your workspace."
                )

        if not image_bytes or len(image_bytes) < 100:
            return f"Source image appears to be empty or too small: {image_path}"

        # Determine the source filename for the multipart upload.
        source_filename = os.path.basename(image_path)
        if not source_filename:
            source_filename = "image.png"

        logger.info(
            "Editing image: path=%r, prompt=%r, size=%s",
            image_path, prompt[:80], size,
        )

        resp = _caps.http_post(
            _EDITS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            files={
                "image": (source_filename, image_bytes, "image/png"),
            },
            data={
                "model": "gpt-image-1",
                "prompt": prompt,
                "n": "1",
                "size": size,
                "response_format": "b64_json",
            },
            timeout=120,
        )
        resp.raise_for_status()

        data = resp.json()
        b64_data = data["data"][0]["b64_json"]
        edited_bytes = base64.b64decode(b64_data)

        if not edited_bytes or len(edited_bytes) < 100:
            raise RuntimeError(
                f"OpenAI returned unexpectedly small image ({len(edited_bytes)} bytes). "
                "The edit may have failed."
            )

    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Image editing failed: {e}"

    try:
        saved_path = _save_image_to_workspace(edited_bytes, prompt, suffix="edited")
    except Exception as e:
        return f"Edited image was generated but saving failed: {e}"

    size_kb = len(edited_bytes) / 1024
    return (
        f"Image edited successfully!\n"
        f"- **File:** {saved_path}\n"
        f"- **Size:** {size_kb:.0f} KB\n"
        f"- **Source:** {image_path}\n"
        f"- **Prompt:** {prompt[:100]}"
    )


# ======================================================================
# Plugin registration
# ======================================================================

def register(caps):
    """Return the tools this plugin provides.

    Receives a PluginCapabilities instance for credentialed operations.
    The API key is accessed via caps.get_approved_secret() — the plugin
    never reads environment variables or settings directly.
    """
    global _caps
    _caps = caps
    return [generate_image, edit_image]
