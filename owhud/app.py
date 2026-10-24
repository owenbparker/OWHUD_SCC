"""The running app: the teams, the series and the map bans (all set in the
control panel), and the web server for the overlay, the map bans graphic and
the control panel."""

import base64
import json
import mimetypes
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from . import heroes, maps
from .config import DATA_DIR, LOGO_DIR, PLAYER, STAT_KEYS, STYLES, web_file
from .enginelink import EngineLink, save_logo_bytes
from .log import log, since
from .updater import Updater, clean_up_old_update

mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/otf", ".otf")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("video/webm", ".webm")

PAGES = {"/": "/control", "/start": "/control", "/control": "control.html", "/overlay": "overlay.html",
         "/scoreboard": "overlay.html", "/map-bans": "mapbans.html", "/mapbans": "mapbans.html"}
SIDES = ("left", "right")
SIDE_NAMES = {"left": "Left team", "right": "Right team"}


class HudApp:
    def __init__(self, config):
        self.config = config
        self.updater = Updater()
        self.server = None
        self.version = 0
        self._cond = threading.Condition()
        self.heroes = heroes.Pictures()
        # The SCC GFX Engine: the series and the map bans go to it; its teams can come here.
        self.engine = EngineLink("Overwatch HUD", "overwatch", self._engine_snapshot, self._engine_apply_global,
                                 load=lambda: self.config.get().get("gfx_engine"),
                                 save=lambda d: self.config.update({"gfx_engine": d}), log=log)

    # --- the series -----------------------------------------------------------------

    def map_result(self, side, delta):
        """The left or right team won the map being played (delta 1), or a draw
        (side "draw"); delta -1 takes back that team's last map."""
        cfg = self.config.get()
        before = maps.series(cfg)
        results = maps.set_result(cfg, side, delta)
        self.config.update({"map_bans": {"results": results}})
        after = maps.series(self.config.get())
        if delta > 0 and before["current"]:
            what = "drawn" if side == "draw" else f"won by {cfg[side]['name']}"
            m = next((x for x in before["maps"] if x["no"] == before["current"]), None)
            log(f"Map {before['current']}{' (' + m['map'] + ')' if m and m['map'] else ''} {what}: "
                f"{after['score']['left']}-{after['score']['right']}")
            if after["winner"]:
                log(f"{cfg[after['winner']]['name']} wins the series")
        self.changed()

    # --- post-game stats ------------------------------------------------------------

    def stats(self, cfg=None):
        """Each team's 5 players, cleaned up: {"left": [{name, hero, kills...}], "right": [...]}."""
        cfg = cfg or self.config.get()
        out = {}
        for side in SIDES:
            rows = list((cfg.get("stats") or {}).get(side) or [])[:5]
            rows += [{}] * (5 - len(rows))
            out[side] = []
            for r in rows:
                r = r if isinstance(r, dict) else {}
                p = {"name": str(r.get("name") or "").strip()[:40], "hero": heroes.find(r.get("hero"))}
                for k in STAT_KEYS:
                    try:
                        p[k] = max(0, int(float(r.get(k) or 0)))
                    except (TypeError, ValueError):
                        p[k] = 0
                out[side].append(p)
        return out

    def set_stats(self, side, players):
        if side not in SIDES or not isinstance(players, list):
            raise ValueError("send a side and its players")
        clean = self.stats({"stats": {side: players}})[side]
        self.config.update({"stats": {side: clean}})
        self.changed()

    def clear_stats(self, names_too=False):
        """New map: the numbers (and heroes) back to nothing; the players stay unless names_too."""
        cur = self.stats()
        new = {side: [dict(PLAYER, name="" if names_too else p["name"]) for p in cur[side]] for side in SIDES}
        self.config.update({"stats": new})
        log("Stats cleared")
        self.changed()

    def hero_icon_base(self):
        """Where the GFX Engine's outputs find this HUD's hero pictures: this PC
        if the engine is on it, else the HUD's address on the network."""
        cfg = self.config.get()
        port = self.server.port if self.server else cfg["http_port"]
        host = (urlsplit(self.engine.url if "//" in (self.engine.url or "") else "http://" + (self.engine.url or "")).hostname or "")
        if not host or host in ("localhost", "::1") or host.startswith("127.") or host == lan_ip():
            return f"http://localhost:{port}"
        return f"http://{lan_ip() or 'localhost'}:{port}"

    # --- what the pages see -------------------------------------------------------------

    def info(self):
        cfg = self.config.get()
        port = self.server.port if self.server else cfg["http_port"]
        lan = lan_ip() if cfg.get("allow_network") else None
        return {"port": port, "lan_url": f"http://{lan}:{port}" if lan else None,
                "network": bool(cfg.get("allow_network")), "update": self.updater.info(), "data_dir": DATA_DIR}

    def snapshot(self):
        cfg = self.config.get()
        rules, team_a, pool = maps.settings(cfg)
        return {"config": cfg, "app": self.info(), "overlay": self.overlay(), "styles": STYLES,
                "map_bans": {"rules": rules, "rules_names": maps.RULES, "team_a": team_a, "pool": pool,
                             "all_maps": maps.ALL_MAPS, "current_pool": maps.CURRENT_POOL, "modes": maps.MODE_NAMES,
                             "steps": maps.steps(cfg), "art": {m: bool(maps.art(m)[0]) for m in maps.MODE_OF}},
                "stats": self.stats(cfg),
                "heroes": {"roles": heroes.ROLES, "pictures": {h: bool(heroes.picture(h)) for h in heroes.ROLE_OF}}}

    def overlay(self):
        """What the overlay and the map bans graphic show (besides the maps)."""
        cfg = self.config.get()
        ser = maps.series(cfg)
        cur = next((m for m in ser["maps"] if m["no"] == ser["current"]), None)
        out = {"style": cfg["overlay"]["style"], "show": cfg["overlay"], "first_to": ser["first_to"],
               "map_no": ser["current"] or len([m for m in ser["maps"] if m["winner"]]), "map": cur["map"] if cur else "",
               "over": ser["over"], "logo": cfg["tournament_logo"] or "assets/logo.png", "info": cfg["info"]}
        for side in SIDES:
            t = cfg[side]
            out[side] = {"name": t["name"], "short": t["short"] or t["name"], "logo": t["logo"], "color": t["color"],
                         "record": t["record"], "score": ser["score"][side]}
        return out

    def map_bans(self):
        v = maps.view(self.config.get())
        v.update(self.overlay())
        v["bg"] = self.config.get()["map_bans"].get("bg", True)
        return v

    # --- the SCC GFX Engine -------------------------------------------------------------

    def _engine_snapshot(self):
        """What the GFX Engine gets: the teams and the series as flat fields, and the HUD's pages."""
        cfg = self.config.get()
        ser = maps.series(cfg)
        cur = next((m for m in ser["maps"] if m["no"] == ser["current"]), None)
        f = {"first_to": ser["first_to"], "best_of": 2 * ser["first_to"] - 1, "map_number": ser["current"] or "",
             "map_name": cur["map"] if cur else "", "map_mode": maps.MODE_NAMES.get(cur["mode"], "") if cur else "",
             "info": cfg["info"]}
        for side in SIDES:
            t = cfg[side]
            f[f"{side}_name"] = t["name"]
            f[f"{side}_short"] = t["short"]
            f[f"{side}_record"] = t["record"]
            f[f"{side}_score"] = ser["score"][side]
        for m in ser["maps"]:
            f[f"map{m['no']}_name"] = m["map"]
            f[f"map{m['no']}_mode"] = maps.MODE_NAMES.get(m["mode"], "")
            f[f"map{m['no']}_winner"] = cfg[m["winner"]]["name"] if m["winner"] in SIDES else ("Draw" if m["winner"] else "")
        base = self.hero_icon_base()
        for side, players in self.stats(cfg).items():
            for n, p in enumerate(players, 1):
                k = f"{side}_p{n}"
                f[f"{k}_name"] = p["name"]
                f[f"{k}_hero"] = p["hero"]
                f[f"{k}_hero_icon"] = f"{base}/heroes/{heroes.slug(p['hero'])}" if p["hero"] and heroes.picture(p["hero"]) else ""
                for s in STAT_KEYS:
                    f[f"{k}_{s}"] = p[s]
        series = {side: {"name": cfg[side]["name"], "short": cfg[side]["short"], "score": ser["score"][side],
                         "color": cfg[side]["color"]} for side in SIDES}
        series["best_of"] = 2 * ser["first_to"] - 1
        hud = {"port": self.server.port if self.server else cfg["http_port"], "network": bool(cfg.get("allow_network")),
               "graphics": [{"id": "overlay", "name": "Overlay", "path": "/overlay", "kind": "overlay"},
                            {"id": "map-bans", "name": "Map bans", "path": "/map-bans?bg=0", "kind": "board", "backdrop": True}]}
        return {"fields": f, "series": series, "hud": hud}

    def _engine_apply_global(self, g, fetch):
        """The GFX Engine's teams (names, short names, colours, logos) and tournament logo into the HUD."""
        changes, done = {}, []
        rosters = {}
        try:   # (the teams' players, from the engine's team list: their in-game names)
            sides = json.loads(fetch(self.engine.url + "/api/roster/links")).get("sides") or {}
            rosters = {s: [m["ign"] or m["name"] for m in (sides.get(s) or {}).get("members") or [] if m["ign"] or m["name"]]
                       for s in SIDES}
        except Exception as e:
            log(f"Couldn't fetch the teams' players from the GFX Engine: {e}", "warn")
        stats = self.stats()
        for side in SIDES:
            if rosters.get(side):
                names = rosters[side][:5]
                changes.setdefault("stats", {})[side] = [dict(p, name=names[i] if i < len(names) else "")
                                                         for i, p in enumerate(stats[side])]
                done.append(f"{side}'s {len(names)} players")
        for side in SIDES:
            t = g.get(side) or {}
            team = {}
            if t.get("name"):
                team["name"] = t["name"]
            if t.get("short"):
                team["short"] = t["short"][:8]
            if re.fullmatch(r"#[0-9a-fA-F]{6}", str(t.get("color") or "")):
                team["color"] = t["color"]
            if t.get("logo_url"):
                try:
                    team["logo"] = "logos/" + save_logo_bytes(LOGO_DIR, f"engine-{side}", fetch(t["logo_url"]))
                except Exception as e:
                    log(f"Couldn't fetch the {side} team's logo from the GFX Engine: {e}", "warn")
            if team:
                changes[side] = team
                done.append(f"{side}: {team.get('name') or team.get('short')}" + (" + logo" if team.get("logo") else ""))
        if g.get("tournament_logo_url"):
            try:
                changes["tournament_logo"] = "logos/" + save_logo_bytes(LOGO_DIR, "engine-tournament", fetch(g["tournament_logo_url"]))
                done.append("tournament logo")
            except Exception as e:
                log(f"Couldn't fetch the tournament logo from the GFX Engine: {e}", "warn")
        if g.get("best_of") and int(g["best_of"]) in (3, 5, 7):
            changes["first_to"] = (int(g["best_of"]) + 1) // 2
        if not done:
            raise ValueError("The engine's Global configuration has no team names or logos yet")
        self.config.update(changes)
        self.changed()
        return ", ".join(done)

    # --- changes ------------------------------------------------------------------------

    def wait(self, timeout):
        with self._cond:
            self._cond.wait(timeout)

    def changed(self):
        with self._cond:
            self.version += 1
            self._cond.notify_all()
        self.engine.send_now()

    def stop(self):
        self.engine.stop()
        if self.server:
            self.server.stop()

    def host(self):
        cfg = self.config.get()
        return "0.0.0.0" if cfg.get("allow_network") else cfg["http_host"]

    def set_network(self, on):
        self.config.update({"allow_network": bool(on)})
        if self.server:
            self.server.restart(self.host())
        log("Other PCs on your network can now read the HUD" if on else "HUD is now only reachable from this PC")


