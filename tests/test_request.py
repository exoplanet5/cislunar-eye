"""On-request NEOCP helper: ~/.venvs/astro313/bin/python -I -m unittest tests.test_request   (no network)

cle.pluto parsers, cle.request (build / fetch / summary / errors), the neocp adapter's page state-vector path and the
find_orb path, and scripts/serve.py on an ephemeral port with stubbed upstream fetchers. The objects are checked with
the structure tests/data_contract.test.mjs applies to tracks and tracklets.

Fixtures: tests/fixtures/adapters/8NIQ001.{htm,txt} + 8NIQ001_page.json (geocentric hyperbolic flyby, 16 obs, the
scheduled flow's parse of the page) and tests/fixtures/request/ (MANIFEST.json, downloaded 2026-11-01): ZTF10H5.htm +
ZTF10H5_mpc.json (heliocentric fit, 4 MPC obs), C46PP91.htm (bound geocentric fit; off the NEOCP, MPC returns ''),
summary.htm (neocp2 summary, 25 rows) and neocp.json (MPC's list).
"""
import base64
import errno
import importlib.util
import io
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cle import catalog, pluto, request, tracklets  # noqa: E402
from cle.adapters import fo, neocp  # noqa: E402
from cle.context import Context  # noqa: E402
from cle.statetable import Segment, StateTable  # noqa: E402
from cle.timeutil import Window, parse_iso  # noqa: E402

FX = ROOT / "tests/fixtures/adapters"
RQ = ROOT / "tests/fixtures/request"
CTX = Context()
HAVE_FO = fo.FO.exists() and (Path.home() / ".find_orb/environ.dat").exists()
NIQ_HTML, NIQ_OBS = (FX / "8NIQ001.htm").read_text(), (FX / "8NIQ001.txt").read_text()
ZTF_HTML, ZTF_OBS = (RQ / "ZTF10H5.htm").read_text(), json.loads((RQ / "ZTF10H5_mpc.json").read_text())[0]["OBS80"]
PP_HTML = (RQ / "C46PP91.htm").read_text()
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
QUALITY = {"ref", "pred", "fit", "extrap", "gp", "conic"}
JOIN = {"start", "continuous", "splice", "gap", "source"}
MODES = {"fo", "fo_daily", "page_sv", "page_table"}


def win_at(now_iso, half_d=15):
    n = parse_iso(now_iso)
    return Window(now_s=n, t0_s=n - half_d * 86400, t1_s=n + half_d * 86400)


WIN_NIQ = win_at("2026-10-22T12:00:00Z")          # dist/data window of the 2026-10-22 build
WIN_ZTF = win_at("2026-11-01T12:00:00Z")


def b64(s, dtype):
    return np.frombuffer(base64.b64decode(s), dtype=dtype)


def decode_track(track):
    """The Track as a geocentric callable (cle.tracklets.track_at over its segments)."""
    segs = []
    for s in track["segments"]:
        rv = b64(s["rv"], "<f8" if track["dtype"] == "f64" else "<f4").reshape(-1, 6).astype(float)
        segs.append(Segment(t0_s=s["t0_ms"] / 1000, step_s=s["step_s"], r_km=rv[:, :3], v_kms=rv[:, 3:], source="x",
                            quality=s["quality"]))
    return tracklets.track_at(StateTable(cle_id="x", centre="earth", segments=segs, provenance=[], validation={}, caveat=""))


class Contract:
    """The checks of tests/data_contract.test.mjs (checkTrack / checkTracklets) plus the helper object's own fields."""

    def check_track(self, track, win):
        self.assertIn(track["center"], ("earth", "moon"))
        self.assertIn(track["dtype"], ("f32", "f64"))
        self.assertTrue(track["srcs"] and all(isinstance(x, str) and ":" in x for x in track["srcs"]))
        self.assertEqual(len(set(track["srcs"])), len(track["srcs"]))
        end, used = -math.inf, set()
        for k, s in enumerate(track["segments"]):
            self.assertNotIn("t0", s)
            self.assertIsInstance(s["t0_ms"], int)
            self.assertEqual(s["t0_ms"] % 1000, 0)
            self.assertTrue(s["step_s"] > 0 and isinstance(s["n"], int) and s["n"] >= 2)
            self.assertIn(s["quality"], QUALITY)
            self.assertIn(s["join_before"], JOIN)
            if k == 0:
                self.assertEqual(s["join_before"], "start")
            self.assertTrue(isinstance(s["src"], int) and 0 <= s["src"] < len(track["srcs"]))
            used.add(s["src"])
            self.assertTrue("jump_km" not in s or s["jump_km"] != 0)
            rv = b64(s["rv"], "<f8" if track["dtype"] == "f64" else "<f4")
            self.assertEqual(len(rv), 6 * s["n"])
            self.assertTrue(np.isfinite(rv).all())
            if "sigma_km" in s:
                self.assertEqual(len(b64(s["sigma_km"], "<f4")), s["n"])
            self.assertGreaterEqual(s["t0_ms"], end - 1)
            end = s["t0_ms"] + (s["n"] - 1) * s["step_s"] * 1000
        self.assertEqual(used, set(range(len(track["srcs"]))))
        self.assertGreaterEqual(track["segments"][0]["t0_ms"], win.t0_s * 1000 - 1)
        self.assertLessEqual(end, win.t1_s * 1000 + 1)

    def check_tracklets(self, items):
        for tk in items:
            self.assertIsInstance(tk["stn"], str)
            self.assertTrue(isinstance(tk["n"], int) and tk["n"] >= 1 and len(tk["t_ms"]) == tk["n"])
            for k in ("ra_deg", "dec_deg", "mag", "range_km", "resid_arcsec"):
                self.assertEqual(len(tk[k]), tk["n"], k)
            self.assertEqual(len(b64(tk["dot_km"], "<f8")), 3 * tk["n"])
            self.assertEqual(len(b64(tk["stn_km"], "<f8")), 3 * tk["n"])

    def check_object(self, o, win, desig):
        self.assertTrue({"cid", "catalog", "track", "fit", "caveat", "tracklets", "rms_arcsec", "request"} <= set(o))
        json.dumps(o, allow_nan=False)
        self.check_track(o["track"], win)
        self.check_tracklets(o["tracklets"])
        c = o["catalog"]
        self.assertEqual((c["id"], c["name"], c["label"], o["cid"]), (desig.lower(), desig, desig, desig.lower()))
        self.assertEqual((c["group"], c["tier"], c["draw"], c["drawn"], c["default_visible"], c["chunk"]),
                         ("NEOCP · requested", 1, True, True, True, None))
        self.assertIn(c["regime"], catalog.CHUNK)
        self.assertNotIn("natural", c)
        self.assertTrue(c["sources"][0].startswith(f"*pluto_mpec:https://www.projectpluto.com/neocp2/mpecs/{desig}.htm["))
        self.assertIn("neocp_obs:mpc-api", c["sources"])
        self.assertEqual([x["label"] for x in c["links"]], ["Project Pluto", "MPC NEOCP"])
        self.assertTrue(c["trail"]["past_s"] >= 0 and c["trail"]["future_s"] == 86400)
        f = o["fit"]
        self.assertIn(f["mode"], MODES)
        self.assertTrue(isinstance(f["n_obs"], int) and isinstance(f["arc_d"], float) and isinstance(f["rms_arcsec"], float))
        self.assertTrue(ISO.match(f["obs_first"]) and ISO.match(f["obs_last"]))
        self.assertTrue(f["stations"] and all(isinstance(x, str) and len(x) == 3 for x in f["stations"]))
        r = o["request"]
        self.assertEqual(r["desig"], desig)
        self.assertEqual(r["mode"], f["mode"])
        self.assertTrue(ISO.match(r["fetched_utc"]) and ISO.match(r["page_created_utc"]))
        for k in ("n_obs_mpc", "n_obs_page", "n_obs_used"):
            self.assertIsInstance(r[k], int)
        self.assertIsInstance(r["on_neocp"], bool)
        self.assertIsInstance(r["seconds"], float)
        self.assertEqual(sum(t["n"] for t in o["tracklets"]) <= r["n_obs_used"], True)


