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

Тематические ролики из длинной записи (один вопрос — один ролик):

  clip  сценарий ролика (куски исходника В НУЖНОМ ПОРЯДКЕ, главы, сноски, заставка) →
        манифест для glue + план оформления. Длинные паузы внутри кусков ужимаются.
        Всё в сценарии — во времени ИСХОДНИКА, clip сам переводит во время ролика.
  dress оформление готового ролика одним проходом: заставка поверх экрана, плашки-главы
        («о чём сейчас» — держат того, кто перематывает), сноски, замазка.
  flags речь готового ролика: запрещённое в РФ (VPN, Instagram/Facebook) и мат — с таймкодами.

    python webinar.py clip  --transcript transcript.json --spec c01.json --out c01 --speed 1.15
    python webinar.py glue  --src эфир.mp4 --pieces c01/pieces.json --out c01/raw.mp4 --speed 1.15
    python webinar.py dress --src c01/raw.mp4 --plan c01/plan.json --out c01/c01.mp4 ...
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


# Что считаем секретом на экране. Ловим с запасом: ложное срабатывание человек
# отметёт за секунду, а пропущенный номер карты в опубликованном ролике не отменить.
SECRET_RES = [
    ("карта", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")),
    ("хвост карты", re.compile(r"[•*·]{2,}\s?\d{4}")),
    ("срок карты", re.compile(r"(?<![\d/])(?:0[1-9]|1[0-2])\s?/\s?\d{2}(?![\d/])")),
    ("почта", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("IP", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")),
    ("телефон", re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")),
    ("ссылка-ключ", re.compile(r"(?i)\b(?:vless|vmess|trojan|ss|hysteria2?|tuic)://")),
    ("параметр ключа", re.compile(r"(?i)\b(?:pbk|sid|uuid|token|api[_-]?key|secret|password|пароль)\s*[=:]")),
    ("токен", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AQVN[A-Za-z0-9_-]{20,}"
                        r"|\d{8,10}:[A-Za-z0-9_-]{30,})")),
    ("длинная строка", re.compile(r"[A-Za-z0-9_\-+/=]{32,}")),
]

# Не секреты, а то, что в РФ нельзя рекламировать или надо сопровождать сноской:
# средства обхода блокировок (реклама запрещена) и сервисы Meta (организация признана
# экстремистской). Человек решает сам: замазать, вырезать, поставить сноску. Ловим
# и на экране (scan), и в речи (flags) — на ролике 04.09 слово «VPN» было только на
# слайде, а «Instagram» — только голосом.
RF_RES = [
    ("РФ: обход блокировок", re.compile(
        r"(?i)(?<![\w])(?:vpn|впн|v2ray\w*|nekobox|hiddify|happ|karing|outline|wireguard|amnezia\w*"
        r"|tor\s*browser|прокси|proxy)(?![\w])")),
    ("РФ: Meta", re.compile(r"(?i)(?<![\w])(?:instagram\w*|инстаграм\w*|инсту|инсте|фейсбук\w*|facebook\w*)"
                            r"(?![\w])|(?<![\w])Meta(?![\w])")),
]
SWEAR_RE = re.compile(r"(?i)(?<![\w])(?:бля\w*|хуй\w*|хуе\w*|хуё\w*|пизд\w*|еба\w*|ёба\w*|ебл\w*|"
                      r"\w*ебан\w*|сук[аи]\w*|муда\w*|залуп\w*)(?![\w])")


def luhn_ok(digits):
    """Контрольная цифра номера карты. Отсекает заглушку «1234 1234 1234 1234» в поле
    оплаты и склейки дат из списка файлов — на живом эфире это были все ложные «карты»."""
    if not 13 <= len(digits) <= 19 or len(set(digits)) < 2:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def mask(s):
    """В отчёт и в консоль секрет целиком не попадает: начало и конец, середина — звёздочки."""
    s = s.strip()
    return s if len(s) <= 6 else s[:3] + "*" * min(12, len(s) - 6) + s[-3:]


def cmd_scan(args):
    """
    OCR экрана кадр в секунду — поиск секретов, которые мелькают на несколько секунд.

    sheet берёт по три кадра на кусок и честно пропускает то, что было на экране
    полминуты: на эфире 21.09 так проскочили полные ключи подписки в терминале
    (40 секунд) и хвост карты со сроком в приложении банка. Звук об этом молчал.
    Распознавание — встроенный в Windows движок (tools/ocr_frames.ps1), ставить
    ничего не нужно. Результат — заготовки "blur" с координатами и интервалом:
    перенести в pieces.json, проверить глазами через glue --only.
    """
    if os.name != "nt" or not shutil.which("powershell"):
        die("scan работает на Windows (встроенный OCR). На другой ОС — sheet и глаза")
    if args.pieces:
        spans = [(p.get("n", 0), p["start"], p["end"])
                 for p in load_pieces(args.pieces) if p["kind"] == "clean"]
    else:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
             args.src], capture_output=True, text=True, check=True).stdout.strip())
        spans = [(0, 0.0, dur)]
    if args.range:
        a, b = (float(x) for x in args.range.split("-"))
        spans = [(n, max(s, a), min(e, b)) for n, s, e in spans if e > a and s < b]
    ps1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_frames.ps1")
    tmp = tempfile.mkdtemp(prefix="webinar_scan_")
    try:
        frames = {}                    # имя кадра -> (кусок, секунда исходника)
        k = 0
        for n, s, e in spans:
            first = k
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}",
                            "-i", args.src, "-vf", f"fps=1/{args.every}", "-start_number",
                            str(first), os.path.join(tmp, "f%06d.png")], check=True)
            k = len([x for x in os.listdir(tmp) if x.endswith(".png")])
            for i in range(first, k):
                frames[f"f{i:06d}.png"] = (n, s + (i - first) * args.every)
        print(f"кадров на распознавание: {len(frames)} (~{len(frames) * 0.5 / 60:.0f} мин)")
        out_json = os.path.join(tmp, "ocr.json")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1,
                        "-Dir", tmp, "-Out", out_json, "-Lang", args.lang], check=True,
                       stdout=subprocess.DEVNULL)
        ocr = json.load(open(out_json, encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # находки -> интервалы: одна и та же находка на соседних кадрах = один интервал
    hits = {}
    for item in ocr:
        n, t = frames.get(item["file"], (0, 0))
        for ln in item.get("lines") or []:
            for kind, rx in SECRET_RES + RF_RES:
                m = rx.search(ln.get("text") or "")
                if m and kind == "карта" and not luhn_ok(re.sub(r"\D", "", m.group(0))):
                    continue
                if m:
                    hits.setdefault((n, kind), []).append((t, ln["box"], m.group(0)))
                    break
    found, pad, gap = [], 12, args.every * 2.5
    for (n, kind), lst in sorted(hits.items()):
        lst.sort()
        group = [lst[0]]
        for h in lst[1:] + [None]:
            if h is not None and h[0] - group[-1][0] <= gap:
                group.append(h)
                continue
            xs = [b[0] for _, b, _ in group]; ys = [b[1] for _, b, _ in group]
            x2 = [b[0] + b[2] for _, b, _ in group]; y2 = [b[1] + b[3] for _, b, _ in group]
            x, y = max(0, min(xs) - pad), max(0, min(ys) - pad)
            box = [x, y, max(x2) + pad - x, max(y2) + pad - y,
                   round(group[0][0] - 0.5, 1), round(group[-1][0] + args.every + 0.5, 1)]
            found.append({"piece": n, "kind": kind, "blur": box, "sample": mask(group[0][2]),
                          "frames": len(group)})
            group = [h] if h else []

    json.dump(found, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for f in found:
        b = f["blur"]
        print(f"  кусок {f['piece']:03d}  {mmss(b[4])}-{mmss(b[5])}  {f['kind']:<15} {f['sample']:<22} "
              f"blur {b}")
    print(f"\nнаходок {len(found)} -> {args.out}. Каждую проверить кадром и перенести нужные "
          f"в \"blur\" куска; координаты — пиксели исходника, время — секунды исходника.")


def cmd_beep(args):
    """
    Запикать слова в ГОТОВОМ ролике — видео не трогаем, пересобираем только звук.

    Нужна расшифровка готового файла с пословными таймингами (transcribe.py --words
    по результату, а не по исходнику: после склейки и ускорения время другое).
    Параметры подобраны на живом эфире: 1 кГц и 0.035 — слышно, что слово закрыто,
    но не режет уши (0.2 и 0.07 забраковали как громкие); длина писка — само слово,
    не дольше 0.45 с, чтобы не съедать соседние. Писк — косметика: смысл фразы он
    не меняет, если сказанное нельзя публиковать — кусок надо вырезать.
    """
    words, dur = load_words(args.transcript)
    stems = [w.strip().lower() for w in args.words.split(",") if w.strip()]
    wins = []
    for s, e, text in words:
        low = re.sub(r"[^\w]", "", text.lower())
        if any(low.startswith(st) for st in stems):
            a, b = max(0.0, s - 0.02), min(e, s + args.max_len)
            wins.append((a, max(b, a + 0.1), text))
    if not wins:
        print("совпадений нет — файл не пересобираю")
        return
    ch = int(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
         "-of", "csv=p=0", args.src], capture_output=True, text=True, check=True).stdout.strip() or 2)
    cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b, _ in wins)
    tone = f"{args.level}*sin(2*PI*{args.freq}*t)*gt({cond},0)"
    graph = (f"[0:a]volume=0:enable='{cond}'[m];"
             f"aevalsrc=exprs='{'|'.join([tone] * ch)}':s=48000:d={dur + 1:.3f}[b];"
             f"[m][b]amix=inputs=2:normalize=0:duration=first[a]")
    tmp = tempfile.mkdtemp(prefix="webinar_beep_")
    try:
        gfile = os.path.join(tmp, "graph.txt")        # сотни окон не лезут в командную строку
        open(gfile, "w", encoding="utf-8").write(graph)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", args.src, "-/filter_complex", gfile,
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                        "-ar", "48000", "-movflags", "+faststart", args.out], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for a, b, text in wins:
        print(f"  {mmss(a)}  {text}")
    print(f"\nзапикано {len(wins)} -> {args.out}. Послушать каждое место: писк должен закрывать "
          f"слово целиком и не задевать соседние.")


