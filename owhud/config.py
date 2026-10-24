"""Settings for the Overwatch HUD, saved to config.json next to the
project (or next to OverwatchHUD.exe in the desktop build). Everything
here can be changed from the control panel while the app runs."""

import copy
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FROZEN = getattr(sys, "frozen", False)
RESOURCE_DIR = getattr(sys, "_MEIPASS", ROOT) if FROZEN else ROOT
DATA_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else ROOT
WEB_DIR = os.path.join(RESOURCE_DIR, "web")
LOGO_DIR = os.path.join(DATA_DIR, "logos")         # uploaded team and tournament logos (served as logos/...)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

STYLES = {1: "SCC 1", 2: "SCC 2", 3: "SCC 3"}
PLAYER = {"name": "", "hero": "", "kills": 0, "assists": 0, "deaths": 0, "damage": 0, "healing": 0}
STAT_KEYS = ("kills", "assists", "deaths", "damage", "healing")

DEFAULTS = {
    # Not the League (8777, 8779), VALORANT (8778), CoD (8780), Rocket League
    # (8781), Smash (8784) HUD's or the GFX Engine's (8782, 8783) ports.
    "http_host": "127.0.0.1",
    "http_port": 8785,
    "allow_network": False,

    # The teams: name, short name, logo ("logos/x.png"), colour and season
    # record (shown beside the name, e.g. "3-1").
    "left": {"name": "TEAM 1", "short": "", "logo": "", "color": "#ba0aba", "record": "0-0"},
    "right": {"name": "TEAM 2", "short": "", "logo": "", "color": "#ba0aba", "record": "0-0"},
    # The series: first to 2, 3 or 4 maps (who won each map is kept with the
    # map bans, in map_bans.results).
    "first_to": 3,
    # The overlay (/overlay): which design (STYLES), and what it shows.
    "overlay": {"style": 1, "logos": True, "records": True, "slashes": True, "accent": "#ff7c01", "y": 0},
    # The logo in the middle of SCC 3 and at the top of the map bans; none
    # shows the St. Clair Saints logo.
    "tournament_logo": "",
    # The line under it on the map bans (e.g. "NACE VARSITY - WEEK 1").
    "info": "",
    # Map bans (see owhud/maps.py).
    "map_bans": {"rules": "owcs", "team_a": "left", "pool": None, "steps": [], "results": [],
                 "bg": True},
    # Post-game stats, typed in from the scoreboard: each team's 5 players
    # (in-game name, hero, eliminations, assists, deaths, damage, healing),
    # for the GFX Engine's Team stats and Player stats.
    "stats": {side: [dict(PLAYER) for _ in range(5)] for side in ("left", "right")},
    # The SCC GFX Engine this HUD sends its series to (see owhud/enginelink.py).
    "gfx_engine": {"url": "", "name": "", "connected": False},
}

_lock = threading.Lock()


def _merge(base, extra):
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = _merge(DEFAULTS, json.load(f))
        self.version = 0

    def get(self):
        with _lock:
            return copy.deepcopy(self.data)

    def update(self, changes):
        with _lock:
            self.data = _merge(self.data, changes)
            self.version += 1
            snapshot = copy.deepcopy(self.data)
        self.save(snapshot)
        return snapshot

    def save(self, snapshot=None):
        snapshot = snapshot if snapshot is not None else self.get()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp, self.path)


def web_file(rel):
    """Path on disk for a URL path: uploaded logos (logos/...), else the web
    folder. Paths that climb out are refused."""
    rel = rel.replace("\\", "/").lstrip("/")
    base = WEB_DIR
    if rel.startswith("logos/"):
        base, rel = LOGO_DIR, rel[len("logos/"):]
    path = os.path.normpath(os.path.join(base, rel))
    if not path.startswith(os.path.normpath(base) + os.sep) or not os.path.isfile(path):
        return None
    return path
