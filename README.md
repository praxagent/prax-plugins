# prax-plugins

Plugin collection for [Prax](https://github.com/praxagent/prax). Each subfolder is a self-contained plugin with its own `plugin.py`.

## Available plugins

| Plugin | Version | Description |
|--------|---------|-------------|
| [`txt2presentation`](txt2presentation/) | 1 | Any text source → narrated video presentation (Beamer + TTS + ffmpeg) |
| [`elevenmusic`](elevenmusic/) | 1 | Generate songs with ElevenLabs Music API |
| [`radio`](radio/) | 1 | Stream audio files as an internet radio station |

## Installing plugins

### Import a single plugin

Tell Prax:

> "Import the txt2presentation plugin: https://github.com/praxagent/prax-plugins"

Or by URL with path:

> "Import this plugin: https://github.com/praxagent/prax-plugins/tree/main/txt2presentation"

Prax clones the repo as a git submodule and loads only the plugin you specified.

### Import all plugins at once

> "Import all plugins from https://github.com/praxagent/prax-plugins"

Prax clones the repo and loads every plugin subfolder that has a `plugin.py`.

### Manual install

```bash
cd /path/to/prax/workspaces/<your-user-id>/plugins/shared/
git submodule add https://github.com/praxagent/prax-plugins.git prax-plugins
```

## Versioning plugins

Every plugin declares its version as a string constant at the top of `plugin.py`:

```python
PLUGIN_VERSION = "2"
```

Prax reads this constant (via regex, without importing) to track which version is active, display it in the UI and catalog, and decide when to back up the previous version for rollback.

### When to bump the version

Bump `PLUGIN_VERSION` in every commit that changes user-facing behavior:

| Change | Action |
|--------|--------|
| New tool added or removed | Bump version |
| Tool arguments or return format changed | Bump version |
| Bug fix that changes output | Bump version |
| Internal refactor, no behavior change | Optional — bump if you want it visible |
| Docs-only change (Skills.md, comments) | Don't bump |

The version is a free-form string. Use whatever scheme you prefer — `"2"`, `"2.1"`, `"2024.03"`. Prax compares versions as opaque strings (old != new = version changed), it does not interpret semver.

### What Prax does when the version changes

When Prax loads a plugin whose `PLUGIN_VERSION` differs from what the registry recorded:

1. **Backs up** the previous `plugin.py` as `plugin.py.prev`
2. **Records** the new version in `registry.json` (`active_version`), saving the old one as `previous_version`
3. **Resets** the failure counter to 0
4. **Regenerates** the plugin catalog
5. **Rebuilds** the agent tool graph so the LLM sees the updated tools immediately

### Rollback

If a plugin tool fails 3 times consecutively, Prax automatically rolls back:

1. Restores `plugin.py.prev` over the current `plugin.py`
2. Swaps `active_version` and `previous_version` in the registry
3. Sets status to `rolled_back`
4. Reloads the tool graph

You can also trigger a manual rollback:

> "Roll back the txt2presentation plugin"

## Updating plugins

Once installed, ask Prax to pull the latest version:

> "Prax, please update the prax-plugins plugin"

or more specifically:

> "Update the txt2presentation plugin"

Or use the Plugins panel in the TeamWork settings UI — click the refresh icon on any plugin.

### What happens during an update

Prax runs `plugin_import_update("prax-plugins")` under the hood, which:

1. **Pulls** the latest commit via `git submodule update --remote --merge`
2. **Compares** the old and new git commit hashes — if identical, returns `"up_to_date"` and stops
3. **Re-scans** the updated code for security warnings (AST + regex)
4. **If clean** — hot-reloads the plugin tools immediately, no restart needed. The new `PLUGIN_VERSION` (if changed) is picked up automatically and recorded in the registry
5. **If warnings are found** — the plugin stays **deactivated** until you explicitly acknowledge the warnings (via chat or the UI)
6. **Commits** the submodule pointer update to the workspace git repo

### Checking status

> "What version of the txt2presentation plugin am I running?"

Prax calls `plugin_status("prax-plugins")` and shows the active version, previous version, health status, and consecutive failure count.

### Checking for updates manually

If you prefer manual control:

```bash
cd /path/to/prax/workspaces/<your-user-id>/plugins/shared/prax-plugins/
git pull origin main
```

Then tell Prax to reload:

> "Reload plugins"

## How it works

When you import a plugin repo, Prax:

1. **Clones** the repo as a git submodule into your workspace at `plugins/shared/<repo-name>/`
2. **Scans** all Python files for security risks using both AST analysis and regex pattern matching (subprocess, eval, os.environ, socket, etc.)
3. **If warnings are found** — Prax shows them to you, emits a `plugin_security_warn` audit event, and waits for explicit confirmation before activating
4. **If clean** — the plugin tools are loaded immediately
5. **Tags** the plugin with trust tier `imported` and emits a `plugin_import` audit event

All plugin lifecycle events (import, activate, block, rollback, remove, security warnings) are recorded in the workspace trace log and searchable via `search_trace`.

### Trust tiers

Prax tags every plugin with a trust tier based on its origin:

| Tier | Meaning |
|------|---------|
| `builtin` | Ships with Prax |
| `workspace` | User-created in their workspace |
| `imported` | Cloned from an external repo (like this one) |

Imported plugins default to the least-trusted tier. Trust tiers are visible in `plugin_list` and `plugin_status`.

When you import a specific subfolder from a multi-plugin repo, Prax writes a filter file (`.reponame_plugin_filter`) next to the submodule so only that subfolder's `plugin.py` is activated. The filter lives outside the submodule to avoid modifying its git working tree.

### Plugin failure tracking

Prax monitors every plugin tool invocation. If a tool fails 3 times consecutively, the plugin is automatically rolled back to its previous version. You'll see a message like:

> "Plugin txt2presentation auto-rolled back after 3 consecutive failures."

You can check health status with `plugin_status` and manually roll back with `plugin_rollback` if needed.

---

## txt2presentation

Any text source → Beamer LaTeX + speaker notes (LLM) → slide images → TTS audio → video (ffmpeg)

Accepts: PDF files, web pages, YouTube videos, audio files (MP3/WAV), plain text, Markdown.

### Tools

| Tool | Description |
|------|-------------|
| `text_to_presentation` | Full pipeline: any source → narrated video (.mp4) |
| `text_to_slides` | Lighter: any source → Beamer slide deck + speaker notes (no video) |

### Input types

| Source | How it's handled |
|--------|-----------------|
| PDF URL or file | Downloaded, text extracted via pymupdf or pdftotext |
| Web page URL | Fetched, HTML stripped to plain text |
| YouTube URL | Transcript via yt-dlp subtitles, or audio download + Whisper |
| Audio file (MP3, WAV, M4A) | Transcribed via OpenAI Whisper API |
| Text/Markdown file | Read directly |
| Raw text (>200 chars) | Used as-is |

### Requirements

**System dependencies (sandbox container):**

```bash
apt install texlive-latex-base texlive-latex-recommended \
    texlive-fonts-recommended poppler-utils ffmpeg yt-dlp
```

**API keys** — handled by the framework via the capabilities gateway.
The plugin never sees raw API keys. Ensure `OPENAI_KEY` (or `ELEVENLABS_API_KEY`)
is set in Prax's `.env`.

**Optional TTS configuration** (in Prax settings):

| Config key | Values | Default |
|------------|--------|---------|
| `presentation_tts_provider` | `openai`, `elevenlabs` | `openai` |
| `presentation_tts_voice` | Any voice name for the provider | `nova` (OpenAI), `Rachel` (ElevenLabs) |

### Usage

> "Turn this article into a presentation: https://example.com/article"

> "Make a video from this YouTube video: https://youtube.com/watch?v=..."

> "Create slides from paper.pdf — business style, no video"

> "Turn this podcast into slides: episode.mp3"

### What Prax does

1. **Detects the source type** and extracts text (PDF, HTML, YouTube transcript, audio transcription, or plain text)
2. **Generates Beamer LaTeX slides** via your configured LLM, with natural speaker notes
3. **Compiles** the LaTeX to a PDF slide deck
4. **Converts** each slide to an image (pdftoppm, 300 DPI)
5. **Narrates** each slide with TTS (OpenAI or ElevenLabs)
6. **Assembles** each slide image + audio into a video segment (ffmpeg)
7. **Concatenates** all segments into a final .mp4
8. **Saves** the video, LaTeX source, and speaker notes to your workspace

### Output files

| File | Description |
|------|-------------|
| `<title>.mp4` | Narrated video presentation |
| `<title>_slides.tex` | Beamer LaTeX source |
| `<title>_slides.pdf` | Compiled slide deck |
| `<title>_notes.md` | Speaker notes (markdown) |

### Architecture

```
Any source (URL, file, text)
  │
  ├─ YouTube URL  → yt-dlp subtitles / Whisper
  ├─ Web page URL → HTTP fetch + HTML strip
  ├─ PDF URL/file → pymupdf / pdftotext
  ├─ Audio file   → Whisper transcription
  ├─ Text file    → read directly
  │
  ▼
Extracted text
  │
  ├─ LLM (GPT-4o / Claude / etc.)
  ▼
┌──────────────┐   ┌───────────────────┐
│ Beamer LaTeX │   │ Speaker notes     │
│ (.tex)       │   │ (JSON per slide)  │
└──────┬───────┘   └────────┬──────────┘
       │                    │
  pdflatex                  │
       │                    │
       ▼                    ▼
  Slide PDF          TTS API (OpenAI/EL)
       │                    │
  pdftoppm                  │
       │                    │
       ▼                    ▼
  Slide PNGs          Audio MP3s
       │                    │
       └───────┬────────────┘
               │
           ffmpeg (per slide: image + audio → video)
               │
           ffmpeg (concat all slide videos)
               │
               ▼
        Final .mp4 presentation
```

---

## elevenmusic

Generate songs with the [ElevenLabs Music API](https://elevenlabs.io/docs/api-reference/music/create-music) and save them as MP3 to your workspace.

### Tools

| Tool | Description |
|------|-------------|
| `generate_song` | Generate a song from a text prompt (MP3) |

### Requirements

**API key** (in Prax's `.env`):

```bash
ELEVENLABS_API_KEY=your_key
```

This plugin uses the [plugin permissions](#plugin-permissions) system — it declares `PLUGIN_PERMISSIONS` for `ELEVENLABS_API_KEY` and accesses it via `caps.get_approved_secret()`. IMPORTED plugins require explicit user approval.

### Usage

> "Generate a lo-fi hip hop beat for studying"

> "Make a 2 minute jazz instrumental"

> "Create a punk rock song about debugging at 3am"

Parameters: `prompt` (required), `duration_seconds` (3–600, default 30), `instrumental` (default false).

---

## radio

Stream a directory of audio files as an internet radio station. All listeners hear the same broadcast in real time.

### Tools

| Tool | Description |
|------|-------------|
| `start_radio` | Start streaming from a directory of audio files |
| `stop_radio` | Stop the station and disconnect listeners |
| `radio_status` | Check what's playing, listener count, and URL |
| `radio_skip` | Skip to the next track |
| `radio_queue` | Show upcoming tracks |

### Requirements

No API keys — just audio files in a directory. Supports MP3, OGG, WAV, FLAC, AAC, M4A.

Optional: install [ngrok](https://ngrok.com/download) for public access (`expose_ngrok=True`).

### Usage

> "Start a radio station from my music folder"

> "Start Prax Radio with shuffle on and expose it via ngrok"

> "What's playing on the radio?"

> "Skip this track"

Listeners connect with any media player: `vlc http://localhost:PORT/stream`

### HTTP endpoints

| Endpoint | Returns |
|----------|---------|
| `/stream` | Audio stream (SHOUTcast-compatible) |
| `/status` | JSON: current track, listeners, uptime |
| `/playlist` | JSON: full playlist with position |

---

## Creating your own plugin

### 1. Create a folder with `plugin.py`

Every Prax plugin needs a `plugin.py` with a `register(caps)` function that
receives a `PluginCapabilities` instance:

```python
PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "What this plugin does"

from langchain_core.tools import tool

_caps = None

@tool
def my_tool(arg: str) -> str:
    """Description shown to the LLM agent."""
    # Use caps for all credentialed operations:
    # _caps.build_llm()              — get an LLM (plugin never sees API key)
    # _caps.http_get(url)            — audited HTTP request
    # _caps.run_command([...])       — run a shell command (sandbox)
    # _caps.save_file(name, b)       — save to workspace
    # _caps.get_config(key)          — read non-secret config
    # _caps.tts_synthesize(...)      — text-to-speech
    # _caps.transcribe_audio(path)   — speech-to-text (Whisper)
    # _caps.shared_tempdir()         — create a temp directory
    return "result"

def register(caps):
    """Return the tools this plugin provides.

    Receives a PluginCapabilities instance for credentialed operations.
    """
    global _caps
    _caps = caps
    return [my_tool]
```

### 2. Create `permissions.md` (required for IMPORTED plugins)

Every plugin must have a `permissions.md` declaring exactly what it can do. **This file is authoritative** — the framework enforces it as the ceiling of the plugin's capabilities. The plugin cannot do anything beyond what's declared here.

```markdown
# Permissions

## capabilities
- llm
- http
- commands

## secrets
- MY_API_KEY: Why the plugin needs this key

## allowed_commands
- ffmpeg
- which
```

**Sections:**

| Section | Purpose |
|---------|---------|
| `## capabilities` | Which gateway methods the plugin may call: `llm`, `http`, `commands`, `tts`, `transcription`, `filesystem` |
| `## secrets` | Environment variable names the plugin needs, with a reason |
| `## allowed_commands` | Exact command names (argv[0]) the plugin may run. If present, anything not listed is blocked |

**Why this matters:**
- Reviewers can audit a plugin by reading one file — no need to trace Python code
- Bad actors can't sneak in new permissions — any change to `permissions.md` is visible in diffs
- IMPORTED plugins without a `permissions.md` get **zero capabilities**

### 3. Add it to a plugins repo (or create your own)

You can either contribute to this repo or create a standalone plugin repo. Standalone repos work exactly the same way — just put `plugin.py` and `permissions.md` at the root.

### 4. Import into Prax

Tell Prax: `"Import this plugin: https://github.com/you/my-plugin"`

### Plugin conventions

- **`permissions.md`** — required for IMPORTED plugins, declares capabilities, secrets, and allowed commands
- **`PLUGIN_VERSION`** — string, bump on every user-facing change (see [Versioning plugins](#versioning-plugins))
- **`PLUGIN_DESCRIPTION`** — one-line summary for the catalog
- **`register(caps)`** — receives a `PluginCapabilities` instance, returns a list of `@tool` decorated functions
- **Use `caps.*` methods** — never import `os.environ`, `prax.settings`, or API keys directly
- **Deferred imports** — import heavy dependencies inside your tool functions, not at module level
- **Error messages** — return user-friendly strings, don't raise exceptions from tools
- **System deps** — check for them at runtime via `caps.run_command(["which", ...])` and return install instructions if missing

### Capabilities gateway

The `PluginCapabilities` object (`caps`) is the official SDK for plugins to access Prax services. Plugins never touch API keys, environment variables, or settings directly — the gateway handles credentials internally.

| Method | Description |
|--------|-------------|
| `caps.build_llm(tier="medium")` | Get a LangChain LLM — plugin never sees API key |
| `caps.http_get(url, **kw)` | Audited, rate-limited HTTP GET |
| `caps.http_post(url, **kw)` | Audited, rate-limited HTTP POST |
| `caps.run_command(cmd, timeout=30)` | Run a shell command (audited, time-limited) |
| `caps.save_file(filename, content)` | Save bytes to the plugin's workspace directory |
| `caps.read_file(filename)` | Read a text file from the plugin's workspace directory |
| `caps.workspace_path(*parts)` | Get an absolute path within the plugin's scoped directory |
| `caps.get_config(key)` | Read a non-secret setting (blocks keys matching `key`, `secret`, `token`, `password`, `credential`) |
| `caps.get_approved_secret(env_key)` | Read a pre-approved secret by env var name (see [Plugin permissions](#plugin-permissions)) |
| `caps.tts_synthesize(text, path, voice, provider)` | Text-to-speech — framework injects API key |
| `caps.transcribe_audio(audio_path)` | Audio transcription via OpenAI Whisper — framework injects API key |
| `caps.shared_tempdir(prefix)` | Create a temporary directory |
| `caps.get_user_id()` | Get the current user's ID |

**Plugin-owned credentials (legacy):** If your plugin needs its own API credentials and you want to use `get_config()`, use config key names that don't match the secret patterns. For example, use `myservice_id` / `myservice_auth` instead of `myservice_api_key` / `myservice_api_secret`.

**Plugin permissions (recommended):** For secrets that match the blocked patterns (e.g., `ELEVENLABS_API_KEY`), declare them in `PLUGIN_PERMISSIONS` and access them via `caps.get_approved_secret()`. See [Plugin permissions](#plugin-permissions) below.

### Plugin permissions

Plugins can declare that they need access to specific secrets (API keys, tokens, etc.) by setting a `PLUGIN_PERMISSIONS` constant:

```python
PLUGIN_PERMISSIONS = [
    {
        "key": "ELEVENLABS_API_KEY",
        "reason": "Authenticate with the ElevenLabs API to generate music.",
    },
]
```

At load time, Prax reads the declaration and records it in the plugin registry. Access is gated by trust tier:

| Tier | Behavior |
|------|----------|
| `builtin` | Always allowed — no approval needed |
| `workspace` | Auto-approved at load time |
| `imported` | Requires explicit user approval before the secret is accessible |

To read an approved secret at runtime:

```python
api_key = caps.get_approved_secret("ELEVENLABS_API_KEY")
```

The secret value is read from `prax.settings` using the Pydantic field alias mapping (e.g., `ELEVENLABS_API_KEY` → `settings.elevenlabs_api_key`). The raw value is never stored in the registry — only the approval flag is persisted.

Unapproved access raises `PermissionError` with a message telling the user to approve it in plugin settings.

### Security restrictions

Prax applies multiple security layers when importing plugins. Your plugin will be **rejected** if it triggers any of these:

| Restriction | Details |
|-------------|---------|
| **No `subprocess`, `os.system`, `os.popen`** | Detected by AST analysis. Use `caps.run_command()` instead. |
| **No `eval`, `exec`, `compile`, `__import__`** | Dynamic code execution is blocked. |
| **No `os.environ` access** | Plugins cannot read environment variables. Use `caps.get_config()`. |
| **No raw `socket` usage** | Use `caps.http_get()` / `caps.http_post()`. |
| **No direct `prax.settings` import** | Use `caps.get_config()` for non-secret values. |
| **No built-in tool name collisions** | Your tools cannot share names with Prax's ~100+ built-in tools. |
| **Sandbox test must pass** | Before activation, your plugin is imported in an isolated subprocess with a stripped environment (no API keys) and a 30-second timeout. |

If security warnings are found, Prax shows them to the user and requires explicit confirmation before activating.

**Filesystem scoping:** IMPORTED plugins are confined to `plugin_data/{plugin_name}/` within the user's workspace. `caps.save_file()`, `caps.read_file()`, and `caps.workspace_path()` are automatically scoped. Path traversal attempts (e.g., `../`) are blocked.

**Risk classification:** Plugin tools are automatically classified as HIGH risk for IMPORTED plugins (require user confirmation). BUILTIN and WORKSPACE plugin tools default to MEDIUM risk.

---

## Development

### Setup

```bash
uv sync --extra dev
```

### Running tests

```bash
uv run pytest tests/ -x -q
```

### Linting

```bash
uv run ruff check .
```

### CI

Pull requests run lint + tests automatically via GitHub Actions. Merges to `main` trigger [release-please](https://github.com/googleapis/release-please) for automated semantic versioning.

## License

Apache 2.0
