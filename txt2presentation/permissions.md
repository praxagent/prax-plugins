# Permissions

> This file is the authoritative declaration of what this plugin can do.
> The Prax framework enforces these limits at runtime — the plugin cannot
> exceed what is declared here. Changes to this file are visible in diffs
> and should be reviewed carefully.

## capabilities
- llm
- http
- commands
- tts
- transcription

## secrets
(none — all credentialed operations go through the capabilities gateway)

## allowed_commands
- pdflatex — compile Beamer LaTeX to PDF
- pdftoppm — convert PDF pages to PNG images
- pdftotext — extract text from PDF (fallback)
- ffmpeg — assemble slide videos and concatenate
- ffprobe — detect audio duration
- yt-dlp — download YouTube subtitles or audio
- which — check if system dependencies are installed
- rm — clean up temporary files
