"""Text → Narrated Video Presentation plugin for Prax.

Converts any text-based content into a narrated Beamer presentation video.

Supported input types:
  - PDF files (URL or workspace file)
  - Web pages / HTML URLs
  - YouTube videos (auto-transcript via yt-dlp)
  - Audio files (MP3, WAV, M4A — transcribed via Whisper)
  - Plain text / Markdown files
  - Raw text passed directly

Pipeline:
  Source → Text extraction
       → Beamer LaTeX + speaker notes (LLM)
       → Slide images (pdflatex + pdftoppm)
       → Audio narration per slide (TTS via capabilities gateway)
       → Slide videos (ffmpeg: image + audio)
       → Final concatenated video (ffmpeg)

System requirements (sandbox): pdflatex, pdftoppm (poppler-utils), ffmpeg

Optional: yt-dlp (for YouTube transcripts)

Configure TTS in your Prax settings:
    presentation_tts_provider=openai   (or "elevenlabs")
    presentation_tts_voice=nova        (or any voice name)

This plugin uses the PluginCapabilities gateway — it never directly
accesses os.environ, prax.settings, or API keys.
"""
from __future__ import annotations

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Convert any text source into a narrated video presentation"

import json
import logging
import os
import re

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Module-level caps reference, set during register().
_caps = None

# Audio file extensions we can transcribe.
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".flac"}


# ======================================================================
# Configuration
# ======================================================================

def _get_tts_config() -> dict:
    """Read TTS configuration via the capabilities gateway."""
    provider = (_caps.get_config("presentation_tts_provider") if _caps else None) or "openai"
    provider = provider.lower()

    if provider == "elevenlabs":
        voice = (_caps.get_config("presentation_tts_voice") if _caps else None) or "Rachel"
    else:
        provider = "openai"
        voice = (_caps.get_config("presentation_tts_voice") if _caps else None) or "nova"

    return {"provider": provider, "voice": voice}


def _check_system_deps(need_ffmpeg: bool = True) -> list[str]:
    """Check which system dependencies are missing (runs in sandbox)."""
    missing = []
    for cmd in ["pdflatex", "pdftoppm"]:
        result = _caps.run_command(["which", cmd], timeout=5)
        if result.returncode != 0:
            missing.append(cmd)
    if need_ffmpeg:
        for cmd in ["ffmpeg", "ffprobe"]:
            result = _caps.run_command(["which", cmd], timeout=5)
            if result.returncode != 0:
                missing.append(cmd)
    return missing


# ======================================================================
# Input detection helpers
# ======================================================================

_YOUTUBE_PATTERNS = re.compile(
    r"(?:youtube\.com/watch|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/)",
    re.IGNORECASE,
)


def _is_youtube_url(source: str) -> bool:
    return bool(_YOUTUBE_PATTERNS.search(source))


def _is_url(source: str) -> bool:
    return source.strip().startswith(("http://", "https://", "ftp://"))


# ======================================================================
# Text extraction: YouTube
# ======================================================================

def _parse_vtt(vtt_text: str) -> str:
    """Strip timestamps and cue markers from a VTT subtitle file."""
    lines = []
    for line in vtt_text.splitlines():
        line = line.strip()
        # Skip WEBVTT header, timestamps, cue IDs, empty lines
        if not line or line.startswith("WEBVTT") or "-->" in line:
            continue
        if re.match(r"^\d+$", line):
            continue
        # Strip inline tags like <c> </c>
        line = re.sub(r"<[^>]+>", "", line)
        if line and line not in lines[-1:]:  # basic dedup
            lines.append(line)
    return " ".join(lines)


