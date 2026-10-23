"""Data-core tests: ~/.venvs/astro313/bin/python -I -m unittest discover -s tests -p 'test_*.py'"""
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cle import catalog, earthrot, export, timeutil  # noqa: E402
from cle.adapters import horizons as hz  # noqa: E402
from cle.statetable import Segment, StateTable  # noqa: E402
from cle.timeutil import parse_iso  # noqa: E402

PROBE = ROOT / "work/cache/arch_probe"


def decode(text, dtype, n, width):
    return np.frombuffer(base64.b64decode(text), dtype="<f8" if dtype == "f64" else "<f4").reshape(n, width)


def circular(t, r=7000.0, mu=398600.435507):
    w = np.sqrt(mu / r ** 3)
    c, s = np.cos(w * t), np.sin(w * t)
    return np.column_stack([r * c, r * s, 0 * t, -r * w * s, r * w * c, 0 * t])


class FakeSampler:
    """Sampler stand-in: circular orbit, optionally with a 50 km along-track jump at t_jump."""

    def __init__(self, t_jump=None):
        self.t_jump, self.provenance, self.header = t_jump, [], ""

    def states(self, start, stop, step, kind="grid"):
        t = start + step * np.arange(int(round((stop - start) / step)) + 1)
        rv = circular(t)
        if self.t_jump is not None:
            rv[t >= self.t_jump, 1] += 50.0
        return hz.Piece(start, step, rv)


class WindowTest(unittest.TestCase):
    def test_floor_and_grid(self):
        w = timeutil.window("2026-10-21T13:27:41Z")
        self.assertEqual(timeutil.iso(w.now_s), "2026-10-21T13:00:00Z")
        self.assertEqual(timeutil.iso(w.t0_s), "2026-10-06T13:00:00Z")
        self.assertEqual(timeutil.iso(w.t1_s), "2026-11-05T13:00:00Z")
        g = w.grid(3600)
        self.assertEqual((len(g), g[0], g[-1]), (721, w.t0_s, w.t1_s))
        sub = w.grid(21600, w.t0_s + 1, w.t1_s - 1)
        self.assertTrue(np.all((sub - w.t0_s) % 21600 == 0))
        self.assertEqual(sub[0], w.t0_s + 21600)

    def test_horizons_plan_edges(self):
        w = timeutil.window("2026-10-21T13:00:00Z")
        end = parse_iso("2026-11-02T23:50:00Z") - hz.SPAN_MARGIN_S      # SOHO-like coverage end
        pieces = hz.plan(w.t0_s, end, 21600, w)
        self.assertEqual([p[2] for p in pieces], [21600, 600])
        self.assertEqual(pieces[0][1], pieces[1][0])
        self.assertLess(end - pieces[-1][1], 600)
        self.assertEqual(hz.plan(w.t0_s, w.t0_s + 900, 21600, w), [])


class StateTableTest(unittest.TestCase):
    def test_round_trip(self):
        t = np.arange(5) * 60.0
        a = Segment(1e9, 60.0, circular(t)[:, :3], circular(t)[:, 3:], "horizons:-1", "ref")
        b = Segment(1e9 + 240, 30.0, circular(t)[:, :3], circular(t)[:, 3:], "horizons:-1", "pred", "splice",
                    sigma_km=np.arange(5, dtype=np.float32), jump_km=3.5)
        st = StateTable("x", "moon", [a, b], provenance=[{"src": "horizons:-1"}], validation={"m": 1.0}, caveat="c")
        with tempfile.TemporaryDirectory() as d:
            st.save(Path(d))
            back = StateTable.load(Path(d), "x")
        self.assertEqual((back.centre, back.caveat, back.validation, len(back.segments)), ("moon", "c", {"m": 1.0}, 2))
        for s, r in zip(st.segments, back.segments):
            np.testing.assert_array_equal(s.r_km, r.r_km)
            np.testing.assert_array_equal(s.v_kms, r.v_kms)
            self.assertEqual((s.t0_s, s.step_s, s.quality, s.join_before, s.jump_km),
                             (r.t0_s, r.step_s, r.quality, r.join_before, r.jump_km))
        np.testing.assert_array_equal(back.segments[1].sigma_km, b.sigma_km)
        self.assertIsNone(back.segments[0].sigma_km)


