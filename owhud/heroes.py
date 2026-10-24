"""Overwatch's heroes (for the Stats page's hero dropdown) and their pictures.

The pictures (the heroes' portraits) are downloaded once from the OverFast API
(overfast-api.tekrop.fr, Blizzard's own art) into heroes/ next to the app, in
the background; a picture can also be put there by hand, named like
"soldier-76.png". The GFX Engine's stats graphics show them when its player
pictures are set to champions / agents / heroes.
"""

import json
import os
import re
import threading
import time
import unicodedata
import urllib.request

from .config import DATA_DIR
from .log import log

# The roster, October 2026 (Season 5).
ROLES = {
    "tank": ["D.Mon", "D.Va", "Domina", "Doomfist", "Hazard", "Junker Queen", "Mauga", "Orisa", "Ramattra",
             "Reinhardt", "Roadhog", "Sigma", "Winston", "Wrecking Ball", "Zarya"],
    "damage": ["Anran", "Ashe", "Bastion", "Cassidy", "Echo", "Emre", "Freja", "Genji", "Hanzo", "Junkrat", "Mei",
               "Pharah", "Reaper", "Shion", "Sierra", "Sojourn", "Soldier: 76", "Symmetra", "Torbjörn", "Tracer",
               "Vendetta", "Venture", "Widowmaker"],
    "support": ["Ana", "Baptiste", "Brigitte", "Doctrine", "Illari", "Jetpack Cat", "Juno", "Kiriko", "Lifeweaver",
                "Lúcio", "Mercy", "Mizuki", "Moira", "Sombra", "Wuyang", "Zenyatta"],
}
ROLE_OF = {h: role for role, hs in ROLES.items() for h in hs}

PICTURE_DIR = os.path.join(DATA_DIR, "heroes")
API = "https://overfast-api.tekrop.fr/heroes"


def slug(name):
    plain = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def _key(name):
    return re.sub(r"[^a-z0-9]", "", slug(name))


def find(name):
    """A hero's proper name from any spelling ("soldier 76", "lucio", "dva"), or ""."""
    k = _key(name)
    return next((h for h in ROLE_OF if _key(h) == k), "") if k else ""


def picture(name):
    """The hero's picture on disk, or None."""
    s = slug(name)
    for ext in (".png", ".jpg", ".webp"):
        p = os.path.join(PICTURE_DIR, s + ext)
        if os.path.isfile(p):
            return p
    return None


class Pictures:
    """Downloads the portraits the HUD doesn't have yet (in the background,
    not more than once every 10 minutes; offline, the stats show no picture)."""

    def __init__(self, opener=None):
        self._opener = opener or urllib.request.build_opener()
        self._lock = threading.Lock()
        self._busy = False
        self._tried_at = 0
        self.missing_online = []   # heroes the API doesn't have a picture of (yet)

    def missing(self):
        return [h for h in ROLE_OF if not picture(h)]

    def fetch_missing(self):
        with self._lock:
            if self._busy or time.time() - self._tried_at < 600 or not self.missing():
                return
            self._busy, self._tried_at = True, time.time()
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            req = urllib.request.Request(API, headers={"User-Agent": "SCC-Overwatch-HUD"})
            with self._opener.open(req, timeout=15) as r:
                data = json.loads(r.read())
            by_key = {_key(d.get("name") or d.get("key") or ""): d for d in data if isinstance(d, dict)}
            by_key.update({_key(d.get("key") or ""): d for d in data if isinstance(d, dict)})
            os.makedirs(PICTURE_DIR, exist_ok=True)
            got, none = [], []
            for h in self.missing():
                d = by_key.get(_key(h))
                if not d or not d.get("portrait"):
                    none.append(h)
                    continue
                with self._opener.open(urllib.request.Request(d["portrait"], headers={"User-Agent": "SCC-Overwatch-HUD"}),
                                       timeout=20) as r:
                    blob = r.read()
                ext = ".jpg" if blob[:3] == b"\xff\xd8\xff" else ".webp" if blob[8:12] == b"WEBP" else ".png"
                tmp = os.path.join(PICTURE_DIR, slug(h) + ext + ".tmp")
                with open(tmp, "wb") as f:
                    f.write(blob)
                os.replace(tmp, os.path.join(PICTURE_DIR, slug(h) + ext))
                got.append(h)
            self.missing_online = none
            if got:
                log(f"Downloaded {len(got)} hero pictures")
            if none:
                log(f"No picture online yet for {', '.join(none)} (put one in {PICTURE_DIR})", "warn")
        except Exception as e:
            log(f"Couldn't download the hero pictures ({e}); the stats show the heroes' names", "warn")
        finally:
            with self._lock:
                self._busy = False
