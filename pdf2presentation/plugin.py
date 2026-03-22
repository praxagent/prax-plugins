"""PDF → Narrated Video Presentation plugin for Prax.

Converts a PDF document into a narrated Beamer presentation video.

Pipeline:
  PDF → Markdown text (opendataloader-pdf)
      → Beamer LaTeX + speaker notes (LLM)
      → Slide images (pdflatex + pdftoppm)
      → Audio narration per slide (OpenAI TTS or ElevenLabs)
      → Slide videos (ffmpeg: image + audio)
      → Final concatenated video (ffmpeg)

System requirements: pdflatex, pdftoppm (poppler-utils), ffmpeg
TTS providers: OpenAI (default) or ElevenLabs — see README for .env config.
"""
from __future__ import annotations

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Convert a PDF into a narrated video presentation"

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


# ======================================================================
# Configuration
# ======================================================================

def _get_tts_config() -> dict:
    """Read TTS configuration from environment variables.

    Env vars:
        PRESENTATION_TTS_PROVIDER  — "openai" (default) or "elevenlabs"
        PRESENTATION_TTS_VOICE     — voice name (defaults: "nova" / "Rachel")
    """
    provider = os.environ.get("PRESENTATION_TTS_PROVIDER", "openai").lower()

    if provider == "elevenlabs":
        voice = os.environ.get("PRESENTATION_TTS_VOICE", "Rachel")
        api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    else:
        provider = "openai"
        voice = os.environ.get("PRESENTATION_TTS_VOICE", "nova")
        api_key = os.environ.get("OPENAI_KEY", "") or os.environ.get("OPENAI_API_KEY", "")

    return {"provider": provider, "voice": voice, "api_key": api_key}


def _check_system_deps(need_ffmpeg: bool = True) -> list[str]:
    """Check which system dependencies are missing."""
    missing = []
    for cmd in ["pdflatex", "pdftoppm"]:
        if not _which(cmd):
            missing.append(cmd)
    if need_ffmpeg:
        for cmd in ["ffmpeg", "ffprobe"]:
            if not _which(cmd):
                missing.append(cmd)
    return missing


