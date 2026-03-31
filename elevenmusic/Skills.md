# ElevenLabs Music Plugin

## When to use

- User wants to generate a song, beat, jingle, or musical track
- User describes a genre, mood, or vibe they want ("lo-fi hip hop", "punk rock", "jazz instrumental")
- User wants background music or a soundtrack for a video or presentation
- User provides lyrics or a theme and wants it turned into a song

## When NOT to use

- User wants text-to-speech narration — use the TTS capabilities in pdf2presentation or the framework's `caps.tts_synthesize()`
- User wants to play/stream existing audio files — use the radio plugin instead
- User wants to edit or remix an existing audio file — this plugin generates from scratch only
- User wants sound effects, not music

## Tips

- Keep prompts descriptive: genre + mood + instruments + tempo gives the best results. Example: "A mellow acoustic folk song with fingerpicked guitar and soft vocals"
- Default duration is 30 seconds — suggest longer durations (60–120s) for full songs
- Set `instrumental=True` when the user wants a beat, backing track, or background music without vocals
- Maximum duration is 600 seconds (10 minutes) — warn users that longer tracks take more time and API credits
- The generated file is saved as MP3 to the workspace with a slugified name based on the prompt
- If the file is under 8 MB, use `workspace_send_file` to deliver it directly; otherwise use `workspace_share_file` for a link

## Requirements

- `ELEVENLABS_API_KEY` must be set in Prax's environment
- The permission must be approved after importing (Prax will prompt automatically)
- No system dependencies — everything goes through the ElevenLabs HTTP API

## Example prompts

> "Generate a lo-fi hip hop beat for studying, about 2 minutes"

> "Make a punk rock song about debugging at 3am"

> "Create a 30 second jazz instrumental for a presentation intro"

> "Generate an upbeat pop track with catchy synth hooks, instrumental only"
