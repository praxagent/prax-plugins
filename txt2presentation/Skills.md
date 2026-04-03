# Text to Presentation Plugin

## When to use

- User wants to turn ANY content into a presentation or video
- User shares a URL (web page, YouTube, PDF, article) and wants slides
- User has an audio file (podcast, recording) they want turned into slides
- User shares plain text or a document and wants a presentation
- User wants speaker notes generated from content
- User wants a quick slide deck without video — use `text_to_slides`

## When NOT to use

- The user wants to create slides from scratch without a source document
- The user just wants a summary, not slides — use a summarization tool instead

## Tips

- `text_to_slides` is much faster than `text_to_presentation` — suggest it when the user doesn't explicitly need video or audio
- The plugin auto-detects the source type: YouTube URLs, web pages, PDFs, audio files, and plain text are all handled automatically
- For YouTube videos, the plugin first tries to get existing subtitles (fast), then falls back to downloading audio and transcribing with Whisper
- The `style` parameter accepts `"academic"`, `"business"`, or `"casual"` — pick based on context
- If the generated video is larger than 7 MB, suggest `workspace_share_file` instead of `workspace_send_file` (Discord has an 8 MB upload limit)
- The LLM generates 8-15 slides by default — the content is trimmed to the first 12,000 characters
- If LaTeX compilation fails, it's usually because the LLM produced invalid LaTeX — retry once before reporting failure

## System requirements

These must be installed in the sandbox container:

| Command | Package | Required for |
|---------|---------|-------------|
| `pdflatex` | texlive-latex-base | Compiling Beamer slides |
| `pdftoppm` | poppler-utils | Converting PDF pages to images |
| `ffmpeg` | ffmpeg | Video assembly (`text_to_presentation` only) |
| `ffprobe` | ffmpeg | Audio duration detection |
| `yt-dlp` | yt-dlp | YouTube transcript extraction (optional) |

## Configuration

TTS settings are read via the capabilities gateway:

| Config key | Values | Default |
|------------|--------|---------|
| `presentation_tts_provider` | `openai`, `elevenlabs` | `openai` |
| `presentation_tts_voice` | Any voice name for the provider | `nova` (OpenAI) |

API keys for TTS, LLM, and transcription are handled by the framework — this plugin never sees them.

## Output files

All outputs are saved to the user's workspace:

| File | Description |
|------|-------------|
| `<title>.mp4` | Narrated video presentation |
| `<title>_slides.pdf` | Compiled Beamer slide deck |
| `<title>_slides.tex` | LaTeX source (editable) |
| `<title>_notes.md` | Speaker notes in markdown |

## Example prompts

> "Turn this article into a video presentation: https://gizmodo.com/some-article"

> "Make slides from this YouTube video: https://youtube.com/watch?v=abc123"

> "Create a presentation from paper.pdf — business style"

> "Turn this podcast into slides: episode.mp3"

> "Generate a casual presentation from this text: [paste text]"