class ExportTest(unittest.TestCase):
    def test_base64_dtype_round_trip(self):
        t = np.arange(4) * 3600.0
        near = circular(t, r=4.0e5)
        far = circular(t, r=1.5e6)
        for rv, dtype in ((near, "f32"), (far, "f64")):
            seg = Segment(1.79e9, 3600.0, rv[:, :3], rv[:, 3:], "x:1", "ref")
            tr = export.track([seg], "earth")
            self.assertEqual(tr["dtype"], dtype)
            out = decode(tr["segments"][0]["rv"], dtype, 4, 6)
            if dtype == "f64":
                np.testing.assert_array_equal(out, rv)
            else:
                np.testing.assert_allclose(out, rv, rtol=1e-7, atol=1e-6)
            self.assertEqual(tr["segments"][0]["t0_ms"], 1790000000000)
            self.assertEqual((tr["srcs"], tr["segments"][0]["src"]), (["x:1"], 0))
        raw = base64.b64decode(export.b64(np.array([1.0]), "f64"))
        self.assertEqual(raw, b"\x00\x00\x00\x00\x00\x00\xf0\x3f")          # little-endian 1.0

    def test_track_interning(self):
        """EFF-6: no ISO t0, src as an index into the track's srcs, sigma_km / jump_km only when present."""
        t = np.arange(3) * 60.0
        rv = circular(t)
        segs = [Segment(1.79e9, 60.0, rv[:, :3], rv[:, 3:], "horizons:-1", "ref"),
                Segment(1.79e9 + 120, 60.0, rv[:, :3], rv[:, 3:], "fo:X@2026-10-19", "fit", "splice",
                        sigma_km=np.arange(3, dtype=np.float32), jump_km=2.5),
                Segment(1.79e9 + 240, 60.0, rv[:, :3], rv[:, 3:], "horizons:-1", "pred", "continuous")]
        tr = export.track(segs, "earth")
        self.assertEqual(tr["srcs"], ["horizons:-1", "fo:X@2026-10-19"])
        self.assertEqual([s["src"] for s in tr["segments"]], [0, 1, 0])
        self.assertEqual([s["join_before"] for s in tr["segments"]], ["start", "splice", "continuous"])
        self.assertEqual([sorted(s) for s in tr["segments"]],
                         [["join_before", "n", "quality", "rv", "src", "step_s", "t0_ms"],
                          ["join_before", "jump_km", "n", "quality", "rv", "sigma_km", "src", "step_s", "t0_ms"],
                          ["join_before", "n", "quality", "rv", "src", "step_s", "t0_ms"]])
        self.assertEqual(tr["segments"][1]["jump_km"], 2.5)
        np.testing.assert_array_equal(decode(tr["segments"][1]["sigma_km"], "f32", 3, 1).ravel(), [0, 1, 2])
        self.assertEqual(export.span(tr), ("2026-09-21T14:13:20Z", "2026-09-21T14:19:20Z"))
        json.dumps(tr, allow_nan=False)

    def test_write_js(self):
        with tempfile.TemporaryDirectory() as d:
            p = export.write_js(Path(d) / "a.js", "core", {"x": [1, 2], "s": "é"})
            text = p.read_text()
        self.assertTrue(text.startswith('CLE.register("core", ') and text.endswith(");\n"))
        self.assertEqual(json.loads(text[len('CLE.register("core", '):-3]), {"x": [1, 2], "s": "é"})

    def test_collinear(self):
        em = export.collinear(0.0121506)
        self.assertAlmostEqual(em["gamma_l1"], 0.15093, places=4)
        self.assertAlmostEqual(em["gamma_l2"], 0.16783, places=4)
        se = export.collinear(3.0404e-6)
        self.assertAlmostEqual(se["gamma_l2"] * 1.496e8, 1.5077e6, delta=2e3)


