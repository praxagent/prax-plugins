"""Internet radio station plugin for Prax.

Streams audio files from a workspace directory as a SHOUTcast-compatible
HTTP audio stream.  Listeners connect with any media player (VLC, browsers,
mpv, etc.) and hear the same continuous broadcast.

The station runs as a background thread inside the Prax process and can
optionally be exposed publicly via ngrok.

No API keys required — this plugin only uses the capabilities gateway for
workspace access and shell commands (ngrok tunnel).
"""
from __future__ import annotations

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Stream a directory of audio files as internet radio"

import logging
import os
import queue
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Module-level caps reference, set during register().
_caps = None

# Supported audio formats (by extension).
_AUDIO_EXTENSIONS = {".mp3", ".ogg", ".wav", ".flac", ".aac", ".m4a"}

# Chunk size for reading audio files and pushing to listeners.
_CHUNK_SIZE = 8192  # 8 KB — small enough for low latency

# Maximum listener queue depth before we consider them stalled.
_MAX_QUEUE_DEPTH = 200


class _RadioStation:
    """Core radio station: reads audio files and broadcasts to listeners."""

    def __init__(self) -> None:
        self.running = False
        self.music_dir: str = ""
        self.shuffle: bool = True
        self.station_name: str = "Prax Radio"
        self.port: int = 0
        self.ngrok_url: str | None = None

        self._playlist: list[str] = []
        self._playlist_index: int = 0
        self._current_track: str = ""
        self._tracks_played: int = 0
        self._started_at: float = 0.0

        self._listeners: list[queue.Queue] = []
        self._listeners_lock = threading.Lock()

        self._server: HTTPServer | None = None
        self._broadcast_thread: threading.Thread | None = None
        self._server_thread: threading.Thread | None = None
        self._skip_event = threading.Event()

    # ------------------------------------------------------------------
    # Playlist
    # ------------------------------------------------------------------

    def _scan_music(self, music_dir: str) -> list[str]:
        """Find all audio files in the directory (recursive)."""
        files = []
        for root, _dirs, filenames in os.walk(music_dir):
            for fname in filenames:
                if os.path.splitext(fname)[1].lower() in _AUDIO_EXTENSIONS:
                    files.append(os.path.join(root, fname))
        files.sort()
        return files

    def _build_playlist(self) -> None:
        self._playlist = self._scan_music(self.music_dir)
        if self.shuffle:
            random.shuffle(self._playlist)
        self._playlist_index = 0

    def _next_track(self) -> str | None:
        """Return the next track path, rebuilding playlist if exhausted."""
        if not self._playlist:
            self._build_playlist()
        if not self._playlist:
            return None
        if self._playlist_index >= len(self._playlist):
            # Re-shuffle and restart.
            if self.shuffle:
                random.shuffle(self._playlist)
            self._playlist_index = 0
        track = self._playlist[self._playlist_index]
        self._playlist_index += 1
        return track

    # ------------------------------------------------------------------
    # Broadcast loop
    # ------------------------------------------------------------------

    def _broadcast_loop(self) -> None:
        """Read audio files and push chunks to all listener queues."""
        while self.running:
            track = self._next_track()
            if track is None:
                logger.warning("Radio: no audio files found in %s", self.music_dir)
                time.sleep(5)
                continue

            self._current_track = os.path.basename(track)
            self._tracks_played += 1
            logger.info("Radio: now playing %s", self._current_track)

            self._skip_event.clear()
            try:
                with open(track, "rb") as f:
                    while self.running and not self._skip_event.is_set():
                        chunk = f.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        self._push_to_listeners(chunk)
                        # Throttle to roughly real-time for 128kbps MP3.
                        # 8192 bytes at 128kbps = ~0.5s of audio.
                        time.sleep(0.4)
            except Exception:
                logger.exception("Radio: error reading %s", track)

        logger.info("Radio: broadcast loop stopped")

    def _push_to_listeners(self, chunk: bytes) -> None:
        """Push a chunk to all connected listeners, dropping stalled ones."""
        with self._listeners_lock:
            stalled = []
            for q in self._listeners:
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    stalled.append(q)
            for q in stalled:
                self._listeners.remove(q)
                logger.debug("Radio: dropped stalled listener")

    # ------------------------------------------------------------------
    # Listener management
    # ------------------------------------------------------------------

    def add_listener(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=_MAX_QUEUE_DEPTH)
        with self._listeners_lock:
            self._listeners.append(q)
        return q

    def remove_listener(self, q: queue.Queue) -> None:
        with self._listeners_lock:
            if q in self._listeners:
                self._listeners.remove(q)

    @property
    def listener_count(self) -> int:
        with self._listeners_lock:
            return len(self._listeners)

    # ------------------------------------------------------------------
    # HTTP server
    # ------------------------------------------------------------------

    def _make_handler(station_ref):  # noqa: N805 — used as a closure, not a method
        """Create a request handler class bound to this station."""
        station = station_ref

        class RadioHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in ("/", "/stream"):
                    self._handle_stream()
                elif self.path == "/status":
                    self._handle_status()
                elif self.path == "/playlist":
                    self._handle_playlist()
                else:
                    self.send_error(404)

            def _handle_stream(self):
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("icy-name", station.station_name)
                self.send_header("icy-genre", "Mixed")
                self.send_header("Cache-Control", "no-cache, no-store")
                self.send_header("Connection", "close")
                self.end_headers()

                q = station.add_listener()
                try:
                    while station.running:
                        try:
                            chunk = q.get(timeout=5)
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        except queue.Empty:
                            continue
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    station.remove_listener(q)

            def _handle_status(self):
                import json
                info = {
                    "station": station.station_name,
                    "running": station.running,
                    "current_track": station._current_track,
                    "tracks_played": station._tracks_played,
                    "listeners": station.listener_count,
                    "uptime_seconds": int(time.time() - station._started_at) if station.running else 0,
                    "playlist_size": len(station._playlist),
                    "shuffle": station.shuffle,
                }
                body = json.dumps(info, indent=2).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle_playlist(self):
                tracks = [os.path.basename(t) for t in station._playlist]
                current_idx = station._playlist_index - 1
                import json
                body = json.dumps({
                    "current_index": current_idx,
                    "tracks": tracks,
                }, indent=2).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                # Suppress default stderr logging.
                logger.debug("Radio HTTP: %s", format % args)

        return RadioHandler

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def start(
        self,
        music_dir: str,
        port: int = 0,
        shuffle: bool = True,
        station_name: str = "Prax Radio",
    ) -> dict:
        """Start the radio station. Returns connection info."""
        if self.running:
            return {"error": "Radio is already running. Stop it first."}

        if not os.path.isdir(music_dir):
            return {"error": f"Directory not found: {music_dir}"}

        self.music_dir = music_dir
        self.shuffle = shuffle
        self.station_name = station_name
        self._tracks_played = 0
        self._current_track = ""

        self._build_playlist()
        if not self._playlist:
            return {"error": f"No audio files found in {music_dir}"}

        self.running = True
        self._started_at = time.time()

        # Start HTTP server (port 0 = OS auto-selects a free port).
        handler = self._make_handler()
        self._server = HTTPServer(("0.0.0.0", port), handler)
        self.port = self._server.server_address[1]
        self._server.timeout = 1
        self._server_thread = threading.Thread(
            target=self._serve_loop, daemon=True, name="radio-http",
        )
        self._server_thread.start()

        # Start broadcast loop.
        self._broadcast_thread = threading.Thread(
            target=self._broadcast_loop, daemon=True, name="radio-broadcast",
        )
        self._broadcast_thread.start()

        return {
            "status": "started",
            "port": self.port,
            "local_url": f"http://localhost:{self.port}/stream",
            "status_url": f"http://localhost:{self.port}/status",
            "tracks": len(self._playlist),
            "shuffle": self.shuffle,
        }

    def _serve_loop(self) -> None:
        """HTTP server loop — handles requests until stopped."""
        while self.running:
            self._server.handle_request()
        logger.info("Radio: HTTP server stopped")

    def stop(self) -> dict:
        """Stop the radio station."""
        if not self.running:
            return {"status": "not_running"}

        self.running = False
        self._skip_event.set()

        # Stop ngrok tunnel if running.
        if self.ngrok_url is not None:
            try:
                _caps.run_command(["pkill", "-f", "ngrok http"], timeout=5)
            except Exception:
                pass
            self.ngrok_url = None

        # Close server.
        if self._server:
            self._server.server_close()

        # Drain all listener queues so threads unblock.
        with self._listeners_lock:
            self._listeners.clear()

        return {
            "status": "stopped",
            "tracks_played": self._tracks_played,
            "uptime_seconds": int(time.time() - self._started_at),
        }

    def skip(self) -> str:
        """Skip to the next track."""
        if not self.running:
            return "Radio is not running."
        self._skip_event.set()
        return f"Skipping '{self._current_track}'..."

    def status(self) -> dict:
        return {
            "running": self.running,
            "station_name": self.station_name,
            "current_track": self._current_track,
            "tracks_played": self._tracks_played,
            "listeners": self.listener_count,
            "playlist_size": len(self._playlist),
            "shuffle": self.shuffle,
            "port": self.port,
            "local_url": f"http://localhost:{self.port}/stream" if self.running else None,
            "ngrok_url": self.ngrok_url,
            "uptime_seconds": int(time.time() - self._started_at) if self.running else 0,
        }