class PlutoTest(unittest.TestCase):
    def test_parse_matches_the_scheduled_parse(self):
        """parse_mpec on 8NIQ001.htm equals the scheduled flow's parsed.json entry where the fields overlap."""
        page = json.loads((FX / "8NIQ001_page.json").read_text())
        p = pluto.parse_mpec(NIQ_HTML, "8NIQ001")
        for k in page:
            self.assertEqual(p[k], page[k], k)
        self.assertEqual(p["obs_lines"], pluto.obs80_lines(NIQ_OBS))      # 16 lines, the cached obs file
        self.assertEqual(pluto.page_center(p["elements"]), "earth")
        self.assertFalse(p["astrometry_redacted"])

    def test_geocentric_and_heliocentric_pages(self):
        c = pluto.parse_mpec(PP_HTML, "C46PP91")
        self.assertEqual((pluto.page_center(c["elements"]), c["created"], len(c["obs_lines"])), ("earth", "2026-10-21T10:35:07Z", 8))
        self.assertEqual(len(c["comments"]["sv_central_au"]), 3)
        g = pluto.derive_geocentric(c["elements"])
        self.assertAlmostEqual(g["a_km"], 119433.0503)
        self.assertAlmostEqual(g["apogee_km"], 192080.1713)
        self.assertEqual(pluto.regime_for_bound(g["period_d"], g["apogee_km"]), "CISLUNAR_HEO")
        z = pluto.parse_mpec(ZTF_HTML, "ZTF10H5")
        self.assertEqual((pluto.page_center(z["elements"]), len(z["obs_lines"])), ("sun", 4))
        self.assertNotIn("sv_central_au", z["comments"])
        self.assertEqual(len(z["ephemeris"]), 48)
        self.assertEqual(z["ephemeris"][0]["sig_arcsec"], 39 * 3600)

    def test_obs80_merge(self):
        """MPC lines (no '*', no NEOCP note) and the page's merge by astrometric key, the page copy first."""
        page = pluto.parse_mpec(ZTF_HTML, "ZTF10H5")["obs_lines"]
        mpc = pluto.obs80_lines("junk\n" + ZTF_OBS + "   \n")
        self.assertEqual(len(mpc), 4)
        self.assertNotEqual(set(page), set(mpc))
        self.assertEqual({pluto.astrometric_key(x) for x in page}, {pluto.astrometric_key(x) for x in mpc})
        merged = pluto.merge_obs(page, mpc)
        self.assertEqual(merged, sorted(page, key=lambda ln: (pluto.obs80_time(ln), ln[77:80])))
        extra = mpc[0][:23] + "26.300000" + mpc[0][32:]
        self.assertEqual(len(pluto.merge_obs(page, mpc + [extra])), 5)
        self.assertEqual(pluto.merge_obs(page, mpc + [extra])[-1], extra)
        st = pluto.obs_stats(merged)
        self.assertEqual((st["n"], st["stations"]), (4, ["I41"]))

    def test_satellite_second_lines_kept(self):
        first = "     SWAN26Q 2S2026 08 11.95713016 13 38.424-39 50 06.00   ~AIHo       vNEOCPC49"
        second = "     SWAN26Q 2s2026 08 11.9571302 +0.10231295 +0.99336454 +0.43090773   NEOCPC49"
        self.assertEqual(pluto.obs80_lines(f"{first}\n{second}\n{second}\n"), [first, second])
        self.assertEqual(pluto.optical([first, second]), [first])
        self.assertEqual(len(tracklets.parse(pluto.merge_obs([first, second]))), 1)

    def test_summary(self):
        s = pluto.parse_summary((RQ / "summary.htm").read_text())
        self.assertEqual((s["regenerated"], len(s["rows"]), s["unparsed"]), ("2026-11-01T12:35:01Z", 25, []))
        self.assertFalse(any(r["geo"] for r in s["rows"].values()))
        self.assertEqual(s["rows"]["ZTF10H5"]["moid_au"], 0.0003)
        geo = (RQ / "summary.htm").read_text().replace("   0.0003       23 20 31", "   (geo)        23 20 31")
        r = pluto.parse_summary(geo)["rows"]["ZTF10H5"]
        self.assertEqual((r["geo"], r["moid_au"]), (True, None))

    def test_designations_and_case_safe_names(self):
        for d in ("ZTF10Gu", "C46PP91", "8NIQ001", "X91537"):
            self.assertTrue(pluto.valid_desig(d))
        for d in ("ab", "ABCDEFGHI", "C46 P91", "../etc", "ZTF10Gu\n", None, "ÄBCDE"):
            self.assertFalse(pluto.valid_desig(d))
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(Path(tmp) / "ZTF10GU")
            self.assertEqual(pluto.case_safe_name("ZTF10GU", tmp), "ZTF10GU")
            other = pluto.case_safe_name("ZTF10Gu", tmp)
            self.assertNotEqual(other.lower(), "ztf10gu")
            self.assertEqual(other, pluto.case_safe_name("ZTF10Gu", tmp))


