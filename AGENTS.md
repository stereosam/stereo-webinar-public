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
- **Three frames per piece are not enough.** Secrets flash for 2–3 seconds (a terminal
  printing keys, a bank app showing the card tail). `scan` reads one frame per second with
  the Windows OCR engine and turns findings into ready `blur` entries. Run it again on the
  RESULT: it must come back empty.
- **`ocr_frames.ps1` is ASCII-only and runs in Windows PowerShell 5.1.** PS 5.1 reads scripts
  as ANSI (Cyrillic breaks the parser), and WinRT OCR types do not load in PowerShell 7.
- **Scan output masks the middle of every match.** Findings go to the console and the agent's
  context; a full card number or key must not.
- **Sound is encoded once.** AAC per piece clicks at every joint; per-piece PCM trimmed to the
  exact video length, one AAC pass at the end. Measured: 3 pieces at 1.08× → video and audio
  both 83.28 s.
- **Bleep the result, not the source.** Timings change after gluing and speed-up; transcribe
  the finished file. 1 kHz at 0.035 is the approved level (≈ −32 dB in the 1 kHz band).
- **`-/filter_complex file`**, not `-filter_complex_script` (removed in ffmpeg 7+): a bleep
  expression with hundreds of windows does not fit on a Windows command line.
- **`--diarize` drops word timings** on the recognition server. Transcribe without it.
- **The server's clock runs short on long files.** A 8479.00 s file came back as 8478.25 s
  with every timestamp compressed by the same ratio — words 0.07 s early at minute 13 and
  0.76 s early at minute 138 (checked against excerpts transcribed on their own). Clips cut
  late in a stream then lose word endings and pick up the tail of the previous phrase.
  `transcribe.py` now rescales all times by real / reported duration (`time_scale` in JSON).
- **`clip` part `"exact": true`** skips snapping when the gap between phrases is shorter
  than the tail of the previous word.
- Dependencies: Python 3.9+, `ffmpeg`/`ffprobe` in PATH. Standard library only.
