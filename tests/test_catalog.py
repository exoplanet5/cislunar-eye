"""Catalog workflow tests (review DW-06/07/08, C3/C4): ~/.venvs/astro313/bin/python -I -m unittest tests.test_catalog

build_master merge determinism and the graded GP rule, overrides, the source-TSV validator over every merged
source, extract_all's plan, horizons_sweep's --now epochs and the window-relative distance columns, and the
catalog's staleness / left-NEOCP export.
"""
import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cle import catalog  # noqa: E402


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bm = load_script("build_master")
vst = load_script("validate_source_tsv")
xa = load_script("extract_all")
HAVE_SOURCES = all((bm.SRC / f"{s}.tsv").exists() for s in ("horizons", "billgray", "neocpsat"))


def merge(out: Path, now="2026-10-21", overrides=None):
    """build_master into `out` (the real sources, the real or a temporary overrides file)."""
    bm.OUT_TSV, bm.OUT_JSON, bm.OUT_MD = out / "master_list.tsv", out / "master_list.json", out / "TARGETS.md"
    bm.OVERRIDES = overrides or ROOT / "catalog/overrides.tsv"
    with contextlib.redirect_stdout(io.StringIO()):
        objs, _ = bm.run(now)
    return objs


class JudgeTest(unittest.TestCase):
    def test_gp_age_thresholds(self):
        """<= 30 d 'window', 30-120 d 'stale-gp' (same GP adapter), > 120 d unusable (DW-07)."""
        bm.set_now("2026-10-21")
        for days, want in ((29, (3, "window")), (30, (3, "window")), (31, (6, "stale-gp")), (119, (6, "stale-gp")),
                           (120, (6, "stale-gp")), (121, (None, "outside"))):
            ep = bm.NOW - timedelta(days=days)
            rank, cov, note = bm.judge({"kind": "spacetrack_gp", "start": ep, "end": ep, "ref": "1", "raw": ""}, False)
            self.assertEqual((rank, cov), want, f"{days} d")
            if cov == "stale-gp":
                self.assertIn(f"{days} d old", note)

    def test_horizons_and_fit(self):
        bm.set_now("2026-10-21")
        self.assertEqual(bm.judge({"kind": "horizons", "start": None, "end": None}, True)[:2], (0, "window"))
        self.assertEqual(bm.judge({"kind": "horizons", "start": None, "end": None}, False)[:2], (None, "unverified"))
        self.assertEqual(bm.judge({"kind": "horizons", "start": None, "end": bm.W0 - timedelta(days=1)}, True)[0], None)
        self.assertEqual(bm.judge({"kind": "neocpsat_fit", "start": None, "end": bm.NOW - timedelta(days=10)}, False)[:2], (4, "fit"))


