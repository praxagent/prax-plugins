# prax-plugins

Plugin collection for [Prax](https://github.com/praxagent/prax). Each subfolder is a self-contained plugin with its own `plugin.py`.

## Available plugins

| Plugin | Version | Description |
|--------|---------|-------------|
| [`pdf2presentation`](pdf2presentation/) | 4 | PDF → narrated video presentation (Beamer + TTS + ffmpeg) |
| [`flight_search`](flight_search/) | 1 | Search for the cheapest flights between airports (Amadeus API) |

## Installing plugins

### Import a single plugin

Tell Prax:

> "Import the pdf2presentation plugin: https://github.com/praxagent/prax-plugins"

Or by URL with path:

> "Import this plugin: https://github.com/praxagent/prax-plugins/tree/main/pdf2presentation"

Prax clones the repo as a git submodule and loads only the plugin you specified.

### Import all plugins at once

> "Import all plugins from https://github.com/praxagent/prax-plugins"

Prax clones the repo and loads every plugin subfolder that has a `plugin.py`.

### Manual install

```bash
cd /path/to/prax/workspaces/<your-user-id>/plugins/shared/
git submodule add https://github.com/praxagent/prax-plugins.git prax-plugins
```

## Updating plugins

Once installed, ask Prax to pull the latest version:

> "Prax, please update the prax-plugins plugin"

or more specifically:

> "Update the pdf2presentation plugin"

Prax runs `plugin_import_update("prax-plugins")` under the hood, which:

1. Pulls the latest commit from this repo via `git submodule update --remote --merge`
2. Re-scans the updated code for security warnings
3. If clean, hot-reloads the plugin tools immediately — no restart needed
4. If new security concerns are found, shows them and waits for your confirmation

You can also check the current plugin version at any time:

> "What version of the pdf2presentation plugin am I running?"

Prax will call `plugin_status("prax-plugins")` and show the active version, health status, and failure count.

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

> "Plugin pdf2presentation auto-rolled back after 3 consecutive failures."

You can check health status with `plugin_status` and manually roll back with `plugin_rollback` if needed.

---

## pdf2presentation

PDF → Markdown → Beamer LaTeX + speaker notes (LLM) → slide images → TTS audio → video (ffmpeg)

### Tools

| Tool | Description |
|------|-------------|
| `pdf_to_presentation` | Full pipeline: PDF → narrated video (.mp4) |
| `pdf_to_slides` | Lighter: PDF → Beamer slide deck + speaker notes (no video) |

### Input validation

The plugin validates that the source is actually a PDF before processing:

- **Content-Type check** — HTTP responses with `text/html` or other non-PDF content types are rejected immediately with a clear error message
- **Magic bytes check** — Downloaded files are verified to start with `%PDF`. HTML pages, JSON responses, and other non-PDF content are detected and rejected with guidance (e.g., "use fetch_url_content to extract text first")

This prevents cryptic parser crashes when a URL returns an HTML page instead of a PDF.

### Requirements

**System dependencies:**

```bash
# macOS
brew install basictex poppler ffmpeg

# Ubuntu / Debian
sudo apt install texlive-latex-base texlive-latex-recommended \
    texlive-fonts-recommended poppler-utils ffmpeg

# Arch
sudo pacman -S texlive-basic poppler ffmpeg
```

**API keys** (in Prax's `.env`):

| Key | Required for |
|-----|-------------|
| `OPENAI_KEY` | LLM (slide generation) + TTS (default) |
| `ELEVENLABS_API_KEY` | TTS (if you prefer ElevenLabs) |

**Optional TTS configuration:**

```bash
# TTS provider: "openai" (default) or "elevenlabs"
PRESENTATION_TTS_PROVIDER=openai

# Voice name — depends on provider
# OpenAI voices: alloy, echo, fable, onyx, nova, shimmer
# ElevenLabs: use any voice name from your account
PRESENTATION_TTS_VOICE=nova
```

### Usage

Once installed, just talk to Prax:

> "Turn this paper into a presentation: https://arxiv.org/abs/1706.03762"

> "Make a video presentation from paper.pdf in my workspace"

> "Create slides from this PDF — business style, no video"

### What Prax does

1. **Extracts text** from the PDF (opendataloader-pdf, pymupdf, or pdftotext)
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
PDF file / URL
  │
  ├─ Content-Type + magic bytes validation
  │
  ├─ opendataloader-pdf / pymupdf / pdftotext
  ▼
Markdown text
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

## flight_search

Search for the cheapest flights between airports using the Amadeus Flight Offers Search API (free test tier: 2,000 calls/month).

### Tools

| Tool | Description |
|------|-------------|
| `flight_search` | Search for cheapest flights between two airports (one-way or round-trip) |
| `airport_lookup` | Look up airport IATA codes by city name or partial code |

### Requirements

**API keys** (in Prax's `.env`):

| Key | Required for |
|-----|-------------|
| `AMADEUS_API_KEY` | Flight search API authentication |
| `AMADEUS_API_SECRET` | Flight search API authentication |

Sign up free at https://developers.amadeus.com/

### Usage

Once installed, just talk to Prax:

> "Find the cheapest flights from JFK to Paris on March 15"

> "Round-trip flights LAX to Tokyo, April 1–10, business class"

> "What's the airport code for Munich?"

Prax will use `airport_lookup` automatically when you say a city name instead of an IATA code, then pass the code to `flight_search`. Results are sorted by price (cheapest first) and include airline, times, duration, stops, and cabin class.

---

## Creating your own plugin

### 1. Create a folder with `plugin.py`

Every Prax plugin needs a `plugin.py` with three things:

```python
PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "What this plugin does"

from langchain_core.tools import tool

@tool
def my_tool(arg: str) -> str:
    """Description shown to the LLM agent."""
    return "result"

def register():
    """Return the tools this plugin provides."""
    return [my_tool]
```

### 2. Add it to a plugins repo (or create your own)

You can either contribute to this repo or create a standalone plugin repo. Standalone repos work exactly the same way — just put `plugin.py` at the root.

### 3. Import into Prax

Tell Prax: `"Import this plugin: https://github.com/you/my-plugin"`

### Plugin conventions

- **`PLUGIN_VERSION`** — string, increment when you update
- **`PLUGIN_DESCRIPTION`** — one-line summary for the catalog
- **`register()`** — must return a list of `@tool` decorated functions
- **Deferred imports** — import heavy dependencies inside your tool functions, not at module level
- **Error messages** — return user-friendly strings, don't raise exceptions from tools
- **System deps** — check for them at runtime and return install instructions if missing

### Security restrictions

Prax applies multiple security layers when importing plugins. Your plugin will be **rejected** if it triggers any of these:

| Restriction | Details |
|-------------|---------|
| **No `subprocess`, `os.system`, `os.popen`** | Detected by AST analysis. Use Prax's sandbox tools instead. |
| **No `eval`, `exec`, `compile`, `__import__`** | Dynamic code execution is blocked. |
| **No `os.environ` access** | Plugins cannot read environment variables (API keys, secrets). Use `prax.settings` for configuration. |
| **No raw `socket` usage** | Use `requests` (flagged but shown to user) or Prax's `fetch_url_content` tool. |
| **No built-in tool name collisions** | Your tools cannot share names with Prax's ~100+ built-in tools. Attempting to register `browser_read_page` or `get_current_datetime` will be rejected. |
| **Sandbox test must pass** | Before activation, your plugin is imported in an isolated subprocess with a stripped environment (no API keys) and a 30-second timeout. |

If security warnings are found, Prax shows them to the user and requires explicit confirmation before activating.

**Risk classification:** Plugin tools are automatically classified as MEDIUM risk (external reads, state changes). If your tool performs side-effectful external actions (sending messages, HTTP POST, file deletion), consider using `@risk_tool(risk=RiskLevel.HIGH)` from `prax.agent.action_policy` instead of `@tool` — this adds a user confirmation gate.

### Accessing Prax services

Plugins run inside Prax, so you can import its services:

```python
from prax.services.pdf_service import process_pdf_url   # PDF extraction
from prax.agent.user_context import current_user_id      # Current user
from prax.services.workspace_service import save_file     # Workspace files
from prax.agent.llm_factory import build_llm              # LLM
from prax.settings import settings                        # Settings (NOT os.environ)
```

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
