# AGENTS.md

Things that are not visible from the code.

- **Look at the screen before gluing.** The transcript cannot tell you a card number, a
  private chat or a mailbox is on screen. `webinar.py sheet` exists because the first live
  run found five of those, none mentioned in the audio.
- **Stop after cutting drafts.** Do not glue until the human has reviewed trash, then clean.
- **Status is the file name.** Glue reads `ЧИСТ`/`МУСОР` from the draft names and seconds from
  `pieces.json`. A deleted draft means "drop it". Never edit status in JSON behind the human's back.
- **Glue renders from the original.** Drafts are 480p for review only.
- **`-t` goes before `-i`.** As an output option after `-i`, ffmpeg limits the output by the
  source's timeline and silently pads a sped-up piece back to its old length with repeated
  frames and silence — `--speed` would do nothing. Found by measuring, not by reading.
- **Blur by shrinking, not `boxblur`.** A 20× downscale and upscale destroys digits at any
  region size; `boxblur`'s radius is capped by the region and leaves narrow fields readable.
- **Boundaries between two trash pieces stay where the human put them.** Snapping them into
  the middle of a long pause hands the next piece a minute of someone else's silence.
- **`drawtext` needs an explicit `fontfile` on Windows**, or ffmpeg crashes with an access
  violation inside fontconfig. `font_file()` finds one per OS.
- **`--diarize` drops word timings** on the recognition server. Transcribe without it.
- Dependencies: Python 3.9+, `ffmpeg`/`ffprobe` in PATH. Standard library only.
