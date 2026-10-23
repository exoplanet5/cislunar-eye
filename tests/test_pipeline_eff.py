"""Pipeline efficiency (REVIEW EFF-2, C2, EFF-9 + DW-05): the extrap fit memo, cli.states lanes / pool / lost-worker
fallback, scripts/prune.py retention and its guards.

    ~/.venvs/astro313/bin/python -I -m unittest tests.test_pipeline_eff
"""
import contextlib
import fcntl
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import BrokenExecutor, Future
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cle import catalog, cli  # noqa: E402
from cle.adapters import extrap  # noqa: E402
from cle.context import KERNEL  # noqa: E402
from cle.statetable import Segment, StateTable  # noqa: E402
from cle.timeutil import window  # noqa: E402

NOW = "2026-10-22T12:00:00Z"
WIN = window(NOW)
TRUTH = np.array([50000.0, 20000.0, 10000.0, -1.5, 2.0, 0.5])


class FitMemoTest(unittest.TestCase):
    """extrap.fit_state: the second call with the same inputs comes from fits.json without a propagation and returns
    the identical state; a changed file, epoch, start state or CLE_REFRESH=1 refits."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cle_eff_")
        self.d = Path(self.tmp.name)
        (self.d / "x.tle").write_text("fake\n")
        self.ctx, self.n = SimpleNamespace(kernel_path=Path("/k/de440s.bsp")), 0

        def propagate(ctx, t_s, rv, a_s, b_s):            # a constant "trajectory": the fit moves r to the track
            self.n += 1
            rv = np.asarray(rv, dtype=float)
            return lambda ts: np.tile(rv, (len(np.atleast_1d(ts)), 1))

        class Track:
            def __init__(self, ref, ctx):
                pass

            def covered(self, a_s=None, b_s=None, range_=(None, None)):
                return 1.7e9 - 30 * 86400, 1.7e9

            def __call__(self, ts):
                return np.tile(TRUTH, (len(ts), 1))

        self.patches = [mock.patch.object(extrap.bgtle, "TLES", self.d), mock.patch.object(extrap.bgtle, "Track", Track),
                        mock.patch.object(extrap.bgtle, "entry", lambda ref: (None, None, None)),
                        mock.patch.object(extrap.nbody, "propagate", propagate),
                        mock.patch.object(extrap, "FITS", self.d / "cache/extrap/fits.json")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def fit(self, t=1.7e9, rv=None):
        return extrap.fit_state("x.tle", self.ctx, t, TRUTH + [100, 0, 0, 0, 0, 0] if rv is None else rv, span_d=2, step=21600.0)

    def memo(self):
        return json.loads((self.d / "cache/extrap/fits.json").read_text())

    def test_hit_is_identical_and_free(self):
        x1, i1 = self.fit()
        n = self.n
        self.assertGreater(n, 1)
        self.assertAlmostEqual(x1[0], TRUTH[0], places=3)
        self.assertLess(i1["fit_rms_km"], i1["single_state_rms_km"])
        self.assertEqual((i1["fit_samples"], i1["fit_span"][1]), (8, "2023-11-14T22:13:20Z"))
        x2, i2 = self.fit()
        self.assertEqual(self.n, n)
        self.assertTrue(np.array_equal(x1, x2))
        self.assertEqual(i1, i2)
        self.assertEqual(len(self.memo()), 1)
        self.assertEqual(next(iter(self.memo().values()))["file"], "x.tle")

    def test_kept_single_state_is_memoised_too(self):
        x, info = self.fit(rv=TRUTH)                         # the start state already fits: kept, no fit_dr_km
        self.assertTrue(np.array_equal(x, TRUTH))
        self.assertNotIn("fit_dr_km", info)
        n = self.n
        x2, info2 = self.fit(rv=TRUTH)
        self.assertEqual((self.n, info2), (n, info))
        self.assertTrue(np.array_equal(x2, TRUTH))

    def test_changed_inputs_refit(self):
        self.fit()
        for change in (lambda: self.fit(t=1.7e9 + 60), lambda: self.fit(rv=TRUTH + [0, 50, 0, 0, 0, 0]),
                       lambda: ((self.d / "x.tle").write_text("new\n"), self.fit())):
            n = self.n
            change()
            self.assertGreater(self.n, n)
        self.assertEqual(len(self.memo()), 4)
        with mock.patch.dict(os.environ, {"CLE_REFRESH": "1"}):
            n = self.n
            self.fit()
            self.assertGreater(self.n, n)
        self.assertEqual(len(self.memo()), 4)


def row(cid, primary):
    return {"cid": cid, "draw": "Y", "primary_ephem": primary, "norad": "", "regime": "CISLUNAR_HEO"}


def table(cid):
    t = WIN.t0_s + 3600.0 * np.arange(6)
    return StateTable(cid, "earth", [Segment(float(t[0]), 3600.0, np.column_stack([7000 + 0 * t, 0 * t, 0 * t]),
                                             np.column_stack([0 * t, 7.5 + 0 * t, 0 * t]), "x:1", "ref")])


class DeadExecutor:
    """A ProcessPoolExecutor whose workers never answer."""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def submit(self, fn, *a, **k):
        f = Future()
        f.set_exception(BrokenExecutor("worker gone"))
        return f


class StatesLanesTest(unittest.TestCase):
    """cli.states with a fake adapter: --jobs 1 is the in-process sequential path in catalog order; with workers the
    Horizons targets build here and a lost worker's target is rebuilt here, never lost."""
    ROWS = [row("a-bg", "bgtle:x.tle[2026-01-01..2026-12-31]"), row("b-fo", "neocpsat_fit:x/a.html@2026-10-02[..2026-10-01]"),
            row("c-hz", "horizons:-1[2026-01-01..2026-12-31]"), row("d-gp", "spacetrack_gp:1[2026-01-01..2026-12-31]"),
            row("e-ex", "STALE spacetrack_gp:2[2026-01-01..2026-09-01]"), row("f-nc", "pluto_mpec:https://x/a.html[..2026-10-01]")]

    def run_states(self, jobs, order):
        fake = SimpleNamespace(build=lambda r, win, ctx: (order.append(r["cid"]), table(r["cid"]))[1])
        args = SimpleNamespace(only=None, adapter=None, tier=2, fetch=False, jobs=jobs)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "BUILD", Path(d)), \
                mock.patch.object(cli, "STATES", Path(d) / "states"), mock.patch.object(cli.catalog, "targets", lambda *x, **k: self.ROWS), \
                mock.patch("cle.adapters.dispatch", lambda r: fake), mock.patch("cle.adapters.neocp.write_json"), \
                contextlib.redirect_stdout(io.StringIO()) as log:
            cli.states(args, WIN, None)
            rep = json.loads((Path(d) / "states_report.json").read_text())
            built = sorted(p.stem for p in (Path(d) / "states").glob("*.npz"))
        return rep, built, log.getvalue()

    def test_lanes(self):
        self.assertEqual([cli.adapter_of(r) for r in self.ROWS], ["bgtle", "fo", "horizons", "gp", "extrap", "neocp"])

    def test_jobs_1_is_sequential_in_catalog_order(self):
        order = []
        rep, built, log = self.run_states(1, order)
        self.assertEqual(order, [r["cid"] for r in self.ROWS])
        self.assertEqual(built, sorted(r["cid"] for r in self.ROWS))
        self.assertEqual({v["status"] for v in rep["targets"].values()}, {"ok"})
        self.assertEqual(rep["states_run"]["jobs"], 1)
        self.assertEqual(re.findall(r"^\[(\d)/6\] (\S+)", log, re.M), [(str(k), r["cid"]) for k, r in enumerate(self.ROWS, 1)])
        self.assertNotIn("worker lost", log)

    def test_lock_wait_is_reported_apart_from_the_work(self):
        """A target queued on work/fo.lock behind another process's find_orb run reports the wait as lock_wait_s;
        seconds is its own work, comparable between --jobs 1 and a pool run; no wait, no key."""
        from cle.adapters import fo
        code = (f"import fcntl, time\nf = open({str(fo.LOCK)!r}, 'a')\nfcntl.flock(f, fcntl.LOCK_EX)\n"
                "print('held', flush=True)\ntime.sleep(0.6)")
        fo.LOCK.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.Popen([sys.executable, "-I", "-c", code], stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "held")

        def build(r, win, ctx):
            with fo.locked():
                return table(r["cid"])
        with tempfile.TemporaryDirectory() as d, mock.patch("cle.adapters.dispatch", lambda r: SimpleNamespace(build=build)):
            _, item = cli.build_one(self.ROWS[0], ["a", "b"], WIN, None, Path(d))
        p.communicate()
        self.assertGreater(item["lock_wait_s"], 0.3)
        self.assertLess(item["seconds"], item["lock_wait_s"])
        self.assertNotIn("lock_wait_s", self.run_states(1, [])[0]["targets"]["a-bg"])

    def test_lost_worker_is_rebuilt_here(self):
        order = []
        with mock.patch.object(cli, "ProcessPoolExecutor", DeadExecutor):
            rep, built, log = self.run_states(4, order)
        self.assertEqual(order[0], "c-hz")                              # the Horizons lane runs here first
        self.assertEqual(sorted(order), sorted(r["cid"] for r in self.ROWS))
        self.assertEqual(built, sorted(r["cid"] for r in self.ROWS))
        self.assertEqual({v["status"] for v in rep["targets"].values()}, {"ok"})
        self.assertEqual(log.count("worker lost"), 5)
        self.assertEqual(len(re.findall(r"^\[\d/6\]", log, re.M)), 6)
        self.assertEqual(rep["states_run"]["jobs"], 4)


