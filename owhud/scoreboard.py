"""Reads the post-game stats off a screenshot of Overwatch's scoreboard.

The Stats page takes a screenshot (dropped, pasted or picked): its text is
read with Windows' built-in text recognition (Windows.Media.Ocr, nothing
extra to install). That finds the players' rows by their names, but it skips
short numbers standing on their own ("0", "5"), so each row's numbers are
then cut out of the picture one by one and read again in a made-up line of
words ("max 5 max 0 max 11,234 ..."), where they're read well. Two tables
(or a table either side of the screen) are the two teams.

Windows only; elsewhere the stats are typed in.

    python -m owhud.scoreboard screenshot.png    read a saved screenshot
"""

import asyncio
import io
import re
import statistics
import sys

COLS = ("kills", "assists", "deaths", "damage", "healing")
HEADERS = {"E": "kills", "A": "assists", "D": "deaths", "DMG": "damage", "H": "healing"}
# Letters the text recognition sometimes returns for digits.
_DIGITS = str.maketrans({"O": "0", "o": "0", "Q": "0", "l": "1", "I": "1", "|": "1", "i": "1", "S": "5",
                         "B": "8", "Z": "2", "z": "2", "g": "9"})
MAX_SIDE = 2600   # (the most Windows' text recognition takes)


class WindowsOcr:
    """Windows.Media.Ocr through the winrt packages."""

    def __init__(self):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        engine = OcrEngine.try_create_from_language(Language("en-US"))
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("Windows text recognition isn't installed for any of your languages")
        self.engine = engine

    def words(self, image):
        """[(text, x, y, w, h), ...] in the image's pixels."""
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter
        rgba = image.convert("RGBA")
        writer = DataWriter()
        writer.write_bytes(rgba.tobytes("raw", "BGRA"))
        buffer = writer.detach_buffer()
        try:
            bitmap = SoftwareBitmap.create_copy_from_buffer(buffer, BitmapPixelFormat.BGRA8, rgba.width, rgba.height)
        except TypeError:
            bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, rgba.width, rgba.height, BitmapAlphaMode.PREMULTIPLIED)
            bitmap.copy_from_buffer(buffer)

        async def run():
            return await self.engine.recognize_async(bitmap)

        result = asyncio.run(run())
        out = []
        for line in result.lines:
            for word in line.words:
                r = word.bounding_rect
                out.append((word.text, r.x, r.y, r.width, r.height))
        return out


_ocr = None


def ocr_words(image):
    """The screenshot's words, [(text, x, y, w, h)] in its own pixels. It's read
    twice (as it is, and as dark text on white, enlarged as far as Windows
    allows); the reading with more player rows is kept."""
    global _ocr
    from PIL import ImageOps
    if _ocr is None:
        _ocr = WindowsOcr()
    scale = min(2.0, MAX_SIDE / max(image.width, image.height))
    big = image.convert("L").resize((int(image.width * scale), int(image.height * scale)))
    best = []
    for variant in (big, ImageOps.invert(ImageOps.autocontrast(big))):
        words = [(t, x / scale, y / scale, w / scale, h / scale) for t, x, y, w, h in _ocr.words(variant)]
        if len(_candidates(words)) > len(_candidates(best)):
            best = words
    return best


def _candidates(words, any_line=False):
    """The lines that could be a player's: a name, then at least one number
    (any_line: or no number read at all, since lone digits are often missed)."""
    out = []
    if not words:
        return out
    tall = statistics.median(w[4] for w in words) or 1
    for line in _lines(words):
        for part in _split_rows(line, tall):
            toks = _tokens(part)
            while toks and number(toks[0][0]) is not None:
                toks = toks[1:]       # (a number before the name: Overwatch's ultimate charge)
            first = next((i for i, t in enumerate(toks) if number(t[0]) is not None), None)
            name = [t for t in (toks if first is None and any_line else toks[:first or 0]) if not t[0].endswith("%")]
            if name:
                h = max(t[4] for t in toks)
                out.append({"name": " ".join(t[0] for t in name), "y": line["y"], "h": h, "x": name[0][1],
                            "name_end": name[-1][1] + name[-1][3], "numbers": first is not None})
    return out


