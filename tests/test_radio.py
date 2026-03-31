"""Tests for the radio plugin.

Tests cover registration, playlist scanning, HTTP streaming, and station
lifecycle.  The streaming tests start a real HTTP server on a random port
and connect to verify audio data is served.
"""
from __future__ import annotations

import os
import struct
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock Prax imports.
sys.modules.setdefault("prax", MagicMock())
sys.modules.setdefault("prax.settings", MagicMock())

from radio import plugin  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav(path: Path, duration_seconds: float = 0.5) -> None:
    """Write a minimal valid WAV file with silence."""
    sample_rate = 8000
    num_samples = int(sample_rate * duration_seconds)
    data_size = num_samples * 2  # 16-bit mono

    with open(path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))  # chunk size
        f.write(struct.pack("<H", 1))   # PCM
        f.write(struct.pack("<H", 1))   # mono
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", sample_rate * 2))  # byte rate
        f.write(struct.pack("<H", 2))   # block align
        f.write(struct.pack("<H", 16))  # bits per sample
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(b"\x00" * data_size)


def _make_fake_mp3(path: Path, size: int = 16384) -> None:
    """Write a fake MP3 file (just bytes, not a valid MP3 frame)."""
    with open(path, "wb") as f:
        # MP3 sync word + padding
        f.write(b"\xff\xfb\x90\x00" * (size // 4))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_caps(tmp_path):
    """Create a mock PluginCapabilities instance."""
    caps = MagicMock()
    caps.get_user_id.return_value = "test-user"
    caps.workspace_path.return_value = str(tmp_path / "music")
    return caps


@pytest.fixture(autouse=True)
def _register_and_clear(mock_caps):
    """Register mock caps and ensure station is stopped between tests."""
    plugin.register(mock_caps)
    yield
    # Always stop the station after each test.
    plugin._station.stop()
    plugin._caps = None


@pytest.fixture()
def music_dir(tmp_path):
    """Create a temporary directory with test audio files."""
    d = tmp_path / "music"
    d.mkdir()
    _make_fake_mp3(d / "track_01.mp3")
    _make_fake_mp3(d / "track_02.mp3")
    _make_fake_mp3(d / "track_03.mp3")
    _make_wav(d / "bonus.wav", duration_seconds=0.3)
    return str(d)


@pytest.fixture()
def empty_dir(tmp_path):
    """Create an empty temporary directory."""
    d = tmp_path / "empty"
    d.mkdir()
    return str(d)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    """Plugin registration and metadata."""

    def test_register_returns_tools(self, mock_caps):
        tools = plugin.register(mock_caps)
        assert isinstance(tools, list)
        names = {t.name for t in tools}
        assert names == {"start_radio", "stop_radio", "radio_status", "radio_skip", "radio_queue"}

    def test_plugin_version(self):
        assert plugin.PLUGIN_VERSION == "1"

    def test_plugin_description(self):
        assert plugin.PLUGIN_DESCRIPTION

    def test_no_permissions_needed(self):
        """Radio plugin needs no API keys."""
        perms = getattr(plugin, "PLUGIN_PERMISSIONS", None)
        assert perms is None


# ---------------------------------------------------------------------------
# Playlist scanning
# ---------------------------------------------------------------------------

class TestPlaylistScanning:
    """Verify audio file discovery."""

    def test_finds_audio_files(self, music_dir):
        station = plugin._RadioStation()
        files = station._scan_music(music_dir)
        assert len(files) == 4
        exts = {os.path.splitext(f)[1] for f in files}
        assert ".mp3" in exts
        assert ".wav" in exts

    def test_empty_directory(self, empty_dir):
        station = plugin._RadioStation()
        files = station._scan_music(empty_dir)
        assert files == []

    def test_ignores_non_audio(self, tmp_path):
        d = tmp_path / "mixed"
        d.mkdir()
        (d / "readme.txt").write_text("not audio")
        (d / "image.png").write_bytes(b"\x89PNG")
        _make_fake_mp3(d / "song.mp3")
        station = plugin._RadioStation()
        files = station._scan_music(str(d))
        assert len(files) == 1
        assert files[0].endswith("song.mp3")

    def test_recursive_scan(self, tmp_path):
        d = tmp_path / "nested"
        d.mkdir()
        sub = d / "genre" / "artist"
        sub.mkdir(parents=True)
        _make_fake_mp3(d / "root.mp3")
        _make_fake_mp3(sub / "deep.mp3")
        station = plugin._RadioStation()
        files = station._scan_music(str(d))
        assert len(files) == 2


# ---------------------------------------------------------------------------
# Station lifecycle
# ---------------------------------------------------------------------------

class TestStationLifecycle:
    """Start/stop behavior."""

    def test_start_and_stop(self, music_dir):
        station = plugin._station
        result = station.start(music_dir=music_dir)
        assert result["status"] == "started"
        assert result["port"] > 0
        assert result["tracks"] == 4
        assert station.running

        stop_result = station.stop()
        assert stop_result["status"] == "stopped"
        assert not station.running

    def test_start_nonexistent_dir(self):
        station = plugin._station
        result = station.start(music_dir="/nonexistent/path")
        assert "error" in result

    def test_start_empty_dir(self, empty_dir):
        station = plugin._station
        result = station.start(music_dir=empty_dir)
        assert "error" in result
        assert "no audio" in result["error"].lower()

    def test_double_start_rejected(self, music_dir):
        station = plugin._station
        station.start(music_dir=music_dir)
        result = station.start(music_dir=music_dir)
        assert "error" in result
        assert "already running" in result["error"].lower()

    def test_stop_when_not_running(self):
        station = plugin._station
        result = station.stop()
        assert result["status"] == "not_running"


# ---------------------------------------------------------------------------
# HTTP streaming (real server, real connections)
# ---------------------------------------------------------------------------

class TestHTTPStreaming:
    """End-to-end streaming tests with a real HTTP server."""

    def test_stream_endpoint_serves_audio(self, music_dir):
        station = plugin._station
        result = station.start(music_dir=music_dir, shuffle=False)
        port = result["port"]

        # Give the broadcast thread a moment to start reading.
        time.sleep(1)

        # Connect to the stream and read some data.
        try:
            resp = requests.get(
                f"http://localhost:{port}/stream",
                stream=True, timeout=5,
            )
            assert resp.status_code == 200
            assert resp.headers.get("Content-Type") == "audio/mpeg"
            assert resp.headers.get("icy-name") == "Prax Radio"

            # Read at least one chunk.
            data = b""
            for chunk in resp.iter_content(chunk_size=4096):
                data += chunk
                if len(data) >= 4096:
                    break
            assert len(data) >= 4096, f"Expected at least 4KB of audio data, got {len(data)}"
        finally:
            resp.close()

    def test_status_endpoint(self, music_dir):
        station = plugin._station
        result = station.start(music_dir=music_dir)
        port = result["port"]

        time.sleep(0.5)

        resp = requests.get(f"http://localhost:{port}/status", timeout=5)
        assert resp.status_code == 200
        info = resp.json()
        assert info["running"] is True
        assert info["station"] == "Prax Radio"
        assert info["playlist_size"] == 4

    def test_playlist_endpoint(self, music_dir):
        station = plugin._station
        result = station.start(music_dir=music_dir, shuffle=False)
        port = result["port"]

        time.sleep(0.5)

        resp = requests.get(f"http://localhost:{port}/playlist", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert "tracks" in data
        assert len(data["tracks"]) == 4

    def test_multiple_listeners(self, music_dir):
        station = plugin._station
        result = station.start(music_dir=music_dir)
        port = result["port"]

        time.sleep(1)

        # Connect two listeners simultaneously.
        sessions = []
        responses = []
        try:
            for _ in range(2):
                s = requests.Session()
                r = s.get(
                    f"http://localhost:{port}/stream",
                    stream=True, timeout=5,
                )
                sessions.append(s)
                responses.append(r)

            # Both should get 200 and be streaming.
            for r in responses:
                assert r.status_code == 200

            time.sleep(0.5)

            # Station should report 2 listeners.
            assert station.listener_count >= 2
        finally:
            for r in responses:
                r.close()
            for s in sessions:
                s.close()

    def test_listener_disconnect_cleanup(self, music_dir):
        station = plugin._station
        result = station.start(music_dir=music_dir)
        port = result["port"]

        time.sleep(1)

        # Connect and immediately disconnect.
        resp = requests.get(
            f"http://localhost:{port}/stream",
            stream=True, timeout=5,
        )
        assert resp.status_code == 200
        resp.close()

        # Give the server a moment to notice the disconnect.
        time.sleep(2)

        # Listener count should be back to 0.
        assert station.listener_count == 0

    def test_404_on_unknown_path(self, music_dir):
        station = plugin._station
        result = station.start(music_dir=music_dir)
        port = result["port"]

        time.sleep(0.5)

        resp = requests.get(f"http://localhost:{port}/nonexistent", timeout=5)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Skip
# ---------------------------------------------------------------------------

class TestSkip:
    """Track skipping."""

    def test_skip_when_running(self, music_dir):
        station = plugin._station
        station.start(music_dir=music_dir)
        time.sleep(1)  # Let it start playing a track.

        result = station.skip()
        assert "skipping" in result.lower()

    def test_skip_when_not_running(self):
        station = plugin._station
        result = station.skip()
        assert "not running" in result.lower()


# ---------------------------------------------------------------------------
# Tools (invoke through langchain interface)
# ---------------------------------------------------------------------------

class TestTools:
    """Test tools via the LangChain invoke interface."""

    def test_start_radio_tool(self, music_dir):
        result = plugin.start_radio.invoke({
            "music_directory": music_dir,
            "station_name": "Test FM",
        })
        assert "live" in result.lower() or "Test FM" in result
        assert "localhost" in result

    def test_start_radio_no_directory(self, mock_caps, empty_dir):
        mock_caps.workspace_path.return_value = empty_dir
        result = plugin.start_radio.invoke({"music_directory": empty_dir})
        assert "no audio" in result.lower()

    def test_stop_radio_tool(self, music_dir):
        plugin.start_radio.invoke({"music_directory": music_dir})
        result = plugin.stop_radio.invoke({})
        assert "stopped" in result.lower()

    def test_radio_status_tool_when_stopped(self):
        result = plugin.radio_status.invoke({})
        assert "not running" in result.lower()

    def test_radio_status_tool_when_running(self, music_dir):
        plugin.start_radio.invoke({"music_directory": music_dir})
        time.sleep(0.5)
        result = plugin.radio_status.invoke({})
        assert "on air" in result.lower()

    def test_radio_queue_tool(self, music_dir):
        plugin.start_radio.invoke({
            "music_directory": music_dir,
            "shuffle": False,
        })
        time.sleep(0.5)
        result = plugin.radio_queue.invoke({"count": 5})
        assert "playlist" in result.lower()
        assert "track_" in result.lower() or "bonus" in result.lower()

    def test_radio_skip_tool(self, music_dir):
        plugin.start_radio.invoke({"music_directory": music_dir})
        time.sleep(1)
        result = plugin.radio_skip.invoke({})
        assert "skipping" in result.lower()
