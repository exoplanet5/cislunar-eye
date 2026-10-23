"""Upstream outages and the Horizons cache (REVIEW.md DW-04 b/c, EFF-4): tiles, the solution probe, the circuit
breaker, the offline fallback, and GPFetchError.

    ~/.venvs/astro313/bin/python -I -m unittest discover -s tests -p 'test_workflow_upstream.py'

Synthetic Horizons replies (circular orbits) are written through Snapshots.get with a stubbed fetch, so the cache
under test has the real layout (stamp folder, manifest, sha256). FallbackTest uses a pre-tile cache (whole-window
grid + mid files); TileTest a fake Horizons that answers any request. The test on the project cache
(work/cache/horizons_eph) is skipped where that cache is absent.
"""
import io
import json
import math
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cle.adapters import gp, horizons as hz  # noqa: E402
from cle.timeutil import iso, parse_iso, window  # noqa: E402

FX = ROOT / "tests/fixtures/adapters"
W13, W14 = window("2026-10-21T13:00:00Z"), window("2026-10-21T14:00:00Z")
MU, R_FAR, R_GEO, R_LLO = 398600.435507, 1.5e6, 42164.0, 1800.0
TILE_S = hz.TILE_D * 86400


def circular(t, r, mu=MU):
    w = np.sqrt(mu / r ** 3)
    c, s = np.cos(w * t), np.sin(w * t)
    return np.column_stack([r * c, r * s, 0 * t, -r * w * s, r * w * c, 0 * t])


def reply(command, start_s, stop_s, step_s, r, obj_data=False, centre="earth", revised="Oct 19, 2026", error=None) -> bytes:
    """A Horizons JSON reply: the header tokens parse_response checks (plus an object block naming the solution for
    OBJ_DATA requests) and CSV rows of a circular orbit; or an error reply."""
    if error:
        return json.dumps({"error": error}).encode()
    mu = MU * 0.0123 if centre == "moon" else MU
    t = start_s + step_s * np.arange(int(round((stop_s - start_s) / step_s)) + 1)
    rows = [f"{ti / 86400 + 2440587.5:.9f}, A.D. {datetime.fromtimestamp(ti, tz=timezone.utc):%Y-%b-%d %H:%M:%S}.0000, "
            + ", ".join(f"{x:.15E}" for x in row) + "," for ti, row in zip(t, circular(t, r, mu))]
    head = ((f" Revised: {revised}      Test ({command})\n Tracking data through October 19, 2026.\n"
             "Ephemeris / API_USER\n" if obj_data else "")
            + f"Target body name: Test ({command})                      {{source: TEST_merged}}\n"
            + ("Center body name: Moon (301)\n" if centre == "moon" else "Center body name: Earth (399)\n")
            + "Reference frame : ICRF\nOutput units    : KM-S\nGEOMETRIC cartesian states\nCalendar Date (UT), ...\n")
    return json.dumps({"result": head + "$$SOE\n" + "\n".join(rows) + "\n$$EOE\n"}).encode()


def req(cid, command, t0, t1, step, r, kind):
    params = hz.request_params(command, t0, t1, step, obj_data=kind == "grid")
    return f"{cid}_{kind}_{iso(t0)}_{step}", params, reply(command, t0, t1, step, r, obj_data=kind == "grid")


def stamp(root, name, retrieved, requests):
    """One cache stamp written the real way (Snapshots.get with a stubbed fetch); requests = [(name, params, body)]."""
    snap = hz.Snapshots(root)
    snap.folder, snap.index, snap.spans = root / name, {}, {}
    snap.folder.mkdir(parents=True)
    snap.manifest = {"retrieved_utc": retrieved, "api": hz.API, "requests": {}}
    with mock.patch("time.time", return_value=parse_iso(retrieved)), mock.patch.dict(os.environ, {"CLE_OFFLINE": ""}):
        for name_, params, body in requests:
            snap.fetch = lambda p, b=body: (b, 0.1)
            snap.get(name_, params)


