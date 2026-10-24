"""Overwatch's maps and the map veto: the competitive pool, OWCS's and NACE's
rules for who bans and picks each map, and what the map bans graphic
(/map-bans) shows.

The control panel keeps the veto in config["map_bans"]:
    rules     "owcs" or "nace" (see RULES)
    team_a    "left" or "right": Team A, the higher seed (picks map 1 in
              OWCS, bans first in NACE)
    pool      {mode: [maps]}, or None for the current competitive pool
    steps     [{"map"}]: what was chosen at each step of the veto, in order
    results   ["left" / "right" / "draw" / ""]: who won each map played

OWCS (2025-26 Official Rules, 9.4): map 1 is Control, picked by the higher
seed; after that the loser of each map picks the next map, from a mode that
hasn't been played yet (once every mode has been played, from a mode not
played since). A map is never played twice. There are no map bans.

NACE (the FACEIT veto collegiate Overwatch is played with, as in the OWCS
2024 rules 6.17-6.19): each map has a fixed mode; teams ban maps of that mode
in turn, then one picks the map; the tiebreakers are the map of that mode
left over.

Overwatch maps can end in a draw: a draw adds a map to the series (and in
OWCS the team that didn't pick the drawn map picks the next one).
"""

import os
import re
import unicodedata

from .config import DATA_DIR, WEB_DIR

# The graphic's columns, left to right (its mode icons).
MODES = ["control", "hybrid", "escort", "push", "flashpoint"]
MODE_NAMES = {"control": "Control", "hybrid": "Hybrid", "escort": "Escort", "push": "Push", "flashpoint": "Flashpoint"}

ALL_MAPS = {
    "control": ["Antarctic Peninsula", "Busan", "Ilios", "Lijiang Tower", "Nepal", "Oasis", "Samoa"],
    "hybrid": ["Blizzard World", "Eichenwalde", "Hollywood", "King's Row", "Midtown", "Numbani", "Paraíso"],
    "escort": ["Circuit Royal", "Dorado", "Grímsvötn", "Havana", "Junkertown", "Rialto", "Route 66",
               "Shambali Monastery", "Watchpoint: Gibraltar"],
    "push": ["Colosseo", "Esperança", "New Queen Street", "Runasapi"],
    "flashpoint": ["Aatlis", "New Junk City", "Suravasa"],
}
# The current competitive pool (OWCS 2026 Stage 3, October 2026).
CURRENT_POOL = {
    "control": ["Busan", "Ilios", "Oasis"],
    "hybrid": ["Eichenwalde", "Midtown", "Paraíso"],
    "escort": ["Grímsvötn", "Havana", "Route 66"],
    "push": ["Esperança", "New Queen Street", "Runasapi"],
    "flashpoint": ["Aatlis", "New Junk City", "Suravasa"],
}
MODE_OF = {m: mode for mode, maps in ALL_MAPS.items() for m in maps}

RULES = {"owcs": "OWCS", "nace": "NACE"}

# NACE: each map of a first to 2 / 3 / 4: (mode, the bans in order, who picks
# the map); a pick of None is a tiebreaker (the map of that mode left over).
NACE = {
    2: [("control", "AB", "A"), ("hybrid", "BA", "B"), ("escort", "AB", "A"), ("flashpoint", "", "A")],
    3: [("control", "AB", "A"), ("hybrid", "BA", "B"), ("push", "B", "A"), ("flashpoint", "", "B"),
        ("escort", "AB", "A"), ("control", "", None), ("push", "", None)],
    4: [("control", "AB", "A"), ("hybrid", "BA", "B"), ("push", "B", "A"), ("flashpoint", "", "B"),
        ("escort", "AB", "A"), ("control", "A", "B"), ("escort", "B", "A"), ("push", "", None),
        ("escort", "", None), ("control", "", None)],
}

ART_DIR = os.path.join(WEB_DIR, "maps")       # the graphic's own map pictures (with the name on them)
UPLOAD_DIR = os.path.join(DATA_DIR, "maps")   # pictures added in the control panel (served as /map-art/...)


def slug(name):
    plain = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower().replace("'", "")).strip("-")


def find(name):
    """A map's proper name from any spelling (case, accents), or None."""
    s = slug(name)
    return next((m for m in MODE_OF if slug(m) == s), None)


def other(side):
    return "right" if side == "left" else "left"


def settings(cfg):
    mb = cfg.get("map_bans") or {}
    rules = mb.get("rules") if mb.get("rules") in RULES else "owcs"
    team_a = "right" if mb.get("team_a") == "right" else "left"
    pool = {}
    for mode in MODES:
        chosen = ((mb.get("pool") or CURRENT_POOL).get(mode)) or []
        pool[mode] = [m for m in ALL_MAPS[mode] if m in chosen]
    return rules, team_a, pool


def first_to(cfg):
    return max(2, min(4, int(cfg.get("first_to") or 3)))


def series_over(results, ft):
    return any(results.count(s) >= ft for s in ("left", "right"))