def _extract_text_from_youtube(url: str, work_dir: str) -> str:
    """Extract transcript from a YouTube video.

    Strategy:
    1. Try yt-dlp to get auto-generated subtitles (runs in sandbox)
    2. Fall back to downloading audio + Whisper transcription
    """
    # Try yt-dlp subtitles first — fast and free.
    result = _caps.run_command(
        [
            "yt-dlp",
            "--write-auto-sub",
            "--sub-lang", "en",
            "--skip-download",
            "--sub-format", "vtt",
            "-o", os.path.join(work_dir, "yt_sub"),
            url,
        ],
        timeout=60,
    )

    # Look for the downloaded VTT file.
    if result.returncode == 0:
        for fname in os.listdir(work_dir):
            if fname.startswith("yt_sub") and fname.endswith(".vtt"):
                vtt_path = os.path.join(work_dir, fname)
                with open(vtt_path, encoding="utf-8", errors="replace") as f:
                    vtt_text = f.read()
                text = _parse_vtt(vtt_text)
                if len(text.strip()) > 100:
                    logger.info("Extracted YouTube transcript via subtitles (%d chars)", len(text))
                    return text

    # Fallback: download audio and transcribe with Whisper.
    logger.info("No subtitles available, downloading audio for transcription")
    audio_path = os.path.join(work_dir, "yt_audio.mp3")
    dl_result = _caps.run_command(
        [
            "yt-dlp",
            "-x", "--audio-format", "mp3",
            "--audio-quality", "5",
            "-o", audio_path,
            url,
        ],
        timeout=300,
    )
    if dl_result.returncode != 0:
        raise RuntimeError(
            f"Could not download YouTube audio. "
            f"yt-dlp error: {dl_result.stderr[:300]}"
        )

    # Find the actual output file (yt-dlp may add extensions).
    actual_audio = audio_path
    if not os.path.isfile(actual_audio):
        for fname in os.listdir(work_dir):
            if fname.startswith("yt_audio") and os.path.splitext(fname)[1] in _AUDIO_EXTENSIONS:
                actual_audio = os.path.join(work_dir, fname)
                break

    if not os.path.isfile(actual_audio):
        raise RuntimeError("yt-dlp did not produce an audio file.")

    return _caps.transcribe_audio(actual_audio)


# ======================================================================
# Text extraction: HTML / web page
# ======================================================================

def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities to get plain text."""
    import html as html_module
    # Remove script and style blocks.
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace block elements with newlines.
    text = re.sub(r"<(br|p|div|h[1-6]|li|tr|blockquote)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags.
    text = re.sub(r"<[^>]+>", "", text)
    # Decode entities.
    text = html_module.unescape(text)
    # Collapse whitespace.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_text_from_url(url: str, work_dir: str) -> str:
    """Fetch a URL and extract text.

    For PDFs, downloads and extracts text.
    For everything else, strips HTML.
    """
    resp = _caps.http_get(url, timeout=60, allow_redirects=True)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").lower()

    # PDF response — save and extract.
    if "pdf" in content_type or url.rstrip("/").lower().endswith(".pdf"):
        pdf_path = os.path.join(work_dir, "input.pdf")
        with open(pdf_path, "wb") as f:
            f.write(resp.content)
        if resp.content[:5].startswith(b"%PDF"):
            return _extract_text_from_pdf(pdf_path)
        # Content-type said PDF but body isn't — fall through to text.

    # HTML or other text response.
    text = resp.text
    if "html" in content_type or text.strip().startswith(("<", "<!DOCTYPE")):
        text = _strip_html(text)

    if len(text.strip()) < 50:
        raise ValueError(
            f"URL returned very little extractable text ({len(text.strip())} chars). "
            f"Content-Type: {content_type}"
        )
    return text


# ======================================================================
# Text extraction: PDF
# ======================================================================

def _extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from a local PDF file as markdown."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text = "\n\n".join(page.get_text() for page in doc)
        doc.close()
        return text
    except ImportError:
        pass

    result = _caps.run_command(
        ["pdftotext", "-layout", pdf_path, "-"],
        timeout=60,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout

    raise RuntimeError(
        "Could not extract text from PDF. Install pymupdf or "
        "poppler-utils (pdftotext)."
    )


def _validate_pdf(path: str, source_url: str = "") -> None:
    """Check that a file is actually a PDF by reading its magic bytes."""
    try:
        with open(path, "rb") as f:
            header = f.read(16)
    except OSError:
        return
    if not header.startswith(b"%PDF"):
        if header.startswith((b"<!DOCTYPE", b"<html", b"<HTML", b"<head", b"<HEAD")):
            hint = "The URL returned an HTML page, not a PDF."
        elif header.startswith((b"{", b"[")):
            hint = "The URL returned JSON, not a PDF."
        else:
            hint = f"File does not start with %PDF header (got: {header[:20]!r})."
        url_note = f" URL: {source_url}" if source_url else ""
        raise ValueError(
            f"Not a valid PDF file. {hint}{url_note} "
            f"If this is a web page, use fetch_url_content to extract its text first."
        )


def _download_pdf(url: str, dest_dir: str) -> str:
    """Download a PDF from a URL. Returns the local file path."""
    dest = os.path.join(dest_dir, "input.pdf")
    resp = _caps.http_get(url, timeout=60, allow_redirects=True)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").lower()
    if "html" in content_type:
        raise ValueError(
            f"URL returned HTML, not a PDF (Content-Type: {content_type}). "
            f"This is fine — the plugin will extract text from the HTML instead."
        )
    with open(dest, "wb") as f:
        f.write(resp.content)
    _validate_pdf(dest, url)
    return dest


# ======================================================================
# Text extraction: Audio files
# ======================================================================

def _extract_text_from_audio(audio_path: str) -> str:
    """Transcribe an audio file using the capabilities gateway."""
    return _caps.transcribe_audio(audio_path)


# ======================================================================
# Unified source resolver
# ======================================================================

def _resolve_source(source: str, work_dir: str) -> str:
    """Resolve any source input to extracted text.

    Detects the input type and dispatches to the appropriate extractor.

    Args:
        source: URL, filename, file path, or raw text.
        work_dir: Temporary working directory for downloads.

    Returns:
        Extracted text content.
    """
    source = source.strip()

    # YouTube URL
    if _is_youtube_url(source):
        logger.info("Detected YouTube URL: %s", source[:80])
        return _extract_text_from_youtube(source, work_dir)

    # General URL
    if _is_url(source):
        logger.info("Detected URL: %s", source[:80])
        return _extract_text_from_url(source, work_dir)

    # Workspace file or local file
    file_path = _resolve_file(source)
    if file_path:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            logger.info("Extracting text from PDF: %s", file_path)
            return _extract_text_from_pdf(file_path)
        if ext in _AUDIO_EXTENSIONS:
            logger.info("Transcribing audio file: %s", file_path)
            return _extract_text_from_audio(file_path)
        # Text-based files (txt, md, html, tex, etc.)
        logger.info("Reading text file: %s", file_path)
        with open(file_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if ext in (".html", ".htm"):
            text = _strip_html(text)
        return text

    # Raw text — if long enough to be real content
    if len(source) > 200:
        logger.info("Treating source as raw text (%d chars)", len(source))
        return source

    raise ValueError(
        f"Could not resolve source: '{source[:100]}'. "
        f"Pass a URL (web page, YouTube, PDF), a workspace filename, "
        f"or paste the text content directly."
    )


def _resolve_file(source: str) -> str | None:
    """Try to resolve a source string to a local file path."""
    # Try workspace file via capabilities gateway.
    if _caps and _caps.get_user_id():
        try:
            candidate = _caps.workspace_path(source)
            if os.path.isfile(candidate):
                return candidate
        except Exception:
            pass

    # Try as absolute/relative path.
    if os.path.isfile(source):
        return source

    return None


# ======================================================================
# LLM: generate Beamer LaTeX + speaker notes
# ======================================================================

_BEAMER_PROMPT = """\
Convert the following document into a Beamer LaTeX presentation with speaker notes.

