"""Horizons sweep gate and outage rules (REVIEW DW-08): `make daily` runs `sweep` before `catalog`, the sweep does
nothing while the audit table is fresh (--max-age), a request that fails after every retry aborts the run without
caching anything, and the Makefile wiring (FORCE=1, SWEEP_MAX_AGE_D).

    ~/.venvs/astro313/bin/python -I -m unittest tests.test_sweep
"""
import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
NOW = "2026-10-21T13:00:00Z"
HDR = "horizons_id\tname\tkind\tin_scope\tsweep_now\td_m15_km\td_0_km\td_p15_km"


def load_sweep():
    spec = importlib.util.spec_from_file_location("horizons_sweep", ROOT / "scripts/horizons_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hs = load_sweep()


def audit(*rows):
    return {r.split("\t")[0]: dict(zip(HDR.split("\t"), r.split("\t"))) for r in rows}


def make_n(target, *args):
    env = {k: v for k, v in os.environ.items() if k != "CLE_NOW"} | {"CLE_NOW": NOW}
    return subprocess.run(["make", "-n", target, *args], cwd=ROOT, env=env, capture_output=True, text=True, check=True).stdout


class Stop(Exception):
    pass


class AuditAgeTest(unittest.TestCase):
    def test_age_is_the_oldest_in_scope_sweep(self):
        hs.set_now("2026-10-28T13:00:00Z")
        prev = audit("-1\tA\tmb\tY\t2026-10-21\t3\t2\t1", "-2\tB\tmb\tY\t2026-10-25\t3\t2\t1", "-3\tC\tmb\tN\t2026-01-01\t3\t2\t1")
        self.assertEqual(hs.audit_age(prev), 7)
        hs.set_now("2026-10-27T00:00:00Z")
        self.assertEqual(hs.audit_age(prev), 6)
        hs.set_now("2026-10-14T00:00:00Z")                      # a sweep from a later "now" is as stale as an older one
        self.assertEqual(hs.audit_age(prev), 11)

    def test_dated_columns_and_missing_sweeps(self):
        hs.set_now("2026-10-24T00:00:00Z")
        dated = {"-1": {"horizons_id": "-1", "in_scope": "Y", "d_20260921_km": "3", "d_20261021_km": "2", "d_20261120_km": "1"}}
        self.assertEqual(hs.audit_age(dated), 3)
        self.assertIsNone(hs.audit_age(audit("-1\tA\tmb\tY\t\t\t\t")))          # never swept
        self.assertIsNone(hs.audit_age(audit("-1\tA\tmb\tY\t2026-10-21\t3\t2\t1", "-2\tB\tmb\tY\t\t\t\t")))
        self.assertIsNone(hs.audit_age(audit("-3\tC\tmb\tN\t2026-10-21\t3\t2\t1")))   # no in-scope row
        self.assertIsNone(hs.audit_age({}))


class GateTest(unittest.TestCase):
    """main(--max-age N): a fresh audit table ends the run before any request; a stale one goes on to the MB list."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (hs.OUT_ALL, hs.OUT_TSV, hs.RAW, hs.CACHE, hs.hz)
        hs.OUT_ALL = Path(self.tmp.name) / "horizons_all_spacecraft.tsv"
        hs.OUT_TSV = Path(self.tmp.name) / "horizons.tsv"
        hs.CACHE = Path(self.tmp.name) / "cache"
        hs.RAW = hs.CACHE / "raw"

        def no_requests(*a, **k):
            raise Stop(a[1:3])
        hs.hz = no_requests

    def tearDown(self):
        hs.OUT_ALL, hs.OUT_TSV, hs.RAW, hs.CACHE, hs.hz = self.saved
        self.tmp.cleanup()

    def run_main(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return hs.main(argv), err.getvalue()

    def test_fresh_table_does_nothing(self):
        hs.OUT_ALL.write_text(HDR + "\n-1\tA\tmb\tY\t2026-10-21\t3\t2\t1\n-9\tZ\tmb\tN\t2026-01-01\t\t\t\n")
        got, err = self.run_main(["--now", "2026-10-26T13:00:00Z", "--in-scope-only", "--max-age", "7"])
        self.assertIsNone(got)
        self.assertIn("swept 5 d from 2026-10-26 (<= 7 d); nothing to do", err)
        self.assertEqual(hs.OUT_ALL.read_text().count("\n"), 3)              # untouched
        self.assertFalse(hs.OUT_TSV.exists())

    def test_stale_or_unswept_table_goes_on(self):
        hs.OUT_ALL.write_text(HDR + "\n-1\tA\tmb\tY\t2026-10-21\t3\t2\t1\n")
        for now, text in (("2026-10-29T13:00:00Z", "8 d old (> 7 d)"), ("2026-12-06T13:00:00Z", "46 d old (> 7 d)")):
            with self.assertRaises(Stop) as cm:
                self.run_main(["--now", now, "--in-scope-only", "--max-age", "7"])
            self.assertEqual(cm.exception.args[0], ("mb", "MB"))
        hs.OUT_ALL.write_text(HDR.replace("sweep_now", "d_20261021_km") + "\n-1\tA\tmb\tY\t2\t\t\t\n")   # dated columns
        self.assertIsNone(self.run_main(["--now", "2026-10-26T13:00:00Z", "--in-scope-only", "--max-age", "7"])[0])
        with self.assertRaises(Stop):
            self.run_main(["--now", "2026-11-06T13:00:00Z", "--in-scope-only", "--max-age", "7"])
        hs.OUT_ALL.unlink()
        with self.assertRaises(Stop):
            self.run_main(["--now", "2026-10-22T13:00:00Z", "--in-scope-only", "--max-age", "7"])   # no table: due
        hs.OUT_ALL.write_text(HDR + "\n-1\tA\tmb\tY\t2026-10-21\t3\t2\t1\n")
        with self.assertRaises(Stop):
            self.run_main(["--now", "2026-10-22T13:00:00Z", "--in-scope-only"])                     # no gate: runs
        with self.assertRaises(Stop):
            self.run_main(["--now", "2026-10-22T13:00:00Z", "--in-scope-only", "--max-age", "7", "--refresh"])


class OutageTest(unittest.TestCase):
    """hz(): every retry failed -> HorizonsDown, nothing cached, no request counted; a 200 is cached as before."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (hs.RAW, hs.CACHE, hs.RETRIES, hs._SESSION.get, hs.time.sleep, dict(hs.STATS))
        hs.CACHE = Path(self.tmp.name)
        hs.RAW = hs.CACHE / "raw"
        hs.RETRIES = 3
        hs.time.sleep = lambda s: None
        self.calls = []

    def tearDown(self):
        hs.RAW, hs.CACHE, hs.RETRIES, hs._SESSION.get, hs.time.sleep, stats = self.saved
        hs.STATS.update(stats)
        self.tmp.cleanup()

    def hz(self, *a):
        with contextlib.redirect_stderr(io.StringIO()):
            return hs.hz(*a)

    def get(self, responses):
        def _get(url, params=None, timeout=None):
            self.calls.append(params["COMMAND"])
            r = responses[min(len(self.calls), len(responses)) - 1]
            if isinstance(r, Exception):
                raise r
            status, text = r
            return type("R", (), {"status_code": status, "text": text})()
        hs._SESSION.get = _get

    def test_connection_errors_abort_without_caching(self):
        self.get([requests.ConnectionError("down")])
        net = hs.STATS["net"]
        with self.assertRaisesRegex(hs.HorizonsDown, r"-170 ep3: no response after 3 attempts"):
            self.hz(dict(hs.VEC, **hs.ep3_params()), "ep3", "-170")
        self.assertEqual((len(self.calls), hs.STATS["net"]), (3, net))
        self.assertEqual(list(hs.RAW.glob("*")) if hs.RAW.exists() else [], [])

    def test_http_errors_abort_and_a_late_200_is_cached(self):
        self.get([(503, "Service Unavailable")])
        with self.assertRaises(hs.HorizonsDown):
            self.hz(dict(hs.VEC, TLIST="2026-10-21"), "ep1", "-170")
        self.assertEqual(len(self.calls), 3)
        self.calls.clear()
        self.get([(503, "busy"), (200, "API VERSION: 1.2\n$$SOE\n$$EOE\n")])
        text = self.hz(dict(hs.VEC, TLIST="2026-10-21"), "ep1", "-170")
        self.assertEqual((text[:11], len(self.calls)), ("API VERSION", 2))
        self.assertEqual(len(list(hs.RAW.glob("-170__ep1__*.txt.gz"))), 1)
        self.assertEqual(self.hz(dict(hs.VEC, TLIST="2026-10-21"), "ep1", "-170"), text)   # now from the cache
        self.assertEqual(len(self.calls), 2)


class MakeWiringTest(unittest.TestCase):
    def test_daily_sweeps_before_the_catalog_with_the_gate(self):
        out = make_n("daily")
        step = f"horizons_sweep.py --now {NOW} --in-scope-only --max-age 7"
        self.assertIn(step, out)
        order = [out.index(s) for s in ("extract_all.py", step, "validate_source_tsv.py", "build_master.py")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("sweep.py --refresh", out)

    def test_force_and_max_age_knobs(self):
        self.assertRegex(make_n("sweep", "FORCE=1"), rf"horizons_sweep\.py --now {NOW} --in-scope-only\s*$")
        self.assertIn("--in-scope-only --max-age 3", make_n("sweep", "SWEEP_MAX_AGE_D=3"))
        self.assertNotIn("--max-age", make_n("sweep", "FORCE=1", "SWEEP_MAX_AGE_D=3"))


if __name__ == "__main__":
    unittest.main()