def steps(cfg):
    """The veto, step by step: [{"n", "action" ("ban" / "pick" / "decider"),
    "team" (whose turn, or None: not known yet), "mode", "map", "options",
    "map_no", "winner"}], up to the last map the series can need."""
    rules, team_a, pool = settings(cfg)
    ft = first_to(cfg)
    mb = cfg.get("map_bans") or {}
    saved = [s.get("map") if isinstance(s, dict) else "" for s in (mb.get("steps") or [])]
    results = [r if r in ("left", "right", "draw") else "" for r in (mb.get("results") or [])]
    who = {"A": team_a, "B": other(team_a)}
    out, banned, picked = [], [], []
    played_modes, maps_needed = [], 2 * ft - 1 + results.count("draw")

    def choose(i, options):
        m = find(saved[i]) if i < len(saved) and saved[i] else None
        return m if m in options else ""

    def add(action, team, mode, options, map_no=None):
        i = len(out)
        s = {"n": i + 1, "action": action, "team": team, "mode": mode, "options": options,
             "map": choose(i, options), "map_no": map_no}
        if map_no:
            s["winner"] = results[map_no - 1] if map_no <= len(results) else ""
        out.append(s)
        return s

    if rules == "owcs":
        prev = None
        for k in range(1, min(maps_needed, 12) + 1):
            if k == 1:
                team, modes = team_a, ["control"]
            else:
                w = prev["winner"]
                team = other(w) if w in ("left", "right") else other(prev["team"]) if w == "draw" and prev["team"] else None
                this_round = played_modes[len(played_modes) - len(played_modes) % len(MODES):]
                modes = [m for m in MODES if m not in this_round and pool[m]] or [m for m in MODES if pool[m]]
            options = [m for mode in modes for m in pool[mode] if m not in picked]
            if not options:   # (every map of those modes played: they can come back)
                options = [m for mode in modes for m in pool[mode]]
            s = add("pick", team, "control" if k == 1 else None, options, k)
            if s["map"]:
                s["mode"] = MODE_OF[s["map"]]
                picked.append(s["map"])
            played_modes.append(s["mode"] or "?")
            prev = s
    else:
        for k, (mode, bans, pick) in enumerate(NACE[ft][:maps_needed], 1):
            for t in bans:
                s = add("ban", who[t], mode, [m for m in pool[mode] if m not in banned and m not in picked])
                if s["map"]:
                    banned.append(s["map"])
            left = [m for m in pool[mode] if m not in banned and m not in picked]
            s = add("pick" if pick else "decider", who[pick] if pick else None, mode,
                    left or [m for m in pool[mode] if m not in picked], k)
            if not s["map"] and len(s["options"]) == 1:
                s["map"], s["auto"] = s["options"][0], True
            if s["map"]:
                picked.append(s["map"])
    return out


def series(cfg):
    """The maps of the series in order: [{"no", "map", "mode", "picked_by",
    "winner"}], the score, and which map is being played now."""
    ft = first_to(cfg)
    st = steps(cfg)
    maps = [{"no": s["map_no"], "map": s["map"], "mode": s["mode"] or "",
             "picked_by": s["team"] if s["action"] == "pick" else None, "winner": s["winner"]}
            for s in st if s.get("map_no")]
    results = [m["winner"] for m in maps]
    score = {side: results.count(side) for side in ("left", "right")}
    over = series_over(results, ft)
    current = None if over else next((m for m in maps if not m["winner"]), None)
    return {"maps": maps, "score": score, "first_to": ft, "over": over,
            "winner": next((s for s in ("left", "right") if score[s] >= ft), None),
            "current": current["no"] if current else None}


def set_result(cfg, side, delta):
    """+1: `side` won the map being played (or "draw"); -1: undo `side`'s last
    win. Returns the new results list."""
    mb = cfg.get("map_bans") or {}
    results = [r if r in ("left", "right", "draw") else "" for r in (mb.get("results") or [])]
    if delta > 0:
        if series_over(results, first_to(cfg)):
            return results
        i = results.index("") if "" in results else len(results)
        results[i:i + 1] = [side]
    else:
        for i in range(len(results) - 1, -1, -1):
            if results[i] == side:
                results[i] = ""
                break
    while results and not results[-1]:
        results.pop()
    return results


def art(name):
    """(path on disk, the name is on the picture) for a map's picture, or (None, False)."""
    s = slug(name)
    for folder, named in ((UPLOAD_DIR, False), (ART_DIR, True)):
        for ext in (".png", ".jpg", ".webp"):
            p = os.path.join(folder, s + ext)
            if os.path.isfile(p):
                return p, named
    return None, False


def view(cfg):
    """What the map bans graphic shows: the teams, the pool by mode, each map's
    state, and the series."""
    rules, team_a, pool = settings(cfg)
    st = steps(cfg)
    ser = series(cfg)
    state = {}
    for s in st:
        if not s["map"]:
            continue
        if s["action"] == "ban":
            state[s["map"]] = {"state": "ban", "team": s["team"]}
        else:
            w = s["winner"]
            state[s["map"]] = {"state": "won" if w else ("next" if s["map_no"] == ser["current"] else "picked"),
                               "team": w if w in ("left", "right") else None, "draw": w == "draw",
                               "picked_by": s["team"], "map_no": s["map_no"]}
    columns = []
    for mode in MODES:
        tiles = []
        for m in pool[mode]:
            path, named = art(m)
            src = f"/map-art/{slug(m)}?v={int(os.path.getmtime(path))}" if path else ""
            tiles.append(dict({"map": m, "art": src, "named": named, "state": "open"},
                              **state.get(m, {})))
        columns.append({"mode": mode, "name": MODE_NAMES[mode], "maps": tiles})
    return {"rules": rules, "columns": columns, "series": ser}