# Singleton station instance.
_station = _RadioStation()


def _try_ngrok(port: int) -> str | None:
    """Try to start an ngrok tunnel via caps. Returns public URL or None."""
    if _caps is None:
        return None
    try:
        # Check if ngrok is available.
        result = _caps.run_command(["which", "ngrok"], timeout=5)
        if result.returncode != 0:
            return None

        # Start ngrok in background (detached via shell).
        _caps.run_command(
            ["sh", "-c", f"nohup ngrok http {port} --log=/dev/null > /dev/null 2>&1 &"],
            timeout=5,
        )

        # Give ngrok a moment to establish the tunnel.
        time.sleep(3)

        # Fetch the public URL from the ngrok local API.
        try:
            resp = _caps.http_get("http://localhost:4040/api/tunnels", timeout=5)
            tunnels = resp.json().get("tunnels", [])
            for t in tunnels:
                if str(port) in t.get("config", {}).get("addr", ""):
                    public_url = t.get("public_url", "")
                    if public_url:
                        return public_url + "/stream"
        except Exception:
            pass

        return None
    except Exception:
        logger.debug("ngrok not available", exc_info=True)
        return None


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------


@tool
def start_radio(
    music_directory: str = "",
    shuffle: bool = True,
    station_name: str = "Prax Radio",
    expose_ngrok: bool = False,
    port: int = 0,
) -> str:
    """Start an internet radio station streaming audio files from a directory.

    The station serves an HTTP audio stream compatible with VLC, browsers,
    mpv, and most media players. All listeners hear the same broadcast.

    Args:
        music_directory: Path to the directory containing audio files
            (MP3, OGG, WAV, FLAC, AAC, M4A). If empty, uses the user's
            workspace active directory. Scans subdirectories recursively.
        shuffle: If true, play tracks in random order (default true).
        station_name: Name for the radio station (shown in player metadata).
        expose_ngrok: If true, try to create a public ngrok tunnel.
        port: Specific port to use (0 = auto-select a free port).
    """
    if _station.running:
        return (
            f"Radio is already running on port {_station.port}.\n"
            f"URL: {_station.ngrok_url or f'http://localhost:{_station.port}/stream'}\n"
            f"Use stop_radio to stop it first."
        )

    # Resolve music directory.
    if not music_directory:
        if _caps:
            music_directory = _caps.workspace_path("music")
        else:
            return "No music directory specified and no workspace context available."

    if not os.path.isdir(music_directory):
        return (
            f"Directory not found: {music_directory}\n"
            f"Create it and add some audio files (MP3, OGG, WAV, FLAC, AAC, M4A)."
        )

    result = _station.start(
        music_dir=music_directory,
        port=port,
        shuffle=shuffle,
        station_name=station_name,
    )

    if "error" in result:
        return result["error"]

    local_url = result["local_url"]
    status_url = result["status_url"]
    lines = [
        f"**{station_name}** is now live!",
        f"- **Stream URL:** {local_url}",
        f"- **Status:** {status_url}",
        f"- **Tracks:** {result['tracks']} audio files",
        f"- **Shuffle:** {'On' if shuffle else 'Off'}",
        "",
        "Connect with: `vlc {url}` or open in any browser/media player.",
    ]

    # Try ngrok if requested.
    if expose_ngrok:
        ngrok_url = _try_ngrok(result["port"])
        if ngrok_url:
            _station.ngrok_url = ngrok_url
            lines.insert(2, f"- **Public URL:** {ngrok_url}")
            lines.append(f"\nShare this link for public access: {ngrok_url}")
        else:
            lines.append(
                "\nngrok not available — install it for public access: "
                "https://ngrok.com/download"
            )

    return "\n".join(lines)


