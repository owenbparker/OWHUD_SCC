import json
import os
import shutil
import tempfile
import unittest
import urllib.request

from owhud import heroes, maps, scoreboard
from owhud.app import HudApp, start
from owhud.config import Config


def cfg(rules="owcs", steps=(), results=(), first_to=3, team_a="left", pool=None):
    return {"first_to": first_to, "left": {"name": "Saints"}, "right": {"name": "Hounds"},
            "map_bans": {"rules": rules, "team_a": team_a, "pool": pool, "steps": [{"map": m} for m in steps],
                         "results": list(results)}}


class OwcsRules(unittest.TestCase):
    def test_map_one_is_control_picked_by_team_a(self):
        s = maps.steps(cfg(team_a="right"))
        self.assertEqual(len(s), 5)
        self.assertEqual((s[0]["action"], s[0]["team"], s[0]["mode"]), ("pick", "right", "control"))
        self.assertEqual(s[0]["options"], ["Busan", "Ilios", "Oasis"])

    def test_loser_picks_from_a_mode_not_played(self):
        s = maps.steps(cfg(steps=["Busan", "Midtown"], results=["left", "left"]))
        self.assertEqual(s[1]["team"], "right")       # lost map 1
        self.assertEqual(s[2]["team"], "right")       # lost map 2
        modes = {maps.MODE_OF[m] for m in s[2]["options"]}
        self.assertEqual(modes, {"escort", "push", "flashpoint"})
        self.assertIsNone(maps.steps(cfg(steps=["Busan"]))[1]["team"])   # map 1 not decided yet

    def test_a_draw_adds_a_map_and_the_other_team_picks(self):
        s = maps.steps(cfg(steps=["Busan", "Midtown"], results=["left", "draw"]))
        self.assertEqual(len(s), 6)
        self.assertEqual(s[1]["team"], "right")
        self.assertEqual(s[2]["team"], "left")        # right picked the drawn map

    def test_every_mode_once_then_again_and_no_map_twice(self):
        played = ["Busan", "Midtown", "Havana", "Runasapi", "Aatlis"]
        s = maps.steps(cfg(steps=played, results=["left", "right", "left", "right", "draw"], first_to=4))
        sixth = s[5]
        self.assertEqual({maps.MODE_OF[m] for m in sixth["options"]}, set(maps.MODES))
        self.assertFalse(set(played) & set(sixth["options"]))

    def test_series_score_and_end(self):
        c = cfg(steps=["Busan"], results=["left", "left", "right", "left"])
        ser = maps.series(c)
        self.assertEqual(ser["score"], {"left": 3, "right": 1})
        self.assertTrue(ser["over"])
        self.assertEqual(ser["winner"], "left")
        self.assertIsNone(ser["current"])
        self.assertEqual(maps.set_result(c, "right", 1), ["left", "left", "right", "left"])   # (over: no more)
        self.assertEqual(maps.set_result(c, "left", -1), ["left", "left", "right"])


class NaceRules(unittest.TestCase):
    def test_bans_then_the_last_map_is_picked(self):
        s = maps.steps(cfg(rules="nace", steps=["Busan", "Ilios"]))
        self.assertEqual([(x["action"], x["team"]) for x in s[:3]], [("ban", "left"), ("ban", "right"), ("pick", "left")])
        self.assertEqual((s[2]["map"], s[2].get("auto")), ("Oasis", True))
        self.assertEqual([x["mode"] for x in s if x.get("map_no")], ["control", "hybrid", "push", "flashpoint", "escort"])

    def test_tiebreakers_come_with_draws(self):
        s = maps.steps(cfg(rules="nace", results=["left", "draw", "right", "left", "right"]))
        last = [x for x in s if x.get("map_no")][-1]
        self.assertEqual((last["action"], last["mode"], last["map_no"]), ("decider", "control", 6))

    def test_graphic_shows_bans_picks_and_winners(self):
        v = maps.view(cfg(rules="nace", steps=["Busan", "Ilios"], results=["right"]))
        control = v["columns"][0]["maps"]
        self.assertEqual([(t["map"], t["state"]) for t in control], [("Busan", "ban"), ("Ilios", "ban"), ("Oasis", "won")])
        self.assertEqual(control[2]["team"], "right")
        self.assertTrue(control[0]["named"])          # the design's own picture, with the name on it


