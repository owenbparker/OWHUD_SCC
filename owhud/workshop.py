"""The post-game stats from the Overwatch Workshop's log.

The lobby host runs a stats-logging Workshop mode (ScrimTime, the one
Parsertime reads) with Settings > Gameplay > Enable Workshop Inspector Log
File on. Overwatch then writes each match to Documents\\Overwatch\\Workshop
as Log-YYYY-MM-DD-HH-MM-SS.txt, one event a line:

    [00:20:03] ,player_stat,661.03,3,Team 1,Gray,Mei,27,14,3,38321.31,...

player_stat lines come at the end of each round, one per player per hero
they played, with the match's totals so far; the last lot is the map's.
"""

import glob
import os
import re

# player_stat's fields after the event name (from ScrimTime's documentation).
STAT_FIELDS = ("time", "round", "team", "name", "hero", "eliminations", "final_blows", "deaths",
               "all_damage", "barrier_damage", "hero_damage", "healing", "healing_received",
               "self_healing", "damage_taken", "damage_blocked", "defensive_assists",
               "offensive_assists", "ultimates_earned", "ultimates_used", "multikill_best",
               "multikills", "solo_kills", "objective_kills", "environmental_kills",
               "environmental_deaths", "critical_hits", "critical_hit_accuracy", "scoped_accuracy",
               "scoped_critical_hit_accuracy", "scoped_critical_hit_kills", "shots_fired",
               "shots_hit", "shots_missed", "scoped_shots", "scoped_shots_hit", "weapon_accuracy",
               "time_played")
TEXT = {"team", "name", "hero"}


def folders():
    """Where Overwatch may keep its Workshop logs (OneDrive can move Documents)."""
    home = os.path.expanduser("~")
    docs = [os.path.join(home, "Documents")]
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            docs.append(os.path.join(os.environ[var], "Documents"))
    docs.append(os.path.join(home, "OneDrive", "Documents"))
    return [os.path.join(d, "Overwatch", "Workshop") for d in docs]


def folder(setting=""):
    """The Workshop log folder: the one set, else the first that exists."""
    if setting:
        return setting
    return next((f for f in folders() if os.path.isdir(f)), folders()[0])


def latest(where):
    """The newest log in the folder (path), or None."""
    logs = glob.glob(os.path.join(where, "*.txt"))
    return max(logs, key=os.path.getmtime) if logs else None


def _split(line):
    """A line's fields: commas inside (x, y, z) positions don't split."""
    out, cur, depth = [], "", 0
    for ch in line:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and not depth:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur]


def _num(text):
    try:
        return float(text)
    except ValueError:
        return None    # ("****": Overwatch censors a number now and then)


def events(text):
    """Each line as (event, [fields])."""
    for line in text.splitlines():
        line = re.sub(r"^﻿?\[[\d:]+\]\s*", "", line.strip())
        if not line:
            continue
        fields = [f.strip() for f in _split(line)]
        if fields and fields[0] == "":
            fields = fields[1:]
        if fields:
            yield fields[0], fields[1:]


def parse(text):
    """The log's last match: {"map", "mode", "teams": [name 1, name 2],
    "score": [1, 2], "ended", "players": [[team 1's], [team 2's]]}, each
    player {"name", "hero" (the one played longest), kills, assists, deaths,
    damage, healing}. None if there's no match in it."""
    match, stats, last_time = None, {}, None
    for event, f in events(text):
        if event == "match_start" and len(f) >= 5:
            match = {"map": f[1], "mode": f[2], "teams": [f[3], f[4]], "score": [0, 0], "ended": False}
            stats, last_time = {}, None
        elif match is None:
            continue
        elif event == "match_end" and len(f) >= 4:
            match["score"] = [int(_num(f[2]) or 0), int(_num(f[3]) or 0)]
            match["ended"] = True
        elif event == "round_end" and len(f) >= 5:
            match["score"] = [int(_num(f[3]) or 0), int(_num(f[4]) or 0)]
        elif event == "player_stat" and len(f) >= 17:
            row = {k: (v if k in TEXT else _num(v)) for k, v in zip(STAT_FIELDS, f)}
            if row["all_damage"] is None:
                row["all_damage"] = (row["hero_damage"] or 0) + (row["barrier_damage"] or 0)
            row = {k: (0 if v is None else v) for k, v in row.items()}
            if row["time"] != last_time:       # (a new round's totals)
                stats, last_time = {}, row["time"]
            stats[row["team"], row["name"], row["hero"]] = row    # (the lot can be written twice)
    if match is None:
        return None
    players = [[], []]
    for i, team in enumerate(match["teams"]):
        by_name = {}
        for r in stats.values():
            if r["team"] == team and r["name"]:
                by_name.setdefault(r["name"], []).append(r)
        for name, rows in by_name.items():
            total = lambda k: int(round(sum(r.get(k, 0) for r in rows)))
            hero = max(rows, key=lambda r: r.get("time_played", 0))["hero"]
            players[i].append({"name": name, "hero": hero, "kills": total("eliminations"),
                               "assists": total("offensive_assists") + total("defensive_assists"),
                               "deaths": total("deaths"), "damage": total("all_damage"), "healing": total("healing"),
                               "_time": sum(r.get("time_played", 0) for r in rows)})
        # (5 a side: whoever played the most, in case someone swapped in)
        players[i] = [{k: v for k, v in p.items() if k != "_time"}
                      for p in sorted(players[i], key=lambda p: -p["_time"])[:5]]
    return dict(match, players=players)


def read_file(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse(f.read())