# --- each row's numbers, cut out and read again ------------------------------

LABEL = "max"     # (letters the recognition doesn't take for digits)


def _ink(strip):
    """The strip's text as a mask (255 = text): whatever differs a lot from
    the strip's background, lighter or darker."""
    bg = _background(strip)
    return strip.convert("L").point(lambda v: 255 if abs(v - bg) > 70 else 0)


def _background(strip):
    """The strip's commonest brightness (its median)."""
    hist = strip.convert("L").histogram()
    half, acc = sum(hist) / 2, 0
    for v, n in enumerate(hist):
        acc += n
        if acc >= half:
            return v
    return 0


def blobs(strip, tall):
    """The strip's separate words (numbers): [(x0, x1)], left to right. Ink
    columns closer than ~half a letter's height belong together."""
    mask = _ink(strip)
    w, h = mask.size
    px = mask.load()
    cols = [sum(1 for y in range(h) if px[x, y]) for x in range(w)]
    out, start, gap = [], None, 0
    join = max(3, tall * 0.45)
    for x, c in enumerate(cols + [0] * int(join + 2)):
        if c:
            if start is None:
                start = x
            gap, end = 0, x
        elif start is not None:
            gap += 1
            if gap > join:
                ink = sum(cols[start:end + 1])
                # (text, not a block of another colour: not too wide, not solid)
                if ink >= 6 and 2 <= end - start <= tall * 8 and ink < 0.7 * h * (end + 1 - start):
                    out.append((start, end + 1))
                start = None
    return out


