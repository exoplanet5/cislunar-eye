"""Adapter tests: ~/.venvs/astro313/bin/python -I -m unittest tests.test_adapters   (or discover -s tests)

Fixtures in tests/fixtures/adapters (MANIFEST.json: sha256 and origin): Horizons vectors of TESS, Chandra, SMILE
and Gaia, a Space-Track GP sample, and the 8NIQ001 NEOCP astrometry plus its pseudo-MPEC. The fo, get_vect and
~/tles tests are skipped where those are missing.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cle import nbody, tracklets  # noqa: E402
from cle.adapters import bgtle, extrap, fo, gp  # noqa: E402
from cle.context import GM_EARTH, Context  # noqa: E402
from cle.timeutil import parse_iso, window  # noqa: E402

FX = ROOT / "tests/fixtures/adapters"
WIN = window("2026-10-21T13:00:00Z")
CTX = Context()
HAVE_FO = fo.FO.exists()
HAVE_BG = bgtle.GET_VECT.exists() and (bgtle.TLES / "tle_list.txt").exists()


def horizons(cmd, start, stop, step="1 h"):
    return nbody.hz_vectors(cmd, start, stop, step, FX)


def sep_arcsec(ra1, dec1, ra2, dec2):
    a = np.radians([ra1, dec1, ra2, dec2])
    c = np.sin(a[1]) * np.sin(a[3]) + np.cos(a[1]) * np.cos(a[3]) * np.cos(a[0] - a[2])
    return np.degrees(np.arccos(np.clip(c, -1, 1))) * 3600


class NbodyTest(unittest.TestCase):
    def test_vs_horizons_from_window_start(self):
        """TESS and Chandra from their Horizons state at the window start: unmodelled SRP gives tens of km."""
        for cmd, lim15, lim30 in ((-95, 80, 150), (-151, 40, 120)):
            t, ref = horizons(cmd, "2026-10-06 13:00", "2026-11-05 13:00")
            dr = np.linalg.norm(nbody.propagate(CTX, t[0], ref[0], t[0], t[-1])(t)[:, :3] - ref[:, :3], axis=1)
            d = (t - t[0]) / 86400
            self.assertLess(dr[d <= 15].max(), lim15, cmd)
            self.assertLess(dr.max(), lim30, cmd)

    def test_backward_and_forward(self):
        t, ref = horizons(-151, "2026-10-06 13:00", "2026-11-05 13:00")
        k = len(t) // 2
        traj = nbody.propagate(CTX, t[k], ref[k], t[0], t[-1])
        self.assertEqual(traj.span, (t[0], t[-1]))
        dr = np.linalg.norm(traj(t)[:, :3] - ref[:, :3], axis=1)
        self.assertLess(dr.max(), 60.0)
        self.assertLess(dr[k], 1e-9)
        with self.assertRaises(ValueError):
            traj(t[-1] + 3600)

    def test_regime_sampling_refines_perigee(self):
        t, ref = horizons(-151, "2026-10-06 13:00", "2026-11-05 13:00")
        traj = nbody.propagate(CTX, t[0], ref[0], WIN.t0_s, WIN.t1_s)
        segs, mid = nbody.segments(traj, WIN, 3600.0, WIN.t0_s, WIN.t1_s, "test", "extrap", ctx=CTX)
        self.assertLess(mid, 1.0)
        self.assertLess(min(s.step_s for s in segs), 3600.0)
        self.assertEqual(segs[0].t0_s, WIN.t0_s)
        for a, b in zip(segs[:-1], segs[1:]):
            self.assertAlmostEqual(a.t_s[-1], b.t0_s, places=6)
            self.assertEqual(b.join_before, "continuous")
            np.testing.assert_allclose(a.r_km[-1], b.r_km[0], atol=1e-9)


class GPTest(unittest.TestCase):
    rows = json.loads((FX / "gp_sample.json").read_text())

    def test_placeholder(self):
        self.assertTrue(gp.placeholder({"MEAN_MOTION": "15.2"}))
        self.assertFalse(gp.placeholder({"MEAN_MOTION": "0.24"}))

    def test_radius_within_perigee_apogee(self):
        """SDP4 -> GCRS geocentric distance over +/- 7 d stays inside the mean-element perigee..apogee (5 %)."""
        for g in self.rows:
            if g["NORAD_CAT_ID"] in ("59228",):
                continue
            n = float(g["MEAN_MOTION"]) * 2 * np.pi / 86400
            a, e = (GM_EARTH / n ** 2) ** (1 / 3), float(g["ECCENTRICITY"])
            t0 = gp.epoch_s(g)
            r = np.linalg.norm(gp.sgp4_at(g, CTX)(t0 + np.arange(-7, 7, 0.01) * 86400)[:, :3], axis=1)
            self.assertGreater(r.min(), 0.95 * a * (1 - e), g["OBJECT_NAME"])
            self.assertLess(r.max(), 1.05 * a * (1 + e), g["OBJECT_NAME"])

    def test_gcrs_not_teme(self):
        """The GP state is rotated TEME -> GCRS: it differs from the raw TEME vector by the equinox/nutation angle."""
        from skyfield.api import EarthSatellite
        g = self.rows[0]
        t = CTX.times(gp.epoch_s(g))
        sat = EarthSatellite(g["TLE_LINE1"], g["TLE_LINE2"], ts=CTX.ts)
        teme = np.array(sat._position_and_velocity_TEME_km(t)[0]).ravel()
        gcrs = gp.sgp4_at(g, CTX)(gp.epoch_s(g))[0, :3]
        ang = np.degrees(np.arccos(teme @ gcrs / np.linalg.norm(teme) / np.linalg.norm(gcrs))) * 3600
        self.assertGreater(ang, 60)               # ~0.37 deg of precession since J2000 plus nutation
        self.assertAlmostEqual(np.linalg.norm(teme), np.linalg.norm(gcrs), places=3)

    @unittest.skipUnless(HAVE_BG, "needs get_vect and ~/tles")
    def test_dro_a_bill_gray_extrapolation_stays_in_the_dro(self):
        """Documents why DRO-A's AM6 track is flagged: Bill Gray's DRO-A TLEs, extrapolated, stay 50-120
        thousand km from the Moon, while the SDP4 epoch state integrated the same way does not."""
        t = WIN.grid(3600.0)
        tb, rvb, _ = bgtle.end_state("24048a26.tle", CTX)
        moon = np.linalg.norm(nbody.propagate(CTX, tb, rvb, tb, WIN.t1_s)(t)[:, :3] - CTX.moon_rv(t)[:, :3], axis=1)
        self.assertGreater(moon.min(), 50000)
        self.assertLess(moon.max(), 120000)
        g = next(r for r in self.rows if r["NORAD_CAT_ID"] == "59228")
        te = gp.epoch_s(g)
        traj = nbody.propagate(CTX, te, gp.sgp4_at(g, CTX)(te)[0], WIN.t0_s, WIN.t1_s)
        self.assertLess(np.linalg.norm(traj(t)[:, :3] - CTX.moon_rv(t)[:, :3], axis=1).min(), 30000)


@unittest.skipUnless(HAVE_BG, "needs get_vect and ~/tles")
class BgtleTest(unittest.TestCase):
    def test_smile_inside_range_and_tt(self):
        """26109a.tle inside its Range against Horizons -463; passing UTC as TT (69 s) is visibly worse."""
        t, ref = horizons(-463, "2026-06-22 06:00", "2026-07-06 00:00")
        trk = bgtle.Track("26109a.tle", CTX)
        k = trk.index(t)
        good = np.median(np.linalg.norm(trk(t, k)[:, :3] - ref[:, :3], axis=1))
        bad = bgtle.Track("26109a.tle", CTX, force=lambda s: s / 86400 + 2440587.5)(t, k)
        self.assertLess(good, 30.0)
        self.assertGreater(np.median(np.linalg.norm(bad[:, :3] - ref[:, :3], axis=1)), 3 * good)

    def test_gaia_frame_is_precession_only(self):
        """At 1.4e6 km the TEME -> GCRS rotation (nutation, ~7") is ~50 km: get_vect J2000 matches Horizons
        Gaia to < 2 km (median), skyfield TEME -> GCRS of the of-date vector does not."""
        from skyfield.sgp4lib import TEME
        t, ref = horizons(-139479, "2025-01-05 00:00", "2025-01-19 00:00")
        trk = bgtle.Track("13074a25.tle", CTX)
        k = trk.index(t)
        j2000 = trk(t, k)[:, :3]
        od = bgtle.Track("13074a25.tle", CTX, of_date=True)(t, k)[:, :3]
        teme = np.einsum("jik,kj->ki", TEME.rotation_at(CTX.times(t)), od)
        self.assertLess(np.median(np.linalg.norm(j2000 - ref[:, :3], axis=1)), 2.0)
        self.assertGreater(np.median(np.linalg.norm(teme - ref[:, :3], axis=1)), 30.0)

    def test_strictly_inside_range(self):
        e, r0, r1 = bgtle.entry("c45gf81.tle")
        self.assertLessEqual(r0, WIN.t0_s)
        self.assertGreaterEqual(r1, WIN.t1_s)
        trk = bgtle.Track("08051a25.tle", CTX)
        with self.assertRaises(ValueError):
            trk.index(np.array([WIN.now_s]))                 # IBEX file ends 2026-01-01
        t_end, rv, info = bgtle.end_state("08051a25.tle", CTX)
        self.assertLessEqual(t_end, parse_iso("2026-01-01T00:00:00Z"))
        self.assertTrue(1e4 < np.linalg.norm(rv[:3]) < 1e6)


@unittest.skipUnless(HAVE_FO, "needs local find_orb fo")
class FoTest(unittest.TestCase):
    """8NIQ001 (NEOCP, hyperbolic Earth flyby, 16 obs): fo vectors against the pseudo-MPEC and the astrometry."""

    @classmethod
    def setUpClass(cls):
        cls.cache, fo.CACHE = fo.CACHE, Path(tempfile.mkdtemp(prefix="cle_fo_test_"))
        cls.lines = [ln[:80] for ln in (FX / "8NIQ001.txt").read_text().splitlines() if len(ln) >= 80]
        cls.page = json.loads((FX / "8NIQ001_page.json").read_text())
        first, last = fo.obs_span(cls.lines)
        cls.a, cls.b = WIN.grid(3600.0, first - 2 * 86400)[0], WIN.now_s + 7 * 86400
        cls.fit = fo.Fit("t8n", (cls.lines, ["-h", "3"], FX / "8NIQ001.htm", {}), cls.a, cls.b, 120.0, sigma_step=3600.0)
        cls.fit.verify(CTX, cls.a, cls.b, WIN.now_s)

    @classmethod
    def tearDownClass(cls):
        fo.CACHE = cls.cache

    def test_vector_format(self):
        """-E 0: header JD = start in TT, geocentric, km and km/s rows (VECTOR_OPTS=0,AU,86400)."""
        head = (self.fit.dir / "ephem.txt").read_text().splitlines()[0].split()
        self.assertAlmostEqual(float(head[0]), float(fo.jd(self.a, tt=True)), delta=2e-5)
        self.assertIn("0,149597870.7,86400", head)
        self.assertIn("Geocentric:", head)
        self.assertTrue(1e3 < np.linalg.norm(self.fit.x[0, :3]) < 1e8)
        self.assertTrue(0.1 < np.linalg.norm(self.fit.x[0, 3:]) < 30)

    def test_reproduces_page_elements(self):
        c = self.fit.check()
        self.assertTrue(c["accepted"])
        for k in "ei":
            self.assertLess(c[f"d{k}_sigma"], 0.1)

    def test_table_consistent_with_one_leg(self):
        """After verify() the table agrees with fo's one-leg propagation (raw fo multi-step tables drift)."""
        for t in (self.a + 86400 * 3.5, WIN.now_s + 3600 * 7):
            self.assertLess(np.linalg.norm(self.fit.single(t)[:3] - self.fit(t)[0, :3]), 2.0)

    def test_geocentric_table(self):
        """Astrometric RA/Dec from our fo vectors against the pseudo-MPEC's 48-h geocentric table."""
        rows = [r for r in self.page["ephemeris"] if self.a <= parse_iso(r["t"]) <= self.b]
        t = np.array([parse_iso(r["t"]) for r in rows])
        x, _ = tracklets.astrometric(self.fit, CTX, t, np.zeros((len(t), 3)))
        ra, dec = np.degrees(np.arctan2(x[:, 1], x[:, 0])) % 360, np.degrees(np.arcsin(x[:, 2] / np.linalg.norm(x, axis=1)))
        sep = sep_arcsec(ra, dec, np.array([r["ra_deg"] for r in rows]), np.array([r["dec_deg"] for r in rows]))
        self.assertLess(sep.max(), 1.0)

    def test_topocentric_tracklets(self):
        """Station + fitted range along the observed RA/Dec: O-C RMS reproduces fo's mean residual (10 %),
        every O-C < 3", the dot less v_earth * tau lies exactly on the observed line of sight, and the dot is on the
        fitted track at its light-time epoch within rho * residual."""
        trk = tracklets.build(tracklets.parse(self.lines), self.fit, CTX, self.a, self.b)
        self.assertEqual(sum(t["n"] for t in trk), 16)
        self.assertEqual({t["stn"] for t in trk}, {"I93", "K19", "L01", "V06"})
        rms = tracklets.rms(trk)
        self.assertLess(abs(rms - self.fit.el["rms_arcsec"]) / self.fit.el["rms_arcsec"], 0.1)
        for t in trk:
            r = np.array(t["resid_arcsec"])
            self.assertLess(np.hypot(r[:, 0], r[:, 1]).max(), 3.0)
            stn = np.frombuffer(__import__("base64").b64decode(t["stn_km"]), "<f8").reshape(-1, 3)
            dot = np.frombuffer(__import__("base64").b64decode(t["dot_km"]), "<f8").reshape(-1, 3)
            rho, te = np.array(t["range_km"]), np.array(t["t_ms"]) / 1000
            u = dot - stn - tracklets.earth_velocity(CTX, te + rho / tracklets.C_KMS) * (rho / tracklets.C_KMS)[:, None]
            off = np.linalg.norm(dot - self.fit(te)[:, :3], axis=1)
            self.assertTrue(np.all(off < rho * np.hypot(r[:, 0], r[:, 1]) / 206265 + 1.0), off)
            ra, dec = np.degrees(np.arctan2(u[:, 1], u[:, 0])) % 360, np.degrees(np.arcsin(u[:, 2] / np.linalg.norm(u, axis=1)))
            self.assertLess(sep_arcsec(ra, dec, np.array(t["ra_deg"]), np.array(t["dec_deg"])).max(), 0.01)
            self.assertTrue(np.all(np.abs(np.linalg.norm(stn, axis=1) - 6370) < 20))


class TrackletTest(unittest.TestCase):
    def test_station_constants(self):
        lon, rc, rs = tracklets.obscodes()["G96"]
        self.assertAlmostEqual(lon, 249.21128, places=5)
        self.assertAlmostEqual(np.hypot(rc, rs), 1.0, delta=0.002)

    def test_satellite_observer(self):
        """'S'/'s' pair: the geocentric vector comes from the second line (km)."""
        head = "     A123456  S2026 10 20.50000 "
        line = (head + "10 00 00.000+10 00 00.00" + " " * 9 + "18.0 G").ljust(77) + "250"
        second = (head[:14] + "s" + head[15:] + "1 " + " ".join(f"{s}{abs(v):10.4f}" for s, v in
                                                            (("+", 4000), ("-", 5000), ("+", 1000)))).ljust(77) + "250"
        obs = tracklets.parse([line, second])
        self.assertEqual(len(obs), 1)
        np.testing.assert_allclose(tracklets.stations(obs, CTX)[0], [4000.0, -5000.0, 1000.0])


class ExtrapTest(unittest.TestCase):
    def test_impact(self):
        line = lambda t: np.column_stack([7000 - 1.0 * (np.atleast_1d(t) - WIN.t0_s), 0 * np.atleast_1d(t),
                                          0 * np.atleast_1d(t), -np.ones(len(np.atleast_1d(t))),
                                          0 * np.atleast_1d(t), 0 * np.atleast_1d(t)])
        hit = extrap.impact(line, CTX, WIN.t0_s, WIN.t0_s + 2 * 3600)
        self.assertAlmostEqual(hit, WIN.t0_s + 1200, delta=601)
        far = lambda t: np.tile([1e5, 2e5, 0, 0, 0, 0], (len(np.atleast_1d(t)), 1))
        self.assertIsNone(extrap.impact(far, CTX, WIN.t0_s, WIN.t1_s))


if __name__ == "__main__":
    unittest.main()