class Pool(unittest.TestCase):
    def test_current_pool_has_three_maps_a_mode(self):
        self.assertEqual({m: len(l) for m, l in maps.CURRENT_POOL.items()}, {m: 3 for m in maps.MODES})
        for mode, names in maps.CURRENT_POOL.items():
            for n in names:
                self.assertEqual(maps.MODE_OF[n], mode)

    def test_names_any_spelling(self):
        self.assertEqual(maps.find("esperanca"), "Esperança")
        self.assertEqual(maps.find("kings-row"), "King's Row")
        self.assertEqual(maps.slug("Watchpoint: Gibraltar"), "watchpoint-gibraltar")


class Heroes(unittest.TestCase):
    def test_every_hero_once_with_a_role(self):
        names = [h for hs in heroes.ROLES.values() for h in hs]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(names), 54)
        for h in ("D.Va", "Soldier: 76", "Lúcio", "Torbjörn", "Wuyang", "Jetpack Cat", "Domina"):
            self.assertIn(h, heroes.ROLE_OF)

    def test_names_any_spelling(self):
        self.assertEqual(heroes.find("soldier 76"), "Soldier: 76")
        self.assertEqual(heroes.find("lucio"), "Lúcio")
        self.assertEqual(heroes.find("DVA"), "D.Va")
        self.assertEqual(heroes.find("nobody"), "")
        self.assertEqual(heroes.slug("Soldier: 76"), "soldier-76")


def board(teams, x0=300, y0=200, header=True, gap=330, dx=0):
    """The words text recognition finds on a scoreboard: a table per team, one
    under the other ((text, x, y, w, h) like WindowsOcr.words)."""
    cols = [x + dx for x in (700, 780, 860, 960, 1080, 1200)]
    words, y = [], y0
    for team in teams:
        if header:
            words += [(t, x - 8, y, 16, 18) for t, x in zip(("E", "A", "D", "DMG", "H", "MIT"), cols)]
        for i, (name, *nums) in enumerate(team):
            ry = y + 40 + i * 50
            words += [(part, x0 + 70 * j, ry, 60, 22) for j, part in enumerate(name.split())]
            words += [(str(v), x - 10 * len(str(v)) / 2, ry, 10 * len(str(v)), 22) for v, x in zip(nums, cols) if v is not None]
        y += gap
    return words


