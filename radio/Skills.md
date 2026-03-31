# Radio Station Plugin

## When to use

- User wants to stream or listen to their audio files as a radio station
- User wants to share music with others via a stream URL
- User says "start the radio", "play my music", or "set up a radio station"
- User wants a background music stream while working

## When NOT to use

- User wants to generate new music — use the elevenmusic plugin instead
- User wants to play a single specific file — just send them the file directly
- User wants to edit or convert audio files — suggest ffmpeg or an audio tool
- User wants podcast/speech playback with chapters or seeking — radio is linear, no seeking

## Tips

- Default music directory is `{workspace}/music/` — remind users to put audio files there first
- Supports MP3, OGG, WAV, FLAC, AAC, M4A — scans subdirectories recursively
- Shuffle is on by default — turn it off with `shuffle=False` for sequential playback
- The stream URL works in VLC, browsers, mpv, and most media players: `vlc http://localhost:{port}/stream`
- Use `expose_ngrok=True` to create a public URL (requires ngrok installed)
- Port auto-selects if not specified — read it from the start response
- Use `radio_status` to check what's playing and how many listeners are connected
- Use `radio_skip` if the user wants to skip the current track
- Use `radio_queue` to show upcoming tracks — helpful when the user asks "what's next?"
- Stop the radio with `stop_radio` when the user is done — it frees the port

## Requirements

- Audio files in a directory (no API keys needed)
- Optional: [ngrok](https://ngrok.com/download) for public access

## HTTP endpoints

Once running, the station exposes:

| Endpoint | Returns |
|----------|---------|
| `/stream` | Audio stream (SHOUTcast-compatible) |
| `/status` | JSON: current track, listeners, uptime |
| `/playlist` | JSON: full playlist with position |

## Example prompts

> "Start a radio station from my music folder"

> "Start Prax Radio with shuffle on and expose it via ngrok"

> "What's playing on the radio?"

> "Skip this track"

> "Show me the upcoming playlist"