def pieces(strip, spans):
    """Each number cut out of the strip: black on white, 48 pixels high."""
    from PIL import ImageOps
    mask, bg = _ink(strip), _background(strip)
    contrast = strip.convert("L").point(lambda v: min(255, abs(v - bg) * 255 // 110))
    out = []
    for x0, x1 in spans:
        box = mask.crop((x0, 0, x1, mask.height)).getbbox() or (0, 0, x1 - x0, mask.height)
        crop = contrast.crop((x0, max(0, box[1] - 2), x1, min(mask.height, box[3] + 2)))
        scale = 48 / max(1, crop.height)
        out.append(ImageOps.invert(crop.resize((max(1, int(crop.width * scale)), 48))))
    return out


TIMES = 3     # (a lone digit is written 3 times: "5" -> "555", read like any number)


def single(piece):
    return piece.width < piece.height * 0.75


# Ways to write a number for the recognition, tried in turn until one reads:
# (written TIMES times?, height, letters thickened?). A lone digit always is.
VARIANTS = [(False, 48, False), (True, 48, False), (True, 34, False), (True, 48, True), (True, 64, False)]


def variant(piece, n):
    """The piece as VARIANTS[n] writes it, and whether it's repeated."""
    from PIL import ImageFilter
    repeat, height, thick = VARIANTS[n]
    repeat = repeat or single(piece)
    if height != piece.height:
        piece = piece.resize((max(1, piece.width * height // piece.height), height))
    if thick:
        piece = piece.filter(ImageFilter.MinFilter(3))
    return piece, repeat


def compose(piece, repeat=None):
    """One number between two label words ("max 5 max"), so it's read as
    part of a line rather than a lone digit. Even so, Windows' text
    recognition won't read a single digit, so one is written TIMES times."""
    from PIL import Image, ImageDraw, ImageFont
    from .config import WEB_DIR
    import os
    H = piece.height
    if single(piece) if repeat is None else repeat:
        step = piece.width + max(2, H // 10)
        many = Image.new("L", (step * (TIMES - 1) + piece.width, H), 255)
        for i in range(TIMES):
            many.paste(piece, (i * step, 0))
        piece = many
    font = ImageFont.truetype(os.path.join(WEB_DIR, "fonts", "Inter.ttf"), int(H * 0.72))
    lw, gap = int(font.getlength(LABEL)), int(H * 0.6)
    img = Image.new("L", (40 + lw + gap + piece.width + gap + lw + 40, H + 60), 255)
    d = ImageDraw.Draw(img)
    d.text((40, 30 + H), LABEL, font=font, fill=0, anchor="ls")
    img.paste(piece, (40 + lw + gap, 30))
    d.text((40 + lw + gap + piece.width + gap, 30 + H), LABEL, font=font, fill=0, anchor="ls")
    return img


def read_number(words, repeated=False):
    """The number in a composed line's words (None: not read). repeated: it
    was written TIMES times; all the copies have to agree."""
    text = "".join(w[0] for w in sorted(words, key=lambda w: w[1])).replace(LABEL, " ").strip()
    text = re.sub(r"^[^\dOoQlI|SBZzg]+|[^\dOoQlI|SBZzg,.]+$", "", text).replace(" ", "")
    if repeated:
        digits = re.sub(r"\D", "", text.translate(_DIGITS))
        n = len(digits) // TIMES
        if not n or len(digits) % TIMES or digits != digits[:n] * TIMES:
            return None
        text = digits[:n]
    return number(text)


debug = []     # (what the last screenshot's numbers were read as)


def image_rows(image, words):
    """The players' rows, read from the picture: [{"name", "y", "x", "nums"}]."""
    rows, used = [], []
    # (lines with numbers first: a line under one, like a player's title, is part of its row)
    for c in sorted(_candidates(words, any_line=True), key=lambda c: not c["numbers"]):
        if any(abs(c["y"] - y) < c["h"] * 1.2 and abs(c["x"] - x) < 200 for y, x in used):
            continue
        used.append((c["y"], c["x"]))
        top, bottom = int(c["y"] - c["h"] * 0.9), int(c["y"] + c["h"] * 0.9)
        left = int(c["name_end"] + c["h"] * 0.3)
        strip = image.crop((left, max(0, top), image.width, min(image.height, bottom)))
        spans = blobs(strip, c["h"])
        # (the row ends at a gap far wider than between its columns: another table)
        for i in range(1, len(spans)):
            if spans[i][0] - spans[i - 1][1] > c["h"] * 9:
                spans = spans[:i]
                break
        if len(spans) < 4:
            continue
        values = []
        for piece in pieces(strip, spans):
            value = None
            for n in range(len(VARIANTS)):
                p, repeat = variant(piece, n)
                if n and repeat and VARIANTS[n - 1][1:] == VARIANTS[n][1:] and single(piece):
                    continue      # (the same as the last try)
                words = _ocr.words(compose(p, repeat))
                value = read_number(words, repeat)
                debug.append(f"{c['name']}: {' '.join(w[0] for w in words)!r} -> {value}")
                if value is not None:
                    break
            values.append(value)
        if sum(v is not None for v in values) < 4:
            continue      # (not a player's row)
        nums = [(v, left + (a + b) / 2) for v, (a, b) in zip(values, spans)]
        rows.append({"name": c["name"], "y": c["y"], "x": c["x"], "nums": nums})
    return rows


def number(text):
    """"12,345" -> 12345; None if it isn't a number."""
    t = (text or "").strip().translate(_DIGITS)
    if not re.fullmatch(r"[\d][\d,.' ]*", t):
        return None
    return int(re.sub(r"\D", "", t))


def _lines(words):
    """Words in lines, top to bottom, each left to right."""
    if not words:
        return []
    tall = statistics.median(w[4] for w in words) or 1
    lines = []
    for w in sorted(words, key=lambda w: w[2] + w[4] / 2):
        yc = w[2] + w[4] / 2
        for line in lines:
            if abs(line["y"] - yc) < tall * 0.6:
                line["words"].append(w)
                break
        else:
            lines.append({"y": yc, "words": [w]})
    for line in lines:
        line["words"].sort(key=lambda w: w[1])
    return lines


def _split_rows(line, tall):
    """A line can be two tables' rows side by side: split it at a big gap
    before a name (words that aren't numbers after numbers)."""
    parts, cur = [], []
    for w in line["words"]:
        if cur and number(w[0]) is None and number(cur[-1][0]) is not None and w[1] - (cur[-1][1] + cur[-1][3]) > tall * 3:
            parts.append(cur)
            cur = []
        cur.append(w)
    return parts + [cur]


def _tokens(words):
    """The line's words with numbers split by the recognition joined up again
    ("12" ",345")."""
    out = []
    for w in words:
        t = w[0]
        if out and number(t) is not None and number(out[-1][0]) is not None and \
                (out[-1][0].endswith((",", ".")) or t.startswith((",", "."))):
            p = out[-1]
            out[-1] = (p[0] + t, p[1], p[2], w[1] + w[3] - p[1], max(p[4], w[4]))
        else:
            out.append(w)
    return out


def rows_of(words):
    """[{"name", "y", "x", "nums": [(value, x_centre)]}] for each line that
    looks like a player's (a name and at least 4 numbers), and the header
    lines ("E A D DMG H MIT") as {"header": {col: x_centre}}."""
    if not words:
        return []
    tall = statistics.median(w[4] for w in words) or 1
    out = []
    for line in _lines(words):
        for part in _split_rows(line, tall):
            toks = _tokens(part)
            labels = {HEADERS[t[0].upper()]: t[1] + t[3] / 2 for t in toks if t[0].upper() in HEADERS}
            if len(labels) >= 4:
                out.append({"header": labels, "y": line["y"], "x": toks[0][1]})
                continue
            while toks and number(toks[0][0]) is not None:
                toks = toks[1:]       # (a number before the name: the ultimate charge)
            nums = [(number(t[0]), t[1] + t[3] / 2) for t in toks if number(t[0]) is not None]
            first = next((i for i, t in enumerate(toks) if number(t[0]) is not None), len(toks))
            name = " ".join(t[0] for t in toks[:first] if not t[0].endswith("%")).strip()   # (not an ult charge)
            if len(nums) >= 4 and name:
                out.append({"name": name, "y": line["y"], "x": toks[0][1], "nums": nums})
    return out


def _columns(rows, header):
    """Each stat's x: from the header if there is one, else from the rows
    with the most numbers (E A D DMG H, then MIT)."""
    if header and len(header) == len(COLS):
        return header
    # The columns the rows' numbers line up in (there can be others: icons
    # read as numbers, MIT); DMG is the one with the biggest numbers that has
    # three columns to its left (E A D) and one to its right (H).
    pts = sorted((x, v) for r in rows for v, x in r["nums"] if v is not None)
    clusters = []
    for x, v in pts:
        if clusters and x - clusters[-1][-1][0] < 20:
            clusters[-1].append((x, v))
        else:
            clusters.append([(x, v)])
    keep = [c for c in clusters if len(c) >= max(2, len(rows) // 2)]
    xs = [statistics.median(x for x, v in c) for c in keep]
    if len(keep) >= len(COLS):
        options = range(3, len(keep) - 1)
        if header and "damage" in header:
            d = min(options, key=lambda i: abs(xs[i] - header["damage"]))
        else:
            d = max(options, key=lambda i: statistics.median(v for x, v in keep[i]))
        return dict(zip(COLS, xs[d - 3:d + 2]))
    counts = [len(r["nums"]) for r in rows]
    most = max(set(counts), key=lambda c: (counts.count(c), c))
    full = [r for r in rows if len(r["nums"]) == most]
    xs = [statistics.median(r["nums"][i][1] for r in full) for i in range(most)]
    cols = dict(zip(COLS, xs))
    cols.update(header or {})
    return cols


def _values(row, cols):
    """The row's numbers by stat, each to the nearest column (None: not read)."""
    xs = sorted(cols.values())
    gap = min((b - a for a, b in zip(xs, xs[1:])), default=200)
    out = dict.fromkeys(COLS)
    for value, x in row["nums"]:
        if value is None:
            continue
        col, cx = min(cols.items(), key=lambda c: abs(c[1] - x))
        if abs(cx - x) < gap * 0.6 and out[col] is None:
            out[col] = value
    return out


def parse(words, width, rows=None):
    """The players on a scoreboard: {"teams": [[player, ...], [player, ...]]},
    each player {"name", "kills"... (None if it couldn't be read)}. The first
    team is the table at the top (or on the left). rows: the rows read from
    the picture (else they're the lines of words with numbers)."""
    found = rows_of(words)
    if rows is not None:
        found = [r for r in found if "header" in r] + rows
    rows = [r for r in found if "nums" in r]
    if not rows:
        return {"teams": [[], []]}
    heads = [r for r in found if "header" in r]
    # Two tables: side by side (the rows start far apart), or one under the other.
    xs = sorted(r["x"] for r in rows)
    split_x = next((b for a, b in zip(xs, xs[1:]) if b - a > width * 0.25), None)
    if split_x is not None:
        groups = [[r for r in rows if r["x"] < split_x], [r for r in rows if r["x"] >= split_x]]
        heads_of = [[h for h in heads if h["x"] < split_x], [h for h in heads if h["x"] >= split_x]]
    else:
        rows.sort(key=lambda r: r["y"])
        second = sorted(h["y"] for h in heads)[1:2]
        if second and any(r["y"] < second[0] for r in rows) and any(r["y"] > second[0] for r in rows):
            groups = [[r for r in rows if r["y"] < second[0]], [r for r in rows if r["y"] > second[0]]]
        else:
            groups = [rows[:5], rows[5:]]
        heads_of = [heads, heads]
    teams = []
    for group, hs in zip(groups, heads_of):
        group = sorted(group, key=lambda r: r["y"])[:5]
        if not group:
            teams.append([])
            continue
        cols = _columns(group, hs[0]["header"] if hs else None)
        teams.append([dict(name=r["name"], **_values(r, cols)) for r in group])
    return {"teams": teams}


def read(image):
    """A screenshot (a PIL image) -> parse()'s result."""
    debug.clear()
    words = ocr_words(image)
    return parse(words, image.width, image_rows(image, words))


def read_bytes(data):
    from PIL import Image
    return read(Image.open(io.BytesIO(data)).convert("RGB"))


def _key(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower().translate(str.maketrans("015", "ois")))


def merge(current, teams):
    """The HUD's stats with a screenshot's read in: {"left": [...], "right": [...]},
    and which team on the screenshot went to which side.

    The screenshot's teams go to the sides whose players' names they match
    (else top/left team -> left). A player goes to the row with their name,
    or the first row without a match; a number that couldn't be read keeps
    what was there."""
    def score(team, players):
        names = {_key(p["name"]) for p in players if p["name"]}
        return sum(1 for p in team if _key(p["name"]) in names)

    a, b = (teams + [[], []])[:2]
    swap = score(a, current["right"]) + score(b, current["left"]) > score(a, current["left"]) + score(b, current["right"])
    by_side = {"left": b if swap else a, "right": a if swap else b}
    out, missing = {}, []
    for side, team in by_side.items():
        rows = [dict(p) for p in current[side]]
        if not team:
            out[side] = rows
            continue
        free = list(range(len(rows)))
        place = {}
        for i, p in enumerate(team):
            j = next((j for j in free if rows[j]["name"] and _key(rows[j]["name"]) == _key(p["name"])), None)
            if j is not None:
                place[i] = j
                free.remove(j)
        for i, p in enumerate(team):
            if i not in place and free:
                # (a slot nobody's name matched: keep its typed name only if empty)
                j = next((j for j in free if not rows[j]["name"]), free[0])
                place[i] = j
                free.remove(j)
                rows[j]["name"], rows[j]["hero"] = p["name"], ""
        for i, j in place.items():
            for k in COLS:
                if team[i][k] is None:
                    missing.append(f"{rows[j]['name']} {k}")
                else:
                    rows[j][k] = team[i][k]
        out[side] = rows
    return out, {"swapped": swap, "players": sum(len(t) for t in by_side.values()), "missing": missing}


SAMPLE = [[("SAINT ONE", 22, 5, 6, 11234, 300, 4021), ("Saint2", 10, 0, 4, 5000, 9800, 0), ("Saint3", 1, 2, 3, 400, 0, 0),
           ("Saint4", 0, 0, 0, 0, 0, 0), ("Saint5", 3, 3, 3, 3, 3, 3)],
          [("HOUND", 7, 8, 9, 12000, 0, 100), ("Hound2", 1, 1, 1, 1, 1, 1), ("Hound3", 15, 4, 2, 9876, 120, 5555),
           ("Hound4", 0, 12, 1, 2345, 15678, 0), ("Hound5", 4, 4, 4, 4444, 4, 44)]]


def sample(fonts):
    """A made-up scoreboard laid out like Overwatch's Tab screen (fonts: the
    HUD's fonts folder): the build reads it to check the text recognition
    works. Each row: portrait, ultimate charge, name (and a title under some),
    role icon, E A D DMG H MIT, report button."""
    import os
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1920, 1080), (40, 22, 18))
    d = ImageDraw.Draw(img)
    name_font = ImageFont.truetype(os.path.join(fonts, "SairaCondensed-Bold.ttf"), 28)
    stat = ImageFont.truetype(os.path.join(fonts, "Inter.ttf"), 17)
    title = ImageFont.truetype(os.path.join(fonts, "Inter.ttf"), 15)
    cols = (940, 991, 1041, 1122, 1213, 1305)
    d.rectangle((530, 157, 1390, 188), fill=(225, 225, 228))
    for label, x in zip(("E", "A", "D", "DMG", "H", "MIT"), cols):
        d.text((x, 172), label, font=stat, fill=(40, 40, 50), anchor="mm")
    for t, team in enumerate(SAMPLE):
        for i, (name, *nums) in enumerate(team):
            y = (221 if t == 0 else 627) + i * 66
            colour = (30, 120, 190) if t == 0 else (160, 30, 50)
            d.rectangle((530, y - 32, 1390, y + 32), fill=colour)
            d.rectangle((916, y - 32, 1066, y + 32), fill=tuple(c * 85 // 100 for c in colour))
            d.rectangle((1354, y - 32, 1390, y + 32), fill=tuple(c * 70 // 100 for c in colour))
            d.rectangle((558, y - 32, 620, y + 32), fill=(200, 160, 120))           # (the hero's portrait)
            d.ellipse((631, y - 16, 663, y + 16), outline=(255, 255, 255), width=2)
            d.text((647, y), "0", font=stat, fill=(255, 255, 255), anchor="mm")     # (ultimate charge)
            d.text((676, y - (8 if i % 2 else 0)), name, font=name_font, fill=(255, 255, 255), anchor="lm")
            if i % 2:
                d.text((677, y + 21), "Title", font=title, fill=(230, 230, 230), anchor="lm")
            d.ellipse((860, y - 24, 908, y + 24), fill=(255, 255, 255))           # (the role icon)
            d.polygon([(872, y + 10), (884, y - 12), (896, y + 10)], fill=(20, 20, 30))
            d.polygon([(1362, y + 10), (1372, y - 10), (1382, y + 10)], fill=(200, 200, 210))   # (report)
            for v, x in zip(nums, cols):
                d.text((x, y), f"{v:,}", font=stat, fill=(255, 255, 255), anchor="mm")
    return img


if __name__ == "__main__":
    from PIL import Image
    for path in sys.argv[1:]:
        img = Image.open(path).convert("RGB")
        words = ocr_words(img)
        for r in rows_of(words):
            print(r)
        for n, team in enumerate(parse(words, img.width)["teams"], 1):
            print(f"Team {n}")
            for p in team:
                print("  ", p)
