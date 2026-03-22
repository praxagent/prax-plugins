# pdf2presentation

Converts PDF documents into narrated video presentations.

**Pipeline:** PDF → Markdown → Beamer LaTeX + speaker notes (LLM) → slide images → TTS audio → video (ffmpeg)

## Tools

| Tool | Description |
|------|-------------|
| `pdf_to_presentation` | Full pipeline: PDF → narrated video (.mp4) |
| `pdf_to_slides` | Lighter: PDF → Beamer slide deck + speaker notes (no video) |

## Requirements

### System dependencies

```bash
# macOS
brew install basictex poppler ffmpeg

# Ubuntu / Debian
sudo apt install texlive-latex-base texlive-latex-recommended \
    texlive-fonts-recommended poppler-utils ffmpeg
```

### API keys (in Prax's `.env`)

| Key | Required for |
|-----|-------------|
| `OPENAI_KEY` | LLM (slide generation) + TTS (default) |
| `ELEVENLABS_API_KEY` | TTS (optional, if you prefer ElevenLabs) |

### Optional TTS configuration

```bash
PRESENTATION_TTS_PROVIDER=openai     # "openai" (default) or "elevenlabs"
PRESENTATION_TTS_VOICE=nova          # OpenAI: alloy/echo/fable/onyx/nova/shimmer
```

## Usage

> "Turn this paper into a presentation: https://arxiv.org/abs/1706.03762"

> "Make a video presentation from paper.pdf in my workspace"

## Output files

| File | Description |
|------|-------------|
| `<title>.mp4` | Narrated video presentation |
| `<title>_slides.tex` | Beamer LaTeX source |
| `<title>_slides.pdf` | Compiled slide deck |
| `<title>_notes.md` | Speaker notes (markdown) |