class HorizonsTest(unittest.TestCase):
    def test_parse_small_body(self):
        text = (PROBE / "smallbody_2024PT5.json").read_text()
        a, b = parse_iso("2026-10-21T00:00:00Z"), parse_iso("2026-10-21T02:00:00Z")
        t, rv, header = hz.parse_response(text, "DES=2024 PT5", a, b, 3600)
        self.assertEqual(rv.shape, (3, 6))
        np.testing.assert_array_equal(t - a, [0, 3600, 7200])
        self.assertAlmostEqual(rv[0, 0], 1.964395666328873e7)
        with self.assertRaises(ValueError):
            hz.parse_response(text, "DES=2020 SO", a, b, 3600)              # target-ID assertion
        with self.assertRaises(ValueError):
            hz.parse_response(text, "DES=2024 PT5", a, b, 3600, centre="moon")
        with self.assertRaises(ValueError):
            hz.parse_response(text, "DES=2024 PT5", a, b + 3600, 3600)       # sample count

    def test_coverage_error(self):
        with self.assertRaises(hz.CoverageError) as cm:
            hz.parse_response((PROBE / "capstone_past_end.json").read_text(), "-1176", 0, 1, 1)
        self.assertEqual(cm.exception.side, "end")
        self.assertAlmostEqual(cm.exception.t_s, parse_iso("2026-08-14T11:57:50Z") + 0.8169, places=3)

    def test_cached_spacecraft(self):
        for path in sorted((ROOT / "work/cache/horizons_eph").glob("*/manifest.json")):
            for entry in json.loads(path.read_text())["requests"].values():
                p = entry["parameters"]
                if p["COMMAND"] == "'-170'" and "_grid_" in entry["name"]:
                    a, b = (parse_iso(p[k].strip("'").replace(" ", "T") + "Z") for k in ("START_TIME", "STOP_TIME"))
                    step = int(p["STEP_SIZE"].strip("'").split()[0]) * 60
                    t, rv, header = hz.parse_response((path.parent / entry["file"]).read_text(), "-170", a, b, step)
                    self.assertIn("(-170)", header)
                    self.assertTrue(1.2e6 < np.linalg.norm(rv[0, :3]) < 1.8e6)
                    return
        self.skipTest("no cached JWST response")

    def test_request_and_steps(self):
        p = hz.request_params("-85", 0, 3000, 150, "moon")
        self.assertEqual((p["STEP_SIZE"], p["CENTER"], p["OBJ_DATA"]), ("'20'", "'500@301'", "'NO'"))
        self.assertEqual(hz.request_params("-170", 0, 86400, 21600, obj_data=True)["STEP_SIZE"], "'360 m'")
        self.assertEqual((hz.finer(300, 319), hz.finer(3600, 900), hz.finer(21600, 60)), (150, 900, 60))
        self.assertEqual(hz.runs(np.array([0, 1, 1, 0, 0, 1], bool)), [(1, 2), (5, 5)])
        self.assertEqual(hz.runs(np.array([0, 0, 1, 0, 0, 0], bool), pad=1), [(1, 3)])

    def test_pred_boundary(self):
        hdr = " Tracking data through October 19,\n  prediction thereafter.\nEphemeris / API_USER"
        t, note = hz.pred_boundary(hdr, parse_iso("2026-10-21T13:00:00Z"))
        self.assertEqual(timeutil.iso(t), "2026-10-20T00:00:00Z")
        t, _ = hz.pred_boundary(" Last tracking data was October 18. Prediction", parse_iso("2026-10-21T13:00:00Z"))
        self.assertEqual(timeutil.iso(t), "2026-10-19T00:00:00Z")
        self.assertEqual(hz.pred_boundary("no statement", 0.0), (None, ""))

    def test_refine_and_splice(self):
        t0 = 1.79e9
        smooth = FakeSampler()
        p = smooth.states(t0, t0 + 6 * 3600, 600)
        notes = []
        rows = hz.checked(smooth, p, 1.0, notes)                  # 7000 km circle at 600 s fails 1 km
        self.assertTrue(notes and all(n["action"] == "refine" for n in notes))
        self.assertTrue(all(e.max() < 1.0 for _, e, _, _ in rows))
        jump = FakeSampler(t_jump=t0 + 3 * 3600 + 30)
        p = jump.states(t0, t0 + 6 * 3600, 120)
        notes = []
        rows = hz.checked(jump, p, 1.0, notes)
        splices = [n for n in notes if n["action"] == "splice"]
        self.assertEqual(len(splices), 1)
        self.assertAlmostEqual(splices[0]["jump_km"], 50.0, delta=1.0)
        self.assertEqual([q.join_before for q, *_ in rows].count("splice"), 1)
        self.assertGreaterEqual(min(q.t0_s for q, *_ in rows if q.join_before == "splice"), t0 + 3 * 3600)