class BuildTest(Contract, unittest.TestCase):
    """request.build on the fixtures; find_orb off unless the test says otherwise (fast, deterministic)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cle_req_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build(self, desig, html, obs, win, **kw):
        return request.build(desig, html, obs, win, CTX, run_dir=Path(tempfile.mkdtemp(dir=self.tmp)), **kw)

    def test_state_vector_path_with_fo_missing(self):
        """fo forced missing: the page state vector + cle.nbody reproduces Bill Gray's table and the page residuals."""
        with mock.patch.object(fo, "FO", Path("/nonexistent/fo")):
            o = self.build("8NIQ001", NIQ_HTML, NIQ_OBS, WIN_NIQ)
        self.check_object(o, WIN_NIQ, "8NIQ001")
        f, r = o["fit"], o["request"]
        self.assertEqual((f["mode"], f["fallback"]), ("page_sv", "find_orb not installed"))
        self.assertLess(f["page_check"]["table_max_sep_arcsec"], 1.0)
        self.assertEqual(o["track"]["srcs"], ["pluto_sv:8NIQ001"])
        self.assertEqual({t["stn"] for t in o["tracklets"]}, {"I93", "K19", "L01", "V06"})
        self.assertEqual(sum(t["n"] for t in o["tracklets"]), 16)
        self.assertLess(abs(o["rms_arcsec"] - 0.39) / 0.39, 0.1)           # the page's mean residual
        self.assertEqual((f["n_obs"], f["stations"], f["obs_first"]), (16, ["I93", "K19", "L01", "V06"], "2026-10-15T19:29:21Z"))
        self.assertTrue(f["geo"] and f["flyby"])
        self.assertEqual((r["n_obs_mpc"], r["n_obs_page"], r["n_obs_used"], r["on_neocp"], r["find_orb"]), (16, 16, 16, True, False))
        self.assertEqual(o["catalog"]["regime"], "EARTH_NEAR_HELIO")          # unbound, 9e6 km out at now
        self.assertIn("without covariance", o["caveat"])
        q = [s["quality"] for s in o["track"]["segments"]]
        self.assertEqual((q[0], q[-1]), ("fit", "extrap"))
        span = decode_track(o["track"]).span
        self.assertAlmostEqual(span[1], WIN_NIQ.now_s + 3 * 86400)            # now + 3 d
        self.assertLessEqual(span[0], parse_iso(f["obs_first"]) - 86400)     # first obs - 1 d

    def test_heliocentric_page(self):
        o = self.build("ZTF10H5", ZTF_HTML, ZTF_OBS, WIN_ZTF, use_fo=False)
        self.check_object(o, WIN_ZTF, "ZTF10H5")
        self.assertEqual((o["fit"]["mode"], o["catalog"]["regime"], o["track"]["dtype"]), ("page_sv", "EARTH_NEAR_HELIO", "f64"))
        self.assertEqual([(t["stn"], t["n"]) for t in o["tracklets"]], [("I41", 4)])
        self.assertFalse(o["fit"]["geo"])
        self.assertIsNotNone(o["fit"]["sigma_limit_utc"])            # the page's table sigma is 39 deg: track cut short
        self.assertLess(parse_iso(o["fit"]["sigma_limit_utc"]), parse_iso("2026-10-27T00:00:00Z"))
        self.assertEqual(o["catalog"]["elements"]["center"], "sun")

    def test_left_the_neocp_and_kept_observations(self):
        """C46PP91: MPC lists nothing (left the NEOCP), the page's 8 obs and any kept earlier ones are used."""
        o = self.build("C46PP91", PP_HTML, "", WIN_NIQ, use_fo=False)
        self.check_object(o, WIN_NIQ, "C46PP91")
        r = o["request"]
        self.assertEqual((r["on_neocp"], r["n_obs_mpc"], r["n_obs_page"], r["n_obs_used"]), (False, 0, 8, 8))
        self.assertEqual((o["catalog"]["regime"], o["fit"]["geo"], o["catalog"]["elements"]["center"]), ("NEOCP_GEO", True, "earth"))
        self.assertEqual({t["stn"] for t in o["tracklets"]}, {"703", "I52"})
        page = pluto.parse_mpec(PP_HTML, "C46PP91")["obs_lines"]
        later = page[-1][:23] + "16.330000" + page[-1][32:]
        o2 = self.build("C46PP91", PP_HTML, "", WIN_NIQ, use_fo=False, kept=page + [later])
        self.assertEqual(o2["request"]["n_obs_used"], 9)
        o3 = self.build("C46PP91", PP_HTML, page[0] + "\n", WIN_NIQ, use_fo=False, kept=page + [later])
        self.assertEqual((o3["request"]["n_obs_used"], o3["request"]["on_neocp"]), (8, True))   # MPC lists it: kept ignored

    def test_errors(self):
        for code, msg in request.MSG.items():
            self.assertTrue(len(msg) <= 60 and not msg.endswith("."), code)
        cases = [(("ab", NIQ_HTML, NIQ_OBS, WIN_NIQ), "bad_designation", 400),
                 (("QQZZ999", None, "", WIN_NIQ), "not_found", 404),
                 (("8NIQ001", None, NIQ_OBS, WIN_NIQ), "no_orbit", 422),
                 (("8NIQ001", re.sub(r'<a name="elements"></a>.*?</pre>', "", NIQ_HTML, flags=re.S), NIQ_OBS, WIN_NIQ), "no_orbit", 422),
                 (("8NIQ001", NIQ_HTML, NIQ_OBS, win_at("2026-12-20T00:00:00Z")), "outside_window", 422)]
        for args, code, status in cases:
            with self.subTest(code=code), self.assertRaises(request.RequestError) as cm:
                self.build(*args, use_fo=False)
            self.assertEqual((cm.exception.code, cm.exception.status), (code, status))
            self.assertLessEqual(len(cm.exception.message), 60)
        with self.assertRaises(request.RequestError) as cm:
            self.build("8NIQ001", None, NIQ_OBS, WIN_NIQ, use_fo=False)
        self.assertEqual(cm.exception.message, "neocp2 page not generated yet (≈ hh:35)")
        # MPC failed and Bill Gray has no page yet: an upstream failure, not "unknown designation"
        with self.assertRaises(request.RequestError) as cm:
            self.build("QQZZ999", None, "", WIN_NIQ, use_fo=False, mpc_error="timeout")
        self.assertEqual((cm.exception.code, cm.exception.status, cm.exception.message),
                         ("upstream", 502, "MPC unreachable (timeout)"))
        with self.assertRaises(request.RequestError) as cm:                     # earlier obs kept: still "no page yet"
            self.build("8NIQ001", None, "", WIN_NIQ, use_fo=False, mpc_error="timeout", kept=NIQ_OBS.splitlines())
        self.assertEqual((cm.exception.code, cm.exception.message), ("no_orbit", request.MSG["no_page"]))
        for t0, t1, now in (("", "1", None), ("a", "b", None), (2e12, 1e12, None), (1e12, 1e12 + 50 * 86400e3, None),
                            (1e12, 1e12 + 86400e3, "x")):
            with self.assertRaises(request.RequestError) as cm:
                request.window_from(t0, t1, now)
            self.assertEqual((cm.exception.code, cm.exception.status), ("bad_request", 400))
        w = request.window_from(1e12, 1e12 + 86400e3, None, wall_s=0)
        self.assertEqual(w.now_s, 1e9)                                            # wall clock clamped into the window
        self.assertEqual(request.window_from("1000000000000", "1000086400000", "1000043200000").now_s, 1e9 + 43200)

    def test_find_orb_failure_reason_is_short_and_path_free(self):
        """A failed fo run (here /usr/bin/false: no output) falls back to the state vector; the answer carries a short
        reason without the run directory (fo's message names it), which goes to stderr only."""
        run = Path(tempfile.mkdtemp(dir=self.tmp))
        with mock.patch.object(fo, "FO", Path("/usr/bin/false")), mock.patch.object(fo, "LOCK", self.tmp / "fo.lock"), \
                mock.patch("sys.stderr", new=io.StringIO()) as err:
            o = request.build("8NIQ001", NIQ_HTML, NIQ_OBS, WIN_NIQ, CTX, run_dir=run)
        self.check_object(o, WIN_NIQ, "8NIQ001")
        self.assertEqual((o["fit"]["mode"], o["fit"]["fallback"]), ("page_sv", "find_orb failed (no fit)"))
        self.assertIn("find_orb failed (no fit)", o["request"]["warnings"])
        body = json.dumps(o)
        self.assertNotIn(str(run), body)
        self.assertNotIn(str(Path.home()), body)
        self.assertIn(str(run), err.getvalue())
        for e, why in ((subprocess.TimeoutExpired("fo", 1), "timeout"), (FileNotFoundError(2, "x", "/a/fo"), "did not run"),
                       (RuntimeError("/a/b"), "no fit"), (ValueError("/a/b"), "bad output"), (KeyError("x"), "bad output")):
            self.assertEqual(neocp.fo_reason(e), why)

    @unittest.skipUnless(HAVE_FO, "needs local find_orb fo")
    def test_find_orb_path_and_lock_timeout(self):
        """fo path in a per-request run directory (not work/cache/fo), close to the page state vector; then, with the
        fo lock held by another process, the page state vector after lock_timeout."""
        runs = ROOT / "work/cache/request/fo" / f"test-{os.getpid()}"            # short: fo's 93-character -O limit
        lock, stamp = self.tmp / "fo.lock", (fo.CACHE, fo.STAMP)
        try:
            with mock.patch.object(fo, "LOCK", lock):
                o = request.build("8NIQ001", NIQ_HTML, NIQ_OBS, WIN_NIQ, CTX, run_dir=runs / "a")
                self.check_object(o, WIN_NIQ, "8NIQ001")
                self.assertIn(o["fit"]["mode"], ("fo", "fo_daily"))
                self.assertFalse(o["fit"]["page_check"]["fit_disagrees"])
                self.assertTrue(any(p.name.startswith("8niq001") for p in (runs / "a").iterdir()))
                self.assertEqual((fo.CACHE, fo.STAMP), stamp)                              # restored
                sv = self.build("8NIQ001", NIQ_HTML, NIQ_OBS, WIN_NIQ, use_fo=False)
                a, b = decode_track(o["track"]), decode_track(sv["track"])
                t = np.linspace(max(a.span[0], b.span[0]), min(a.span[1], b.span[1]), 300)
                self.assertLess(np.linalg.norm(a(t)[:, :3] - b(t)[:, :3], axis=1).max(), 100.0)  # 15 km with Find_Orb 2026 Mar 01, 68 km with 2026 Oct 21
                holder = subprocess.Popen([sys.executable, "-I", "-c", "import fcntl, sys, time; f = open(sys.argv[1], 'w'); "
                                           "fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); time.sleep(60)",
                                           str(lock)], stdout=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(holder.stdout.readline().strip(), "held")
                    t0 = time.time()
                    o2 = request.build("8NIQ001", NIQ_HTML, NIQ_OBS, WIN_NIQ, CTX, run_dir=runs / "b", lock_timeout=0.6)
                    self.assertLess(time.time() - t0, 5)
                finally:
                    holder.kill()
                    holder.wait()
                    holder.stdout.close()
                self.assertEqual(o2["fit"]["mode"], "page_sv")
                self.assertIn("find_orb busy", o2["fit"]["fallback"])
                self.assertIn("find_orb busy", " ".join(o2["request"]["warnings"]))
        finally:
            shutil.rmtree(runs, ignore_errors=True)


class FetchTest(Contract, unittest.TestCase):
    """fetch / summary with stubbed upstream getters and a temporary cache."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cle_fetch_"))
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def getters(self, page=NIQ_HTML, obs=NIQ_OBS):
        def get_page(d):
            self.calls.append(("page", d))
            if isinstance(page, Exception):
                raise page
            return page

        def get_obs(d):
            self.calls.append(("obs", d))
            if isinstance(obs, Exception):
                raise obs
            return obs
        return get_page, get_obs

    def test_cache_and_failures(self):
        cdir = request.cache_dir_for("8NIQ001", self.tmp)
        t = parse_iso("2026-11-01T12:40:00Z")
        f = request.fetch("8NIQ001", cdir, now_s=t, getters=self.getters())
        self.assertEqual((f.cached, f.warnings, len(self.calls)), (False, [], 2))
        self.assertEqual(len(pluto.optical(f.kept)), 16)
        self.assertTrue((cdir / "page.htm").exists() and (cdir / "obs.txt").exists())
        f2 = request.fetch("8NIQ001", cdir, now_s=t + 300, getters=self.getters())
        self.assertEqual((f2.cached, len(self.calls), f2.page_html == NIQ_HTML), (True, 2, True))
        self.assertEqual(request.cache_key(f, WIN_NIQ), request.cache_key(f2, WIN_NIQ))
        # past hh:36 (Bill's regeneration) the cache is stale even within 10 min; MPC down: the page and kept obs serve
        f3 = request.fetch("8NIQ001", cdir, now_s=parse_iso("2026-11-01T13:37:00Z"),
                           getters=self.getters(obs=request.Down("timeout")))
        self.assertEqual((f3.cached, f3.obs_text, f3.warnings), (False, "", ["MPC unreachable (timeout)"]))
        self.assertEqual(len(pluto.optical(f3.kept)), 16)
        # neocp2 down: the cached page is used, with a warning; a failed fetch is never served from the cache
        f4 = request.fetch("8NIQ001", cdir, now_s=parse_iso("2026-11-01T13:38:00Z"),
                           getters=self.getters(page=request.Down("HTTP 503")))
        self.assertEqual(f4.page_html, NIQ_HTML)
        self.assertTrue(f4.warnings[0].startswith("neocp2 unreachable (HTTP 503)"))
        self.assertTrue(f4.warnings and not f4.cached)
        n = len(self.calls)
        request.fetch("8NIQ001", cdir, now_s=parse_iso("2026-11-01T13:39:00Z"), getters=self.getters())
        self.assertEqual(len(self.calls), n + 2)
        self.assertFalse(request.fresh(parse_iso("2026-11-01T12:30:00Z"), parse_iso("2026-11-01T12:37:00Z")))
        self.assertTrue(request.fresh(parse_iso("2026-11-01T12:37:00Z"), parse_iso("2026-11-01T12:46:00Z")))
        self.assertFalse(request.fresh(parse_iso("2026-11-01T12:37:00Z"), parse_iso("2026-11-01T12:48:00Z")))

    def test_upstream_errors_and_not_found(self):
        with self.assertRaises(request.RequestError) as cm:
            request.fetch("ZTF10H5", request.cache_dir_for("ZTF10H5", self.tmp), getters=self.getters(page=request.Down("timeout")))
        self.assertEqual((cm.exception.code, cm.exception.status, cm.exception.message), ("upstream", 502, "neocp2 unreachable (timeout)"))
        with self.assertRaises(request.RequestError) as cm:
            request.fetch("ZTF10H5", self.tmp / "z", getters=self.getters(page=request.Down("timeout"), obs=request.Down("timeout")))
        self.assertEqual(cm.exception.message, "MPC and neocp2 unreachable (timeout)")
        f = request.fetch("QQZZ999", self.tmp / "q", getters=self.getters(page=None, obs=""))
        self.assertEqual((f.page_html, f.mpc_error), (None, None))
        with self.assertRaises(request.RequestError) as cm:
            request.respond(f, WIN_NIQ, CTX, use_fo=False, runs=self.tmp / "runs")
        self.assertEqual((cm.exception.code, cm.exception.status), ("not_found", 404))
        # an unknown designation leaves nothing behind: no cache directory, no find_orb run directory
        self.assertFalse((self.tmp / "q").exists())
        self.assertEqual(list((self.tmp / "runs").glob("*")), [])
        # MPC timed out and Bill Gray has no page (yet): 502 upstream with MPC's reason, not 404 not_found
        f = request.fetch("C9MPCDN", self.tmp / "m", getters=self.getters(page=None, obs=request.Down("timeout")))
        self.assertEqual((f.mpc_error, f.warnings), ("timeout", ["MPC unreachable (timeout)"]))
        self.assertFalse((self.tmp / "m").exists())
        with self.assertRaises(request.RequestError) as cm:
            request.respond(f, WIN_NIQ, CTX, use_fo=False, runs=self.tmp / "runs")
        self.assertEqual((cm.exception.code, cm.exception.status, cm.exception.message),
                         ("upstream", 502, "MPC unreachable (timeout)"))
        with self.assertRaises(request.RequestError) as cm:
            request.fetch("bad/1", self.tmp)
        self.assertEqual(cm.exception.code, "bad_designation")

    def test_respond_document(self):
        f = request.fetch("ZTF10H5", self.tmp / "ZTF10H5", getters=self.getters(page=ZTF_HTML, obs=ZTF_OBS))
        doc = request.respond(f, WIN_ZTF, CTX, use_fo=False, runs=self.tmp / "runs")
        self.assertEqual((doc["ok"], doc["schema"], doc["desig"]), (True, "cle/neocp-request@1", "ZTF10H5"))
        self.assertEqual(doc["window"], {"start_utc": "2026-10-17T12:00:00Z", "stop_utc": "2026-11-16T12:00:00Z"})
        self.assertTrue(ISO.match(doc["built_utc"]))
        self.check_object(doc["object"], WIN_ZTF, "ZTF10H5")
        self.assertEqual(doc["object"]["request"]["page_created_utc"], "2026-11-01T12:35:06Z")

    def test_summary(self):
        text, mpc = (RQ / "summary.htm").read_text(), json.loads((RQ / "neocp.json").read_text())
        calls = []
        getters = (lambda: calls.append("s") or text, lambda: calls.append("l") or mpc)
        t = parse_iso("2026-11-01T12:40:00Z")
        s = request.summary(self.tmp, now_s=t, getters=getters)
        self.assertEqual((s["ok"], s["regenerated_utc"], len(s["objects"])), (True, "2026-11-01T12:35:01Z", 25))
        self.assertEqual(s["objects"]["ZTF10H5"], {"geo": False, "moid_au": 0.0003, "score": 100, "n_obs": 4, "arc_d": 0.0})
        self.assertEqual(request.summary(self.tmp, now_s=t + 120, getters=getters), s)
        self.assertEqual(len(calls), 2)                                            # cached 10 min
        with self.assertRaises(request.RequestError) as cm:
            request.summary(self.tmp / "x", getters=(lambda: (_ for _ in ()).throw(request.Down("timeout")), lambda: []))
        self.assertEqual((cm.exception.code, cm.exception.status), ("upstream", 502))
        s2 = request.summary(self.tmp / "y", getters=(lambda: text, lambda: (_ for _ in ()).throw(request.Down("timeout"))))
        self.assertIsNone(s2["objects"]["ZTF10H5"]["score"])                    # MPC's list is optional


def load_serve():
    spec = importlib.util.spec_from_file_location("cle_serve", ROOT / "scripts/serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ServeTest(Contract, unittest.TestCase):
    """scripts/serve.py on an ephemeral port: stubbed fetchers, a temporary dist/, find_orb off."""

    @classmethod
    def setUpClass(cls):
        cls.serve = load_serve()
        cls.tmp = Path(tempfile.mkdtemp(prefix="cle_serve_"))
        dist = cls.tmp / "dist"
        (dist / "data").mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html><title>cislunar-eye</title>")
        (dist / "data/core.js").write_text('CLE.register("core", {});\n')
        (dist / ".secret").write_text("hidden")
        (cls.tmp / "outside.txt").write_text("OUTSIDE-SECRET")
        cls.fetches, cls.lines = [], []
        cls.slow = 0.0

        def fetcher(desig, cache_dir):
            cls.fetches.append(desig)
            time.sleep(cls.slow)
            if desig == "BOOM123":
                raise RuntimeError("a bug")
            pages = {"8NIQ001": (NIQ_HTML, NIQ_OBS), "QQZZ999": (None, "")}
            html, obs = pages.get(desig, (None, ""))
            return request.Fetched(desig, Path(cache_dir), html, obs, [], "2026-10-22T12:00:00Z")

        def summary(cache_root):
            if cls.summary_down:
                raise request.RequestError("upstream", "neocp2 summary unreachable (timeout)")
            return {"ok": True, "regenerated_utc": "2026-11-01T12:35:01Z", "fetched_utc": "2026-11-01T12:40:00Z",
                    "objects": {"ZTF10H5": {"geo": False, "moid_au": 0.0003, "score": 100, "n_obs": 4, "arc_d": 0.0}}}
        cls.summary_down = False
        cls.server = cls.serve.Server(0, CTX, dist=dist, cache_root=cls.tmp / "cache", runs=cls.tmp / "runs",
                                      fetcher=fetcher, summary=summary, use_fo=False, log=cls.lines.append)
        cls.port = cls.server.port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        t0, t1 = int(WIN_NIQ.t0_s * 1000), int(WIN_NIQ.t1_s * 1000)
        cls.query = f"?t0={t0}&t1={t1}&now={int(WIN_NIQ.now_s * 1000)}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path, host=None, method="GET", headers=()):
        c = HTTPConnection("127.0.0.1", self.port, timeout=60)
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        c.putheader("Host", f"127.0.0.1:{self.port}" if host is None else host)
        for k, v in headers:
            c.putheader(k, v)
        c.endheaders()
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, body

    def test_health_and_cors(self):
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            status, h, body = self.get("/api/health", host)
            js = json.loads(body)
            self.assertEqual((status, js["ok"], js["service"], js["api"], js["fo"]), (200, True, "cislunar-eye", 1, False))
            self.assertTrue(ISO.match(js["now_utc"]))
            self.assertEqual((h["access-control-allow-origin"], h["cache-control"]), ("*", "no-store"))
            self.assertTrue(h["content-type"].startswith("application/json"))

    def test_preflight(self):
        status, h, body = self.get(f"/api/neocp/8NIQ001{self.query}", method="OPTIONS")
        self.assertEqual((status, body), (204, b""))
        self.assertEqual(h["access-control-allow-methods"], "GET, OPTIONS")
        self.assertEqual(h["access-control-allow-headers"], "content-type")
        self.assertEqual(h["access-control-allow-private-network"], "true")
        self.assertEqual((h["access-control-allow-origin"], h["cache-control"]), ("*", "no-store"))

    def test_host_header_rejected(self):
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1:1", f"127.0.0.1.nip.io:{self.port}", ""):
            for path, method in (("/api/health", "GET"), ("/", "GET"), ("/api/health", "OPTIONS")):
                status, h, body = self.get(path, host, method)
                self.assertEqual(status, 421, (host, path, method))
                self.assertEqual(json.loads(body)["error"], "bad_host")

    def test_bad_designation_and_window(self):
        for path in (f"/api/neocp/ab{self.query}", f"/api/neocp/ABC%2F1234{self.query}", f"/api/neocp/TOOLONG123{self.query}"):
            status, h, body = self.get(path)
            self.assertEqual((status, json.loads(body)), (400, {"ok": False, "error": "bad_designation",
                                                               "message": request.MSG["bad_designation"]}))
            self.assertEqual(h["access-control-allow-origin"], "*")
        for q in ("", "?t0=5&t1=1", "?t0=1000000000000&t1=1005000000000", "?t0=x&t1=y"):
            status, _, body = self.get(f"/api/neocp/8NIQ001{q}")
            self.assertEqual((status, json.loads(body)["error"]), (400, "bad_request"))

    def test_request_cached_and_not_found(self):
        n = len(self.fetches)
        status, h, body = self.get(f"/api/neocp/8NIQ001{self.query}")
        self.assertEqual(status, 200, body[:300])
        doc = json.loads(body)
        self.assertEqual((doc["ok"], doc["schema"], doc["desig"]), (True, "cle/neocp-request@1", "8NIQ001"))
        self.assertEqual(doc["window"], {"start_utc": "2026-10-07T12:00:00Z", "stop_utc": "2026-11-06T12:00:00Z"})
        self.check_object(doc["object"], WIN_NIQ, "8NIQ001")
        self.assertEqual(doc["object"]["fit"]["mode"], "page_sv")
        status2, _, body2 = self.get(f"/api/neocp/8NIQ001{self.query}")
        self.assertEqual(status2, 200)
        self.assert_cache_hit(json.loads(body2), doc)
        self.assertEqual(len(self.fetches), n + 2)                     # the upstream cache is the fetcher's
        self.assertIn("result cached", self.lines[-1])
        status, _, body = self.get(f"/api/neocp/QQZZ999{self.query}")
        self.assertEqual((status, json.loads(body)), (404, {"ok": False, "error": "not_found", "message": request.MSG["not_found"]}))
        status, _, body = self.get("/api/nothing")
        self.assertEqual(status, 404)

    def assert_cache_hit(self, hit, first):
        """A result-cache hit is the first answer, its request block saying so: cached, its own served_utc and seconds;
        fetched_utc (when MPC and neocp2 were read) unchanged."""
        r1, r2 = first["object"]["request"], hit["object"]["request"]
        self.assertEqual((r1["cached"], r2["cached"], r2["fetched_utc"]), (False, True, r1["fetched_utc"]))
        self.assertIn("served_utc", r1)
        self.assertGreaterEqual(r2["served_utc"], r1["served_utc"])
        self.assertLess(r2["seconds"], 5)
        strip = lambda d: d | {"object": d["object"] | {"request": {k: v for k, v in d["object"]["request"].items()
                                                                    if k not in ("cached", "served_utc", "seconds")}}}
        self.assertEqual(strip(hit), strip(first))

    def test_client_gone_mid_build(self):
        """The page aborts (AbortController): the build finishes, its result is cached and the helper keeps serving."""
        t0, t1 = int(WIN_NIQ.t0_s * 1000), int(WIN_NIQ.t1_s * 1000) - 3600_000         # a result not cached yet
        path = f"/api/neocp/8NIQ001?t0={t0}&t1={t1}"
        type(self).slow = 0.5
        try:
            s = socket.create_connection(("127.0.0.1", self.port))
            s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode())
            s.close()
            deadline = time.time() + 30
            while time.time() < deadline and not any(path in ln for ln in self.lines):
                time.sleep(0.05)
        finally:
            type(self).slow = 0.0
        self.assertTrue(any(path in ln and " 200 " in ln for ln in self.lines))
        status, _, _ = self.get(path)
        self.assertEqual(status, 200)
        self.assertIn("result cached", self.lines[-1])

    def query_for(self, cut_h):
        """A window of its own (t1 cut_h hours early): a result no other test has cached."""
        t0, t1 = int(WIN_NIQ.t0_s * 1000), int(WIN_NIQ.t1_s * 1000) - cut_h * 3600_000
        return f"?t0={t0}&t1={t1}&now={int(WIN_NIQ.now_s * 1000)}"

    def wait_log(self, pred, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            hit = [ln for ln in list(self.lines) if pred(ln)]
            if hit:
                return hit[-1]
            time.sleep(0.02)
        self.fail("no such log line")

    def test_busy_and_internal(self):
        with self.server.build_lock, mock.patch.object(self.server, "busy_wait_s", 0.2):
            status, _, body = self.get(f"/api/neocp/8NIQ001{self.query_for(5)}")
        self.assertEqual((status, json.loads(body)), (503, {"ok": False, "error": "busy", "message": request.MSG["busy"]}))
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            status, h, body = self.get(f"/api/neocp/BOOM123{self.query}")
        self.assertEqual((status, json.loads(body)), (500, {"ok": False, "error": "internal", "message": "helper error: RuntimeError"}))
        self.assertIn("a bug", err.getvalue())
        self.assertEqual(h["access-control-allow-origin"], "*")
        self.assertEqual(self.get("/api/health")[0], 200)                         # still serving, lock released

    def test_cached_answer_while_another_build_runs(self):
        """A result-cache hit is answered while the build slot is taken (an aborted build still running)."""
        q = self.query_for(7)
        status, _, body = self.get(f"/api/neocp/8NIQ001{q}")
        self.assertEqual(status, 200, body[:300])
        with self.server.build_lock, mock.patch.object(self.server, "busy_wait_s", 0.5):
            t = time.time()
            status2, _, body2 = self.get(f"/api/neocp/8NIQ001{q}")
            self.assertLess(time.time() - t, 0.4)
            self.assertEqual(status2, 200)
            self.assert_cache_hit(json.loads(body2), json.loads(body))
            self.assertEqual(self.get(f"/api/neocp/8NIQ001{self.query_for(8)}")[0], 503)    # not cached: waits, busy

    def test_origin_checks(self):
        """/api/ answers pages of the helper, file://, loopback servers and GitHub Pages; other sites get 403 before
        any upstream fetch; static files stay open."""
        pages = self.serve.PAGES_ORIGIN
        ok = [(), (("Origin", "null"),), (("Origin", f"http://127.0.0.1:{self.port}"),), (("Origin", "http://localhost:8000"),),
              (("Origin", "http://[::1]:8080"),), (("Origin", pages),), (("Origin", pages + "/"),),
              (("Sec-Fetch-Site", "same-origin"),), (("Sec-Fetch-Site", "none"),)]
        for headers in ok:
            self.assertEqual(self.get("/api/health", headers=headers)[0], 200, headers)
        bad = [(("Origin", "https://evil.example"),), (("Origin", "http://127.0.0.1.evil.example"),),
               (("Origin", "https://exoplanet5.github.io.evil.example"),), (("Origin", "http://localhost.evil:8765"),),
               (("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "no-cors")), (("Sec-Fetch-Site", "same-site"),)]
        n = len(self.fetches)
        for headers in bad:
            for path, method in (("/api/health", "GET"), (f"/api/neocp/8NIQ001{self.query_for(9)}", "GET"),
                                 (f"/api/neocp/8NIQ001{self.query_for(9)}", "HEAD"), ("/api/neocp/summary", "GET"),
                                 (f"/api/neocp/8NIQ001{self.query_for(9)}", "OPTIONS")):
                status, h, body = self.get(path, method=method, headers=headers)
                self.assertEqual(status, 403, (headers, path, method))
                if method != "HEAD":
                    self.assertEqual(json.loads(body)["error"], "bad_origin")
        self.assertEqual(len(self.fetches), n)                                       # refused before any fetch
        self.assertIn("bad_origin", self.wait_log(lambda ln: " 403 " in ln))
        self.assertEqual(self.get("/", headers=(("Origin", "https://evil.example"),))[0], 200)
        status, h, _ = self.get("/api/health", headers=(("Origin", "https://other.example"),))
        self.assertEqual(status, 403)
        srv = self.serve.Server(0, CTX, dist=self.tmp / "dist", origins=["https://Other.example/"], log=self.lines.append)
        try:
            self.assertIn("https://other.example", srv.origins)
        finally:
            srv.server_close()

    def test_log_lines(self):
        """Control characters in a request line are escaped; the stdlib's own answers (501, 414) and the rejected Host
        are logged."""
        s = socket.create_connection(("127.0.0.1", self.port))
        s.sendall(f"GET /\x1b]0;PWNED\x07\x1b[2Jx HTTP/1.0\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode())
        s.recv(4096)
        s.close()
        line = self.wait_log(lambda ln: "PWNED" in ln)
        self.assertNotIn("\x1b", line)
        self.assertNotIn("\x07", line)
        self.assertIn("\\x1b]0;PWNED\\x07", line)
        self.assertEqual(self.get("/", method="TRACE")[0], 501)
        self.assertIn("501", self.wait_log(lambda ln: "TRACE" in ln))
        s = socket.create_connection(("127.0.0.1", self.port))                  # 414: the server may reset, unread
        try:
            s.sendall(b"GET /" + b"a" * 70000 + b" HTTP/1.0\r\n\r\n")
            s.recv(4096)
        except OSError:
            pass
        finally:
            s.close()
        self.assertIn("414", self.wait_log(lambda ln: "code 414" in ln))
        self.assertEqual(self.get("/api/health", "evil.example\x1b[31m")[0], 421)
        line = self.wait_log(lambda ln: " 421 " in ln and "evil.example" in ln)
        self.assertNotIn("\x1b", line)

    def test_port_in_use_by_another_program(self):
        """A server on *:<port> already accepts on 127.0.0.1:<port>: the helper refuses the port instead of binding
        127.0.0.1:<port> beside it (SO_REUSEADDR on macOS)."""
        other = socket.socket()
        try:
            other.bind(("0.0.0.0", 0))
            other.listen(1)
            with self.assertRaises(OSError) as cm:
                self.serve.Server(other.getsockname()[1], CTX, dist=self.tmp / "dist", log=self.lines.append)
            self.assertEqual(cm.exception.errno, errno.EADDRINUSE)
        finally:
            other.close()

    def test_summary_endpoint(self):
        status, h, body = self.get("/api/neocp/summary")
        js = json.loads(body)
        self.assertEqual((status, js["ok"], js["objects"]["ZTF10H5"]["geo"]), (200, True, False))
        self.assertEqual(h["access-control-allow-origin"], "*")
        type(self).summary_down = True
        try:
            status, _, body = self.get("/api/neocp/summary")
        finally:
            type(self).summary_down = False
        self.assertEqual((status, json.loads(body)["error"]), (502, "upstream"))

    def test_static_files(self):
        status, h, body = self.get("/")
        self.assertEqual((status, h["content-type"], body), (200, "text/html; charset=utf-8", b"<!doctype html><title>cislunar-eye</title>"))
        self.assertEqual(h["access-control-allow-origin"], "*")
        status, h, body = self.get("/data/core.js")
        self.assertEqual((status, h["content-type"]), (200, "text/javascript; charset=utf-8"))
        status, h, body = self.get("/data/core.js", method="HEAD")
        self.assertEqual((status, body, h["content-length"]), (200, b"", str(len('CLE.register("core", {});\n'))))
        for path in ("/data/", "/data", "/nope.js", "/index.html/x", "/" + "a" * 300, "/data/" + "b" * 300 + ".js"):
            self.assertEqual(self.get(path)[0], 404, path[:40])
        for path in ("/../outside.txt", "/%2e%2e/outside.txt", "/data/../../outside.txt", "/data/%2e%2e/%2e%2e/outside.txt",
                     "/.secret", "/..%2foutside.txt", "/%2e%2e%5coutside.txt", "//etc/passwd"):
            status, _, body = self.get(path)
            self.assertIn(status, (403, 404), path)
            self.assertNotIn(b"OUTSIDE-SECRET", body)
            self.assertNotIn(b"hidden", body)
        self.assertEqual(self.get("/api/health", method="POST")[0], 405)


class FixtureShapeTest(unittest.TestCase):
    """build/fixture/request/C46FX01.json (tests/fixtures/make_fixture.py, for tests/browser.mjs) has the helper's shape."""

    def test_canned_response_matches_the_helper(self):
        path = ROOT / "build/fixture/request/C46FX01.json"
        if not path.exists():
            self.skipTest("fixture not built (npm run fixture)")
        canned = json.loads(path.read_text())
        with tempfile.TemporaryDirectory(prefix="cle_fx_") as tmp:
            real = request.respond(request.Fetched("8NIQ001", Path(tmp), NIQ_HTML, NIQ_OBS, [], "2026-10-22T12:00:00Z"),
                                   WIN_NIQ, CTX, use_fo=False, runs=Path(tmp) / "runs")
        self.assertEqual(set(canned), set(real))
        self.assertEqual(set(canned["window"]), set(real["window"]))
        self.assertEqual(set(canned["object"]), set(real["object"]))
        for k in ("catalog", "fit", "request"):
            self.assertEqual(set(canned["object"][k]), set(real["object"][k]), k)
        self.assertEqual(set(canned["object"]["fit"]["page_check"]), set(real["object"]["fit"]["page_check"]))
        self.assertEqual(set(canned["object"]["catalog"]["elements"]), set(real["object"]["catalog"]["elements"]))
        self.assertEqual({k for t in canned["object"]["tracklets"] for k in t}, {k for t in real["object"]["tracklets"] for k in t})


if __name__ == "__main__":
    unittest.main()
