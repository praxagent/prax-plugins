# txt2presentation

Converts any text-based content into narrated video presentations.

**Supported inputs:** PDF files, web pages, YouTube videos, audio files (MP3/WAV), plain text, Markdown

**Pipeline:** Source → Text extraction → Beamer LaTeX + speaker notes (LLM) → slide images → TTS audio → video (ffmpeg)

## Tools

| Tool | Description |
|------|-------------|
| `text_to_presentation` | Full pipeline: any source → narrated video (.mp4) |
| `text_to_slides` | Lighter: any source → Beamer slide deck + speaker notes (no video) |

## Input types

| Source | How it's handled |
|--------|-----------------|
| PDF URL | Downloaded, text extracted via pymupdf or pdftotext |
| Web page URL | Fetched, HTML stripped to plain text |
| YouTube URL | Transcript via yt-dlp subtitles, or audio download + Whisper |
| Audio file (MP3, WAV, M4A) | Transcribed via OpenAI Whisper API |
| Text/Markdown file | Read directly |
| Raw text (>200 chars) | Used as-is |

## Requirements

### System dependencies (sandbox)

```bash
# These must be installed in the sandbox container
apt install texlive-latex-base texlive-latex-recommended \
    texlive-fonts-recommended poppler-utils ffmpeg yt-dlp
```

### API keys (in Prax's `.env`)

| Key | Required for |
|-----|-------------|
| `OPENAI_KEY` | LLM (slide generation) + TTS (default) + Whisper (audio transcription) |
| `ELEVENLABS_API_KEY` | TTS (optional, if you prefer ElevenLabs) |

### Optional TTS configuration

```bash
PRESENTATION_TTS_PROVIDER=openai     # "openai" (default) or "elevenlabs"
PRESENTATION_TTS_VOICE=nova          # OpenAI: alloy/echo/fable/onyx/nova/shimmer
```

## Usage

> "Turn this article into a video presentation: https://example.com/article"

> "Make a presentation from this YouTube video: https://youtube.com/watch?v=..."

> "Create slides from paper.pdf in my workspace"

> "Turn this podcast episode into slides: recording.mp3"

## Output files

| File | Description |
|------|-------------|
| `<title>.mp4` | Narrated video presentation |
| `<title>_slides.tex` | Beamer LaTeX source |
| `<title>_slides.pdf` | Compiled slide deck |
| `<title>_notes.md` | Speaker notes (markdown) |
