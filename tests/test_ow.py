import json
import os
import shutil
import tempfile
import unittest
import urllib.request

from owhud import maps
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


class Server(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        config = Config(os.path.join(self.dir, "config.json"))
        config.update({"http_port": 8797})
        self.app = HudApp(config)
        self.app.updater.check_in_background = lambda: None
        start(self.app)

    def tearDown(self):
        self.app.stop()
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


if __name__ == "__main__":
    unittest.main()