@unittest.skipUnless(HAVE_SOURCES, "catalog/sources not present")
class MergeTest(unittest.TestCase):
    def test_two_runs_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a", Path(d) / "b"
            a.mkdir(), b.mkdir()
            objs = merge(a)
            merge(b)
            for f in ("master_list.tsv", "master_list.json", "TARGETS.md"):
                self.assertEqual((a / f).read_bytes(), (b / f).read_bytes(), f)
            self.assertTrue(objs)
            cids = [o["cid"] for o in objs]
            self.assertEqual(len(cids), len(set(cids)))
            # the graded GP rule is visible in covers_window and the caveat of every GP primary
            for o in objs:
                if o["primary_ephem"].startswith("spacetrack_gp:"):
                    age = (bm.NOW - date.fromisoformat(o["primary_ephem"].split("..")[1][:10])).days
                    self.assertEqual(o["covers_window"], "stale-gp" if 30 < abs(age) <= 120 else "Y", o["cid"])
                    self.assertEqual(o["caveat"].startswith("stale: Space-Track GP"), o["covers_window"] == "stale-gp", o["cid"])
            md = (a / "TARGETS.md").read_text()
            self.assertIn("| Covers |", md)
            self.assertIn(bm.DECISIONS, md)
            self.assertNotIn("Decisions (2026-10-21)", md)

    def test_overrides_applied(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            cid = merge(out)[0]["cid"]
            ov = out / "overrides.tsv"
            ov.write_text("cid\tfield\tvalue\treason\n"
                          f"{cid}\ttier\t3\ttest\n{cid}\tcaveat\ttest caveat\ttest\nno-such-cid\ttier\t1\tx\n")
            objs = {o["cid"]: o for o in merge(out, overrides=ov)}
            self.assertEqual((objs[cid]["tier"], objs[cid]["tier_reason"]), (3, "override: test"))
            self.assertTrue(objs[cid]["caveat"].startswith("test caveat"))
            md = (out / "TARGETS.md").read_text()
            self.assertIn("override for unknown cid no-such-cid ignored", md)

    def test_left_neocp_from_listed_now(self):
        """DW-06: a neocp row with listed_now=N gives tier_reason 'left the NEOCP <date>' and left_neocp."""
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "sources"
            src.mkdir()
            cols = vst.COLUMNS + vst.NEOCP_EXTRA
            row = dict.fromkeys(cols, "")
            row |= {"src": "neocp", "src_ref": "ZZZ0001", "name": "ZZZ0001", "obj_type": "unknown", "regime": "NEOCP_GEO",
                    "status": "unknown", "status_date": "2026-10-18", "covers_now": "?", "listed_now": "N",
                    "last_listed": "2026-10-19T05:35:01Z",
                    "ephem_sources": "pluto_mpec:https://x/ZZZ0001.htm[2026-10-17..2026-10-18];neocp_obs:work/x.txt[2026-10-17..2026-10-18]"}
            (src / "neocp.tsv").write_text("\t".join(cols) + "\n" + "\t".join(row[c] for c in cols) + "\n")
            old = bm.SRC
            bm.SRC = src
            try:
                objs = merge(Path(d))
            finally:
                bm.SRC = old
            o = objs[0]
            self.assertEqual((o["group"], o["tier"], o["tier_reason"]), ("NEOCP live", 1, "left the NEOCP 2026-10-19"))
            self.assertEqual(o["_left_neocp"], "2026-10-19")
            t = catalog.targets(Path(d) / "master_list.json")[0]
            e = catalog.entry(t)
            self.assertEqual((e["left_neocp"], e["subgroup"]), ("2026-10-19", "left"))


class DistanceColumnsTest(unittest.TestCase):
    """DW-08: horizons_all_spacecraft.tsv distances as d_m15/d_0/d_p15_km around sweep_now, or dated columns."""

    def run_dist(self, header, line, now):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "horizons_all_spacecraft.tsv").write_text(header + "\n" + line + "\n")
            old = bm.SRC
            bm.SRC = Path(d)
            try:
                bm.set_now(now)
                return bm.load_distances()
            finally:
                bm.SRC = old

    def test_relative_columns(self):
        hdr = "horizons_id\tkind\tin_scope\tsweep_now\td_m15_km\td_0_km\td_p15_km\tmin_d_2024_2030_km\tmin_d_date"
        d, meta = self.run_dist(hdr, "-1\tmb\tY\t2026-12-06\t300\t200\t100\t50\t2027-01-01", "2026-12-06")
        self.assertEqual((d["-1"]["min_window"], d["-1"]["frozen"], meta["in_window"], meta["frozen"]), (100.0, False, 1, 0))
        self.assertEqual(meta["sweep_now"], {date(2026, 12, 6)})
        d, meta = self.run_dist(hdr, "-1\tmb\tY\t2026-10-21\t300\t200\t100\t50\t2027-01-01", "2026-12-06")
        self.assertEqual((d["-1"]["min_window"], d["-1"]["frozen"], meta["frozen"]), (100.0, True, 1))   # nearest: 11-05

    def test_dated_columns(self):
        hdr = "horizons_id\tkind\tin_scope\td_20260921_km\td_20261021_km\td_20261120_km\tmin_d_2024_2030_km\tmin_d_date"
        d, meta = self.run_dist(hdr, "-2\tmb\tY\t300\t200\t100\t50\t", "2026-10-21")
        self.assertEqual((d["-2"]["min_window"], d["-2"]["frozen"], meta["sweep_now"]), (200.0, False, {date(2026, 10, 21)}))


