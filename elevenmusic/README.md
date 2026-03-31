# elevenmusic

Generate songs with the [ElevenLabs Music API](https://elevenlabs.io/docs/api-reference/music/create-music) and save them to your workspace.

## Tools

| Tool | Description |
|------|-------------|
| `generate_song` | Generate a song from a text prompt and save the MP3 to workspace |

## Setup

1. **Sign up** for an ElevenLabs account at https://elevenlabs.io/
2. **Get your API key** from the ElevenLabs dashboard
3. **Add it** to your Prax `.env`:

```bash
ELEVENLABS_API_KEY=your_key_here
```

4. **Import the plugin** and **approve the permission** when prompted:

> "Import the elevenmusic plugin from prax-plugins"

Prax will show the permission request:

> elevenmusic needs access to ELEVENLABS_API_KEY: "Authenticate with the ElevenLabs Music API to generate songs."
> Approve? [yes/no]

## Usage

Once installed:

> "Generate a lo-fi hip hop beat for studying"

> "Make a 60 second jazz instrumental"

> "Create a punk rock song about debugging code at 3am"

> "Generate a 2 minute orchestral piece, instrumental only"

### Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `prompt` | Yes | — | Description of the song (genre, mood, lyrics, instruments, style) |
| `duration_seconds` | No | 30 | Length in seconds (3–600) |
| `instrumental` | No | false | If true, no vocals |

## Permissions

This plugin declares `PLUGIN_PERMISSIONS` to request access to `ELEVENLABS_API_KEY`. The key is accessed through the capabilities gateway's `get_approved_secret()` method — the plugin never reads environment variables directly.

- **BUILTIN/WORKSPACE** plugins: auto-approved
- **IMPORTED** plugins: requires explicit user approval

## Requirements

- ElevenLabs API key with music generation access
- Python `requests` library (included with Prax)
