# PDF to Presentation Plugin

## When to use

- User wants to turn a PDF paper, report, or document into a presentation or video
- User shares a PDF URL (arXiv, Google Drive, direct link) and wants slides
- User wants speaker notes generated from a document
- User wants a quick slide deck without video — use `pdf_to_slides`

## When NOT to use

- The source is a web page, not a PDF — use `fetch_url_content` to extract text first
- The user wants to create slides from scratch without a source document
- The PDF is image-based with no extractable text (suggest OCR first)
- The user just wants a summary, not slides — use a summarization tool instead

## Tips

- `pdf_to_slides` is much faster than `pdf_to_presentation` — suggest it when the user doesn't explicitly need video or audio
- For arXiv papers, pass the PDF URL directly (e.g., `https://arxiv.org/pdf/1706.03762`)
- The `style` parameter accepts `"academic"`, `"business"`, or `"casual"` — pick based on context
- If the generated video is larger than 7 MB, suggest `workspace_share_file` instead of `workspace_send_file` (Discord has an 8 MB upload limit)
- The LLM generates 8–15 slides by default — the content is trimmed to the first 12,000 characters of the PDF
- If LaTeX compilation fails, it's usually because the LLM produced invalid LaTeX — retry once before reporting failure

## System requirements

These must be installed on the sandbox or host system:

| Command | Package | Required for |
|---------|---------|-------------|
| `pdflatex` | texlive-latex-base | Compiling Beamer slides |
| `pdftoppm` | poppler-utils | Converting PDF pages to images |
| `ffmpeg` | ffmpeg | Video assembly (`pdf_to_presentation` only) |
| `ffprobe` | ffmpeg | Audio duration detection |

## Configuration

TTS settings are read via the capabilities gateway:

| Config key | Values | Default |
|------------|--------|---------|
| `presentation_tts_provider` | `openai`, `elevenlabs` | `openai` |
| `presentation_tts_voice` | Any voice name for the provider | `nova` (OpenAI) |

API keys for TTS and LLM are handled by the framework — this plugin never sees them.

## Output files

All outputs are saved to the user's workspace:

| File | Description |
|------|-------------|
| `<title>.mp4` | Narrated video presentation |
| `<title>_slides.pdf` | Compiled Beamer slide deck |
| `<title>_slides.tex` | LaTeX source (editable) |
| `<title>_notes.md` | Speaker notes in markdown |

## Example prompts

> "Turn this paper into a presentation: https://arxiv.org/pdf/1706.03762"

> "Make a video presentation from report.pdf in my workspace"

> "Create slides from this PDF — business style, no video"

> "Generate a casual presentation from the PDF I uploaded"