class Scoreboard(unittest.TestCase):
    TEAMS = [[("Saint1", 22, 5, 6, "11,234", 300, 4000), ("Saint2", 10, 0, 4, "5,000", "9,800", 0),
              ("Saint3", 1, 2, 3, 400, 0, 0), ("Saint4", 0, 0, 0, 0, 0, 0), ("Saint5", 3, 3, 3, 3, 3, 3)],
             [("Hound One", 7, 8, 9, "12,000", 0, 100), ("Hound2", 1, 1, 1, 1, 1, 1)]]

    def test_rows_and_columns(self):
        teams = scoreboard.parse(board(self.TEAMS), 1920)["teams"]
        self.assertEqual([len(t) for t in teams], [5, 2])
        self.assertEqual(teams[0][0], {"name": "Saint1", "kills": 22, "assists": 5, "deaths": 6, "damage": 11234, "healing": 300})
        self.assertEqual((teams[0][1]["healing"], teams[1][0]["name"], teams[1][0]["damage"]), (9800, "Hound One", 12000))

    def test_without_header_a_missing_number_is_none(self):
        t = [list(self.TEAMS[0]), self.TEAMS[1]]
        t[0][2] = ("Saint3", 1, None, 3, 400, 0, 0)
        teams = scoreboard.parse(board(t, header=False), 1920)["teams"]
        self.assertEqual(teams[0][2], {"name": "Saint3", "kills": 1, "assists": None, "deaths": 3, "damage": 400, "healing": 0})
        self.assertEqual(teams[1][0]["damage"], 12000)

    def test_tables_side_by_side(self):
        one, two = board([self.TEAMS[0]], x0=40, dx=-350), board([self.TEAMS[1]], x0=40, dx=-350)
        teams = scoreboard.parse(one + [(w[0], w[1] + 960, w[2], w[3], w[4]) for w in two], 1920)["teams"]
        self.assertEqual(([p["name"] for p in teams[0]][:2], [p["name"] for p in teams[1]]), (["Saint1", "Saint2"], ["Hound One", "Hound2"]))
        self.assertEqual(teams[1][0]["deaths"], 9)

    def test_overwatch_layout_icons_and_ult_charge(self):
        # (like the real Tab screen: the ultimate charge before the name, a
        # role icon read as a number, and MIT and the report button after H)
        words = []
        for i, (name, *nums) in enumerate(self.TEAMS[0] + self.TEAMS[1][:2]):
            y = 220 + i * 66
            words += [("0", 640, y, 12, 20), (name, 676, y, 120, 26), ("8", 880, y, 20, 20), ("A", 1370, y, 18, 18)]
            words += [(str(v), x - 10, y, 20, 20) for v, x in zip(nums, (940, 991, 1041, 1122, 1213, 1305))]
        teams = scoreboard.parse(words, 1920)["teams"]
        self.assertEqual(teams[0][0], {"name": "Saint1", "kills": 22, "assists": 5, "deaths": 6, "damage": 11234, "healing": 300})
        self.assertEqual((teams[1][0]["name"], teams[1][0]["deaths"]), ("Hound One", 9))

    def test_merge_matches_names_and_sides(self):
        blank = {"name": "", "hero": "", **dict.fromkeys(scoreboard.COLS, 0)}
        cur = {"left": [dict(blank, name="Hound2", hero="Ana")] + [dict(blank)] * 4,
               "right": [dict(blank, name="saint2"), dict(blank, name="Saint1", hero="Mercy")] + [dict(blank)] * 3}
        teams = scoreboard.parse(board(self.TEAMS), 1920)["teams"]
        teams[0][3]["kills"] = None
        stats, info = scoreboard.merge(cur, teams)
        self.assertTrue(info["swapped"])
        self.assertEqual([p["name"] for p in stats["right"]], ["saint2", "Saint1", "Saint3", "Saint4", "Saint5"])
        self.assertEqual((stats["right"][1]["hero"], stats["right"][1]["damage"]), ("Mercy", 11234))
        self.assertEqual((stats["left"][0]["name"], stats["left"][0]["hero"], stats["left"][0]["kills"]), ("Hound2", "Ana", 1))
        self.assertEqual(stats["left"][1]["name"], "Hound One")
        self.assertEqual(info["missing"], ["Saint4 kills"])