def src_time(v):
    """'44:04', '1:48:31' или число секунд -> секунды."""
    if isinstance(v, (int, float)):
        return float(v)
    return sum(float(x) * 60 ** i for i, x in enumerate(reversed(str(v).strip().split(":"))))


def cmd_clip(args):
    """
    Тематический ролик из длинной записи: куски исходника в ЗАДАННОМ порядке.

    Ответ на вопрос в уроке обычно размазан: определение в начале, пример через
    час, вывод в середине. Сценарий (--spec) перечисляет куски так, как их надо
    смотреть, а не так, как они шли. Границы подгоняются в паузы между словами,
    паузы длиннее --gap внутри куска ужимаются до --after + --before.

    --after больше --before не случайно: у GigaAM конец слова — момент, когда
    модель выдала слово, а не момент, когда звук затих. С запасом 0.2 с после
    слова на ролике 04.09 срезались хвосты «тебе придётся…» и «…клоду»; 0.4 с — нет.

    Всё в сценарии — во времени исходника. clip переводит главы, сноски и окно
    заставки во время ролика (с учётом ужатых пауз и --speed) и пишет plan.json
    для dress. Замазка из сценария уходит в манифест — её делает glue по исходнику.
    """
    words, _ = load_words(args.transcript)
    spec = json.load(open(args.spec, encoding="utf-8"))
    blur = [[*b[:4], *(src_time(x) for x in b[4:6])] for b in spec.get("blur") or []]
    pieces = []
    for k, part in enumerate(spec["parts"], 1):
        s = round(snap(src_time(part["start"]), "trash", "clean", words), 3)
        e = round(snap(src_time(part["end"]), "clean", "trash", words), 3)
        if e <= s:
            die(f"часть {k}: конец {part['end']} не позже начала {part['start']}")
        inside = [w for w in words if w[0] >= s - 0.05 and w[1] <= e + 0.05]
        print(f"{k}. [{stamp(s)}–{stamp(e)}, {e - s:.1f} с] {part.get('title', '')}\n   "
              + " ".join(w[2] for w in inside) + "\n")
        cuts = [s]
        if args.gap > 0:
            for p, q in zip(inside, inside[1:]):
                a, b = p[1] + args.after, q[0] - args.before
                if q[0] - p[1] > args.gap and b > a:
                    cuts += [a, b]
        cuts.append(e)
        for a, b in zip(cuts[::2], cuts[1::2]):
            n = len(pieces) + 1
            pieces.append({"n": n, "part": k, "kind": "clean", "title": part.get("title") or f"часть {k}",
                           "file": f"{n:03d}_{slug(part.get('title') or str(k))}_{stamp(a)}_{stamp(b)}",
                           "start": round(a, 3), "end": round(b, 3), **({"blur": blur} if blur else {})})

    t = 0.0
    for p in pieces:
        p["out"] = round(t, 3)
        t += (p["end"] - p["start"]) / args.speed

    def to_out(v):
        x = src_time(v)
        for p in pieces:
            if p["start"] - 0.05 <= x <= p["end"] + 0.05:
                return p["out"] + max(0.0, x - p["start"]) / args.speed
        # время попало в ужатую паузу — берём ближайший следующий кусок
        after = [p for p in pieces if 0 <= p["start"] - x <= args.gap * 3]
        if not after:
            die(f"время {v} не попадает ни в один кусок сценария")
        return min(after, key=lambda p: p["start"] - x)["out"]

    plan = {"duration": round(t, 3), "titles": [], "notes": [], "cover": []}
    for c in spec.get("chapters") or []:
        # text — на плашку (капс, коротко), desc — в главы описания (обычным регистром)
        plan["titles"].append({"t": round(to_out(c["at"]), 2), "text": c["text"],
                               "desc": c.get("desc") or c["text"]})
    plan["titles"].sort(key=lambda c: c["t"])
    for nt in spec.get("notes") or []:
        a = to_out(nt["at"])
        plan["notes"].append({"from": round(a, 2), "to": round(min(t, a + nt.get("dur", 6)), 2),
                              "text": nt["text"]})
    for cv in spec.get("cover") or []:
        if "parts" in cv:
            ps = [p for p in pieces if p["part"] in cv["parts"]]
            a, b = ps[0]["out"], ps[-1]["out"] + (ps[-1]["end"] - ps[-1]["start"]) / args.speed
        else:
            a, b = to_out(cv["from"]), to_out(cv["to"])
        plan["cover"].append({"from": round(a, 2), "to": round(b, 2)})

    os.makedirs(args.out, exist_ok=True)
    json.dump(pieces, open(os.path.join(args.out, "pieces.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(plan, open(os.path.join(args.out, "plan.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    src_len = sum(src_time(x["end"]) - src_time(x["start"]) for x in spec["parts"])
    print(f"частей {len(spec['parts'])}, кусков {len(pieces)}; исходник {src_len:.0f} с -> "
          f"после пауз {sum(p['end'] - p['start'] for p in pieces):.0f} с -> при {args.speed} — "
          f"{t:.0f} с ({mmss(t)})")
    if plan["titles"]:
        print("\nглавы для описания YouTube:")
        for c in plan["titles"]:
            print(f"  {mmss(c['t'])} {c['desc']}")
    for c in plan["cover"]:
        print(f"заставка: {c['from']:.2f}–{c['to']:.2f} с")
    for nt in plan["notes"]:
        print(f"сноска: {nt['from']:.2f}–{nt['to']:.2f} с — {nt['text']}")
    print(f"\n-> {args.out}/pieces.json (для glue без --drafts), {args.out}/plan.json (для dress)")


def box_arg(s, n=4):
    v = [float(x) for x in s.split(",")]
    if len(v) != n:
        die(f"ожидалось {n} чисел через запятую: {s}")
    return v


def ff_path(p):
    """Путь для параметра фильтра ffmpeg: прямые слэши, двоеточие экранировано."""
    return os.path.abspath(p).replace("\\", "/").replace(":", "\\:")


def cmd_dress(args):
    """
    Оформление готового ролика одним проходом кодера.

    Заставка (--cover) — картинка в области экрана на время, когда на экране
    ничего полезного (на ролике 04.09 — погода в ChatGPT, пока шла речь про
    агентов). Вписывается в область демонстрации, панель задач и камеры остаются —
    выглядит так, будто она открыта у автора на экране.

    Плашки-главы (titles из plan.json) — крупный заголовок «о чём сейчас» в пустой
    полосе кадра. Тот, кто перематывает, видит тему и не уходит; формулировки те же,
    что в главах описания. Текст идёт через textfile — двоеточия, кавычки и запятые
    в заголовке не ломают фильтр.

    Сноски (notes) — мелкая плашка внизу: «*Instagram принадлежит Meta…».
    Замазка (--blur x,y,w,h,от,до) — в пикселях и секундах ГОТОВОГО ролика.
    """
    plan = json.load(open(args.plan, encoding="utf-8")) if args.plan else {}
    w, h, dur = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "default=nw=1:nk=1", args.src],
        capture_output=True, text=True, check=True).stdout.split()[:3]
    W, H, dur = int(w), int(h), float(dur)
    font = ff_path(args.font) if args.font else font_file()
    if not font:
        die("нет шрифта — укажи --font")
    tmp = tempfile.mkdtemp(prefix="webinar_dress_")
    try:
        g, v, k, inputs = [], "[0:v]", 0, ["-i", args.src]

        def step(expr):
            nonlocal v, k
            g.append(f"{v}{expr}[d{k}]")
            v, k = f"[d{k}]", k + 1

        def win(a, b):
            return f"between(t,{a:.3f},{b:.3f})"

        covers = plan.get("cover") or []
        if args.cover_time:
            covers = [dict(zip(("from", "to"), box_arg(x, 2))) for x in args.cover_time]
        if args.cover and covers:
            if not args.cover_box:
                die("для заставки нужна --cover-box x,y,w,h — область экрана в кадре")
            x, y, cw, ch = (int(q) for q in box_arg(args.cover_box))
            inputs += ["-loop", "1", "-i", args.cover]
            g.append(f"[1:v]scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch}[cov]")
            g.append(f"{v}[cov]overlay={x}:{y}:shortest=1:enable='"
                     + "+".join(win(c["from"], c["to"]) for c in covers) + f"'[d{k}]")
            v, k = f"[d{k}]", k + 1

        for j, s in enumerate(args.blur or []):
            x, y, bw, bh, a, b = box_arg(s, 6)
            x, y, bw, bh = int(x), int(y), int(bw), int(bh)
            sw, sh = max(2, bw // 20 // 2 * 2), max(2, bh // 20 // 2 * 2)
            g.append(f"{v}split[bm{j}][bc{j}]")
            g.append(f"[bc{j}]crop={bw}:{bh}:{x}:{y},scale={sw}:{sh}:flags=area,"
                     f"scale={bw}:{bh}:flags=bicubic[bz{j}]")
            g.append(f"[bm{j}][bz{j}]overlay={x}:{y}:enable='{win(a, b)}'[d{k}]")
            v, k = f"[d{k}]", k + 1

        titles = plan.get("titles") or []
        if titles:
            if not args.title_box:
                die("для плашек-глав нужна --title-box x,y,w,h — пустая полоса кадра")
            x, y, tw, th = (int(q) for q in box_arg(args.title_box))
            step(f"drawbox=x={x}:y={y}:w={tw}:h={th}:color=0x{args.plate}@1:t=fill:"
                 f"enable='gte(t,{titles[0]['t']:.3f})'")
            for i, c in enumerate(titles):
                a = c["t"]
                b = titles[i + 1]["t"] if i + 1 < len(titles) else dur + 1
                tf = os.path.join(tmp, f"title_{i:02d}.txt")
                open(tf, "w", encoding="utf-8").write(c["text"])
                # кегль под ширину: капс жирного гротеска ~0.74 em на знак
                fs = int(min(th * 0.56, tw * 0.9 / (max(1, len(c["text"])) * 0.74)))
                base = (f"drawtext=fontfile='{font}':textfile='{ff_path(tf)}':fontsize={fs}:"
                        f"enable='{win(a, b)}':")
                cx, cy = f"{x}+({tw}-text_w)/2", f"{y}+({th}-text_h)/2"
                if args.glitch:          # фирменная обводка: красный и голубой сдвиг
                    step(base + f"fontcolor=0xE63B2E:x={cx}-3:y={cy}-3")
                    step(base + f"fontcolor=0x2EC4F0:x={cx}+3:y={cy}+3")
                step(base + f"fontcolor=0x{args.ink}:x={cx}:y={cy}")

        nx, ny = (int(q) for q in box_arg(args.note_pos, 2)) if args.note_pos else (40, H - 50)
        for i, nt in enumerate(plan.get("notes") or []):
            tf = os.path.join(tmp, f"note_{i:02d}.txt")
            open(tf, "w", encoding="utf-8").write(nt["text"])
            step(f"drawtext=fontfile='{font}':textfile='{ff_path(tf)}':fontsize={args.note_size}:"
                 f"fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=10:x={nx}:y={ny}:"
                 f"enable='{win(nt['from'], nt['to'])}'")

        if not g:
            die("нечего делать: нет ни заставки, ни замазки, ни глав, ни сносок")
        gfile = os.path.join(tmp, "graph.txt")
        open(gfile, "w", encoding="utf-8").write(";".join(g))
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-/filter_complex", gfile,
                        "-map", v, "-map", "0:a?", "-c:v", "libx264", "-crf", str(args.crf),
                        "-preset", args.preset, "-pix_fmt", "yuv420p", "-c:a", "copy",
                        "-movflags", "+faststart", args.out], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not os.path.isfile(args.out) or os.path.getsize(args.out) == 0:
        die("оформление не записало файл")
    print(f"готово: {args.out} — заставка {len(covers) if args.cover else 0}, замазок "
          f"{len(args.blur or [])}, глав {len(titles)}, сносок {len(plan.get('notes') or [])}. "
          f"Проверить кадрами на каждой смене главы.")


def cmd_flags(args):
    """
    Речь готового ролика: что в РФ требует решения (VPN, Instagram/Facebook) и мат.
    Экран проверяет scan — те же слова он ловит в OCR. Решение за человеком:
    сноска (dress), писк (beep), вырезать.
    """
    words, _ = load_words(args.transcript)
    hits = 0
    for s, e, text in words:
        for kind, rx in RF_RES + [("мат", SWEAR_RE)]:
            if rx.search(text):
                print(f"  {mmss(s)}  {kind:<22} {text}")
                hits += 1
                break
    print(f"\nнаходок {hits}" + (" — каждое место решить: сноска, писк или вырезать" if hits else ""))


def cmd_glue(args):
    pieces = {p["n"]: p for p in load_pieces(args.pieces) if "n" in p}
    if not pieces:
        die("в манифесте нет номеров — сначала cut")
    status = {}
    if args.drafts:
        for name in os.listdir(args.drafts):
            m = NAME_RE.match(name)
            if m and name.endswith(".mp4"):
                status[int(m.group(1))] = KIND_OF[m.group(2)]
    else:
        # манифест от clip: черновиков нет, человек утверждает сам ролик целиком
        status = {n: p["kind"] for n, p in pieces.items()}
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
        parts, sounds = [], []
        for i, p in enumerate(keep):
            out = os.path.join(tmp, f"{i:03d}.mp4")
            d = p["end"] - p["start"]
            # Ускорение: atempo меняет темп без сдвига тона (голос не «мультяшный»),
            # видео — setpts. На вебинаре 5-7 % зритель не замечает, а ролик короче.
            graph = []
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
            frames = max(1, round(d / args.speed * args.fps))
            vcmd = [
                # -t ДО -i: ограничение на вход. После -i ffmpeg режет выход по
                # времени исходника и молча дотягивает ускоренный кусок до прежней
                # длины повтором кадров и тишиной — ускорение пропадает.
                "ffmpeg", "-v", "error", "-y", "-ss", f"{p['start']:.3f}", "-t", f"{d:.3f}",
                "-i", args.src]
            if graph:
                vcmd += ["-filter_complex", ";".join(graph)]
            vcmd += ["-map", vmap, "-an", "-frames:v", str(frames), "-r", str(args.fps),
                     "-fps_mode", "cfr", "-c:v", "libx264", "-preset", args.preset,
                     "-crf", str(args.crf), "-pix_fmt", "yuv420p", out]
            subprocess.run(vcmd, check=True)
            # Звук — отдельно, без сжатия, и ровно по длине видео куска. AAC кусками
            # щёлкает на каждом стыке (у каждого куска свой «разгон» кодера), а разница
            # длин звука и видео в долю кадра на шестидесяти кусках набегает в секунду
            # рассинхрона. Поэтому: PCM на кусок, подрезка под число кадров, одна
            # кодировка в AAC на весь ролик.
            got = int(subprocess.run(
                ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0",
                 "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", out],
                capture_output=True, text=True, check=True).stdout.strip() or frames)
            length = got / args.fps
            fade = min(0.03, d / 4)          # короткий фейд на стыке — без него склейка щёлкает
            tempo = f",atempo={args.speed}" if args.speed != 1 else ""
            wav = os.path.join(tmp, f"{i:03d}.wav")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", f"{p['start']:.3f}", "-t", f"{d:.3f}",
                 "-i", args.src, "-vn", "-af",
                 f"afade=t=in:d={fade:.3f},afade=t=out:st={d - fade:.3f}:d={fade:.3f}{tempo},"
                 f"aresample=48000,apad,atrim=end={length:.6f}",
                 "-ac", "2", "-c:a", "pcm_s16le", wav], check=True)
            parts.append(out)
            sounds.append(wav)
            print(f"  [{i + 1}/{len(keep)}] {p.get('file') or p['title']}")
        lists = []
        for name, files in (("v.txt", parts), ("a.txt", sounds)):
            lst = os.path.join(tmp, name)
            with open(lst, "w", encoding="utf-8") as f:
                f.writelines(f"file '{x}'\n" for x in files)
            lists.append(lst)
        video, audio = os.path.join(tmp, "video.mp4"), os.path.join(tmp, "audio.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lists[0],
                        "-c", "copy", video], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lists[1],
                        "-c", "copy", audio], check=True)
        # adeclick — по желанию: убирает щелчки во рту и в микрофоне, а не на стыках
        # (стыки уже чистые); проход медленный, на час записи — несколько минут
        af = ["-af", "adeclick"] if args.declick else []
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-i", audio, "-map", "0:v",
                        "-map", "1:a", "-c:v", "copy", *af, "-c:a", "aac", "-b:a", "160k",
                        "-ar", "48000", "-movflags", "+faststart", args.out], check=True)
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
    g.add_argument("--drafts", help="папка черновиков (статус из имён); без неё — все clean "
                                    "из манифеста по номерам, как пишет clip")
    g.add_argument("--out", required=True)
    g.add_argument("--fps", type=int, default=25)
    g.add_argument("--crf", type=int, default=20)
    g.add_argument("--preset", default="veryfast")
    g.add_argument("--speed", type=float, default=1.0,
                   help="ускорить итог, напр. 1.06 = +6 %% (темп голоса сохраняется)")
    g.add_argument("--only", type=int, default=0,
                   help="собрать только кусок N — предпросмотр замазки")
    g.add_argument("--declick", action="store_true",
                   help="adeclick по всему звуку: щелчки во рту и микрофона (медленно)")
    sc = sub.add_parser("scan", help="OCR кадр в секунду: номера карт, ключи, почты на экране")
    sc.add_argument("--src", required=True)
    sc.add_argument("--pieces", help="только ЧИСТ-куски манифеста; без него — весь файл")
    sc.add_argument("--out", required=True, help="json с заготовками blur")
    sc.add_argument("--every", type=float, default=1.0, help="кадр раз в N секунд")
    sc.add_argument("--range", help="только отрезок исходника, секунды: 180-260")
    sc.add_argument("--lang", default="ru", help="язык OCR (ru, en-US)")
    b = sub.add_parser("beep", help="запикать слова в готовом ролике (видео не трогаем)")
    b.add_argument("--src", required=True, help="готовый ролик")
    b.add_argument("--transcript", required=True, help="transcribe.py --words ПО ГОТОВОМУ ролику")
    b.add_argument("--words", required=True, help="начала слов через запятую, без учёта регистра")
    b.add_argument("--out", required=True)
    b.add_argument("--freq", type=float, default=1000.0)
    b.add_argument("--level", type=float, default=0.035, help="громкость писка, 0..1")
    b.add_argument("--max-len", type=float, default=0.45, help="писк не длиннее, сек")
    cl = sub.add_parser("clip", help="тематический ролик: куски исходника в заданном порядке")
    cl.add_argument("--transcript", required=True, help="расшифровка ИСХОДНИКА с --words")
    cl.add_argument("--spec", required=True, help="сценарий: parts, chapters, notes, cover, blur")
    cl.add_argument("--out", required=True, help="папка ролика: pieces.json + plan.json")
    cl.add_argument("--speed", type=float, default=1.0, help="то же, что будет у glue")
    cl.add_argument("--gap", type=float, default=1.2, help="ужимать паузы длиннее, сек (0 — нет)")
    cl.add_argument("--after", type=float, default=0.4, help="оставить после слова, сек")
    cl.add_argument("--before", type=float, default=0.2, help="оставить перед словом, сек")
    dr = sub.add_parser("dress", help="оформление готового ролика: заставка, главы, сноски, замазка")
    dr.add_argument("--src", required=True, help="готовый ролик (glue)")
    dr.add_argument("--plan", help="plan.json от clip: titles, notes, cover")
    dr.add_argument("--out", required=True)
    dr.add_argument("--cover", help="картинка-заставка поверх области экрана")
    dr.add_argument("--cover-box", help="x,y,w,h области экрана в кадре")
    dr.add_argument("--cover-time", action="append", help="от,до (сек ролика), вместо cover из плана")
    dr.add_argument("--title-box", help="x,y,w,h полосы под плашки-главы")
    dr.add_argument("--font", help="шрифт плашек и сносок (жирный гротеск с кириллицей)")
    dr.add_argument("--plate", default="FFC400", help="цвет плашки, hex")
    dr.add_argument("--ink", default="151515", help="цвет текста плашки, hex")
    dr.add_argument("--glitch", action="store_true", help="красно-голубой сдвиг под текстом")
    dr.add_argument("--note-pos", help="x,y сноски (по умолчанию слева внизу)")
    dr.add_argument("--note-size", type=int, default=22)
    dr.add_argument("--blur", action="append", help="x,y,w,h,от,до — пиксели и секунды РОЛИКА")
    dr.add_argument("--crf", type=int, default=20)
    dr.add_argument("--preset", default="veryfast")
    fl = sub.add_parser("flags", help="речь ролика: VPN, Instagram/Facebook, мат — с таймкодами")
    fl.add_argument("--transcript", required=True, help="transcribe.py --words ПО ГОТОВОМУ ролику")
    a = p.parse_args()
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            die(f"{tool} не найден в PATH")
    {"lenta": cmd_lenta, "sheet": cmd_sheet, "cut": cmd_cut, "glue": cmd_glue,
     "scan": cmd_scan, "beep": cmd_beep, "clip": cmd_clip, "dress": cmd_dress,
     "flags": cmd_flags}[a.cmd](a)


if __name__ == "__main__":
    main()