def save_upload(folder, name, data_url, stamp=True):
    """A picture from the control panel (a data: URL) saved in folder as name
    (+ a time stamp, so pages showing the old one see a new file) + its type's
    extension; the name's earlier pictures are removed."""
    m = re.fullmatch(r"data:image/(png|jpeg|webp|svg\+xml);base64,(.+)", data_url or "", re.S)
    if not m:
        raise ValueError("send a PNG, JPG, WebP or SVG image")
    ext = {"png": ".png", "jpeg": ".jpg", "webp": ".webp", "svg+xml": ".svg"}[m.group(1)]
    os.makedirs(folder, exist_ok=True)
    for old in os.listdir(folder):
        if os.path.splitext(old)[0] == name or (stamp and re.fullmatch(re.escape(name) + r"-\d+\.\w+", old)):
            os.remove(os.path.join(folder, old))
    out = f"{name}-{int(time.time() * 1000)}{ext}" if stamp else name + ext
    with open(os.path.join(folder, out), "wb") as f:
        f.write(base64.b64decode(m.group(2)))
    return out


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def _is_local(address):
    return address in ("127.0.0.1", "::1") or address.startswith("127.")


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, body, ctype="application/json", status=200):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status=200):
            self._send(json.dumps(data), status=status)

        def _file(self, full):
            with open(full, "rb") as f:
                self._send(f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

        def _stream(self, what):
            """Server-sent events: the overlay or the map bans, whenever they change."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            last, sent = None, 0.0
            try:
                self.wfile.write(b"retry: 1000\n\n")
                while True:
                    key = (app.version, app.config.version)
                    now = time.monotonic()
                    if key != last or now - sent >= 5.0:
                        last, sent = key, now
                        data = app.map_bans() if what == "map-bans" else app.overlay()
                        self.wfile.write(b"data: " + json.dumps(data).encode() + b"\n\n")
                        self.wfile.flush()
                    app.wait(1.0)
            except (ConnectionError, OSError):
                pass

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            if path in PAGES:
                target = PAGES[path]
                if target.startswith("/"):
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.send_header("Content-Length", "0")
                    return self.end_headers()
                path = "/" + target
            if path == "/api/state":
                return self._json(app.snapshot())
            if path == "/api/overlay":
                return self._json(app.overlay())
            if path == "/api/map-bans":
                return self._json(app.map_bans())
            if path == "/api/stream":
                return self._stream((parse_qs(url.query).get("what") or ["overlay"])[0])
            if path == "/api/engine":
                return self._json(app.engine.status())
            if path == "/api/log":
                return self._json({"lines": since(int((parse_qs(url.query).get("after") or ["0"])[0]))})
            if path.startswith("/heroes/"):
                full = heroes.picture(heroes.find(unquote(path[len("/heroes/"):]).rsplit(".", 1)[0]) or "-")
                return self._file(full) if full else self._json({"error": "no picture of that hero"}, 404)
            if path.startswith("/map-art/"):
                name = maps.find(unquote(path[len("/map-art/"):]))
                full = maps.art(name)[0] if name else None
                return self._file(full) if full else self._json({"error": "no picture of that map"}, 404)
            full = web_file(unquote(path))
            if not full:
                return self._json({"error": "not found"}, 404)
            self._file(full)

        def do_POST(self):
            path = urlparse(self.path).path
            if not _is_local(self.client_address[0]):
                return self._json({"error": "settings can only be changed on the HUD's own PC"}, 403)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                side = body.get("side")
                if path.startswith("/api/engine/"):
                    # Connect to GFX Engine (see enginelink.py).
                    reply = app.engine.handle("POST", path, body)
                    if reply is None:
                        return self._json({"error": "not found"}, 404)
                    return self._json(reply)
                if path == "/api/config":
                    mb = body.get("map_bans") or {}
                    if "rules" in mb or "team_a" in mb or "first_to" in body:
                        body.setdefault("map_bans", {}).setdefault("steps", [])   # (a new veto)
                    app.config.update(body)
                    app.changed()
                elif path == "/api/result":
                    if side not in ("left", "right", "draw"):
                        raise ValueError("side must be left, right or draw")
                    app.map_result(side, 1 if int(body.get("delta") or 1) > 0 else -1)
                elif path == "/api/series/reset":
                    app.config.update({"map_bans": {"steps": [], "results": []}})
                    log("New series: map bans and scores cleared")
                    app.changed()
                elif path == "/api/stats":
                    if body.get("clear"):
                        app.clear_stats(bool(body.get("names")))
                    else:
                        app.set_stats(side, body.get("players"))
                elif path == "/api/network":
                    app.set_network(bool(body.get("on")))
                elif path == "/api/logo":
                    # A team's logo (side) or the tournament logo; no picture: back to none.
                    target = side if side in SIDES else "tournament"
                    logo = "logos/" + save_upload(LOGO_DIR, target, body["data"]) if body.get("data") else ""
                    app.config.update({side: {"logo": logo}} if side in SIDES else {"tournament_logo": logo})
                    app.changed()
                elif path == "/api/map-art":
                    name = maps.find(body.get("map"))
                    if not name:
                        raise ValueError("no such map")
                    if body.get("data"):
                        save_upload(maps.UPLOAD_DIR, maps.slug(name), body["data"], stamp=False)
                        log(f"New picture for {name}")
                    else:
                        for ext in (".png", ".jpg", ".webp", ".svg"):
                            p = os.path.join(maps.UPLOAD_DIR, maps.slug(name) + ext)
                            if os.path.isfile(p):
                                os.remove(p)
                    app.changed()
                elif path == "/api/update/check":
                    app.updater.check()
                elif path == "/api/update/install":
                    app.updater.install_in_background()
                else:
                    return self._json({"error": "not found"}, 404)
                self._json(app.snapshot())
            except (ValueError, KeyError, TypeError, OSError) as e:
                self._json({"error": str(e)}, 400)

    return Handler


class Server:
    def __init__(self, app, host, port):
        self.app, self.port = app, port
        self._start(host)

    def _start(self, host):
        self.httpd = ThreadingHTTPServer((host, self.port), make_handler(self.app))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = host

    def restart(self, host):
        if host != self.host:
            self.httpd.shutdown()
            self.httpd.server_close()
            self._start(host)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def start(app):
    """Start the web server (raises OSError if the port is taken)."""
    port = app.config.get()["http_port"]
    app.server = Server(app, app.host(), port)
    app.heroes.fetch_missing()
    clean_up_old_update()
    app.updater.check_in_background()
    log(f"Overwatch HUD {app.updater.current} running - control panel http://localhost:{port}/control")
    log(f"Overlay (browser source, 1920x1080): http://localhost:{port}/overlay")
    log(f"Map bans (browser source, 1920x1080): http://localhost:{port}/map-bans")
    return app.server