@tool
def stop_radio() -> str:
    """Stop the radio station and disconnect all listeners."""
    result = _station.stop()
    if result["status"] == "not_running":
        return "Radio is not running."
    return (
        f"Radio stopped.\n"
        f"- Tracks played: {result['tracks_played']}\n"
        f"- Uptime: {result['uptime_seconds']}s"
    )


@tool
def radio_status() -> str:
    """Check the current status of the radio station.

    Shows what's playing, listener count, stream URL, and uptime.
    """
    info = _station.status()
    if not info["running"]:
        return "Radio is not running. Use start_radio to start it."

    uptime_min = info["uptime_seconds"] // 60
    uptime_sec = info["uptime_seconds"] % 60

    lines = [
        f"**{info['station_name']}** — On Air",
        f"- **Now playing:** {info['current_track'] or '(loading...)'}",
        f"- **Listeners:** {info['listeners']}",
        f"- **Tracks played:** {info['tracks_played']} / {info['playlist_size']}",
        f"- **Shuffle:** {'On' if info['shuffle'] else 'Off'}",
        f"- **Uptime:** {uptime_min}m {uptime_sec}s",
        f"- **Stream:** {info['ngrok_url'] or info['local_url']}",
    ]
    if info["ngrok_url"]:
        lines.append(f"- **Local:** {info['local_url']}")
    return "\n".join(lines)


