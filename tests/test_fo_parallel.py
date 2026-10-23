"""fo runs in a thread pool (REVIEW EFF-1) and 1-row candidate fits (EFF-3):
    ~/.venvs/astro313/bin/python -I -m unittest tests.test_fo_parallel

The real-fo tests use a short run directory under work/tmp/ (fo truncates -O paths longer than 93 characters
and exits 255), so every run's returncode can be asserted. 8NIQ001 over the neocp span (15 one-day chunks at
120 s) is the workload: daily() with 1 worker and with 8 must give bitwise-identical ephem.txt, arrays and saved
npz, and verify()'s pooled legs and rebuild must match the sequential rebuild. Bitwise identity holds because
fo's four automatic improvement steps finish within its quarter-second budget for a 16-obs fit; run() adds
-y STEPS so that slower fits (313-obs Falcon Heavy) converge to the same minimum under load (StepsTest).
"""
import fcntl
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cle import nbody  # noqa: E402
from cle.adapters import fo  # noqa: E402
from cle.statetable import StateTable  # noqa: E402
from cle.timeutil import window  # noqa: E402

FX = ROOT / "tests/fixtures/adapters"
WIN = window("2026-10-21T13:00:00Z")
LINES = [ln[:80] for ln in (FX / "8NIQ001.txt").read_text().splitlines() if len(ln) >= 80]
TMP = ROOT / "work/tmp"


def rc(d):
    return json.loads((d / "command.json").read_text())["returncode"]


def elements_lines(d):
    return [x for x in (d / "elements.txt").read_text().splitlines() if not x.startswith("# Elements written")]


class LockTest(unittest.TestCase):
    def test_hold_is_reentrant_and_exclusive_across_processes(self):
        code = (f"import fcntl\nf = open({str(fo.LOCK)!r}, 'a')\ntry:\n    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)"
                "\n    print('free')\nexcept BlockingIOError:\n    print('held')")
        probe = lambda: subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True).stdout.strip()
        with fo.locked():
            with fo.locked():                       # a nested use neither deadlocks nor releases the hold
                self.assertEqual(probe(), "held")
            self.assertEqual(probe(), "held")
            self.assertEqual(fo._hold[1][0], 1)
        self.assertEqual(probe(), "free")
        self.assertEqual(fo._hold[1][0], 0)

    def test_wait_behind_another_process_is_accounted(self):
        """lock_wait() sums the time this process spends blocked on LOCK (states' pool workers queue on it behind the
        find_orb lane); cli.build_one takes it out of a target's seconds."""
        p = hold_lock(0.6)
        w0, t = fo.lock_wait(), time.time()
        with fo.locked():
            waited = time.time() - t
        p.communicate()
        self.assertGreater(waited, 0.3)
        self.assertAlmostEqual(fo.lock_wait() - w0, waited, delta=0.05)
        with fo.locked():                                   # an uncontended hold adds (next to) nothing
            pass
        self.assertAlmostEqual(fo.lock_wait() - w0, waited, delta=0.05)


