---
name: webinar
description: Clean up a long webinar, stream or lesson recording — cut out the waiting, sound checks, technical failures, off-topic chat, long silences and the tail after "goodbye", find and blur private things on screen (card numbers, keys, chats, mailboxes, IP addresses — frame-by-frame OCR on Windows), optionally speed it up a few percent and bleep words, and glue the rest into one clean video. Splits the recording into numbered CLEAN / TRASH pieces the human reviews by renaming files, then glues from the original at full quality. Use when asked to clean, trim or remove junk from a webinar, stream, live broadcast or recorded lesson. For short vertical clips use reels; for screen recordings use screencast.
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
and read positions off it.

### 4b. Scan the screen frame by frame (Windows)

Three frames per piece miss what is on screen for only a few seconds. On a real stream a
terminal flashed subscription keys and a server IP for 2–3 seconds at a time, three times;
`sheet` did not catch it, `scan` did.

```bash
python $T/webinar.py scan --src stream.mp4 --pieces pieces.json --out scan.json
```
One frame per second of every CLEAN piece through the OCR engine built into Windows
(nothing to install), then patterns: card numbers, card tails and expiry dates, e-mails,
phone numbers, IP addresses, `vless://`-style links, `password=`/`token=`-style fields,
API tokens, long random strings. Each finding comes out as a ready `blur` entry — box in
source pixels, time window in source seconds — with the matched text masked in the middle.
Findings are candidates: look at the frame, drop false alarms, copy the rest into the
piece's `"blur"`. ~0.5 s per frame; `--every 2` halves it, `--range 180-260` scans a window.
Not on Windows: rely on `sheet` and your eyes.

Check a blur on one piece before gluing everything:

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
25 fps — streams are often variable frame rate), blurs applied. Sound goes separately:
each piece as uncompressed PCM with a 30 ms fade at the joints, trimmed to exactly the
length of its video, then all of it encoded to AAC **once**. Encoding AAC per piece clicks
at every joint, and a fraction of a frame of audio/video mismatch per piece adds up to a
second of drift over sixty pieces. `--speed 1.06` makes it 6 % faster with the voice pitch
unchanged (`atempo`) — viewers do not notice 5–8 %. `--declick` runs `adeclick` over the
whole track (mouth and microphone clicks; slow).

### 6. Check

- Length ≈ sum of clean pieces ÷ speed.
- Frames at the blurred pieces, taken from the RESULT, not the draft.
- `scan` over the RESULT without `--pieces`: it must find nothing you meant to hide.
- Transcribe the result and compare with the expected words of the clean pieces: the
  differences should be single words the recogniser heard differently, never a cut-off phrase.

### 7. Bleep words (optional, on the result)

Only if the human names words that must not be heard (swearing, a brand, a name). Transcribe
the RESULT — after gluing and speed-up the timings differ from the source — then:

```bash
python $T/transcribe.py --src stream_clean.mp4 --out result.json --words
python $T/webinar.py beep --src stream_clean.mp4 --transcript result.json \
    --words "word1,word2" --out stream_final.mp4
```
Words are matched by their beginning, case-insensitive. The tone is 1 kHz at 0.035 — clearly
covers the word without hurting ears (0.2 and 0.07 were rejected as too loud) — from 20 ms
before the word to its end, at most 0.45 s. Video is copied, only the sound is re-encoded.
Listen to every listed spot. A bleep hides a word, not the meaning: if what was said must
not be published, cut the piece instead.

## Topic clips from a long recording (one question — one video)

A lesson answers a question in scattered places: the definition at minute 26, the example an
hour later, the conclusion at minute 32. Pick the keyword first (the `keywords` skill), then
write a spec in SOURCE time — the parts in the order they should be watched:

```json
{"parts":    [{"start": "26:38", "end": 1626.3, "title": "what an agent in a chat can do"},
              {"start": "1:48:31", "end": 6557.6, "title": "the chat builds, Codex deploys"}],
 "chapters": [{"at": "26:38", "text": "ЧТО УМЕЕТ ИИ-АГЕНТ", "desc": "Что умеет ИИ-агент"}],
 "notes":    [{"at": 1940.5, "dur": 7, "text": "*Instagram принадлежит компании Meta…"}],
 "cover":    [{"parts": [1]}],
 "blur":     [[485, 575, 32, 18, 1948.5, 2000]]}
```

```bash
python $T/webinar.py clip  --transcript transcript.json --spec c01.json --out c01 --speed 1.15
python $T/webinar.py glue  --src stream.mp4 --pieces c01/pieces.json --out c01/raw.mp4 --speed 1.15
python $T/webinar.py dress --src c01/raw.mp4 --plan c01/plan.json --out c01/c01.mp4 \
    --cover banner.png --cover-box 18,249,1125,604 --title-box 18,112,1125,112 \
    --font Montserrat-Black.ttf --glitch
python $T/transcribe.py --src c01/c01.mp4 --out c01/result.json --words
python $T/webinar.py flags --transcript c01/result.json      # VPN, Instagram, swearing in speech
python $T/webinar.py scan  --src c01/c01.mp4 --out c01/scan.json   # same words + secrets on screen
```

- `clip` snaps every boundary into a word gap and squeezes pauses longer than 1.2 s inside a
  part down to 0.4 s after the word + 0.2 s before the next. Keep `--after` larger: GigaAM
  word ends are emission moments, the sound goes on — at 0.2 s word tails were cut.
  It prints the YouTube chapter lines (`desc`) already shifted to clip time.
- `dress`: a cover image over the screen area while the screen shows nothing useful (the
  taskbar and cameras stay — it looks like it is open on the author's screen); chapter
  plates in an empty band of the frame — "what is this about now" for viewers who scrub,
  same wording as the description chapters; footnotes; blur in RESULT pixels/seconds.
- Russia: advertising ways around blocking (VPN) is banned, Meta is designated extremist.
  `scan` and `flags` report both. The human decides: footnote, blur, bleep or cut —
  never carry over a decision made for another video.
- Collect the human's edits for a clip into one list and render once; every render costs minutes.

## What this skill deliberately does not do

Decide on its own what is junk, publish, or trust the transcript to reveal what is on screen.