def _which(cmd: str) -> bool:
    """Check if a command is available on PATH."""
    try:
        subprocess.run(
            ["which", cmd], capture_output=True, check=True, timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# ======================================================================
# Step 1: PDF text extraction
# ======================================================================

def _extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from a local PDF file as markdown."""
    # Prefer Prax's built-in PDF service (opendataloader-pdf → markdown).
    try:
        from prax.services.pdf_service import extract_markdown
        return extract_markdown(pdf_path)
    except Exception:
        pass

    # Fallback: pymupdf (fitz).
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text = "\n\n".join(page.get_text() for page in doc)
        doc.close()
        return text
    except ImportError:
        pass

    # Fallback: pdftotext (poppler-utils).
    result = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout

    raise RuntimeError(
        "Could not extract text from PDF. Install opendataloader-pdf, "
        "pymupdf, or poppler-utils (pdftotext)."
    )


def _download_pdf(url: str, dest_dir: str) -> str:
    """Download a PDF from a URL. Returns the local file path."""
    # Prefer Prax's PDF service which handles arXiv URLs etc.
    try:
        from prax.services.pdf_service import download_pdf
        return download_pdf(url)
    except Exception:
        pass

    # Fallback: urllib.
    import urllib.request
    dest = os.path.join(dest_dir, "input.pdf")
    urllib.request.urlretrieve(url, dest)
    return dest


# ======================================================================
# Step 2: LLM — generate Beamer LaTeX + speaker notes
# ======================================================================

_BEAMER_PROMPT = """\
Convert the following document into a Beamer LaTeX presentation with speaker notes.

Style: {style}
{topic_line}

Requirements:
- Create 8–15 slides covering the document's key points
- Use \\documentclass{{beamer}} with the Madrid theme
- Include: title slide, content slides, summary/conclusion
- The LaTeX MUST compile cleanly with pdflatex (no special packages beyond beamer)
- Keep slide text concise — bullet points, not paragraphs

For EACH slide, write a natural speaker script (2–4 sentences). The scripts should
sound like a real person presenting — conversational, varied pacing, with transitions
like "Now let's look at…", "What's really interesting here is…", "To wrap up…".
Do NOT make them sound robotic or like they're reading bullet points aloud.

Return ONLY a JSON object (no markdown fences, no extra text) in this exact format:

{{"title": "Presentation Title", "author": "Based on source document", "latex": "<full beamer .tex source>", "slides": [{{"title": "Slide Title", "notes": "Speaker script for this slide…"}}, ...]}}

Important:
- The "slides" array MUST have one entry per \\begin{{frame}} in the LaTeX
- Escape backslashes in the JSON string (use \\\\ for LaTeX commands)
- Do NOT use \\note{{}} in the LaTeX — speaker notes go in the JSON only

Document text (first 12000 chars):
{text}
"""


def _generate_beamer_and_notes(text: str, topic: str, style: str) -> dict:
    """Call the LLM to produce Beamer LaTeX + per-slide speaker notes.

    Returns::

        {"title": str, "latex": str,
         "slides": [{"title": str, "notes": str}, ...]}
    """
    # Build the LLM — prefer Prax's factory, fall back to direct OpenAI.
    try:
        from prax.agent.llm_factory import build_llm
        llm = build_llm()
    except Exception:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=os.environ.get("OPENAI_KEY", ""),
        )

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
# Step 3: LaTeX compilation
# ======================================================================

def _compile_latex(latex_source: str, work_dir: str) -> str:
    """Compile Beamer LaTeX to PDF.  Returns path to the output PDF."""
    tex_path = os.path.join(work_dir, "presentation.tex")
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(latex_source)

    # Run pdflatex twice (for TOC / frame numbers).
    for pass_num in range(2):
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-output-directory", work_dir,
                tex_path,
            ],
            capture_output=True, text=True, timeout=60, cwd=work_dir,
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
# Step 4: Extract slide images
# ======================================================================

def _extract_slide_images(pdf_path: str, work_dir: str) -> list[str]:
    """Convert each page of the PDF to a PNG image.

    Returns a sorted list of image file paths.
    """
    prefix = os.path.join(work_dir, "slide")
    result = subprocess.run(
        ["pdftoppm", "-png", "-r", "300", pdf_path, prefix],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdftoppm failed: {result.stderr[:300]}")

    images = sorted(
        f for f in os.listdir(work_dir)
        if f.startswith("slide-") and f.endswith(".png")
    )
    return [os.path.join(work_dir, f) for f in images]


# ======================================================================
# Step 5: TTS — text to speech
# ======================================================================

def _tts_openai(text: str, output_path: str, voice: str, api_key: str) -> None:
    """Generate speech with OpenAI TTS-1."""
    import openai
    client = openai.OpenAI(api_key=api_key)
    response = client.audio.speech.create(model="tts-1", voice=voice, input=text)
    response.stream_to_file(output_path)


def _tts_elevenlabs(text: str, output_path: str, voice: str, api_key: str) -> None:
    """Generate speech with ElevenLabs."""
    import requests

    headers = {"xi-api-key": api_key}
    # Resolve voice name → voice_id.
    voices_resp = requests.get(
        "https://api.elevenlabs.io/v1/voices", headers=headers, timeout=15,
    )
    voices_resp.raise_for_status()
    voices = voices_resp.json().get("voices", [])
    voice_id = None
    for v in voices:
        if v["name"].lower() == voice.lower():
            voice_id = v["voice_id"]
            break
    if not voice_id:
        if voices:
            voice_id = voices[0]["voice_id"]
            logger.warning("Voice '%s' not found, using '%s'", voice, voices[0]["name"])
        else:
            raise RuntimeError("No ElevenLabs voices available")

    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={**headers, "Content-Type": "application/json"},
        json={"text": text, "model_id": "eleven_monolingual_v1"},
        timeout=60,
    )
    resp.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(resp.content)


def _generate_audio(text: str, output_path: str) -> None:
    """Generate TTS audio using the configured provider."""
    config = _get_tts_config()
    if not config["api_key"]:
        raise RuntimeError(
            f"No API key for TTS provider '{config['provider']}'. "
            f"Set {'ELEVENLABS_API_KEY' if config['provider'] == 'elevenlabs' else 'OPENAI_KEY'} in .env"
        )
    if config["provider"] == "elevenlabs":
        _tts_elevenlabs(text, output_path, config["voice"], config["api_key"])
    else:
        _tts_openai(text, output_path, config["voice"], config["api_key"])


# ======================================================================
# Step 6: Video assembly with ffmpeg
# ======================================================================

def _get_audio_duration(audio_path: str) -> float:
    """Get the duration of an audio file in seconds."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True, text=True, timeout=30,
    )
    return float(result.stdout.strip())


