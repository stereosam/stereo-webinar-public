#!/usr/bin/env python3
"""
Вебинар → чистая запись. Главные шаги cut и glue, между ними — человек.

  lenta расшифровка → читаемая лента с паузами; по ней агент размечает куски.

  cut   манифест кусков → папка черновиков:
            001_ЧИСТ_вступление_00-18-01-00.mp4   (+ .txt с текстом куска)
            002_МУСОР_ждём-участников_01-00-01-20.mp4
        Черновики лёгкие (480p, ultrafast), чтобы нарезка шла быстро. Человек смотрит
        сначала МУСОР, нашёл полезное — переименовывает в ЧИСТ прямо в
        проводнике. Потом смотрит ЧИСТ.

  sheet кадры каждого ЧИСТ-куска одной картинкой — проверить экран на секреты
        (номер карты, чаты, почта), которых нет в тексте.

  glue  склейка всех ЧИСТ по номерам — ИЗ ОРИГИНАЛА в полном качестве, не из
        черновиков. Статус берётся из ИМЕНИ файла в папке черновиков, секунды —
        из манифеста. Переименование ничего не ломает, удалённый файл = выкинут.
        Умеет замазку областей (поле "blur" куска), ускорение (--speed 1.06) и
        предпросмотр одного куска (--only N).

Границы кусков из манифеста — примерные (по расшифровке). Скрипт подгоняет
каждую в паузу между словами (нужны пословные тайминги: tools/transcribe.py
--words): на стыке ЧИСТ→МУСОР граница ставится сразу после последнего слова,
на стыке МУСОР→ЧИСТ — прямо перед первым, между двумя ЧИСТ — в середину паузы.
Так кусок не начинается посреди слова и не тащит за собой полминуты тишины.

    python webinar.py cut  --src эфир.mp4 --transcript transcript.json --pieces pieces.json --out drafts
    python webinar.py glue --src эфир.mp4 --pieces pieces.json --drafts drafts --out эфир_чистый.mp4

Формат pieces.json: [{"kind": "clean"|"trash", "title": "...", "start": 18.0, "end": 60.0,
"note": "необязательно: почему мусор / что проверить",
"blur": [[x, y, w, h], [x, y, w, h, от, до]]}, ...] — куски подряд, без дыр.
Координаты замазки — в пикселях исходника, время — в секундах исходника.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

LABEL = {"clean": "ЧИСТ", "trash": "МУСОР"}
KIND_OF = {v: k for k, v in LABEL.items()}
NAME_RE = re.compile(r"^(\d{3})_(ЧИСТ|МУСОР)_")
LEAD, TAIL = 0.25, 0.30        # запас перед первым словом / после последнего, сек


def die(msg):
    print(f"ошибка: {msg}", file=sys.stderr)
    sys.exit(1)


def stamp(t):
    t = int(round(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}-{m:02d}-{s:02d}" if h else f"{m:02d}-{s:02d}"


def slug(title):
    s = re.sub(r"[\\/:*?\"<>|]+", "", title.strip().lower())
    s = re.sub(r"\s+", "-", s)
    return s[:48].strip("-") or "кусок"


def load_words(path):
    d = json.load(open(path, encoding="utf-8"))
    words = []
    for seg in d.get("segments", []):
        for w in seg.get("words") or []:
            text = (w.get("word") or w.get("w") or "").strip()
            if text:
                words.append((float(w["start"]), float(w["end"]), text))
    if not words:
        die(f"{path}: нет пословных таймингов — расшифруй с --words")
    words.sort()
    return words, float(d.get("duration") or words[-1][1])


def snap(b, left_kind, right_kind, words):
    """
    Подогнать примерную границу b в паузу между словами.

    Ищем паузу, в которую попадает b, иначе ближайшую. Дальше место внутри
    паузы зависит от стыка: мусор режем вплотную к речи, между чистыми
    кусками делим тишину пополам — после склейки выйдет естественная пауза.
    """
    best, best_d = None, None
    for i in range(len(words) - 1):
        a, z = words[i][1], words[i + 1][0]
        if a <= b <= z:
            best = i
            break
        d = min(abs(b - a), abs(b - z))
        if best_d is None or d < best_d:
            best, best_d = i, d
    if best is None:
        return b
    a, z = words[best][1], words[best + 1][0]
    if z <= a:
        return (a + z) / 2
    if left_kind == "clean" and right_kind == "trash":
        return min(a + TAIL, (a + z) / 2)
    if left_kind == "trash" and right_kind == "clean":
        return max(z - LEAD, (a + z) / 2)
    if left_kind == "trash" and right_kind == "trash":
        # оба куска выкидываются — граница остаётся там, где её поставил
        # человек, иначе на длинной паузе она уезжает в середину и следующему
        # куску достаётся минута чужой тишины
        return min(max(b, a), z)
    return (a + z) / 2


def load_pieces(path):
    pieces = json.load(open(path, encoding="utf-8"))
    for i, p in enumerate(pieces):
        if p.get("kind") not in LABEL:
            die(f"кусок {i + 1}: kind должен быть clean или trash")
        p["start"], p["end"] = float(p["start"]), float(p["end"])
    return pieces


def cmd_cut(args):
    words, dur = load_words(args.transcript)
    pieces = load_pieces(args.pieces)
    pieces[0]["start"], pieces[-1]["end"] = 0.0, dur
    # границы подряд: конец куска k = начало k+1, после подгонки
    for k in range(len(pieces) - 1):
        b = snap((pieces[k]["end"] + pieces[k + 1]["start"]) / 2,
                 pieces[k]["kind"], pieces[k + 1]["kind"], words)
        pieces[k]["end"] = pieces[k + 1]["start"] = round(b, 3)

    os.makedirs(args.out, exist_ok=True)
    for i, p in enumerate(pieces, 1):
        p["n"] = i
        p["file"] = f"{i:03d}_{LABEL[p['kind']]}_{slug(p['title'])}_{stamp(p['start'])}_{stamp(p['end'])}"
    json.dump(pieces, open(args.pieces, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    for p in pieces:
        base = os.path.join(args.out, p["file"])
        text = " ".join(w for s, e, w in words if s >= p["start"] - 0.05 and e <= p["end"] + 0.05)
        with open(base + ".txt", "w", encoding="utf-8-sig") as f:
            f.write(f"{p['title']}  [{stamp(p['start'])} – {stamp(p['end'])}, "
                    f"{p['end'] - p['start']:.0f} с]\n")
            if p.get("note"):
                f.write(f"Заметка: {p['note']}\n")
            f.write("\n" + (text or "(речи нет)") + "\n")
        if not args.text_only:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", f"{p['start']:.3f}",
                 "-t", f"{p['end'] - p['start']:.3f}", "-i", args.src, "-vf", "scale=-2:480",
                 "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
                 "-c:a", "aac", "-b:a", "64k", base + ".mp4"], check=True)
        print(f"  {p['file']}  ({p['end'] - p['start']:.0f} с)")

    clean = sum(p["end"] - p["start"] for p in pieces if p["kind"] == "clean")
    print(f"\nкусков {len(pieces)}: ЧИСТ {sum(p['kind'] == 'clean' for p in pieces)}, "
          f"МУСОР {sum(p['kind'] == 'trash' for p in pieces)}; "
          f"чистого {clean / 60:.1f} мин из {dur / 60:.1f}")


def mmss(t):
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def cmd_lenta(args):
    """
    Читаемая лента для разметки: фразы с таймкодами, длинные паузы отдельной
    строкой. Её агент читает ЦЕЛИКОМ перед тем, как писать pieces.json —
    мусор на вебинаре узнаётся по смыслу («тут я помолчу, ты вырежешь»),
    а не по громкости.
    """
    d = json.load(open(args.transcript, encoding="utf-8"))
    segs = d.get("segments") or []
    out, prev = [], 0.0
    for s in segs:
        if s["start"] - prev >= args.pause:
            out.append(f"        ... пауза {s['start'] - prev:.0f} с ...")
        out.append(f"{mmss(s['start'])}-{mmss(s['end'])} {s['text'].strip()}")
        prev = s["end"]
    dur = float(d.get("duration") or prev)
    if dur - prev >= args.pause:
        out.append(f"        ... хвост без речи {dur - prev:.0f} с ...")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    speech = sum(s["end"] - s["start"] for s in segs)
    print(f"{len(out)} строк; речь {speech / 60:.1f} мин из {dur / 60:.1f} -> {args.out}")


def font_file():
    """
    Шрифт для подписи кадров, путём, пригодным для фильтра ffmpeg.

    drawtext без явного fontfile на Windows падает (access violation внутри
    fontconfig), поэтому шрифт ищем сами. Не нашли — подписи не будет,
    картинки соберутся всё равно.
    """
    for p in (os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf"),
              "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if os.path.isfile(p):
            return p.replace("\\", "/").replace(":", "\\:")
    return None


def cmd_sheet(args):
    """
    Кадры каждого ЧИСТ-куска одной картинкой — проверка экрана на секреты.

    Текст расшифровки не видит экрана. На первом же живом эфире так нашлись
    две вещи, о которых речь молчала: полный номер карты с CVC в форме оплаты
    и личная переписка в Telegram за окном мини-приложения. Смотреть надо
    каждый кусок, который идёт в итог, — по три кадра на кусок.
    """
    pieces = [p for p in load_pieces(args.pieces) if p["kind"] == "clean"]
    tmp = tempfile.mkdtemp(prefix="webinar_sheet_")
    try:
        frames = []
        for p in pieces:
            d = p["end"] - p["start"]
            for k, frac in enumerate((0.15, 0.5, 0.85)):
                path = os.path.join(tmp, f"{p.get('n', 0):03d}_{k}.png")
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss",
                                f"{p['start'] + d * frac:.2f}", "-i", args.src,
                                "-frames:v", "1", "-vf", "scale=640:-2", path], check=True)
                frames.append((p, path))
        # склейка сеткой: строка = кусок, три кадра; подпись номером через drawtext
        font = font_file()
        rows = []
        for i in range(0, len(frames), 3):
            p = frames[i][0]
            row = os.path.join(tmp, f"row_{i // 3:03d}.png")
            # без двоеточия: в фильтре ffmpeg оно разделяет параметры
            label = f"{p.get('n', 0):03d} {stamp(p['start'])}"
            graph = "[0:v][1:v][2:v]hstack=3,drawbox=x=0:y=0:w=150:h=34:color=yellow@1:t=fill"
            if font:
                graph += (f",drawtext=fontfile='{font}':text='{label}':x=8:y=6:"
                          f"fontsize=22:fontcolor=black")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", frames[i][1], "-i", frames[i + 1][1],
                 "-i", frames[i + 2][1], "-filter_complex", graph, row], check=True)
            rows.append(row)
        os.makedirs(args.out, exist_ok=True)
        for j in range(0, len(rows), args.per_sheet):
            chunk = rows[j:j + args.per_sheet]
            out = os.path.join(args.out, f"sheet_{j // args.per_sheet + 1:02d}.png")
            inputs = sum((["-i", r] for r in chunk), [])
            vstack = (f"{''.join(f'[{i}:v]' for i in range(len(chunk)))}vstack={len(chunk)}"
                      if len(chunk) > 1 else "null")
            subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs,
                            "-filter_complex", vstack, out], check=True)
            print("  " + out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"кусков {len(pieces)}: смотри каждый кадр — номера карт, пароли, чаты, адреса")


def cmd_glue(args):
    pieces = {p["n"]: p for p in load_pieces(args.pieces) if "n" in p}
    if not pieces:
        die("в манифесте нет номеров — сначала cut")
    status = {}
    for name in os.listdir(args.drafts):
        m = NAME_RE.match(name)
        if m and name.endswith(".mp4"):
            status[int(m.group(1))] = KIND_OF[m.group(2)]
    keep = [pieces[n] for n in sorted(status) if status[n] == "clean" and n in pieces]
    if args.only:
        # предпросмотр одного куска в полном качестве — проверить замазку
        keep = [pieces[args.only]]
    if not keep:
        die("нет ни одного ЧИСТ-куска")
    moved = [n for n in sorted(status) if n in pieces and status[n] != pieces[n]["kind"]]
    if moved:
        print("переименованы человеком: " + ", ".join(
            f"{n:03d} {LABEL[pieces[n]['kind']]}→{LABEL[status[n]]}" for n in moved))

    tmp = tempfile.mkdtemp(prefix="webinar_glue_")
    try:
        parts = []
        for i, p in enumerate(keep):
            out = os.path.join(tmp, f"{i:03d}.mp4")
            d = p["end"] - p["start"]
            # короткий фейд звука на каждом стыке — иначе склейка щёлкает
            fade = min(0.03, d / 4)
            # Ускорение: atempo меняет темп без сдвига тона (голос не «мультяшный»),
            # видео — setpts. На вебинаре 5-7 % зритель не замечает, а ролик короче.
            tempo = f",atempo={args.speed}" if args.speed != 1 else ""
            graph = [f"[0:a]afade=t=in:d={fade:.3f},afade=t=out:st={d - fade:.3f}:d={fade:.3f}"
                     f"{tempo}[aout]"]
            # Замазка: "blur": [[x, y, w, h], ...] в пикселях ИСХОДНИКА — на весь кусок,
            # или [x, y, w, h, от, до] — только в этом интервале (секунды исходника).
            # Сжатие в 20 раз и растяжение обратно, а не boxblur: у boxblur радиус
            # упирается в размер области, и на узком поле цифры остаются читаемыми.
            v, used = "[0:v]", 0
            for j, box in enumerate(p.get("blur") or []):
                x, y, w, h = (int(q) for q in box[:4])
                enable = ""
                if len(box) >= 6:
                    a, b = float(box[4]) - p["start"], float(box[5]) - p["start"]
                    if b <= 0 or a >= d:
                        continue        # интервал мимо этого куска
                    enable = f":enable='between(t,{max(0.0, a):.3f},{min(d, b):.3f})'"
                sw, sh = max(2, w // 20 // 2 * 2), max(2, h // 20 // 2 * 2)
                graph.append(f"{v}split[m{j}][c{j}]")
                graph.append(f"[c{j}]crop={w}:{h}:{x}:{y},scale={sw}:{sh}:flags=area,"
                             f"scale={w}:{h}:flags=bicubic[b{j}]")
                graph.append(f"[m{j}][b{j}]overlay={x}:{y}{enable}[v{j}]")
                v, used = f"[v{j}]", used + 1
            if args.speed != 1:
                graph.append(f"{v}setpts=PTS/{args.speed}[vs]")
                v, used = "[vs]", used + 1
            vmap = v if used else "0:v:0"
            subprocess.run(
                # -t ДО -i: ограничение на вход. После -i ffmpeg режет выход по
                # времени исходника и молча дотягивает ускоренный кусок до прежней
                # длины повтором кадров и тишиной — ускорение пропадает.
                ["ffmpeg", "-v", "error", "-y", "-ss", f"{p['start']:.3f}", "-t", f"{d:.3f}",
                 "-i", args.src, "-filter_complex", ";".join(graph),
                 "-map", vmap, "-map", "[aout]", "-r", str(args.fps), "-fps_mode", "cfr",
                 "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf),
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", out],
                check=True)
            parts.append(out)
            print(f"  [{i + 1}/{len(keep)}] {p['file']}")
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            f.writelines(f"file '{x}'\n" for x in parts)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                        "-c", "copy", "-movflags", "+faststart", args.out], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = sum(p["end"] - p["start"] for p in keep) / args.speed
    if not os.path.isfile(args.out) or os.path.getsize(args.out) == 0:
        die("склейка не записала файл")
    print(f"\nготово: {args.out} — {len(keep)} кусков, {total / 60:.1f} мин")


def main():
    p = argparse.ArgumentParser(description="вебинар → куски ЧИСТ/МУСОР → чистая склейка")
    sub = p.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("lenta", help="читаемая лента для разметки")
    l.add_argument("--transcript", required=True)
    l.add_argument("--out", required=True)
    l.add_argument("--pause", type=float, default=4.0, help="показывать паузы от, сек")
    sh = sub.add_parser("sheet", help="кадры ЧИСТ-кусков для проверки экрана на секреты")
    sh.add_argument("--src", required=True)
    sh.add_argument("--pieces", required=True)
    sh.add_argument("--out", required=True, help="папка для картинок")
    sh.add_argument("--per-sheet", type=int, default=8, help="кусков на картинку")
    c = sub.add_parser("cut")
    c.add_argument("--src", required=True)
    c.add_argument("--transcript", required=True, help="tools/transcribe.py --words")
    c.add_argument("--pieces", required=True)
    c.add_argument("--out", required=True, help="папка черновиков")
    c.add_argument("--text-only", action="store_true", help="только .txt, без видео")
    g = sub.add_parser("glue")
    g.add_argument("--src", required=True)
    g.add_argument("--pieces", required=True)
    g.add_argument("--drafts", required=True)
    g.add_argument("--out", required=True)
    g.add_argument("--fps", type=int, default=25)
    g.add_argument("--crf", type=int, default=20)
    g.add_argument("--preset", default="veryfast")
    g.add_argument("--speed", type=float, default=1.0,
                   help="ускорить итог, напр. 1.06 = +6 %% (темп голоса сохраняется)")
    g.add_argument("--only", type=int, default=0,
                   help="собрать только кусок N — предпросмотр замазки")
    a = p.parse_args()
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            die(f"{tool} не найден в PATH")
    {"lenta": cmd_lenta, "sheet": cmd_sheet, "cut": cmd_cut, "glue": cmd_glue}[a.cmd](a)


if __name__ == "__main__":
    main()