Style: {style}
{topic_line}

Requirements:
- Create 8-15 slides covering the document's key points
- Use \\documentclass{{beamer}} with the Madrid theme
- Include: title slide, content slides, summary/conclusion
- The LaTeX MUST compile cleanly with pdflatex (no special packages beyond beamer)
- Keep slide text concise — bullet points, not paragraphs

For EACH slide, write a natural speaker script (2-4 sentences). The scripts should
sound like a real person presenting — conversational, varied pacing, with transitions
like "Now let's look at...", "What's really interesting here is...", "To wrap up...".
Do NOT make them sound robotic or like they're reading bullet points aloud.

Return ONLY a JSON object (no markdown fences, no extra text) in this exact format:

{{"title": "Presentation Title", "author": "Based on source document", "latex": "<full beamer .tex source>", "slides": [{{"title": "Slide Title", "notes": "Speaker script for this slide..."}}, ...]}}

Important:
- The "slides" array MUST have one entry per \\begin{{frame}} in the LaTeX
- Escape backslashes in the JSON string (use \\\\ for LaTeX commands)
- Do NOT use \\note{{}} in the LaTeX — speaker notes go in the JSON only

Document text (first 12000 chars):
{text}
"""


def _generate_beamer_and_notes(text: str, topic: str, style: str) -> dict:
    """Call the LLM to produce Beamer LaTeX + per-slide speaker notes."""
    llm = _caps.build_llm()

    topic_line = f"Topic/title: {topic}" if topic else ""
    prompt = _BEAMER_PROMPT.format(
        style=style, topic_line=topic_line, text=text[:12000],
    )

    response = llm.invoke(prompt)
    content = response.content.strip()

    # Strip markdown code fences if present.
    m = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    if m:
        content = m.group(1).strip()

    # Some LLMs wrap in extra text — try to find the JSON object.
    start = content.find("{")
    end = content.rfind("}") + 1
    if start >= 0 and end > start:
        content = content[start:end]

    data = json.loads(content)

    if "latex" not in data or "slides" not in data:
        raise ValueError("LLM response missing 'latex' or 'slides' keys")

    return data


# ======================================================================
# LaTeX compilation (runs in sandbox)
# ======================================================================

def _compile_latex(latex_source: str, work_dir: str) -> str:
    """Compile Beamer LaTeX to PDF. Returns path to the output PDF."""
    tex_path = os.path.join(work_dir, "presentation.tex")
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(latex_source)

    for pass_num in range(2):
        result = _caps.run_command(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-output-directory", work_dir,
                tex_path,
            ],
            timeout=60,
        )
        if result.returncode != 0 and pass_num == 1:
            logger.warning("pdflatex stderr: %s", result.stderr[:500])

    pdf_path = os.path.join(work_dir, "presentation.pdf")
    if not os.path.exists(pdf_path):
        raise RuntimeError(
            f"LaTeX compilation failed.\n"
            f"Log tail:\n{result.stdout[-1000:]}"
        )
    return pdf_path


# ======================================================================
# Slide image extraction (runs in sandbox)
# ======================================================================

def _extract_slide_images(pdf_path: str, work_dir: str) -> list[str]:
    """Convert each page of the PDF to a PNG image."""
    prefix = os.path.join(work_dir, "slide")
    result = _caps.run_command(
        ["pdftoppm", "-png", "-r", "300", pdf_path, prefix],
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdftoppm failed: {result.stderr[:300]}")

    images = sorted(
        f for f in os.listdir(work_dir)
        if f.startswith("slide-") and f.endswith(".png")
    )
    return [os.path.join(work_dir, f) for f in images]


# ======================================================================
# TTS — text to speech via capabilities gateway
# ======================================================================

def _generate_audio(text: str, output_path: str) -> None:
    """Generate TTS audio using the capabilities gateway."""
    config = _get_tts_config()
    _caps.tts_synthesize(
        text=text,
        output_path=output_path,
        voice=config["voice"],
        provider=config["provider"],
    )


# ======================================================================
# Video assembly with ffmpeg (runs in sandbox)
# ======================================================================

def _create_slide_video(
    image_path: str, audio_path: str, output_path: str,
) -> None:
    """Create a video segment: still slide image + audio narration."""
    result = _caps.run_command(
        [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-i", audio_path,
            "-c:v", "libx264", "-tune", "stillimage",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-af", "apad=pad_dur=1",
            "-shortest",
            "-fflags", "+shortest",
            "-max_interleave_delta", "100M",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                   "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white",
            output_path,
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg slide video failed: {result.stderr[:300]}")


def _concatenate_videos(video_paths: list[str], output_path: str) -> None:
    """Concatenate slide videos into one final presentation video."""
    concat_file = output_path + ".concat.txt"
    with open(concat_file, "w") as f:
        for vp in video_paths:
            f.write(f"file '{vp}'\n")

    result = _caps.run_command(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            output_path,
        ],
        timeout=300,
    )
    _caps.run_command(["rm", "-f", concat_file], timeout=5)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr[:300]}")


# ======================================================================
# Tools
# ======================================================================

@tool
def text_to_presentation(
    source: str,
    topic: str = "",
    style: str = "academic",
) -> str:
    """Convert any text source into a narrated video presentation.

    Accepts: URLs (web pages, YouTube videos, PDFs), workspace filenames
    (PDF, text, audio), or raw text pasted directly.

    The source text is used to generate Beamer slides with an LLM,
    narrate each slide with TTS, and assemble a video.

    The resulting video, slide PDF, and LaTeX source are saved to your
    workspace. Use workspace_send_file to deliver the video.

    Requires (in sandbox): pdflatex, pdftoppm, ffmpeg, and a TTS API key.

    Args:
        source: Any of: URL (web page, YouTube, PDF), workspace filename,
                file path, or raw text content.
        topic: Optional title/topic override for the presentation.
        style: Presentation style — "academic", "business", or "casual".
    """
    missing = _check_system_deps(need_ffmpeg=True)
    if missing:
        return (
            f"Missing system dependencies: {', '.join(missing)}.\n"
            f"These should be installed in the sandbox container.\n"
            f"  apt install texlive-latex-base poppler-utils ffmpeg"
        )

    work_dir = _caps.shared_tempdir(prefix="prax_pres_")
    try:
        return _run_pipeline(source, topic, style, work_dir)
    except Exception as e:
        logger.exception("text_to_presentation failed")
        return f"Error: {e}"


@tool
def text_to_slides(
    source: str,
    topic: str = "",
    style: str = "academic",
) -> str:
    """Convert any text source into Beamer LaTeX slides (no video, no TTS).

    A lighter version of text_to_presentation that only generates the slide
    deck. Useful when you don't have ffmpeg or just want the LaTeX/PDF.

    Accepts the same source types as text_to_presentation.

    Requires (in sandbox): pdflatex, pdftoppm.

    Args:
        source: Any of: URL (web page, YouTube, PDF), workspace filename,
                file path, or raw text content.
        topic: Optional title/topic override.
        style: Presentation style — "academic", "business", or "casual".
    """
    missing = _check_system_deps(need_ffmpeg=False)
    if missing:
        return (
            f"Missing system dependencies: {', '.join(missing)}.\n"
            f"These should be installed in the sandbox container.\n"
            f"  apt install texlive-latex-base poppler-utils"
        )

    work_dir = _caps.shared_tempdir(prefix="prax_slides_")
    try:
        return _run_slides_only(source, topic, style, work_dir)
    except Exception as e:
        logger.exception("text_to_slides failed")
        return f"Error: {e}"


# ======================================================================
# Pipeline implementation
# ======================================================================

def _save_to_workspace(src_path: str, filename: str) -> str | None:
    """Save a file to the workspace via the capabilities gateway."""
    if not _caps or not _caps.get_user_id():
        return None
    try:
        with open(src_path, "rb") as f:
            content = f.read()
        return _caps.save_file(filename, content)
    except Exception:
        logger.debug("Could not save %s to workspace", filename, exc_info=True)
    return None


def _run_slides_only(
    source: str, topic: str, style: str, work_dir: str,
) -> str:
    """Generate Beamer slides from any text source (no video)."""
    # Step 1: Extract text from the source.
    logger.info("Resolving source and extracting text")
    text = _resolve_source(source, work_dir)
    if len(text.strip()) < 100:
        return (
            "Could not extract enough text from the source (< 100 chars). "
            "Check the URL or file — it may be image-based, empty, or require OCR."
        )

    # Step 2: Generate Beamer + notes.
    logger.info("Generating Beamer presentation via LLM")
    data = _generate_beamer_and_notes(text, topic, style)
    title = data.get("title", "Presentation")

    # Step 3: Compile LaTeX.
    logger.info("Compiling LaTeX (%d slides)", len(data["slides"]))
    slides_pdf = _compile_latex(data["latex"], work_dir)

    # Save artifacts to workspace.
    safe_title = re.sub(r"[^a-zA-Z0-9_-]", "_", title)[:40]
    _save_to_workspace(
        os.path.join(work_dir, "presentation.tex"),
        f"{safe_title}_slides.tex",
    )

    notes_md = f"# Speaker Notes: {title}\n\n"
    for i, slide in enumerate(data["slides"], 1):
        notes_md += f"## Slide {i}: {slide.get('title', 'Untitled')}\n\n"
        notes_md += f"{slide.get('notes', '')}\n\n"
    notes_path = os.path.join(work_dir, "speaker_notes.md")
    with open(notes_path, "w", encoding="utf-8") as f:
        f.write(notes_md)
    _save_to_workspace(notes_path, f"{safe_title}_notes.md")

    _save_to_workspace(slides_pdf, f"{safe_title}_slides.pdf")

    return (
        f"Slides generated: **{title}** ({len(data['slides'])} slides)\n\n"
        f"Saved to workspace:\n"
        f"- `{safe_title}_slides.pdf` — the slide deck\n"
        f"- `{safe_title}_slides.tex` — LaTeX source\n"
        f"- `{safe_title}_notes.md` — speaker notes\n\n"
        f"Use `workspace_send_file('{safe_title}_slides.pdf')` to deliver."
    )


def _run_pipeline(
    source: str, topic: str, style: str, work_dir: str,
) -> str:
    """Full pipeline: source → text → slides → TTS → video."""
    # Step 1: Extract text.
    logger.info("Resolving source and extracting text")
    text = _resolve_source(source, work_dir)
    if len(text.strip()) < 100:
        return (
            "Could not extract enough text from the source (< 100 chars). "
            "Check the URL or file — it may be image-based, empty, or require OCR."
        )

    # Step 2: Generate Beamer + notes.
    logger.info("Generating Beamer presentation via LLM")
    data = _generate_beamer_and_notes(text, topic, style)
    title = data.get("title", "Presentation")
    slides = data["slides"]

    # Step 3: Compile LaTeX.
    logger.info("Compiling LaTeX (%d slides)", len(slides))
    slides_pdf = _compile_latex(data["latex"], work_dir)

    # Step 4: Extract slide images.
    logger.info("Extracting slide images")
    images = _extract_slide_images(slides_pdf, work_dir)

    num_slides = min(len(images), len(slides))
    if len(images) != len(slides):
        logger.warning(
            "Image/notes count mismatch: %d images, %d notes — using first %d",
            len(images), len(slides), num_slides,
        )

    # Step 5: Generate TTS audio for each slide.
    logger.info("Generating TTS audio for %d slides", num_slides)
    audio_dir = os.path.join(work_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    audio_paths = []
    for i in range(num_slides):
        notes = slides[i].get("notes", slides[i].get("title", ""))
        if not notes.strip():
            notes = f"Slide {i + 1}."
        audio_path = os.path.join(audio_dir, f"slide_{i:03d}.mp3")
        _generate_audio(notes, audio_path)
        audio_paths.append(audio_path)
        logger.info("  TTS slide %d/%d done", i + 1, num_slides)

    # Step 6: Create per-slide videos.
    logger.info("Creating slide videos")
    video_dir = os.path.join(work_dir, "videos")
    os.makedirs(video_dir, exist_ok=True)
    slide_videos = []
    for i in range(num_slides):
        video_path = os.path.join(video_dir, f"slide_{i:03d}.mp4")
        _create_slide_video(images[i], audio_paths[i], video_path)
        slide_videos.append(video_path)
        logger.info("  Video slide %d/%d done", i + 1, num_slides)

    # Step 7: Concatenate into final video.
    logger.info("Concatenating final video")
    safe_title = re.sub(r"[^a-zA-Z0-9_-]", "_", title)[:40]
    final_video = os.path.join(work_dir, f"{safe_title}.mp4")
    _concatenate_videos(slide_videos, final_video)

    size_mb = os.path.getsize(final_video) / (1024 * 1024)

    # Save artifacts to workspace.
    _save_to_workspace(
        os.path.join(work_dir, "presentation.tex"),
        f"{safe_title}_slides.tex",
    )

    notes_md = f"# Speaker Notes: {title}\n\n"
    for i, slide in enumerate(slides[:num_slides], 1):
        notes_md += f"## Slide {i}: {slide.get('title', 'Untitled')}\n\n"
        notes_md += f"{slide.get('notes', '')}\n\n"
    notes_path = os.path.join(work_dir, "speaker_notes.md")
    with open(notes_path, "w", encoding="utf-8") as f:
        f.write(notes_md)
    _save_to_workspace(notes_path, f"{safe_title}_notes.md")

    _save_to_workspace(final_video, f"{safe_title}.mp4")

    if size_mb > 7:
        delivery_hint = (
            f"Use `workspace_share_file('active/{safe_title}.mp4')` to get a shareable link "
            f"(video is {size_mb:.1f} MB — too large for direct Discord upload)."
        )
    else:
        delivery_hint = (
            f"Use `workspace_send_file('{safe_title}.mp4')` to upload directly, "
            f"or `workspace_share_file('active/{safe_title}.mp4')` for a shareable link."
        )

    tts_cfg = _get_tts_config()
    return (
        f"Presentation video created: **{title}**\n\n"
        f"- {num_slides} slides, {size_mb:.1f} MB\n"
        f"- TTS: {tts_cfg['provider']} ({tts_cfg['voice']})\n\n"
        f"Saved to workspace:\n"
        f"- `{safe_title}.mp4` — narrated video presentation\n"
        f"- `{safe_title}_slides.tex` — LaTeX source\n"
        f"- `{safe_title}_notes.md` — speaker notes\n\n"
        f"{delivery_hint}"
    )


# ======================================================================
# Plugin registration
# ======================================================================

def register(caps):
    """Return the tools this plugin provides.

    Receives a PluginCapabilities instance for credentialed operations.
    All HTTP calls, LLM access, TTS, transcription, and shell commands
    go through caps — the plugin never touches API keys directly.
    """
    global _caps
    _caps = caps
    return [text_to_presentation, text_to_slides]