@unittest.skipUnless(KERNEL.exists() and (ROOT / "work/cache/gp").is_dir(), "needs DE440s and the cached GP sets")
class StatesPoolTest(unittest.TestCase):
    """Two spawned workers (own Context each) save the same tables as the sequential path: offline, cached GP sets,
    pinned window; no find_orb, no Horizons."""
    CIDS = ("imp-8", "vela-5b", "geotail")

    def test_pool_matches_sequential(self):
        from cle.context import Context
        rows = [r for r in catalog.targets() if r["cid"] in self.CIDS]
        self.assertEqual(len(rows), 3)
        ctx, out = Context(), {}
        clean = [p for p in sys.path if Path(p) != ROOT / "scripts"]    # other tests put scripts/ first: scripts/cle.py
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"CLE_OFFLINE": "1", "CLE_NOW": NOW}), \
                mock.patch.object(sys, "path", clean):                      # would shadow the cle package in a spawn child
            for jobs in (1, 2):
                build = Path(d) / f"j{jobs}"
                build.mkdir()
                args = SimpleNamespace(only=None, adapter=None, tier=2, fetch=False, jobs=jobs)
                with mock.patch.object(cli, "BUILD", build), mock.patch.object(cli, "STATES", build / "states"), \
                        mock.patch.object(cli.catalog, "targets", lambda *x, **k: rows), mock.patch("cle.adapters.neocp.write_json"), \
                        contextlib.redirect_stdout(io.StringIO()) as log:
                    cli.states(args, WIN, ctx)
                rep = json.loads((build / "states_report.json").read_text())
                self.assertEqual({c: rep["targets"][c]["status"] for c in self.CIDS}, dict.fromkeys(self.CIDS, "ok"), log.getvalue())
                self.assertEqual(rep["states_run"]["jobs"], jobs)
                self.assertEqual(len(re.findall(r"^\[\d/3\]", log.getvalue(), re.M)), 3)
                self.assertNotIn("worker lost", log.getvalue())
                out[jobs] = {c: dict(np.load(build / f"states/{c}.npz")) for c in self.CIDS}
        for c in self.CIDS:
            a, b = out[1][c], out[2][c]
            self.assertEqual(sorted(a), sorted(b))
            for k in a:
                self.assertTrue(np.array_equal(a[k], b[k]), (c, k))