@tool
def radio_skip() -> str:
    """Skip to the next track on the radio station."""
    return _station.skip()


@tool
def radio_queue(count: int = 10) -> str:
    """Show the upcoming tracks in the radio playlist.

    Args:
        count: Number of upcoming tracks to show (default 10).
    """
    if not _station.running:
        return "Radio is not running."

    playlist = _station._playlist
    idx = _station._playlist_index
    count = max(1, min(count, 50))

    if not playlist:
        return "Playlist is empty."

    lines = [f"**Playlist** ({len(playlist)} tracks, {'shuffled' if _station.shuffle else 'sequential'})\n"]

    # Show current track.
    if _station._current_track:
        lines.append(f"  **Now:** {_station._current_track}")

    # Show upcoming.
    upcoming = []
    for i in range(count):
        pos = (idx + i) % len(playlist)
        upcoming.append(os.path.basename(playlist[pos]))

    if upcoming:
        lines.append("\n  **Up next:**")
        for i, name in enumerate(upcoming, 1):
            lines.append(f"  {i}. {name}")

    return "\n".join(lines)


def register(caps):
    """Return the tools this plugin provides.

    Receives a PluginCapabilities instance for workspace access.
    No API keys needed — this plugin only streams local audio files.
    """
    global _caps
    _caps = caps
    return [start_radio, stop_radio, radio_status, radio_skip, radio_queue]
