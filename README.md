# STEREO Webinar

An agent skill that turns a long, messy stream or webinar recording into one clean video.
It cuts the recording into numbered **CLEAN** and **TRASH** pieces with boundaries in the
pauses between words, lets a human review them by renaming files, shows frames of every
clean piece to catch secrets the transcript cannot see, blurs them, and glues the approved
pieces from the original at full quality. Two scripts, `ffmpeg`, standard library only.

**Status:** working, built on a real 39-minute stream.

---

## The problem it solves

A live lesson is maybe half content. The rest is "can you hear me", waiting for an
installer, a chat with the cameraman, "I'll be quiet, you'll cut this", three minutes of
silence while something loads. Silence detectors cut the breathing out of good speech and
keep the off-topic chat, because junk here is recognised by **meaning**, not loudness.

The second problem is worse and invisible in the transcript: what is **on screen**. A
lesson about paying for a subscription shows a card being typed in. A lesson on a real
machine shows the author's mailbox, messenger chats and IP address. The audio says none of it.

## How it works

```
recording
   │ transcribe --words          per-word timings
   ▼
lenta ─────────► lenta.txt        phrases + pauses, the AGENT reads all of it
   │                              and marks pieces in pieces.json
   ▼
cut ───────────► drafts/          001_ЧИСТ_intro_00-18_01-00.mp4  (+ .txt)
   │                              002_МУСОР_sound-check_01-00_01-20.mp4
   │                              boundaries snapped into pauses between words
   ▼
sheet ─────────► sheets/          3 frames of every CLEAN piece → blur what must not show
scan ──────────► scan.json        OCR 1 frame/s (Windows): cards, keys, IPs, e-mails →
   │                              ready blur entries
   │   the HUMAN reviews: trash first, then clean; renames МУСОР ↔ ЧИСТ in Explorer
   ▼
glue ──────────► clean.mp4        every ЧИСТ, from the ORIGINAL, blurred, faded at joints,
   │                              sound encoded once; optionally --speed 1.06
   ▼
beep ──────────► final.mp4        optional: bleep named words in the result, video copied
```

## Measured

One real stream: 39 min 27 s, 1920×1080, variable frame rate, mono. Laptop i7-8565U.

| | |
|---|---|
| Transcription with word timings | 2367 s of audio, 2195 words, **1 min 50 s** |
| Speech in the recording | 16.4 min of 39.5 |
| Marked | 62 pieces: 32 clean (21.2 min), 30 trash (18.3 min) |
| Drafts for review | 62 files in 4 min 17 s (at 720p; now 480p ultrafast) |
| Frames of all clean pieces | 4 images, **44 s** |
| Secrets found on screen, none in the audio | card number + expiry + CVC, a private chat, the chat list, the author's mailbox and e-mail, his home IP |
| Glue from the original, 1080p, with blurs | ≈ 5 min |
| Result at `--speed 1.06` | **20 min 03 s** from 39 min 27 s |
| `scan` of an 80-second window, 1 frame/s | **22 s**; unblurred version: 4 findings (server IP twice, a subscription key twice), each flashing 2–3 s; published blurred version: 0 |
| `beep` over an 18-minute result | 16 words, **1.5 min**; 1 kHz band in the window −52 → −32.6 dB |
| Re-transcription of the result vs expected words | 1716 → 1718 words, 97 % match; every difference a single word heard differently, no phrase cut at a joint |

## Details worth stealing

**Boundaries depend on what they separate.** Clean → trash: right after the last word.
Trash → clean: right before the first word. Clean → clean: the middle of the pause, so the
joint breathes naturally. Trash → trash: where the human put it — snapping into the middle
of a 132-second silence would hand the next piece a minute of nothing.

**Status in the file name, seconds in the manifest.** The human never touches JSON: they
watch the drafts in Explorer and rename. Glue reads the names, so a rename or a deletion is
the whole interface.

**Blur by shrinking.** `boxblur`'s radius is capped by the size of the region, so a narrow
field of digits stays readable. Scaling the region down 20× and back destroys it at any size.
Blurs can cover a whole piece or a time window inside it, in source seconds.

**Sound is encoded once.** Every piece's audio is uncompressed and trimmed to exactly the
length of its video; the whole track goes through AAC in one pass. Per-piece AAC clicked at
the joints on the first real stream, and small per-piece length differences add up to drift.

**The screen is read, not glanced at.** `sheet` shows three frames per piece — good for
a chat window that stays open, useless for a terminal that prints a key for two seconds.
`scan` reads a frame every second with the OCR engine already built into Windows and turns
each finding into a `blur` entry with coordinates.

**`-t` before `-i`.** With `-t` as an output option, ffmpeg limits the output by the source
timeline and pads a sped-up piece back to its original length with repeated frames and
silence. The speed-up simply vanished — found by measuring the output, not by reading docs.

## Setup

```bash
# 1. ffmpeg and ffprobe in PATH, Python 3.9+
ffmpeg -version

# 2. token: send /app to @stereo_dictator_bot
cp .env.example .env      # then paste the token into DICTATOR_TOKEN
```

Recognition runs in the cloud (GigaAM v3, a Russian speech model, behind the STEREO
Dictator gateway) — nothing to install, no model to download.

### Install as a skill

| Agent | Where the folder goes | How to call it |
|---|---|---|
| Claude Code | `~/.claude/skills/webinar/` (or `<project>/.claude/skills/webinar/`) | ask to clean a stream recording, or `/webinar` |
| Codex (CLI, IDE, app) | `~/.agents/skills/webinar/` (or `<repo>/.agents/skills/webinar/`) | ask to clean a stream recording, `$webinar`, or pick it in `/skills` |

Both agents read the same `SKILL.md` (`name` + `description` frontmatter); Codex also reads
`AGENTS.md`. **Copy the folder, do not symlink it** for Codex: symlinked skill folders are not
always discovered by the CLI. Restart the agent after installing.

## Quick run

```bash
python tools/transcribe.py --src stream.mp4 --out transcript.json --words
python tools/webinar.py lenta --transcript transcript.json --out lenta.txt
# read lenta.txt, write pieces.json
python tools/webinar.py cut   --src stream.mp4 --transcript transcript.json --pieces pieces.json --out drafts
python tools/webinar.py sheet --src stream.mp4 --pieces pieces.json --out sheets
python tools/webinar.py scan  --src stream.mp4 --pieces pieces.json --out scan.json   # Windows
# review drafts, add "blur" to pieces that show secrets
python tools/webinar.py glue  --src stream.mp4 --pieces pieces.json --drafts drafts --out clean.mp4 --speed 1.06
```

## Known limitations

- **Marking is done by the agent reading the transcript.** There is no automatic junk
  detector, by design: "I'll cut this later" is junk because of what it says.
- **`scan` needs Windows** (built-in OCR). Elsewhere: `sheet` and your eyes.
- **Blur regions are rectangles in fixed positions.** If the secret moves (a scrolling page),
  cover the whole area it moves through, or split the piece.
- **Russian-first.** The recogniser and the file labels are Russian.
- **Word timings and speakers do not come together** on the recognition server.

## License

MIT © 2026 Sergey Drozdov
