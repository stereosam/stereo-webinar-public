---
name: webinar
description: Clean up a long webinar, stream or lesson recording — cut out the waiting, sound checks, technical failures, off-topic chat, long silences and the tail after "goodbye", blur private things on screen (card numbers, chats, mailboxes, IP addresses), optionally speed it up a few percent, and glue the rest into one clean video. Splits the recording into numbered CLEAN / TRASH pieces the human reviews by renaming files, then glues from the original at full quality. Use when asked to clean, trim or remove junk from a webinar, stream, live broadcast or recorded lesson. For short vertical clips use reels; for screen recordings use screencast.
---

# Webinar → one clean video

Two tools in `tools/`: `transcribe.py` (speech → text with word timings) and `webinar.py`
(`lenta`, `cut`, `sheet`, `glue`). Between cutting and gluing — the human.

**You do the judgement:** which stretches are content and which are junk, and what on the
screen must not be shown. The script does the mechanics: snaps your boundaries into the
pauses between words, renders light drafts, blurs, glues the approved pieces from the original.

## The rules

1. **Never glue what the human has not reviewed.** Review order is fixed: first the TRASH
   pieces (is anything useful in there?), then the CLEAN ones (is everything right?).
2. **The transcript does not see the screen.** Speech never says "and here is my card
   number". Before gluing, look at frames of every CLEAN piece (`sheet`). On the first live
   run this found a full card number with CVC, a private Telegram chat, the author's mailbox,
   his e-mail on the Google sign-in form and his home IP on an error page — none of it was in
   the audio.
3. **A clean step that shows a secret needs a blur, not a cut.** Entering the card is the
   point of a payment lesson; hide the digits, keep the step.
4. **Drafts are for looking, the original is for the result.** Glue always renders from the
   source file, never from drafts.
5. **Status lives in the file name.** The human renames `ЧИСТ` ↔ `МУСОР` in Explorer or
   deletes a file; glue reads the names. Do not ask them to edit JSON.

## Sequence

```bash
T=tools          # run from the repo root; .env with DICTATOR_TOKEN sits there
export PYTHONIOENCODING=utf-8
```

### 1. Transcribe with word timings

```bash
python $T/transcribe.py --src stream.mp4 --out transcript.json --words
```
**Words are required** — boundaries are placed between words. No `--diarize`: with it the
server returns speakers but no word timings. A 40-minute stream: ~2 minutes.

### 2. Read it and mark the pieces

```bash
python $T/webinar.py lenta --transcript transcript.json --out lenta.txt
```
Phrases with timecodes, pauses ≥ 4 s as separate lines. **Read all of it**, then write
`pieces.json` — consecutive, no gaps:

```json
[{"kind": "trash", "title": "sound check", "start": 0, "end": 18.2},
 {"kind": "clean", "title": "intro why pay for chatgpt", "start": 18.2, "end": 59.5,
  "note": "optional: what to check"}]
```

What is trash: the wait before the start, "can you hear me", reconnects, "I'll be quiet,
you'll cut this", off-topic chat and swearing, silences ≥ 10 s while something installs or
loads, fumbling ("why is the screen so small, help"), the tail after the goodbye. Short
pauses < 10 s inside a clean stretch stay — they are breathing, not junk. Full names of
people and personal talk go to trash. When in doubt, trash with a note — trash is reviewed.

Validate before cutting: every `end` equals the next `start`, no piece has `end ≤ start`.

### 3. Cut the drafts — then stop

```bash
python $T/webinar.py cut --src stream.mp4 --transcript transcript.json \
    --pieces pieces.json --out drafts
```
`drafts/NNN_ЧИСТ_title_mm-ss_mm-ss.mp4` + `.txt` (text of the piece and the note). Drafts
are 480p ultrafast. `--text-only` writes just the `.txt` files.

### 4. Look at the screen

```bash
python $T/webinar.py sheet --src stream.mp4 --pieces pieces.json --out sheets
```
Three frames of every CLEAN piece, eight pieces per image, each row labelled with the piece
number. Read every image. For anything private add a `blur` to that piece in
`pieces.json` — coordinates in SOURCE pixels (1920×1080 for a typical stream):

```json
"blur": [[250, 195, 475, 770],                 // the whole piece
         [570, 655, 300, 35, 112, 515]]        // only 112–515 s of the source
```
To find coordinates, grab a full-size frame (`ffmpeg -ss T -i stream.mp4 -frames:v 1 f.png`)
and read positions off it. Check a blur on one piece before gluing everything:

```bash
python $T/webinar.py glue --src stream.mp4 --pieces pieces.json --drafts drafts \
    --out check.mp4 --only 36
```

Tell the human: how many pieces, clean time out of total, what you blurred and what you
flagged, then the review order — trash first, then clean. **Wait.**

### 5. Glue

```bash
python $T/webinar.py glue --src stream.mp4 --pieces pieces.json \
    --drafts drafts --out stream_clean.mp4 [--speed 1.06]
```
Every ЧИСТ piece in number order, re-rendered from the original (libx264 CRF 20, constant
25 fps — streams are often variable frame rate), blurs applied, a 30 ms audio fade on each
joint so it does not click, then concatenated without re-encoding. `--speed 1.06` makes it
6 % faster with the voice pitch unchanged (`atempo`) — viewers do not notice 5–7 %.

### 6. Check

- Length ≈ sum of clean pieces ÷ speed.
- Frames at the blurred pieces, taken from the RESULT, not the draft.
- Transcribe the result and compare with the expected words of the clean pieces: the
  differences should be single words the recogniser heard differently, never a cut-off phrase.

## What this skill deliberately does not do

Decide on its own what is junk, publish, or trust the transcript to reveal what is on screen.