def _create_slide_video(
    image_path: str, audio_path: str, output_path: str
) -> None:
    """Create a video segment: still slide image + audio narration."""
    duration = _get_audio_duration(audio_path) + 1.0  # 1s padding after speech

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-i", audio_path,
            "-c:v", "libx264", "-tune", "stillimage",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-t", str(duration),
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                   "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white",
            output_path,
        ],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg slide video failed: {result.stderr[:300]}")


def _concatenate_videos(video_paths: list[str], output_path: str) -> None:
    """Concatenate slide videos into one final presentation video."""
    concat_file = output_path + ".concat.txt"
    with open(concat_file, "w") as f:
        for vp in video_paths:
            f.write(f"file '{vp}'\n")

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            output_path,
        ],
        capture_output=True, text=True, timeout=300,
    )
    os.unlink(concat_file)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr[:300]}")


# ======================================================================
# Tools
# ======================================================================

@tool
def pdf_to_presentation(
    pdf_source: str,
    topic: str = "",
    style: str = "academic",
) -> str:
    """Convert a PDF document into a narrated video presentation.

    Takes a PDF (URL or workspace filename), generates Beamer slides with
    an LLM, narrates each slide with TTS, and assembles a video.

    The resulting video, slide PDF, and LaTeX source are saved to your
    workspace.  Use workspace_send_file to deliver the video.

    Requires: pdflatex, pdftoppm (poppler-utils), ffmpeg, and a TTS API key.

    Args:
        pdf_source: URL to a PDF, or a filename already in the workspace.
        topic: Optional title/topic override for the presentation.
        style: Presentation style — "academic", "business", or "casual".
    """
    # --- Pre-flight checks ---
    missing = _check_system_deps(need_ffmpeg=True)
    if missing:
        return (
            f"Missing system dependencies: {', '.join(missing)}.\n"
            f"Install them:\n"
            f"  macOS: brew install basictex poppler ffmpeg\n"
            f"  Ubuntu: apt install texlive-latex-base poppler-utils ffmpeg"
        )

    tts_cfg = _get_tts_config()
    if not tts_cfg["api_key"]:
        key_var = "ELEVENLABS_API_KEY" if tts_cfg["provider"] == "elevenlabs" else "OPENAI_KEY"
        return f"No TTS API key. Set {key_var} in .env (using provider: {tts_cfg['provider']})."

    work_dir = tempfile.mkdtemp(prefix="prax_pres_")
    try:
        return _run_pipeline(pdf_source, topic, style, work_dir)
    except Exception as e:
        logger.exception("pdf_to_presentation failed")
        return f"Error: {e}"
    # Note: work_dir intentionally NOT cleaned up so workspace files persist.


@tool
def pdf_to_slides(
    pdf_source: str,
    topic: str = "",
    style: str = "academic",
) -> str:
    """Convert a PDF into Beamer LaTeX slides (no video, no TTS).

    A lighter version of pdf_to_presentation that only generates the slide
    deck.  Useful when you don't have ffmpeg or just want the LaTeX/PDF.

    The slide PDF, LaTeX source, and speaker notes are saved to the workspace.

    Requires: pdflatex, pdftoppm (poppler-utils).

    Args:
        pdf_source: URL to a PDF, or a filename already in the workspace.
        topic: Optional title/topic override.
        style: Presentation style — "academic", "business", or "casual".
    """
    missing = _check_system_deps(need_ffmpeg=False)
    if missing:
        return (
            f"Missing system dependencies: {', '.join(missing)}.\n"
            f"Install them:\n"
            f"  macOS: brew install basictex poppler\n"
            f"  Ubuntu: apt install texlive-latex-base poppler-utils"
        )

    work_dir = tempfile.mkdtemp(prefix="prax_slides_")
    try:
        return _run_slides_only(pdf_source, topic, style, work_dir)
    except Exception as e:
        logger.exception("pdf_to_slides failed")
        return f"Error: {e}"