def hold_lock(seconds):
    """A process that holds fo.LOCK for `seconds`; returns once it has it."""
    code = (f"import fcntl, time\nf = open({str(fo.LOCK)!r}, 'a')\nfcntl.flock(f, fcntl.LOCK_EX)\n"
            f"print('held', flush=True)\ntime.sleep({seconds})")
    fo.LOCK.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([sys.executable, "-I", "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


class ScratchTest(unittest.TestCase):
    def test_scratch_run_keeps_only_what_is_read_back(self):
        """Probes, legs, one-day chunks and short tables drop fo's side files right after the run (prune trims every
        unreferenced run the same way later); a window table keeps them."""
        def fake(cmd, **kw):
            d = Path(cmd[cmd.index("-O") + 1])
            for name in ("elements.txt", "ephem.txt", "total.json", "elements.json", "covar.txt", "residual.txt"):
                (d / name).write_text("x\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with tempfile.TemporaryDirectory(prefix="fo_scratch_") as tmp, mock.patch.object(fo, "CACHE", Path(tmp)), \
                mock.patch.object(fo, "STAMP", "s"), mock.patch.object(fo.subprocess, "run", fake), \
                mock.patch.object(fo, "environ", lambda path, *a: path.write_text("")):
            kept = fo.run("a", LINES, ["-h", "3"], WIN.t0_s, 1, 3600.0)
            scratch = fo.run("a_l0", LINES, ["-h", "3"], WIN.t0_s, 1, 3600.0, scratch=True)
            self.assertEqual({f.name for f in scratch.iterdir()}, fo.KEEP)
            self.assertLessEqual({"total.json", "elements.json", "covar.txt", "fo_env.dat"} | fo.KEEP, {f.name for f in kept.iterdir()})


class EpochLabelTest(unittest.TestCase):
    def test_epoch_label_carries_the_cid(self):
        """states' pool workers share one STAMP and run() replaces an existing run directory, so two targets
        extrapolated from one page must not share '<page>_epoch': extrap passes the cid, epoch_state labels by it."""
        from cle.adapters import extrap
        made, ref = [], "x/8NIQ001.htm@2026-10-02"
        TMP.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fo_epoch_", dir=TMP) as tmp:          # fit.dir is kept relative to ROOT
            class Stub:
                def __init__(self, label, src, *a, **k):
                    made.append(label)
                    self.dir, self.args, self.el = Path(tmp) / label, [], {"epoch_tt_s": WIN.now_s}
                    self.dir.mkdir()
                    for name, text in (("obs.txt", "x\n"), ("elements.txt", "x\n"), ("command.json", '{"cmd": []}')):
                        (self.dir / name).write_text(text)
                single = lambda self, t, k=0: np.zeros(6)
                check = lambda self: {}
            with mock.patch.object(fo, "Fit", Stub), \
                    mock.patch.object(fo, "resolve", lambda r: (LINES, ["-h", "3"], FX / "8NIQ001.htm", {})):
                fo.epoch_state(ref, None, cid="t8n")
                fo.epoch_state(ref, None)
        self.assertEqual(made, ["t8n_epoch", "8NIQ001_epoch"])
        got = {}
        fake = lambda r, ctx, **k: (got.update(k), (WIN.now_s, np.zeros(6), {"provenance": []}))[1]
        with mock.patch.object(extrap.fo, "epoch_state", fake):
            extrap.latest({"cid": "t8n", "primary_ephem": f"STALE neocpsat_fit:{ref}[..2026-10-01]"}, WIN, None)
        self.assertEqual(got["cid"], "t8n")


class StepsTest(unittest.TestCase):
    def test_every_run_adds_fixed_improvement_steps(self):
        """fo's automatic fit is wall-clock limited (four steps or 0.25 s), so run() always passes -y STEPS unless the
        arguments carry their own (a memoised verdict's, or the -y 30 retry)."""
        cmds = []
        fake = lambda cmd, **kw: (cmds.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1]
        with tempfile.TemporaryDirectory(prefix="fo_steps_") as tmp, mock.patch.object(fo, "CACHE", Path(tmp)), \
                mock.patch.object(fo, "STAMP", "s"), mock.patch.object(fo.subprocess, "run", fake), \
                mock.patch.object(fo, "environ", lambda path, *a: path.write_text("")):
            fo.run("a", LINES, ["-h", "3"], WIN.t0_s, 1, 3600.0)
            fo.run("b", LINES, ["-h", "3", "-y", "30"], WIN.t0_s, 1, 3600.0, option="16")
        for cmd, y in zip(cmds, (str(fo.STEPS), "30")):
            self.assertEqual(cmd.count("-y"), 1)
            self.assertEqual(cmd[cmd.index("-y") + 1], y)
            self.assertLess(cmd.index("-y"), cmd.index("-i"))
        self.assertEqual(cmds[0][2:4], ["-h", "3"])


class LabelTest(unittest.TestCase):
    def test_short_keeps_every_run_path_under_the_limit(self):
        long = "falcon-heavy-013-stage-2_Falcon_Heavy_S2_combined"
        s = fo.short(long)
        self.assertNotEqual(s, long)
        self.assertEqual(s, fo.short(long))
        self.assertNotEqual(s, fo.short(long + "_2026_10_01"))
        head, sha = s.rsplit("_", 1)
        self.assertTrue(long.startswith(head) and len(head) >= 5 and len(sha) == 8, s)
        for suffix in ("_d00_E0", "_fy_E0", "_l0_E0", "_s_E0", "_E16"):
            self.assertLessEqual(len(str(fo.CACHE / fo.STAMP / (s + suffix))), fo.PATH_MAX)
        self.assertEqual(fo.short("cew2vg2"), "cew2vg2")
        with mock.patch.object(fo, "CACHE", Path("/" + "x" * 120)):       # no label fits: left alone
            self.assertEqual(fo.short(long), long)


@unittest.skipUnless(fo.FO.exists(), "needs local find_orb fo")
class PoolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cle.context import Context
        TMP.mkdir(parents=True, exist_ok=True)
        cls.tmp = Path(tempfile.mkdtemp(prefix="fo_par_", dir=TMP))
        cls.patches = [mock.patch.object(fo, "CACHE", cls.tmp), mock.patch.object(fo, "STAMP", "s")]
        for p in cls.patches:
            p.start()
        cls.ctx = Context()
        first, _ = fo.obs_span(LINES)
        cls.a, cls.b = WIN.grid(3600.0, first - 2 * 86400)[0], WIN.now_s + 7 * 86400
        cls.ref = (LINES, ["-h", "3"], FX / "8NIQ001.htm", {})

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pool_runs_are_bitwise_identical_to_sequential(self):
        fit = fo.Fit("t8n", self.ref, self.a, self.b, 120.0, sigma_step=3600.0)
        self.assertEqual(fit.dir.name, "t8n_E0")
        self.assertEqual(elements_lines(self.tmp / "s/t8n_f_E0"), elements_lines(fit.dir))
        with mock.patch.object(fo, "WORKERS", 1):
            t = time.time()
            seq = fit.daily(self.a, self.b)
            t_seq = time.time() - t
        with mock.patch.object(fo, "STAMP", "p"):
            t = time.time()
            par = fit.daily(self.a, self.b)
            t_par = time.time() - t
        np.testing.assert_array_equal(seq[0], par[0])
        np.testing.assert_array_equal(seq[1], par[1])
        self.assertEqual(seq[2], par[2])
        chunks = sorted((self.tmp / "s").glob("t8n_d*_E0"))
        self.assertEqual([d.name for d in chunks], [f"t8n_d{i:02d}_E0" for i in range(15)])
        for d in chunks + [self.tmp / "s/t8n_l3_E0"]:
            p = self.tmp / "p" / d.name
            self.assertEqual((rc(d), rc(p)), (0, 0), d.name)
            self.assertEqual((d / "ephem.txt").read_bytes(), (p / "ephem.txt").read_bytes(), d.name)
            self.assertEqual(elements_lines(d), elements_lines(p), d.name)
            self.assertEqual({f.name for f in d.iterdir()}, fo.KEEP, d.name)       # scratch runs: no side files
        self.assertTrue((fit.dir / "total.json").exists() and (fit.sigma_dir / "total.json").exists())
        print(f"\n  daily(): {len(chunks)} one-day chunks + end leg, 1 worker {t_seq:.1f} s, 8 workers {t_par:.1f} s")
        with mock.patch.object(fo, "STAMP", "v"):            # verify: 3 legs and the rebuild in the pool
            t = time.time()
            fit.verify(self.ctx, self.a, self.b, WIN.now_s)
            t_ver = time.time() - t
        self.assertEqual(fit.mode, "fo_daily")
        legs = fit.consistency["fo_table_vs_one_leg_km"]
        self.assertEqual(len(legs), 3)
        self.assertGreater(max(legs.values()), 1.0)
        for k in range(3):
            self.assertEqual(rc(self.tmp / f"v/t8n_l{k}_E0"), 0)
        np.testing.assert_array_equal(fit.x, seq[1])
        print(f"  verify(): legs + rebuild {t_ver:.1f} s, legs {legs}")
        tables = []                                           # the saved npz arrays of both rebuilds
        for name, (t, x, _) in (("seq", seq), ("par", (fit.t, fit.x, None))):
            segs, _ = nbody.segments(fo.hermite(t, x), WIN, 3600.0, self.a, self.b, "fo", "fit", "start", self.ctx)
            tables.append(np.load(StateTable(cle_id=name, centre="earth", segments=segs).save(self.tmp / "st")))
        self.assertEqual(sorted(tables[0].files), sorted(tables[1].files))
        for k in tables[0].files:
            np.testing.assert_array_equal(tables[0][k], tables[1][k])

    def test_candidate_fit_is_one_row_and_checked_on_a_short_table(self):
        cand = fo.Fit("cand", (LINES[:8], *self.ref[1:]), self.a, self.b, 120.0, with_sigma=False, table=False)
        self.assertEqual(cand.dir.name, "cand_f_E0")
        self.assertEqual(json.loads((cand.dir / "command.json").read_text())["n"], 1)
        self.assertTrue(cand.check()["accepted"])
        self.assertIsNone(cand.sigma_arcsec)
        with self.assertRaises(RuntimeError):
            cand(WIN.now_s)
        self.assertIsNone(cand.later_oc(LINES[:8], self.ctx))         # nothing after its own arc
        later = cand.later_oc(LINES, self.ctx)                          # the other 8 obs, on a short table
        short = self.tmp / "s/cand_s_E0"
        self.assertTrue(short.exists() and not (self.tmp / "s/cand_E0").exists())
        self.assertTrue(0 < later < 60, later)
        n = json.loads((short / "command.json").read_text())["n"]
        self.assertLess(n, 1000)
        cand.table()
        self.assertEqual(cand.dir.name, "cand_E0")
        self.assertEqual(elements_lines(short), elements_lines(cand.dir))
        self.assertGreater(len(cand.t), 10000)
        self.assertLess(abs(cand.later_oc(LINES, self.ctx) - later), 10.0)   # the window table has drifted a little
        cand.sigma_table(3600.0)
        self.assertTrue(np.isfinite(cand.sigma(WIN.now_s)))


if __name__ == "__main__":
    unittest.main()