class FallbackTest(unittest.TestCase):
    """Pre-tile synthetic cache. '-1' (SE-L2-like, 21600 s): whole-window grid + midpoints of an older 10-20 12:00
    window and of the 10-21 13:00 window. '-2' (GEO-like, 3600 s): the 13:00 window, and a newer but shifted
    10-22 09:00 window grid."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        old = window("2026-10-20T12:00:00Z")
        newer = window("2026-10-22T09:00:00Z")
        stamp(cls.root, "20261020T120000Z_old", "2026-10-20T12:00:00Z",
              [req("x", "-1", old.t0_s, old.t1_s, 21600, R_FAR, "grid"),
               req("x", "-1", old.t0_s + 10800, old.t1_s - 10800, 21600, R_FAR, "mid")])
        stamp(cls.root, "20261021T130000Z_new", "2026-10-21T13:05:00Z",
              [req("x", "-1", W13.t0_s, W13.t1_s, 21600, R_FAR, "grid"),
               req("x", "-1", W13.t0_s + 10800, W13.t1_s - 10800, 21600, R_FAR, "mid"),
               req("g", "-2", W13.t0_s, W13.t1_s, 3600, R_GEO, "grid"),
               req("g", "-2", W13.t0_s + 1800, W13.t1_s - 1800, 3600, R_GEO, "mid")])
        stamp(cls.root, "20261022T090000Z_newer", "2026-10-22T09:00:00Z",
              [req("g", "-2", newer.t0_s, newer.t1_s, 3600, R_GEO, "grid")])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def snapshots(self, down=False):
        snap = hz.Snapshots(self.root)
        snap.down = down
        return mock.patch.object(hz, "SNAPSHOTS", snap)

    def test_exact_window_served_from_grid_and_mid(self):
        """The window lattice (grid + step/2) is served from the two files of its own stamp even when a newer,
        shifted reply overlaps it: even samples from the grid, odd from the mid, no fallback."""
        with self.snapshots(), mock.patch.dict(os.environ, {"CLE_OFFLINE": "1"}):
            for command, cid, step, n in (("-1", "x", 21600, 121), ("-2", "g", 3600, 721)):
                s = hz.Sampler(command, "earth", cid)
                p = s.states(W13.t0_s, W13.t1_s, step)
                self.assertEqual((p.t0_s, p.t1_s, len(p.rv), len(p.mid)), (W13.t0_s, W13.t1_s, n, n - 1))
                self.assertEqual([x["kind"] for x in s.provenance], ["grid", "grid"])
                self.assertTrue(all("fallback" not in x and "20261021T130000Z_new" in x["raw"] for x in s.provenance))
                self.assertFalse(np.isnan(p.mid).any())
            self.assertEqual((hz.SNAPSHOTS.reused, hz.SNAPSHOTS.fetched), (4, 0))

    def test_regrid_onto_shifted_lattice(self):
        """+1 h window, 21600-s step: no cached lattice holds 14:00 + 3 h k, so the newest overlapping grid (13:00)
        is Hermite re-gridded onto the 14:00 lattice and stops one step short of the window end."""
        with self.snapshots(), mock.patch.dict(os.environ, {"CLE_OFFLINE": "1"}):
            s = hz.Sampler("-1", "earth", "x")
            p = s.states(W14.t0_s, W14.t1_s, 21600)
        fb = s.provenance[0]["fallback"]
        self.assertEqual((p.t0_s, iso(p.t1_s), p.step_s, len(p.rv)), (W14.t0_s, "2026-11-05T08:00:00Z", 21600, 120))
        self.assertTrue(fb["regrid"])
        self.assertEqual((fb["cached"], fb["requested"]), (f"x_grid_{iso(W13.t0_s)}_21600", [iso(W14.t0_s), iso(W14.t1_s)]))
        self.assertIn("20261021T130000Z_new", s.provenance[0]["raw"])
        self.assertIn("offline and not cached", fb["why"])
        err = np.linalg.norm(p.rv[:, :3] - circular(p.t_s, R_FAR)[:, :3], axis=1)
        self.assertLess(err.max(), 1e-3)

    def test_shifted_window_stitched_from_overlapping_replies(self):
        """+1 h window, 3600-s step: the 13:00 grid and mid serve all but the last hour, the newer 10-22 09:00 grid
        the last grid sample; only the last midpoint (11-05 13:30) is in no reply, so it is unchecked."""
        with self.snapshots(), mock.patch.dict(os.environ, {"CLE_OFFLINE": "1"}):
            s = hz.Sampler("-2", "earth", "g")
            p = s.states(W14.t0_s, W14.t1_s, 3600)
            e, ve, tm = hz.midpoints(s, p)
        self.assertTrue(all("fallback" not in x for x in s.provenance))
        self.assertEqual((p.t0_s, p.t1_s, len(p.rv)), (W14.t0_s, W14.t1_s, 721))
        self.assertEqual(sorted(x["raw"].split("/")[-2] for x in s.provenance),
                         ["20261021T130000Z_new", "20261021T130000Z_new", "20261022T090000Z_newer"])
        np.testing.assert_allclose(p.rv, circular(p.t_s, R_GEO), rtol=1e-12, atol=1e-9)
        self.assertEqual(int(np.isnan(p.mid[:, 0]).sum()), 1)
        self.assertEqual((len(e), s.unchecked[0]["n"], s.unchecked[0]["t0"], e[-1]), (720, 1, "2026-11-05T13:30:00Z", 0.0))
        self.assertTrue(0 < e[:-1].max() < 1.0)                      # the real Hermite error of GEO at 3600 s

    def test_nothing_overlapping(self):
        with self.snapshots(), mock.patch.dict(os.environ, {"CLE_OFFLINE": "1"}):
            with self.assertRaises(LookupError) as cm:
                hz.Sampler("-3", "earth", "z").states(W14.t0_s, W14.t1_s, 3600)
            self.assertIn("no overlapping cached response", str(cm.exception))
            with self.assertRaises(LookupError):                       # same command, other centre
                hz.Sampler("-1", "moon", "x").states(W14.t0_s, W14.t1_s, 21600)

    def test_build_fallback_caveat_and_provenance(self):
        """build() on the +1 h window: track from the 13:00 snapshot, re-gridded midpoints checked, caveat says
        which snapshot and where the track ends; the exact window still builds without any fallback."""
        target = {"cid": "x", "primary_ephem": "horizons:-1", "regime": "SE_L2", "caveat": "station-keeps"}
        with self.snapshots(), mock.patch.dict(os.environ, {"CLE_OFFLINE": "1"}):
            exact = hz.build(target, W13, None)
            table = hz.build(target, W14, None)
        self.assertNotIn("fallback", exact.validation)
        self.assertEqual((exact.caveat, exact.validation["requests"], exact.validation["coverage"]),
                         ("station-keeps", 2, [iso(W13.t0_s), iso(W13.t1_s)]))
        self.assertEqual((exact.validation["midpoint_count"], exact.validation["solution"]["id"]), (120, None))
        v = table.validation
        self.assertEqual(v["coverage"], [iso(W14.t0_s), "2026-11-05T08:00:00Z"])
        self.assertLess(table.segments[-1].t_s[-1], W14.t1_s)
        self.assertEqual((v["fallback"]["snapshot_utc"], v["fallback"]["regrid"], v["fallback"]["requests"],
                          v["midpoint_unchecked"], v["midpoint_count"]), ("2026-10-21", True, 1, 0, 119))
        self.assertTrue(v["midpoint_pass"] and v["midpoint_max_km"] < 1e-3)
        self.assertEqual(table.caveat, "Horizons snapshot of 2026-10-21 (re-gridded offline fallback), ends "
                                       "2026-11-05T08:00Z; station-keeps")
        self.assertEqual([p["fallback"]["cached"] for p in table.provenance], [f"x_grid_{iso(W13.t0_s)}_21600"])
        self.assertEqual(v["fallback"]["files"], sorted({p["raw"] for p in table.provenance}))
        self.assertTrue(all("20261021T130000Z_new" in f for f in v["fallback"]["files"]))
        self.assertEqual([s.quality for s in table.segments], ["ref", "pred"])      # header boundary kept

    def test_down_uses_fallback_without_offline_flag(self):
        with self.snapshots(down=True), mock.patch.dict(os.environ, {"CLE_OFFLINE": ""}):
            s = hz.Sampler("-1", "earth", "x")
            p = s.states(W14.t0_s, W14.t1_s, 21600)
        self.assertEqual(len(p.rv), 120)
        self.assertIn("Horizons down and not cached", s.provenance[0]["fallback"]["why"])

    def test_partial_midpoints_are_counted_unchecked(self):
        """A piece without its own midpoints reaching 10 h before the cached replies: the mid lattice is served from
        the 13:00 mid file and clipped to it (no re-grid), the 10 midpoints before its start are unchecked (error 0)."""
        with self.snapshots(), mock.patch.dict(os.environ, {"CLE_OFFLINE": "1"}):
            s = hz.Sampler("-2", "earth", "g")
            p = s.states(W13.t0_s, W13.t1_s, 3600)                     # exact
            p = hz.Piece(p.t0_s - 10 * 3600, 3600, np.vstack([circular(p.t0_s - 3600 * np.arange(10, 0, -1), R_GEO), p.rv]))
            e, ve, tm = hz.midpoints(s, p)
        self.assertEqual((len(e), len(tm), s.unchecked[0]["n"]), (730, 730, 10))
        self.assertTrue((e[:10] == 0).all() and 0 < e[10:].max() < 1.0)
        self.assertAlmostEqual(tm[0], p.t0_s + 1800)
        fb = s.provenance[-1]["fallback"]
        self.assertEqual((fb["used"][0], fb["regrid"]), ("2026-10-06T13:30:00Z", False))
        self.assertIn("20261021T130000Z_new", s.provenance[-1]["raw"])


class FakeHorizons:
    """Answers any vector request from circular orbits; counts requests by kind and keeps their parameters."""
    RADII = {"-1": R_FAR, "-2": R_GEO, "-85": R_LLO}

    def __init__(self):
        self.revised, self.scale, self.end_s, self.calls, self.down = "Oct 19, 2026", 1.0, None, [], False

    def __call__(self, params):
        if self.down:                                # the breaker already tripped (fetch() past BREAKER failures)
            raise hz.HorizonsDown("fake outage")
        (a, b), step = hz.hz_times(params), hz.step_of(params)
        command, centre = params["COMMAND"].strip("'"), "moon" if params["CENTER"] == "'500@301'" else "earth"
        kind = "probe" if params["OBJ_DATA"] == "'YES'" else "vectors"
        self.calls.append((kind, params))
        if self.end_s is not None and b > self.end_s:
            end = datetime.fromtimestamp(self.end_s, tz=timezone.utc)
            return reply(command, a, b, step, 0, error=f'No ephemeris for target "Test" after A.D. '
                         f'{end:%Y}-{end:%b}-{end:%d} {end:%H:%M:%S}.0000 UT'.replace(f"{end:%b}", f"{end:%b}".upper())), 0.1
        return reply(command, a, b, step, self.RADII[command] * self.scale, kind == "probe", centre, self.revised), 0.1

    def kinds(self, since=0):
        out = {}
        for kind, p in self.calls[since:]:
            out[kind] = out.get(kind, 0) + 1
        return out


class TileTest(unittest.TestCase):
    """EFF-4: the grid is fetched once at step/2 (no 'mid' requests) as TILE_D-day tiles on the 00:00 UTC lattice;
    a moved window fetches only the probe of the day and the tiles it lacks; a new solution refetches the target."""
    SE = {"cid": "x", "primary_ephem": "horizons:-1", "regime": "SE_L2"}
    LLO = {"cid": "m", "primary_ephem": "horizons:-85", "regime": "LUNAR_ORBIT"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server, self.snap = FakeHorizons(), hz.Snapshots(Path(self.tmp.name))
        self.snap.fetch = self.server
        self.patches = [mock.patch.object(hz, "SNAPSHOTS", self.snap), mock.patch.dict(os.environ, {"CLE_OFFLINE": ""}),
                        mock.patch("time.sleep", lambda s: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def tiles(self, since=0):
        """[(start, stop, step)] of the vector requests since call `since`."""
        return [(*hz.hz_times(p), hz.step_of(p)) for kind, p in self.server.calls[since:] if kind == "vectors"]

    def test_cold_build_is_probe_plus_tiles(self):
        table = hz.build(self.SE, W13, None)
        self.assertEqual(self.server.kinds(), {"probe": 1, "vectors": 4})
        tiles = self.tiles()
        self.assertTrue(all(a % TILE_S == 0 and b - a == TILE_S and s == 3600 for a, b, s in tiles), tiles)
        self.assertEqual(tiles[0][0], math.floor(W13.t0_s / TILE_S) * TILE_S)
        self.assertEqual(tiles[-1][1], math.floor(W13.t1_s / TILE_S) * TILE_S + TILE_S)
        self.assertEqual([p["STEP_SIZE"] for k, p in self.server.calls], ["'1 m'"] + ["'60 m'"] * 4)
        seg, v = table.segments[0], table.validation
        self.assertEqual((len(table.segments), seg.t0_s, seg.step_s, seg.n), (2, W13.t0_s, 21600, 55))  # ref | pred at 10-20
        self.assertEqual((v["coverage"], v["midpoint_count"], v["midpoint_unchecked"], v["requests"], v["fetched"]),
                         ([iso(W13.t0_s), iso(W13.t1_s)], 120, 0, 5, 5))
        probe = json.loads(Path(table.provenance[0]["raw"]).read_text())["result"].split("$$SOE")[0]
        self.assertEqual((v["solution"]["id"], v["solution"]["note"]), (hz.solution_id(probe)[0], "Oct 19, 2026"))
        self.assertTrue(v["midpoint_pass"] and v["midpoint_max_km"] < 1e-3)
        t = np.concatenate([s.t_s for s in table.segments])
        rv = np.vstack([np.hstack([s.r_km, s.v_kms]) for s in table.segments])
        np.testing.assert_allclose(rv, circular(t, R_FAR), rtol=1e-12, atol=1e-9)
        self.assertEqual([x["kind"] for x in table.provenance], ["probe"] + ["grid"] * 4)
        m = json.loads(next(Path(self.tmp.name).glob("*/manifest.json")).read_text())
        self.assertEqual({e.get("solution") for e in m["requests"].values() if e["parameters"]["OBJ_DATA"] == "'NO'"},
                         {v["solution"]["id"]})

    def test_same_window_and_moved_windows_reuse_tiles(self):
        first = hz.build(self.SE, W13, None)
        n = len(self.server.calls)
        again = hz.build(self.SE, W13, None)                           # same day: nothing fetched
        self.assertEqual(len(self.server.calls), n)
        for a, b in zip(first.segments, again.segments):
            np.testing.assert_array_equal(a.r_km, b.r_km)
        shifted = hz.build(self.SE, window("2026-10-22T13:00:00Z"), None)   # +1 d, inside the same 4 tiles
        self.assertEqual(self.server.kinds(n), {"probe": 1})
        self.assertEqual(shifted.validation["fetched"], 1)
        np.testing.assert_array_equal(np.vstack([s.r_km for s in first.segments])[4:],      # common epochs identical
                                      np.vstack([s.r_km for s in shifted.segments])[:-4])
        n = len(self.server.calls)
        hz.build(self.SE, window("2026-10-29T13:00:00Z"), None)      # window end crosses a tile boundary: 1 new tile
        self.assertEqual(self.server.kinds(n), {"probe": 1, "vectors": 1})
        self.assertEqual(self.tiles(n)[0][0], math.floor(parse_iso("2026-11-13T13:00:00Z") / TILE_S) * TILE_S)
        self.assertEqual((self.snap.fetched, self.snap.reused), (8, 20))

    def test_new_solution_refetches_the_target(self):
        first = hz.build(self.SE, W13, None)
        self.server.revised, self.server.scale = "Oct 22, 2026", 1.001          # Horizons re-delivers the trajectory
        n = len(self.server.calls)
        same_day = hz.build(self.SE, W13, None)                        # the day's probe is cached: not noticed yet
        self.assertEqual(len(self.server.calls), n)
        np.testing.assert_array_equal(same_day.segments[0].r_km, first.segments[0].r_km)
        later = time.time() + hz.PROBE_TTL_S + 60                       # the day's probe is now older than PROBE_TTL_S
        with mock.patch("time.time", return_value=later):
            fresh = hz.build(self.SE, W13, None)
        self.assertEqual(self.server.kinds(n), {"probe": 1, "vectors": 4})
        self.assertNotEqual(fresh.validation["solution"]["id"], first.validation["solution"]["id"])
        self.assertEqual(fresh.validation["solution"]["note"], "Oct 22, 2026")
        np.testing.assert_allclose(fresh.segments[0].r_km, circular(fresh.segments[0].t_s, R_FAR * 1.001)[:, :3], rtol=1e-12, atol=1e-9)
        self.assertGreater(np.abs(fresh.segments[0].r_km - first.segments[0].r_km).max(), 1.0)
        n = len(self.server.calls)
        with mock.patch("time.time", return_value=later):
            hz.build(self.SE, W13, None)                               # and the new tiles are reused
        self.assertEqual(len(self.server.calls), n)

    def test_down_after_new_probe_uses_older_solution_with_caveat(self):
        """The probe names a new solution, then Horizons goes down before its tiles arrive: the older solution's
        tiles stand in (clipped, not re-gridded) and the track says so."""
        hz.build(self.SE, W13, None)
        self.server.revised, self.server.scale = "Oct 22, 2026", 1.001

        def probe_only(p):
            if p["OBJ_DATA"] == "'YES'":
                return self.server(p)
            self.snap.down = True                    # what fetch() does after BREAKER failures
            raise hz.HorizonsDown("fake outage")
        self.snap.fetch = probe_only
        with mock.patch("time.time", return_value=time.time() + hz.PROBE_TTL_S + 60):
            table = hz.build(self.SE, W13, None)
        v = table.validation
        self.assertEqual((v["solution"]["note"], v["fallback"]["regrid"], v["fallback"]["requests"], v["midpoint_unchecked"]),
                         ("Oct 22, 2026", False, 4, 0))
        self.assertIn("solution", table.provenance[1]["fallback"]["why"])
        self.assertTrue(table.caveat.startswith("Horizons snapshot of 2026-"))
        np.testing.assert_allclose(table.segments[0].r_km, circular(table.segments[0].t_s, R_FAR)[:, :3], rtol=1e-12, atol=1e-9)
        self.assertEqual(v["coverage"], [iso(W13.t0_s), iso(W13.t1_s)])

    def test_lunar_tiles_and_coverage_clip(self):
        """LUNAR_ORBIT (300 s, Moon-centred): tiles at 150 s as an interval count below MAX_ROWS; an SPK end inside
        the window clips the last tile to the coverage after one error reply (the first tiles are reused)."""
        self.server.end_s = parse_iso("2026-11-02T23:50:00Z")
        table = hz.build(self.LLO, W13, None)
        tiles = self.tiles()
        self.assertTrue(all(s == 150 for *_, s in tiles))
        self.assertEqual([p["STEP_SIZE"] for k, p in self.server.calls if k == "vectors"][:3], ["'5760'"] * 3)
        end = math.floor((self.server.end_s - 1) / 150) * 150
        self.assertEqual(sum(b > self.server.end_s for a, b, s in tiles), 1)          # the error reply, then clipped
        self.assertEqual(max(b for a, b, s in tiles if b <= self.server.end_s), end)
        self.assertEqual(self.server.kinds(), {"probe": 1, "vectors": 4})
        self.assertLessEqual(max(round((b - a) / s) + 1 for a, b, s in tiles), hz.MAX_ROWS)
        self.assertEqual(table.centre, "moon")
        self.assertLess(self.server.end_s - table.segments[-1].t_s[-1], 600)
        self.assertEqual(table.validation["midpoint_unchecked"], 0)
        self.assertTrue(table.validation["midpoint_pass"])
        off = hz.build(self.LLO, W13, None)                            # everything cached, including the error reply
        self.assertEqual(off.validation["fetched"], 0)
        for a, b in zip(table.segments, off.segments):
            np.testing.assert_array_equal(a.r_km, b.r_km)

    def test_moon_check_tiles_serve_any_hour(self):
        class Ctx:
            kernel_path = "k"

            def moon_rv(self, t):
                return circular(np.asarray(t), R_GEO)
        self.server.RADII = dict(self.server.RADII, **{"301": R_GEO})
        mc = hz.moon_check(W13, Ctx())
        self.assertEqual((mc["pass"], mc["samples"], mc["span"]), (True, 31, [iso(W13.t0_s), iso(W13.t1_s)]))
        self.assertEqual(self.server.kinds(), {"vectors": 4})
        mc = hz.moon_check(W14, Ctx())                                 # +1 h: on the hourly tiles, nothing fetched
        self.assertEqual((self.server.kinds(), mc["samples"], "fallback" in mc), ({"vectors": 4}, 31, False))


class BreakerTest(unittest.TestCase):
    def test_trips_after_two_failures(self):
        calls, sleeps = [], []
        errors = iter([HTTPError("u", 503, "busy", {}, io.BytesIO(b"busy")), URLError("connection refused")])

        def failing(req, timeout=None):
            calls.append(req.full_url)
            raise next(errors)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(hz, "urlopen", failing), mock.patch("time.sleep", sleeps.append):
            snap = hz.Snapshots(Path(d))
            with self.assertRaises(hz.HorizonsDown):
                snap.get("a", hz.request_params("-1", W13.t0_s, W13.t1_s, 21600))
            self.assertEqual((len(calls), snap.failures, snap.down, snap.degraded()), (2, 2, True, True))
            with self.assertRaises(LookupError) as cm:                 # the rest of the run: no attempt, no wait
                snap.get("b", hz.request_params("-2", W13.t0_s, W13.t1_s, 3600))
            self.assertIn("Horizons down and not cached", str(cm.exception))
            with self.assertRaises(hz.HorizonsDown):
                snap.fetch(hz.request_params("-2", W13.t0_s, W13.t1_s, 3600))
            self.assertEqual(len(calls), 2)
            self.assertEqual([s for s in sleeps if s >= 1], [10])           # one retry pause, then the breaker
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_success_resets_the_count(self):
        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"result": "x"}'
        outcomes = iter([URLError("reset"), Reply(), URLError("reset"), Reply()])

        def flaky(req, timeout=None):
            x = next(outcomes)
            if isinstance(x, Exception):
                raise x
            return x
        with mock.patch.object(hz, "urlopen", flaky), mock.patch("time.sleep", lambda s: None):
            snap = hz.Snapshots(Path("/nonexistent"))
            for _ in range(2):
                body, _ = snap.fetch(hz.request_params("-1", W13.t0_s, W13.t1_s, 21600))
                self.assertEqual((body, snap.failures, snap.down), (b'{"result": "x"}', 0, False))


class GPFetchTest(unittest.TestCase):
    def fetch(self, d, script, norads=(836,)):
        st = Path(d) / "st.py"
        st.write_text(script)
        with mock.patch.object(gp, "ST", st), mock.patch.object(gp, "CACHE", Path(d) / "gp"):
            return gp.fetch(list(norads), W13)

    def test_cli_failure_raises_gpfetcherror(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(gp.GPFetchError) as cm:
                self.fetch(d, "import sys\nsys.stderr.write('Space-Track login failed for user: 401\\n')\nsys.exit('login failed')\n")
            self.assertIn("login failed", str(cm.exception))
            self.assertIn("gp_history.json", str(cm.exception))
            self.assertEqual(list((Path(d) / "gp").iterdir()), [])            # no empty stamp, no partial reply
            stampdir = Path(d) / "gp/20261001T000000Z"
            stampdir.mkdir(parents=True)
            shutil.copy(FX / "gp_sample.json", stampdir / "gp.json")
            with mock.patch.object(gp, "CACHE", Path(d) / "gp"):
                recs, files = gp.records(836)
            self.assertTrue(recs)
            self.assertIn(str(stampdir / "gp.json"), files)

    def test_partial_fetch_keeps_the_completed_reply(self):
        script = ("import sys\nq = sys.argv[2]\n"
                  "sys.exit('gp down') if q.startswith('class/gp/') else open(sys.argv[sys.argv.index('--json') + 1], 'w').write('[]')\n")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(gp.GPFetchError) as cm:
                self.fetch(d, script)
            self.assertIn("gp.json", str(cm.exception))
            folder, = (Path(d) / "gp").iterdir()
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["gp_history.json", "manifest.json"])
            m = json.loads((folder / "manifest.json").read_text())
            self.assertEqual((list(m["requests"]), m["error"]["request"]), (["gp_history.json"], "gp.json"))
            self.assertIn("gp down", m["error"]["error"])

    def test_success_writes_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            folder = self.fetch(d, "import sys\nopen(sys.argv[sys.argv.index('--json') + 1], 'w').write('[]')\n")
            m = json.loads((folder / "manifest.json").read_text())
            self.assertEqual(sorted(m["requests"]), ["gp.json", "gp_history.json"])
            self.assertNotIn("error", m)


@unittest.skipUnless(any(hz.CACHE.glob("*/manifest.json")), "needs work/cache/horizons_eph")
class ProjectCacheTest(unittest.TestCase):
    """The project cache on a +1 h window offline: served from the tiles where they exist (no fallback), otherwise
    the fallback instead of a failure; nothing fetched either way."""

    def test_shifted_window_offline(self):
        from cle import catalog
        from cle.context import Context
        rows = {r["cid"]: r for r in catalog.targets()}
        if not {"jwst", "tess"} <= set(rows):
            self.skipTest("catalog without jwst/tess")
        with mock.patch.object(hz, "SNAPSHOTS", hz.Snapshots()), mock.patch.dict(os.environ, {"CLE_OFFLINE": "1"}):
            for cid in ("jwst", "tess"):
                table = hz.build(rows[cid], W14, None)
                v, fb = table.validation, table.validation.get("fallback")
                self.assertEqual(v["fetched"], 0, cid)
                self.assertTrue(W14.t0_s <= table.segments[0].t0_s < table.segments[-1].t_s[-1] <= W14.t1_s, cid)
                self.assertLess(v["midpoint_max_km"], 3.0, cid)
                self.assertTrue(all("work/cache/horizons_eph/" in p["raw"] for p in table.provenance), cid)
                if fb:                                                 # pre-tile cache: the newest snapshot stands in
                    self.assertGreaterEqual(fb["snapshot_utc"], "2026-10-21", cid)
                    self.assertTrue(table.caveat.startswith(f"Horizons snapshot of {fb['snapshot_utc']}"), cid)
                    self.assertEqual(fb["files"], sorted({p["raw"] for p in table.provenance if "fallback" in p}), cid)
                else:                                                  # tiles: the whole window, no caveat
                    self.assertEqual((table.segments[0].t0_s, v["coverage"]), (W14.t0_s, [iso(W14.t0_s), iso(W14.t1_s)]), cid)
                    self.assertNotIn("Horizons snapshot", table.caveat, cid)
            mc = hz.moon_check(W14, Context())
            self.assertEqual(hz.SNAPSHOTS.fetched, 0)
        self.assertTrue(mc["pass"])
        if "fallback" in mc:
            self.assertTrue(mc["fallback"]["cached"].startswith("moon_moon_check_") and not mc["fallback"]["regrid"])
        else:
            self.assertEqual(mc["span"], [iso(W14.t0_s), iso(W14.t1_s)])


if __name__ == "__main__":
    unittest.main()