# ======================================================================
# Pipeline implementation
# ======================================================================

def _resolve_pdf(pdf_source: str, work_dir: str) -> str:
    """Resolve a PDF source to a local file path."""
    if pdf_source.startswith(("http://", "https://", "ftp://")):
        logger.info("Downloading PDF from %s", pdf_source[:80])
        return _download_pdf(pdf_source, work_dir)

    # Try workspace file.
    try:
        from prax.agent.user_context import current_user_id
        uid = current_user_id.get()
        if uid:
            from prax.services.workspace_service import _workspace_root
            root = _workspace_root(uid)
            candidate = os.path.join(root, "active", pdf_source)
            if os.path.isfile(candidate):
                return candidate
    except Exception:
        pass

    # Try as absolute/relative path.
    if os.path.isfile(pdf_source):
        return pdf_source

    raise FileNotFoundError(f"PDF not found: {pdf_source}")


def _save_to_workspace(src_path: str, filename: str) -> str | None:
    """Copy a file into the current user's active workspace. Returns dest or None."""
    try:
        from prax.agent.user_context import current_user_id
        uid = current_user_id.get()
        if uid:
            from prax.services.workspace_service import save_file, save_binary

            if filename.endswith((".tex", ".md", ".txt", ".json")):
                with open(src_path, encoding="utf-8") as f:
                    content = f.read()
                save_file(uid, filename, content)
            else:
                save_binary(uid, filename, src_path)
            return filename
    except Exception:
        logger.debug("Could not save %s to workspace", filename, exc_info=True)
    return None


def _run_slides_only(
    pdf_source: str, topic: str, style: str, work_dir: str,
) -> str:
    """Generate Beamer slides from a PDF (no video)."""
    # Step 1: Get the PDF.
    pdf_path = _resolve_pdf(pdf_source, work_dir)
    logger.info("Extracting text from PDF")

    # Step 2: Extract text.
    text = _extract_text_from_pdf(pdf_path)
    if len(text.strip()) < 100:
        return "Could not extract enough text from the PDF. Is it image-based? Try OCR first."

    # Step 3: Generate Beamer + notes.
    logger.info("Generating Beamer presentation via LLM")
    data = _generate_beamer_and_notes(text, topic, style)
    title = data.get("title", "Presentation")

    # Step 4: Compile LaTeX.
    logger.info("Compiling LaTeX (%d slides)", len(data["slides"]))
    slides_pdf = _compile_latex(data["latex"], work_dir)

    # Save artifacts to workspace.
    safe_title = re.sub(r"[^a-zA-Z0-9_-]", "_", title)[:40]
    _save_to_workspace(
        os.path.join(work_dir, "presentation.tex"),
        f"{safe_title}_slides.tex",
    )

    # Save speaker notes as markdown.
    notes_md = f"# Speaker Notes: {title}\n\n"
    for i, slide in enumerate(data["slides"], 1):
        notes_md += f"## Slide {i}: {slide.get('title', 'Untitled')}\n\n"
        notes_md += f"{slide.get('notes', '')}\n\n"
    notes_path = os.path.join(work_dir, "speaker_notes.md")
    with open(notes_path, "w", encoding="utf-8") as f:
        f.write(notes_md)
    _save_to_workspace(notes_path, f"{safe_title}_notes.md")

    # Copy the PDF to active workspace.
    try:
        from prax.agent.user_context import current_user_id
        uid = current_user_id.get()
        if uid:
            import shutil
            from prax.services.workspace_service import _ensure_workspace, _safe_join, _git_commit
            root = _ensure_workspace(uid)
            dest = _safe_join(root, "active", f"{safe_title}_slides.pdf")
            shutil.copy2(slides_pdf, dest)
            _git_commit(root, f"Generate slides: {title[:30]}")
    except Exception:
        pass

    return (
        f"Slides generated: **{title}** ({len(data['slides'])} slides)\n\n"
        f"Saved to workspace:\n"
        f"- `{safe_title}_slides.pdf` — the slide deck\n"
        f"- `{safe_title}_slides.tex` — LaTeX source\n"
        f"- `{safe_title}_notes.md` — speaker notes\n\n"
        f"Use `workspace_send_file('{safe_title}_slides.pdf')` to deliver."
    )


