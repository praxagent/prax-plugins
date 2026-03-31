"""ElevenLabs music generation plugin for Prax.

Generates songs via the ElevenLabs Music API and saves them to the
user's workspace.  The plugin never touches API keys directly — it
requests the ELEVENLABS_API_KEY through the approved-secret gateway.

Configure:
    1. Import this plugin into Prax
    2. Approve the ELEVENLABS_API_KEY permission when prompted
    3. Ensure ELEVENLABS_API_KEY is set in your Prax environment

API docs: https://elevenlabs.io/docs/api-reference/music/create-music
"""
from __future__ import annotations

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Generate songs with ElevenLabs Music API"

PLUGIN_PERMISSIONS = [
    {
        "key": "ELEVENLABS_API_KEY",
        "reason": (
            "Authenticate with the ElevenLabs Music API to generate songs. "
            "The key is used in the xi-api-key header for API requests."
        ),
    },
]

import logging
import time

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_MUSIC_API_URL = "https://api.elevenlabs.io/v1/music"

# Module-level caps reference, set during register().
_caps = None


def _get_api_key() -> str:
    """Retrieve the ElevenLabs API key through the approved-secret gateway."""
    if _caps is None:
        raise RuntimeError("Plugin not registered — no capabilities context.")
    key = _caps.get_approved_secret("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY is not configured. "
            "Set it in your Prax environment (.env or settings)."
        )
    return key


def _generate_music(
    prompt: str,
    duration_seconds: int = 30,
    instrumental: bool = False,
) -> bytes:
    """Call the ElevenLabs Music API and return raw audio bytes."""
    api_key = _get_api_key()

    duration_ms = max(3000, min(duration_seconds * 1000, 600000))

    resp = _caps.http_post(
        _MUSIC_API_URL,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        },
        json={
            "prompt": prompt,
            "music_length_ms": duration_ms,
            "force_instrumental": instrumental,
        },
        timeout=180,
    )
    resp.raise_for_status()

    content = resp.content
    if not content or len(content) < 1000:
        raise RuntimeError(
            f"ElevenLabs returned unexpectedly small response ({len(content)} bytes). "
            "The generation may have failed."
        )
    return content


def _save_to_workspace(audio_bytes: bytes, prompt: str) -> str:
    """Save the generated audio to the user's workspace and return the path."""
    # Build a filename from the prompt.
    slug = prompt[:40].lower()
    slug = "".join(c if c.isalnum() or c in " -_" else "" for c in slug)
    slug = slug.strip().replace(" ", "_") or "song"
    timestamp = int(time.time())
    filename = f"{slug}_{timestamp}.mp3"

    path = _caps.save_file(filename, audio_bytes)
    return path


@tool
def generate_song(
    prompt: str,
    duration_seconds: int = 30,
    instrumental: bool = False,
) -> str:
    """Generate a song using ElevenLabs Music and save it to the workspace.

    Creates an MP3 audio file based on the text prompt. The prompt can
    describe genre, mood, instruments, lyrics, and style.

    Args:
        prompt: Description of the song to generate. Can include lyrics,
            genre, mood, tempo, and instrumentation. Example:
            "An upbeat pop song about coding with catchy synth hooks"
        duration_seconds: Length of the song in seconds (3-600, default 30).
        instrumental: If true, generate instrumental music only (no vocals).
    """
    if not prompt or not prompt.strip():
        return "Please provide a prompt describing the song you want to generate."

    duration_seconds = max(3, min(duration_seconds, 600))

    try:
        logger.info(
            "Generating song: prompt=%r, duration=%ds, instrumental=%s",
            prompt[:80], duration_seconds, instrumental,
        )
        audio_bytes = _generate_music(
            prompt=prompt.strip(),
            duration_seconds=duration_seconds,
            instrumental=instrumental,
        )
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Song generation failed: {e}"

    try:
        saved_path = _save_to_workspace(audio_bytes, prompt)
    except Exception as e:
        return f"Song was generated but saving failed: {e}"

    size_mb = len(audio_bytes) / (1024 * 1024)
    return (
        f"Song generated successfully!\n"
        f"- **File:** {saved_path}\n"
        f"- **Size:** {size_mb:.1f} MB\n"
        f"- **Duration:** ~{duration_seconds}s\n"
        f"- **Instrumental:** {'Yes' if instrumental else 'No'}\n"
        f"- **Prompt:** {prompt[:100]}"
    )


def register(caps):
    """Return the tools this plugin provides.

    Receives a PluginCapabilities instance for credentialed operations.
    The API key is accessed via caps.get_approved_secret() — the plugin
    never reads environment variables or settings directly.
    """
    global _caps
    _caps = caps
    return [generate_song]