class CatalogTest(unittest.TestCase):
    def test_selection(self):
        objs = catalog.load()["objects"]
        rows = catalog.targets()
        self.assertEqual(len(rows), sum(o["tier"] <= 2 for o in objs))
        self.assertEqual(sum(r["default_visible"] for r in rows), sum(o["tier"] == 1 and o["draw"] == "Y" for o in objs))
        by = {r["cid"]: r for r in rows}
        for cid in ("ibex", "capstone"):
            self.assertEqual((by[cid]["tier"], by[cid]["draw"]), (1, "Y"))
            self.assertTrue(by[cid]["caveat"])
        for cid in ("queqiao-1", "queqiao-2", "tiandu-2"):
            self.assertEqual((by[cid]["tier"], by[cid]["draw"], by[cid]["default_visible"]), (1, "N", False))
        naturals = [r for r in rows if r["obj_type"] == "natural"]
        self.assertTrue(naturals and all(r["tier"] == 2 and not r["default_visible"] for r in naturals))
        for r in rows:
            self.assertLessEqual(len(r["label"]), 14, r["cid"])
            self.assertIn(r["chunk"], catalog.CHUNKS)
            self.assertEqual(r["centre"], "moon" if r["regime"] == "LUNAR_ORBIT" else "earth")
        e = catalog.entry(by["jwst"], "SE_L2", True)
        self.assertEqual((e["label"], e["chunk"], e["sources"][0][:15]), ("JWST", "targets-se", "*horizons:-170["))

    def test_compact_sources(self):
        """EFF-8: catalog.js sources are `kind:ref[start..end]` strings, `*` on the primary; web/loader.js parseSource
        (the same regex) gets every field back; src_rows is gone."""
        import re
        pat = re.compile(r"^(\*?)(\w+):(.*?)(?:\[(.*?)\.\.(.*?)\])?$")
        n = 0
        for row in catalog.targets():
            e, want = catalog.entry(row), catalog.ephem_sources(row)
            self.assertNotIn("src_rows", e)
            self.assertLessEqual(sum(s["primary"] for s in want), 1, row["cid"])
            got = []
            for text in e["sources"]:
                m = pat.match(text)
                self.assertTrue(m, text)
                got.append({"kind": m[2], "ref": m[3], "start": m[4] or "", "end": m[5] or "", "primary": m[1] == "*"})
            self.assertEqual(got, want, row["cid"])
            n += len(got)
        self.assertGreater(n, 100)
        self.assertEqual(catalog.compact({"kind": "gp", "ref": "59228", "start": "", "end": "", "primary": False}), "gp:59228")


class StarsTest(unittest.TestCase):
    def test_pack_round_trip(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import pack_stars
        doc = {"meta": {"x": 1}, "hip_ids": [1, 2, 3], "stars": [[0.05331696, 38.30408636, 6.53], [359.9990, -89.95, -1.44], [180.0, 0.0, 0.0]]}
        p = pack_stars.pack(doc)
        self.assertEqual((p["stars"]["n"], p["hip_ids"], p["meta"]["x"]), (3, [1, 2, 3], 1))
        self.assertIs(pack_stars.pack(p), p)
        back = pack_stars.unpack(p["stars"])
        err = np.abs(back - np.array(doc["stars"]))
        err[:, 0] = np.minimum(err[:, 0], 360 - err[:, 0])                         # RA wraps at 360
        half = pack_stars.LSB_DEG / 2
        self.assertTrue((err[:, 0] <= half).all() and (err[:, 1] <= half).all() and (err[:, 2] <= 5e-4).all(), err)
        self.assertEqual(back[1, 0], 0.0)
        packed = json.loads((ROOT / "web/assets/hipparcos.json").read_text())
        stars = packed["stars"]
        self.assertEqual((stars["n"], len(packed["hip_ids"])), (9827, 9827))
        a = pack_stars.unpack(stars)
        self.assertEqual(a.shape, (9827, 3))
        self.assertTrue((a[:, 0] >= 0).all() and (a[:, 0] < 360).all() and (np.abs(a[:, 1]) <= 90).all() and (a[:, 2] < 6.6).all())
        if pack_stars.MMX_SRC.exists():
            src = np.asarray(json.loads(pack_stars.MMX_SRC.read_text())["stars"], float)
            err = np.abs(a - src)
            err[:, 0] = np.minimum(err[:, 0], 360 - err[:, 0])
            self.assertTrue((err[:, :2] <= half).all() and (err[:, 2] <= 5e-4).all())


class ContextTest(unittest.TestCase):
    def test_times_and_quaternions(self):
        from cle.context import Context
        ctx = Context()
        t = parse_iso("2026-10-21T13:00:00Z")
        self.assertEqual(ctx.times(t).utc_iso(), ["2026-10-21T13:00:00Z"])        # leap seconds of the day
        w = timeutil.window("2026-10-21T13:00:00Z")
        q = earthrot.track(w, ctx.ts)["xyzw"]
        self.assertEqual(q.shape, (721, 4))
        np.testing.assert_allclose(np.linalg.norm(q, axis=1), 1, atol=1e-12)
        self.assertTrue(np.all(np.sum(q[1:] * q[:-1], axis=1) > 0))
        from scipy.spatial.transform import Rotation
        fx = earthrot.fixtures(ctx.ts, [w.t0_s])
        rot = Rotation.from_quat(q[0])
        for f in fx:
            np.testing.assert_allclose(rot.apply(f["local_scene"]), f["inertial_scene"], atol=1e-12)
        from skyfield.framelib import itrs                  # independent: POSIX seconds through Context.times
        np.testing.assert_allclose(earthrot.scene_matrices(ctx.ts, [t])[0],
                                   earthrot.MAPPING @ itrs.rotation_at(ctx.times(t)[0]).T @ earthrot.MAPPING.T, atol=1e-12)


if __name__ == "__main__":
    unittest.main()