def load_prune():
    spec = importlib.util.spec_from_file_location("prune", ROOT / "scripts/prune.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PruneTest(unittest.TestCase):
    """scripts/prune.py on a synthetic tree: referenced + previous fo stamps kept, chosen ephem.txt plain, the rest
    gzipped, other stamps deleted, young stamps untouched; horizons_eph referenced + newest 2; locks refuse."""
    S1, S2, S3, S4 = "20261020T000000Z", "20261021T000000Z", "20261022T000000Z", "20261022T120000Z"
    HZ = ("20261020T000000Z_aaaa", "20261021T000000Z_bbbb", "20261021T120000Z_cccc", "20261022T000000Z_dddd", "20261022T060000Z_eeee")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cle_prune_")
        self.root, self.prune = Path(self.tmp.name), load_prune()
        self.fo, self.hz = self.root / "work/cache/fo", self.root / "work/cache/horizons_eph"
        old = time.time() - 5 * 3600
        for stamp, labels in ((self.S1, ["a_E0"]), (self.S2, ["a_E0", "b_E0", "c_E0"]),
                              (self.S3, ["a_E0", "a_E16", "b_E0", "a_day_E0"]), (self.S4, ["a_E0"])):
            for label in labels:
                d = self.fo / stamp / label
                d.mkdir(parents=True)
                (d / "ephem.txt").write_bytes(b"2461000.5 1.0 2.0 3.0 0.1 0.2 0.3\n" * 2000)
                (d / "elements.txt").write_text("x\n")
                (d / "command.json").write_text("{}\n")
                (d / "total.json").write_text("{" + "x" * 3000 + "}\n")      # one of fo's side files
            if stamp != self.S4:
                os.utime(self.fo / stamp, (old, old))
        (self.fo / "verdicts.json").write_text("{}")
        (self.fo / "inputs.sha256").write_text("x\n")
        for s in self.HZ:
            d = self.hz / s
            d.mkdir(parents=True)
            (d / "manifest.json").write_text("{}")
            (d / "r.json").write_text("x" * 1000)
            os.utime(d, (old, old))
        (self.root / "work/cache/neocp").mkdir(parents=True)
        (self.root / "work/cache/neocp/obs.txt").write_text("keep\n")
        states = self.root / "build/states"
        states.mkdir(parents=True)
        (states / "a.json").write_text(json.dumps({"provenance": [{"raw": f"work/cache/fo/{self.S3}/a_E0/elements.txt"}],
                                                   "validation": {"fo_dir": f"work/cache/fo/{self.S3}/a_E0"}}))
        (states / "h.json").write_text(json.dumps({"provenance": [{"raw": f"work/cache/horizons_eph/{self.HZ[0]}/r.json"}]}))
        (self.root / "build/moon_check.json").write_text(json.dumps({"raw": f"work/cache/horizons_eph/{self.HZ[1]}/r.json"}))

    def tearDown(self):
        self.tmp.cleanup()

    def run_prune(self, apply):
        out = io.StringIO()
        rc = self.prune.run(self.root, apply, 1.0, out)
        return rc, out.getvalue()

    def tree(self):
        return sorted(str(p.relative_to(self.root)) for p in (self.root / "work/cache").rglob("*") if p.is_file())

    def test_plan_and_apply(self):
        self.assertEqual(self.prune.referenced(self.root / "build"), ({self.S3: {"a_E0"}}, {self.HZ[0], self.HZ[1]}))
        before, size = self.tree(), self.prune.du(self.root / "work/cache")
        rc, text = self.run_prune(False)
        self.assertEqual((rc, self.tree()), (0, before))                       # a dry run changes nothing
        self.assertIn("would free", text)
        want = float(re.search(r"would free ([\d.]+) MB", text)[1])
        rc, text = self.run_prune(True)
        self.assertEqual(rc, 0)
        self.assertAlmostEqual(float(re.search(r"^freed ([\d.]+) MB", text, re.M)[1]), want, places=1)
        self.assertAlmostEqual((size - self.prune.du(self.root / "work/cache")) / 1e6, want, places=1)
        after = self.tree()
        fo = lambda *p: str(Path("work/cache/fo", *p))
        self.assertNotIn(fo(self.S1, "a_E0/elements.txt"), after)             # unreferenced, old: deleted
        self.assertIn(fo(self.S2, "a_E0/ephem.txt"), after)                   # previous fit of a: kept plain
        self.assertIn(fo(self.S2, "b_E0/ephem.txt.gz"), after)                # the rest of a kept stamp: gzipped
        self.assertIn(fo(self.S2, "c_E0/ephem.txt.gz"), after)
        self.assertIn(fo(self.S3, "a_E0/ephem.txt"), after)                   # the chosen fit: plain, side files kept
        self.assertIn(fo(self.S3, "a_E0/total.json"), after)
        self.assertIn(fo(self.S2, "a_E0/total.json"), after)
        for stamp, label in ((self.S3, "a_E16"), (self.S3, "b_E0"), (self.S3, "a_day_E0"), (self.S2, "b_E0"), (self.S2, "c_E0")):
            self.assertIn(fo(stamp, label, "ephem.txt.gz"), after)             # other runs of kept stamps: gzipped, trimmed
            self.assertNotIn(fo(stamp, label, "ephem.txt"), after)
            self.assertNotIn(fo(stamp, label, "total.json"), after)
            self.assertIn(fo(stamp, label, "command.json"), after)
            self.assertIn(fo(stamp, label, "elements.txt"), after)
        self.assertIn(fo(self.S4, "a_E0/ephem.txt"), after)                   # young: untouched
        self.assertIn(fo(self.S4, "a_E0/total.json"), after)
        self.assertIn(fo("verdicts.json"), after)
        self.assertIn(fo("inputs.sha256"), after)
        self.assertEqual([s for s in self.HZ if (self.hz / s).is_dir()], [s for s in self.HZ if s != self.HZ[2]])
        self.assertIn("work/cache/neocp/obs.txt", after)
        self.assertEqual(len(after), len(before) - 4 - 2 - 5)                 # S1 (4 files), cccc (2), 5 side files; gzip is in place
        self.assertIn("delete 5 side files", text)
        rc, text = self.run_prune(True)                                       # idempotent
        self.assertEqual((rc, self.tree()), (0, after))
        self.assertIn("freed 0.0 MB", text)

    def test_refuses_while_locked_or_unreferenced(self):
        before = self.tree()
        lock = self.root / "work/make.lock"
        lock.mkdir()
        p = subprocess.Popen(["sleep", "30"])
        try:
            (lock / "pid").write_text(str(p.pid))
            rc, text = self.run_prune(True)
            self.assertEqual((rc, self.tree()), (2, before))
            self.assertIn("make.lock held by pid", text)
        finally:
            p.kill()
            p.wait()
        (lock / "pid").write_text(str(os.getppid()))                          # our own ops/run.sh: not a conflict
        rc, _ = self.run_prune(False)
        self.assertEqual(rc, 0)
        (lock / "pid").write_text(str(p.pid))                                 # dead pid: stale, not held
        rc, _ = self.run_prune(False)
        self.assertEqual(rc, 0)
        lock.joinpath("pid").unlink()
        lock.rmdir()
        fl = self.root / "work/fo.lock"
        fl.write_text("")
        with open(fl) as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            rc, text = self.run_prune(True)
            self.assertEqual((rc, self.tree()), (2, before))
            self.assertIn("fo.lock held", text)
        rc, _ = self.run_prune(False)
        self.assertEqual(rc, 0)
        for p in (self.root / "build").rglob("*.json"):
            p.unlink()
        rc, text = self.run_prune(True)
        self.assertEqual((rc, self.tree()), (2, before))
        self.assertIn("references no cache stamp", text)

    def test_makefile_wiring(self):
        out = subprocess.run(["make", "-n", "prune", "prune-apply"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        self.assertRegex(out, r"-I scripts/prune\.py\n")
        self.assertRegex(out, r"-I scripts/prune\.py --apply\n")


if __name__ == "__main__":
    unittest.main()