class Server(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        config = Config(os.path.join(self.dir, "config.json"))
        config.update({"http_port": 8797})
        self.app = HudApp(config)
        self.app.updater.check_in_background = lambda: None
        self.app.heroes.fetch_missing = lambda: None
        self.pictures, heroes.PICTURE_DIR = heroes.PICTURE_DIR, os.path.join(self.dir, "heroes")
        start(self.app)

    def tearDown(self):
        self.app.stop()
        heroes.PICTURE_DIR = self.pictures
        shutil.rmtree(self.dir, ignore_errors=True)

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:8797{path}", timeout=5) as r:
            return r.read()

    def post(self, path, body):
        req = urllib.request.Request(f"http://127.0.0.1:8797{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_pages_and_series(self):
        for path in ("/control", "/overlay", "/map-bans", "/map-art/busan", "/mapbans/topbar.png"):
            self.assertTrue(self.get(path))
        self.post("/api/config", {"left": {"name": "Saints"}, "overlay": {"style": 3}})
        self.post("/api/config", {"map_bans": {"steps": [{"map": "Oasis"}]}})
        state = self.post("/api/result", {"side": "left", "delta": 1})
        self.assertEqual(state["overlay"]["left"]["score"], 1)
        self.assertEqual(state["overlay"]["map_no"], 2)
        ov = json.loads(self.get("/api/overlay"))
        self.assertEqual((ov["style"], ov["left"]["name"]), (3, "Saints"))
        bans = json.loads(self.get("/api/map-bans"))
        oasis = next(t for t in bans["columns"][0]["maps"] if t["map"] == "Oasis")
        self.assertEqual((oasis["state"], oasis["team"]), ("won", "left"))
        # Changing the rules starts a new veto; the scores stay.
        state = self.post("/api/config", {"map_bans": {"rules": "nace"}})
        self.assertEqual(state["config"]["map_bans"]["steps"], [])
        self.assertEqual(state["overlay"]["left"]["score"], 1)
        snap = self.app._engine_snapshot()
        self.assertEqual(snap["fields"]["left_score"], 1)
        self.assertEqual([g["path"] for g in snap["hud"]["graphics"]], ["/overlay", "/map-bans?bg=0"])

    def test_stats_and_hero_pictures(self):
        players = [{"name": "Saint1", "hero": "soldier 76", "kills": "22", "deaths": 6, "assists": 5,
                    "damage": 11234, "healing": 300}, {"name": "Saint2", "hero": "lucio", "healing": -4}]
        state = self.post("/api/stats", {"side": "left", "players": players})
        left = state["stats"]["left"]
        self.assertEqual(len(left), 5)
        self.assertEqual((left[0]["hero"], left[0]["kills"], left[1]["hero"], left[1]["healing"]), ("Soldier: 76", 22, "Lúcio", 0))
        os.makedirs(heroes.PICTURE_DIR)
        with open(os.path.join(heroes.PICTURE_DIR, "soldier-76.png"), "wb") as f:
            f.write(b"\x89PNG picture")
        self.assertEqual(self.get("/heroes/soldier-76"), b"\x89PNG picture")
        f = self.app._engine_snapshot()["fields"]
        self.assertEqual((f["left_p1_name"], f["left_p1_hero"], f["left_p1_damage"], f["left_p1_healing"]),
                         ("Saint1", "Soldier: 76", 11234, 300))
        self.assertTrue(f["left_p1_hero_icon"].endswith("/heroes/soldier-76"))
        self.assertEqual(f["left_p2_hero_icon"], "")       # no picture of Lúcio yet
        # A screenshot (read by a stand-in for Windows' text recognition).
        read, scoreboard.read_bytes = scoreboard.read_bytes, lambda data: scoreboard.parse(board(Scoreboard.TEAMS), 1920)
        try:
            state = self.post("/api/stats/screenshot", {"data": "data:image/png;base64,iVBORw0KGgo="})
        finally:
            scoreboard.read_bytes = read
        self.assertEqual(state["read"]["players"], 7)
        self.assertEqual((state["stats"]["left"][0]["name"], state["stats"]["left"][0]["hero"], state["stats"]["left"][0]["damage"]),
                         ("Saint1", "Soldier: 76", 11234))
        self.assertEqual(state["stats"]["right"][0]["name"], "Hound One")
        state = self.post("/api/stats", {"swap": True})
        self.assertEqual(state["stats"]["left"][0]["name"], "Hound One")
        state = self.post("/api/stats", {"swap": True})
        # Swap sides: the teams, their maps won, Team A and their stats.
        self.post("/api/config", {"left": {"name": "Saints", "color": "#00ff00"}, "right": {"name": "Hounds", "color": "#00ccff"},
                                  "map_bans": {"steps": [{"map": "Busan"}], "results": ["left"]}})
        state = self.post("/api/swap", {})
        c = state["config"]
        self.assertEqual((c["left"]["name"], c["left"]["color"], c["right"]["name"]), ("Hounds", "#00ccff", "Saints"))
        self.assertEqual((c["map_bans"]["results"], c["map_bans"]["team_a"]), (["right"], "right"))
        self.assertEqual((state["overlay"]["right"]["score"], state["stats"]["right"][0]["name"]), (1, "Saint1"))
        state = self.post("/api/stats", {"clear": True})
        self.assertEqual((state["stats"]["right"][0]["name"], state["stats"]["right"][0]["kills"]), ("Saint1", 0))


if __name__ == "__main__":
    unittest.main()