@unittest.skipUnless(HAVE_SOURCES, "catalog/sources not present")
class SourceSchemaTest(unittest.TestCase):
    def test_all_merged_sources_validate(self):
        """C4: every file build_master merges passes validate_source_tsv (exit 0)."""
        for src in bm.SOURCES:
            path = bm.SRC / f"{src}.tsv"
            if path.exists():
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    rc = vst.main(str(path))
                self.assertEqual(rc, 0, f"{src}: {out.getvalue()[:600]}")

    def test_neocp_extra_columns(self):
        cols = vst.COLUMNS + vst.NEOCP_EXTRA
        base = dict.fromkeys(cols, "") | {"src": "neocp", "src_ref": "X1", "name": "X1", "obj_type": "unknown",
                                          "regime": "NEOCP_GEO", "status": "unknown", "covers_now": "?"}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "neocp.tsv"
            for extra, rc in (({"listed_now": "N", "last_listed": "2026-10-20T21:03:33Z"}, 0),
                              ({"listed_now": "maybe"}, 1), ({"src": "spacetrack"}, 1)):
                r = base | extra
                p.write_text("\t".join(cols) + "\n" + "\t".join(r[c] for c in cols) + "\n")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(vst.main(str(p)), rc, extra)


class ExtractAllTest(unittest.TestCase):
    def test_plan_and_dry_run(self):
        names = [n for n, _, _ in xa.plan(offline=True)]
        self.assertEqual(names, ["billgray", "neocpsat", "lunarmission", "rocketdebris", "spacetrack"])
        self.assertEqual([skip for _, _, skip in xa.plan(offline=True)][-1], "offline")
        self.assertTrue(all(cmd[1] == "-I" for _, cmd, _ in xa.plan(False)))
        self.assertIn("--offline", xa.plan(False)[2][1])
        r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/extract_all.py"), "--dry-run", "--offline"],
                           capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "CLE_NOW": "2026-10-21T13:00:00Z"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CLE_NOW=2026-10-21T13:00:00Z", r.stdout)
        for n in ("extract_billgray.py", "extract_neocpsat.py", "extract_lunarmission.py --offline", "extract_rocketdebris.py"):
            self.assertIn(n, r.stdout)
        self.assertRegex(r.stdout, r"spacetrack\s+skip \(offline\)")


class SweepNowTest(unittest.TestCase):
    def test_now_epochs(self):
        hs = load_script("horizons_sweep")
        self.assertEqual(hs.set_now("2026-12-06T13:00:00Z"), ["2026-11-21", "2026-12-06", "2026-12-21"])
        self.assertEqual((hs.NOW.isoformat(), hs.WEND.isoformat()), ("2026-12-06T00:00:00", "2026-12-21T00:00:00"))
        self.assertEqual(hs.ep3_params(), {"START_TIME": "2026-11-21", "STOP_TIME": "2026-12-21", "STEP_SIZE": "15 d", "OBJ_DATA": "YES"})
        self.assertEqual(hs.carry_row({"horizons_id": "-1", "d_20260921_km": "3", "d_20261021_km": "2", "d_20261120_km": "1"}),
                         {"horizons_id": "-1", "sweep_now": "2026-10-21", "d_0_km": "2", "d_m15_km": "", "d_p15_km": ""})


class StalenessTest(unittest.TestCase):
    def test_flags(self):
        now = date(2026, 10, 21)
        gp = {"primary_ephem": "spacetrack_gp:3954[2026-08-05..2026-08-05] (stale-gp; GP 2026-08-05, 77 d old)", "covers_window": "stale-gp"}
        self.assertEqual(catalog.staleness(gp, now), {"flag": "gp-stale", "age_d": 77})
        fresh = {"primary_ephem": "spacetrack_gp:59228[2026-10-12..2026-10-12] (window; GP 2026-10-12)", "covers_window": "Y"}
        self.assertEqual(catalog.staleness(fresh, now), {"flag": None, "age_d": 9})
        st = {"primary_ephem": "STALE bgtle:x.tle[..2026-01-01]", "covers_window": "stale"}
        self.assertEqual(catalog.staleness(st, now), {"flag": "extrap", "age_d": 293})
        hz = {"primary_ephem": "horizons:-170[2021-12-25..2026-11-20] (window)", "covers_window": "Y"}
        self.assertEqual(catalog.staleness(hz, now), {"flag": None, "age_d": None})

    def test_entry_keys(self):
        row = {"cid": "x", "name": "X", "label": "X", "group": "g", "obj_type": "payload", "regime": "HEO", "status": "active",
               "tier": 1, "draw": "Y", "chunk": "heo", "centre": "earth", "default_visible": True, "sources": [],
               "staleness": {"flag": "gp-stale", "age_d": 77}, "left_neocp": None, "primary_ephem": "spacetrack_gp:1[..]"}
        e = catalog.entry(row)
        self.assertEqual((e["staleness"], e["left_neocp"], e["subgroup"]), ({"flag": "gp-stale", "age_d": 77}, None, None))


if __name__ == "__main__":
    unittest.main()
