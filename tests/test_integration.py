"""Integration rules between build/states, build/tracklets.json, the export and the page (unittest, -I).

    ~/.venvs/astro313/bin/python -I -m unittest discover -s tests -p 'test_*.py'
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cle import astrometry, export  # noqa: E402
from cle.statetable import Segment, StateTable  # noqa: E402
from cle.timeutil import iso, window  # noqa: E402

WIN, OTHER = window("2026-10-21T13:00:00Z"), window("2026-12-01T13:00:00Z")
OBS = ROOT / "tests/fixtures/adapters/8NIQ001.txt"                 # 16 obs, 2026-10-15/16 (inside WIN, not OTHER)
TK = {"stn": "G96", "n": 1, "t_ms": [1792100000000], "ra_deg": [1.0], "dec_deg": [2.0], "mag": [20.0], "range_km": [1e5],
      "stn_km": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "dot_km": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "resid_arcsec": [[0.1, 0.2]]}


def table(cid, win, centre="earth"):
    t = win.t0_s + 3600.0 * np.arange(6)
    st = StateTable(cid, centre, [Segment(float(t[0]), 3600.0, np.column_stack([7000 + 0 * t, 0 * t, 0 * t]),
                                          np.column_stack([0 * t, 7.5 + 0 * t, 0 * t]), "x:1", "ref")])
    st.now_utc, st.t0_utc, st.t1_utc = iso(win.now_s), iso(win.t0_s), iso(win.t1_s)
    return st


def row(cid, draw="Y", obs=OBS):
    return {"cid": cid, "draw": draw, "chunk": "cislunar", "regime": "CISLUNAR_HEO", "centre": "earth",
            "sources": [{"ephem_sources": f"gp:1[..];neocp_obs:{obs}[2026-10-15..2026-10-16];neocp_obs:{obs}#dup"}]}


class NotDrawnTest(unittest.TestCase):
    def test_failed_build_is_not_drawn_even_with_old_states(self):
        """A target whose adapter failed this build is listed with the reason, never drawn from stale states."""
        with tempfile.TemporaryDirectory() as d, mock.patch.object(export, "STATES", Path(d)):
            (Path(d) / "x.json").write_text("{}")
            row = {"cid": "x"}
            self.assertEqual(export.not_drawn(row, {"status": "ok"}), "")
            why = export.not_drawn(row, {"status": "failed", "error": "RuntimeError: x: no coherent fit (a.html: e=1.19e+03, 4/4 obs)"})
            self.assertEqual(why, "build failed: no coherent fit (details: build/validation_report.md)")
            self.assertIn("no states built", export.not_drawn({"cid": "y"}, {}))


class AstrometryTest(unittest.TestCase):
    """cle.astrometry: a drawn target's neocp_obs files (deduplicated, inside the window) -> build/tracklets.json ->
    the `tracklets` key of its chunk payload, only for the export's own window and only when not empty."""

    def test_neocp_obs_window_and_dedup(self):
        files, lines = astrometry.neocp_obs(row("a"), WIN)
        self.assertEqual(files, [str(OBS.relative_to(ROOT))])               # the same file twice: listed once
        self.assertEqual(len(lines), 16)                                     # ... and its lines not doubled
        self.assertEqual(astrometry.neocp_obs(row("a"), OTHER)[1], [])
        self.assertEqual(astrometry.neocp_obs(row("a", obs="no/such.txt"), WIN), ([], []))

    def test_catalog_tracklets_and_load(self):
        rows = [row("a"), row("b-not-drawn", draw="N"), row("c-other-window"), row("d-no-states")]
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(astrometry.tracklets, "build", lambda obs, at, ctx, *span: [TK] if obs else []):
            states, out = Path(d) / "states", Path(d) / "tracklets.json"
            for cid in ("a", "b-not-drawn"):
                table(cid, WIN).save(states)
            table("c-other-window", OTHER).save(states)
            real = astrometry.catalog_tracklets                              # these rows and states, not build/'s
            with mock.patch.object(astrometry, "catalog_tracklets", lambda win, ctx: real(win, ctx, rows, states)):
                doc = astrometry.write_json(WIN, None, out)
            self.assertEqual((doc["schema"], doc["window"]), ("cle/tracklets@1", [iso(WIN.t0_s), iso(WIN.t1_s)]))
            self.assertEqual(sorted(doc["targets"]), ["a"])
            self.assertEqual(doc["targets"]["a"]["tracklets"], [TK])
            self.assertEqual(doc["targets"]["a"]["obs_files"], [str(OBS.relative_to(ROOT))])
            self.assertEqual(json.loads(out.read_text())["targets"]["a"]["rms_arcsec"], doc["targets"]["a"]["rms_arcsec"])
            self.assertEqual(astrometry.load(WIN, out), {"a": [TK]})
            self.assertEqual(astrometry.load(OTHER, out), {})                 # built for another window: nothing
            self.assertEqual(astrometry.load(WIN, Path(d) / "missing.json"), {})

    def test_export_puts_tracklets_in_the_chunk_payload(self):
        from cle import catalog
        rows = [r for r in catalog.targets() if r["draw"] == "Y"][:2]
        (a, b), this = (r["cid"] for r in rows), export.window_of(WIN)
        with tempfile.TemporaryDirectory() as d:
            build, dist, states = Path(d) / "build", Path(d) / "dist", Path(d) / "build/states"
            for cid in (a, b):
                table(cid, WIN).save(states)
            build.joinpath("states_report.json").write_text(json.dumps({"window": this, "now_utc": iso(WIN.now_s),
                                                                        "targets": {a: {"status": "ok"}, b: {"status": "ok"}}}))
            build.joinpath("tracklets.json").write_text(json.dumps({"schema": "cle/tracklets@1", "window": this, "targets": {
                a: {"tracklets": [TK], "obs_files": [], "rms_arcsec": 0.16}, b: {"tracklets": [], "obs_files": [], "rms_arcsec": None}}}))
            with mock.patch.object(export, "STATES", states), mock.patch.object(export, "BUILD", build), \
                    mock.patch.object(export, "DIST", dist), mock.patch.object(export, "core", lambda win, ctx: {"schema": "cle/core@1"}), \
                    mock.patch.object(export, "regime_now", lambda *x: {"regime_now": "", "check": None, "pass": None}), \
                    mock.patch.object(export.catalog, "targets", lambda *x, **k: rows):
                res = export.run(WIN, None)
                chunk = {r["cid"]: export.read_js(dist / f"targets-{r['chunk']}.js")["targets"][r["cid"]] for r in rows}
                build.joinpath("tracklets.json").write_text(build.joinpath("tracklets.json").read_text()
                                                            .replace(this[0], "2026-09-01T13:00:00Z"))
                export.run(WIN, None)                                         # tracklets of another window: dropped
                stale = export.read_js(dist / f"targets-{rows[0]['chunk']}.js")["targets"][a]
        self.assertEqual(res["tracklets"], {a: 1})
        self.assertEqual(chunk[a]["tracklets"], [TK])
        self.assertNotIn("tracklets", chunk[b])                               # empty: the key is omitted
        self.assertEqual(sorted(chunk[a]), ["caveat", "events", "quality_note", "track", "tracklets", "validation"])
        self.assertNotIn("tracklets", stale)


if __name__ == "__main__":
    unittest.main()