def _run_pipeline(
    pdf_source: str, topic: str, style: str, work_dir: str,
) -> str:
    """Full pipeline: PDF → slides → TTS → video."""
    # Steps 1–4: same as slides-only.
    pdf_path = _resolve_pdf(pdf_source, work_dir)
    logger.info("Extracting text from PDF")
    text = _extract_text_from_pdf(pdf_path)
    if len(text.strip()) < 100:
        return "Could not extract enough text from the PDF. Is it image-based? Try OCR first."

    logger.info("Generating Beamer presentation via LLM")
    data = _generate_beamer_and_notes(text, topic, style)
    title = data.get("title", "Presentation")
    slides = data["slides"]

    logger.info("Compiling LaTeX (%d slides)", len(slides))
    slides_pdf = _compile_latex(data["latex"], work_dir)

    # Step 5: Extract slide images.
    logger.info("Extracting slide images")
    images = _extract_slide_images(slides_pdf, work_dir)

    # Match images to speaker notes (handle count mismatch gracefully).
    num_slides = min(len(images), len(slides))
    if len(images) != len(slides):
        logger.warning(
            "Image/notes count mismatch: %d images, %d notes — using first %d",
            len(images), len(slides), num_slides,
        )

    # Step 6: Generate TTS audio for each slide.
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

    # Step 7: Create per-slide videos.
    logger.info("Creating slide videos")
    video_dir = os.path.join(work_dir, "videos")
    os.makedirs(video_dir, exist_ok=True)
    slide_videos = []
    for i in range(num_slides):
        video_path = os.path.join(video_dir, f"slide_{i:03d}.mp4")
        _create_slide_video(images[i], audio_paths[i], video_path)
        slide_videos.append(video_path)
        logger.info("  Video slide %d/%d done", i + 1, num_slides)

    # Step 8: Concatenate into final video.
    logger.info("Concatenating final video")
    safe_title = re.sub(r"[^a-zA-Z0-9_-]", "_", title)[:40]
    final_video = os.path.join(work_dir, f"{safe_title}.mp4")
    _concatenate_videos(slide_videos, final_video)

    # Get file size.
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

    # Save video to workspace active/ (won't be git-committed due to .gitignore).
    try:
        from prax.agent.user_context import current_user_id
        uid = current_user_id.get()
        if uid:
            import shutil
            from prax.services.workspace_service import _ensure_workspace, _safe_join
            root = _ensure_workspace(uid)
            dest = _safe_join(root, "active", f"{safe_title}.mp4")
            shutil.copy2(final_video, dest)
    except Exception:
        logger.debug("Could not copy video to workspace", exc_info=True)

    return (
        f"Presentation video created: **{title}**\n\n"
        f"- {num_slides} slides, {size_mb:.1f} MB\n"
        f"- TTS: {_get_tts_config()['provider']} ({_get_tts_config()['voice']})\n\n"
        f"Saved to workspace:\n"
        f"- `{safe_title}.mp4` — narrated video presentation\n"
        f"- `{safe_title}_slides.tex` — LaTeX source\n"
        f"- `{safe_title}_notes.md` — speaker notes\n\n"
        f"Use `workspace_send_file('{safe_title}.mp4')` to deliver the video."
    )


# ======================================================================
# Plugin registration
# ======================================================================

def register():
    """Return the tools this plugin provides."""
    return [pdf_to_presentation, pdf_to_slides]
