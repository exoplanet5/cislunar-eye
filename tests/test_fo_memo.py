"""fo verdict memo (REVIEW DW-09) and the update-fits gate:
    ~/.venvs/astro313/bin/python -I -m unittest tests.test_fo_memo

choose() is exercised with a stub Fit (no find_orb) and, where fo is installed, with the real one on the
8NIQ001 fixture; fits_changed.py runs on a temporary copy of the neocpsat inputs.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cle.adapters import fo  # noqa: E402
from cle.timeutil import window  # noqa: E402

FX = ROOT / "tests/fixtures/adapters"
WIN = window("2026-10-21T13:00:00Z")
LINES = [ln[:80] for ln in (FX / "8NIQ001.txt").read_text().splitlines() if len(ln) >= 80]
NEW_OBS = LINES[-1][:15] + "2026 10 20.50000 01 02 03.00 +04 05 06.0          19.0 G      Z99"
TARGET = {"cid": "stub", "regime": "CISLUNAR_HEO", "primary_ephem": "neocpsat_fit:x/a.html@2026-10-02[..2026-10-01]"}


class StubFit:
    """Stands in for fo.Fit: records every construction, window table and sigma run; e per page decides the verdict."""
    made, tables, sigmas, E = [], [], [], {"a.html": 1200.0, "b.html": 0.5}

    def __init__(self, label, ref, a_s, b_s, step, with_sigma=True, sigma_step=21600.0, retry=True, table=True):
        self.lines, args, self.page, self.members = ref
        self.args = list(args) + (["-y", "10"] if self.page.name == "b.html" and "-y" not in args else [])
        StubFit.made.append((self.page.name, list(args), retry))
        self.first, self.last = fo.obs_span(self.lines)
        self.el = {"e": (StubFit.E[self.page.name], None), "a": (1.0, None), "i": (1.0, None), "n_obs": 4, "n_used": 4,
                   "rms_arcsec": 0.5, "epoch_tt_s": self.first, "helio": False}
        self.reproduced, self.dir, self.sigma, self.sigma_arcsec = True, Path("/nonexistent"), None, None
        self.t = np.array([a_s, b_s])
        if table:
            self.table()
        if with_sigma:
            self.sigma_table()

    def table(self):
        StubFit.tables.append(self.page.name)
        return self

    def sigma_table(self, step=21600.0):
        StubFit.sigmas.append(self.page.name)
        self.sigma_arcsec = self.t, np.array([1.0, 1.0])
        return self

    def check(self):
        return {"accepted": True, "n_obs": 4, "n_used": 4, "rms_arcsec": 0.5}

    def later_oc(self, lines, ctx):
        return None


class VerdictKeyTest(unittest.TestCase):
    def test_key_follows_the_observations(self):
        k = fo.verdict_key(LINES, ["-h", "3"], LINES)
        self.assertEqual(k, fo.verdict_key(list(reversed(LINES)), ["-h", "3"], LINES))
        self.assertNotEqual(k, fo.verdict_key(LINES + [NEW_OBS], ["-h", "3"], LINES))
        self.assertNotEqual(k, fo.verdict_key(LINES, ["-h", "3"], LINES + [NEW_OBS]))
        self.assertNotEqual(k, fo.verdict_key(LINES, ["-h", "0"], LINES))
        self.assertEqual(len(k), 64)


class ChooseMemoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cle_fo_memo_")
        self.obs = {"a": LINES[:8], "b": LINES[:12]}
        resolve = lambda ref: (self.obs[ref[2]], ["-h", "3"], Path(ref.split("@")[0]), {})
        self.patches = [mock.patch.object(fo, "VERDICTS", Path(self.tmp.name) / "verdicts.json"),
                        mock.patch.object(fo, "Fit", StubFit), mock.patch.object(fo, "resolve", resolve),
                        mock.patch.object(fo, "candidates", lambda target: (["x/a.html@2026-10-02"], ["x/b.html@2026-09-20"]))]
        for p in self.patches:
            p.start()
        StubFit.made.clear()
        StubFit.tables.clear()
        StubFit.sigmas.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_rejected_candidate_is_skipped_on_the_second_choose(self):
        StubFit.E["b.html"] = 1200.0            # both unbound
        try:
            with self.assertRaises(RuntimeError) as cm:
                fo.choose(TARGET, WIN, None)
            self.assertIn("known-unfittable since", str(cm.exception))
            self.assertIn("2 candidates rejected", str(cm.exception))
            self.assertIn("a: unbound (e=1.2e+03)", str(cm.exception))
            self.assertEqual([m[0] for m in StubFit.made], ["a.html", "b.html"])
            self.assertEqual((StubFit.tables, StubFit.sigmas), ([], []), "no table or sigma run for a rejected candidate")
            saved = json.loads(fo.VERDICTS.read_text())
            self.assertEqual({v["usable"] for v in saved.values()}, {False})
            first = {v["page"]: v["first_seen"] for v in saved.values()}
            with self.assertRaises(RuntimeError) as cm:
                fo.choose(TARGET, WIN, None)
            self.assertEqual(len(StubFit.made), 2, "no fo run for a known-rejected candidate")
            self.assertIn(f"known-unfittable since {min(first.values())[:10]}", str(cm.exception))
            saved = json.loads(fo.VERDICTS.read_text())
            self.assertEqual({v["page"]: v["first_seen"] for v in saved.values()}, first)
            self.obs["a"] = self.obs["a"] + [NEW_OBS]          # a new observation in the newest batch
            with self.assertRaises(RuntimeError):
                fo.choose(TARGET, WIN, None)
            self.assertEqual([m[0] for m in StubFit.made], ["a.html", "b.html"] * 2)
        finally:
            StubFit.E["b.html"] = 0.5

    def test_accepted_candidate_reruns_with_its_recorded_arguments(self):
        fit, later, tried, _ = fo.choose(TARGET, WIN, None)
        self.assertEqual(fit.page.name, "b.html")
        self.assertEqual([m[0] for m in StubFit.made], ["a.html", "b.html"])
        self.assertEqual(StubFit.made[1][1:], (["-h", "3"], True))
        self.assertEqual([t["usable"] for t in tried], [False, True])
        self.assertEqual((StubFit.tables, StubFit.sigmas), (["b.html"], ["b.html"]), "window table and E16 for the chosen fit only")
        fit2, _, tried2, _ = fo.choose(TARGET, WIN, None)
        self.assertEqual(StubFit.made[2:], [("b.html", ["-h", "3", "-y", "10"], False)])
        self.assertEqual((StubFit.tables, StubFit.sigmas), (["b.html"] * 2, ["b.html"] * 2))
        self.assertFalse(tried2[0]["usable"])
        self.assertRegex(tried2[0]["known_since"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        rec = [v for v in json.loads(fo.VERDICTS.read_text()).values() if v["page"] == "b.html"][0]
        self.assertEqual((rec["usable"], rec["args"], rec["e"]), (True, ["-h", "3", "-y", "10"], 0.5))
        self.assertEqual(set(rec), {"cid", "page", "args", "accepted", "usable", "e", "n_obs", "n_used", "rms_arcsec",
                                    "later_oc_arcsec", "reproduced", "elements", "epoch_state", "first_seen", "run_utc"})


@unittest.skipUnless(fo.FO.exists(), "needs local find_orb fo")
class RealFoMemoTest(unittest.TestCase):
    """choose() with the real Fit on 8NIQ001: the record carries elements and the epoch state, and the memoised
    rerun (recorded arguments, no retry) gives the same vector table."""

    def test_record_and_identical_rerun(self):
        from cle.context import Context
        target = {"cid": "t8n", "regime": "EARTH_NEAR_HELIO", "primary_ephem": TARGET["primary_ephem"]}   # e = 7.7
        ref = str(FX / "8NIQ001.htm") + "@2026-10-02"
        with tempfile.TemporaryDirectory(prefix="cle_fo_memo_") as tmp, mock.patch.object(fo, "CACHE", Path(tmp)), \
                mock.patch.object(fo, "VERDICTS", Path(tmp) / "v.json"), \
                mock.patch.object(fo, "resolve", lambda r: (LINES, ["-h", "3"], FX / "8NIQ001.htm", {})), \
                mock.patch.object(fo, "candidates", lambda t: ([ref], [])):
            fit, _, tried, _ = fo.choose(target, WIN, Context())
            self.assertEqual(fit.dir.name, fit.label + "_E0")                     # the window table of the chosen fit
            self.assertTrue(all((fit.dir.parent / (fit.label + s)).exists() for s in ("_f_E0", "_E16")))
            rec = next(iter(json.loads(fo.VERDICTS.read_text()).values()))
            self.assertTrue(rec["usable"] and rec["accepted"])
            self.assertEqual(rec["elements"]["n_obs"], 16)
            self.assertEqual(rec["epoch_state"]["centre"], "central body")
            self.assertTrue(1e3 < np.linalg.norm(rec["epoch_state"]["r_km"]) < 1e7)
            self.assertTrue(0.1 < np.linalg.norm(rec["epoch_state"]["v_km_s"]) < 30)
            fit2, _, _, _ = fo.choose(target, WIN, Context())
            np.testing.assert_array_equal(fit2.x, fit.x)
            self.assertEqual(fit2.args, rec["args"])


class FitsChangedTest(unittest.TestCase):
    def test_exit_codes_on_a_temp_copy(self):
        with tempfile.TemporaryDirectory(prefix="cle_neo_") as d:
            neo, rec = Path(d) / "neocpsat", Path(d) / "inputs.sha256"
            (neo / "data").mkdir(parents=True)
            (neo / "mpecs").mkdir()
            (neo / "catalog.csv").write_text("a,b\n")
            (neo / "data/x.txt").write_text(LINES[0] + "\n")
            (neo / "mpecs/x.html").write_text("<html>x</html>")
            run = lambda *a: subprocess.run([sys.executable, "-I", str(ROOT / "scripts/fits_changed.py"), "--neocpsat",
                                             str(neo), "--record", str(rec), *a], capture_output=True, text=True)
            self.assertEqual(run().returncode, 0)                   # no record
            self.assertFalse(rec.exists())
            self.assertEqual(run("--mark").returncode, 0)
            self.assertEqual(len(rec.read_text().split()), 3)
            self.assertEqual(run().returncode, 1)                   # unchanged
            (neo / "data/x.txt").write_text(LINES[0] + "\n" + LINES[1] + "\n")
            self.assertEqual(run().returncode, 0)                   # one more obs line
            self.assertEqual(run("--mark").returncode, 0)
            (neo / "mpecs/y.html").write_text("<html>y</html>")
            r = run("--status")
            self.assertEqual(r.returncode, 0)                       # a new page
            self.assertIn("recorded ", r.stdout)
            self.assertIn("fits inputs: changed (4 files", r.stdout)
            self.assertEqual(run("--mark").returncode, 0)
            r = run("--status")
            self.assertEqual((r.returncode, "unchanged" in r.stdout), (1, True))


if __name__ == "__main__":
    unittest.main()
