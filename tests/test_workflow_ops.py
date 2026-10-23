"""Workflow and ops rules (REVIEW DW-01, DW-02, DW-04a, DW-13, C7): one window per make, the states_report / export
window guards, `export --neocp-only` leaving the manifest alone, a failed Space-Track pull recorded and survived,
ops/run.sh locking, logging and window pinning, scripts/status.py exit codes.

    ~/.venvs/astro313/bin/python -I -m unittest discover -s tests -p 'test_*.py'
"""
import contextlib
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
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cle import catalog, cli, export  # noqa: E402
from cle.statetable import Segment, StateTable  # noqa: E402
from cle.timeutil import iso, parse_iso, window  # noqa: E402

NOW = "2026-10-21T13:00:00Z"
WIN, OTHER = window(NOW), window("2026-10-22T13:00:00Z")
NOT_RUN = "not drawn (adapter not run in this window)"


def make_n(target, env_now):
    """`make -n <target>` with CLE_NOW unset (None), empty or set; returns (output, echoed CLE_NOW values)."""
    env = {k: v for k, v in os.environ.items() if k != "CLE_NOW"}
    if env_now is not None:
        env["CLE_NOW"] = env_now
    out = subprocess.run(["make", "-n", target], cwd=ROOT, env=env, capture_output=True, text=True, check=True).stdout
    return out, re.findall(r'CLE_NOW=(\S*)"', out)


def table(cid, win=None):
    t = WIN.t0_s + 3600.0 * np.arange(6)
    r = np.column_stack([7000 + 0 * t, 0 * t, 0 * t])
    st = StateTable(cid, "earth", [Segment(float(t[0]), 3600.0, r, np.column_stack([0 * t, 7.5 + 0 * t, 0 * t]), "x:1", "ref")])
    if win is not None:
        st.now_utc, st.t0_utc, st.t1_utc = iso(win.now_s), iso(win.t0_s), iso(win.t1_s)
    return st


class MakeWindowTest(unittest.TestCase):
    def test_one_window_per_make(self):
        """DW-01: every target of `make daily` echoes the same CLE_NOW; the daily runs in the §13 order (C7 at the end)."""
        out, found = make_n("daily", NOW)
        self.assertGreaterEqual(len(found), 12)
        self.assertEqual(set(found), {NOW})
        self.assertIn("build_master.py --now 2026-10-21", out)
        steps = ["neocp_live.py", "extract_all.py", "validate_source_tsv.py", "build_master.py", "git diff --stat -- catalog/",
                 "states --adapter horizons", "states --adapter gp --fetch", "tles log -1", "states --adapter fo", "states --offline",
                 "cle.py export", "cle.py validate", "build_site.mjs", "unittest discover", "sanity_check.py", "browser.mjs --dist"]
        at = [out.index(s) for s in steps]
        self.assertEqual(at, sorted(at), "daily order: " + " < ".join(steps))
        self.assertNotIn("horizons_all_spacecraft", out)          # the validator runs over build_master's SOURCES only

    def test_unset_or_empty_now_is_floored_once(self):
        """Unset or empty CLE_NOW (the old `export CLE_NOW` bug) becomes one wall-clock hour for the whole make."""
        for env_now in (None, ""):
            out, found = make_n("daily", env_now)
            self.assertRegex(found[0], r"^\d{4}-\d\d-\d\dT\d\d:00:00Z$")
            self.assertEqual(len(set(found)), 1, out)
            self.assertNotIn("CLE_NOW=\"", out)

    def test_wiring(self):
        out, _ = make_n("sweep", NOW)
        self.assertIn(f"horizons_sweep.py --now {NOW} --in-scope-only", out)
        out, _ = make_n("update-neocp", NOW)
        self.assertIn("export --neocp-only", out)
        self.assertNotRegex(out, r"cle\.py export\s*$")
        out, _ = make_n("update-fits", NOW)
        self.assertIn("fits_changed.py", out)
        self.assertIn("states --adapter fo", out)
        out, _ = make_n("tles-status", NOW)
        self.assertIn("git -C", out)


class NotDrawnTest(unittest.TestCase):
    def test_missing_entry_or_other_window_is_not_drawn(self):
        """DW-02: states on disk are drawn only from a report entry of the current window."""
        with tempfile.TemporaryDirectory() as d, mock.patch.object(export, "STATES", Path(d)):
            (Path(d) / "x.json").write_text("{}")
            row, this, other = {"cid": "x"}, export.window_of(WIN), export.window_of(OTHER)
            self.assertEqual(export.not_drawn(row, {}), NOT_RUN)                                  # the review's hole
            self.assertEqual(export.not_drawn(row, {}, WIN), NOT_RUN)
            self.assertEqual(export.not_drawn(row, {"status": "ok", "window": other}, WIN), NOT_RUN)
            self.assertEqual(export.not_drawn(row, {"status": "ok", "window": this}, WIN), "")
            self.assertEqual(export.not_drawn(row, {"status": "ok"}, WIN), "")                    # legacy entry, no window
            self.assertIn("build failed", export.not_drawn(row, {"status": "failed", "error": "E: x: boom", "window": this}, WIN))
            self.assertIn("no states built", export.not_drawn({"cid": "y"}, {}, WIN))

    def test_states_window_stamp(self):
        self.assertEqual(export.other_window(table("a", WIN), WIN), "")
        self.assertEqual(export.other_window(table("a"), WIN), "")                                  # unstamped (older builds)
        self.assertEqual(export.other_window(table("a", OTHER), WIN), "not drawn (states built for now 2026-10-22T13:00:00Z, not this window)")
        with tempfile.TemporaryDirectory() as d:
            table("a", WIN).save(Path(d))
            back = StateTable.load(Path(d), "a")
            self.assertEqual((back.now_utc, back.t0_utc, back.t1_utc), (NOW, "2026-10-06T13:00:00Z", "2026-11-05T13:00:00Z"))
            meta = json.loads((Path(d) / "a.json").read_text())
            for k in ("now_utc", "t0_utc", "t1_utc"):
                del meta[k]
            (Path(d) / "a.json").write_text(json.dumps(meta))
            self.assertEqual(StateTable.load(Path(d), "a").now_utc, "")


class ExportWindowTest(unittest.TestCase):
    def test_export_draws_only_the_current_window(self):
        rows = [r for r in catalog.targets() if r["draw"] == "Y"][:3]
        a, b, c = (r["cid"] for r in rows)
        this, other = export.window_of(WIN), export.window_of(OTHER)
        with tempfile.TemporaryDirectory() as d:
            build, dist, states = Path(d) / "build", Path(d) / "dist", Path(d) / "build/states"
            table(a, WIN).save(states)
            table(b, OTHER).save(states)
            table(c, WIN).save(states)
            build.joinpath("states_report.json").write_text(json.dumps({"window": this, "now_utc": NOW, "targets": {
                a: {"status": "ok"}, b: {"status": "ok", "window": this}, c: {"status": "ok", "window": other}}}))
            with mock.patch.object(export, "STATES", states), mock.patch.object(export, "BUILD", build), \
                    mock.patch.object(export, "DIST", dist), mock.patch.object(export, "core", lambda win, ctx: {"schema": "cle/core@1"}), \
                    mock.patch.object(export, "regime_now", lambda *x: {"regime_now": "", "check": None, "pass": None}), \
                    mock.patch.object(export.catalog, "targets", lambda *x, **k: rows):
                res = export.run(WIN, None)
            got = {e["id"]: e for e in export.read_js(dist / "catalog.js")["targets"]}
        self.assertEqual(res["drawn"], 1)
        self.assertTrue(got[a]["drawn"] and "not_drawn_reason" not in got[a])
        self.assertEqual(got[b]["not_drawn_reason"], "not drawn (states built for now 2026-10-22T13:00:00Z, not this window)")
        self.assertEqual(got[c]["not_drawn_reason"], NOT_RUN)
        self.assertEqual(len(res["skipped"]), 1)                      # b was loaded and refused; c never loaded
        self.assertIn(b, res["skipped"][0])

    def test_neocp_only_touches_only_the_neocp_entry(self):
        """DW-01 / §13: `export --neocp-only` rewrites neocp.js and the manifest's neocp entry, nothing else."""
        with tempfile.TemporaryDirectory() as d:
            build, dist = Path(d) / "build", Path(d) / "dist"
            build.mkdir()
            build.joinpath("neocp.json").write_text('{"neocp_snapshot_utc": "2026-10-21T07:02:26Z", "objects": {}}')
            export.write_js(dist / "a.js", "a", {"x": 1})
            old = {"schema": "cle/manifest@1", "built_utc": "2026-01-01T00:00:00Z", "now_utc": "2026-01-01T00:00:00Z",
                   "files": {"a.js": {"bytes": 1, "sha256": "keep"}, "neocp.js": {"bytes": 2, "sha256": "stale"}, "gone.js": {"bytes": 3, "sha256": "g"}}}
            export.write_js(dist / "manifest.js", "manifest", old)
            a_before = (dist / "a.js").read_text()
            with mock.patch.object(export, "BUILD", build), mock.patch.object(export, "DIST", dist):
                res = export.run(WIN, None, neocp_only=True)
                new = export.read_js(dist / "manifest.js")
                self.assertEqual(res, {"files": ["neocp.js"]})
                self.assertEqual({k: v for k, v in new.items() if k != "files"}, {k: v for k, v in old.items() if k != "files"})
                self.assertEqual(new["files"]["a.js"], old["files"]["a.js"])
                self.assertEqual(new["files"]["gone.js"], old["files"]["gone.js"])
                self.assertEqual(new["files"]["neocp.js"], {"bytes": (dist / "neocp.js").stat().st_size, "sha256": export.hashlib.sha256((dist / "neocp.js").read_bytes()).hexdigest()})
                self.assertEqual((dist / "a.js").read_text(), a_before)
                self.assertEqual(sorted(p.name for p in dist.iterdir()), ["a.js", "manifest.js", "neocp.js"])
                (dist / "manifest.js").unlink()                       # no manifest yet: a full one is written
                export.run(WIN, None, neocp_only=True)
                self.assertEqual(sorted(export.read_js(dist / "manifest.js")["files"]), ["a.js", "neocp.js"])


class StatesReportTest(unittest.TestCase):
    """cli.states with a fake adapter: no network, no find_orb, nothing written outside a temp dir."""

    def run_states(self, d, row, args, report, fetch=None):
        build = Path(d) / "build"
        build.mkdir(exist_ok=True)
        build.joinpath("states_report.json").write_text(json.dumps(report))
        fake = SimpleNamespace(build=lambda r, win, ctx: table(r["cid"]))
        with mock.patch.object(cli, "BUILD", build), mock.patch.object(cli, "STATES", build / "states"), \
                mock.patch.object(cli.catalog, "targets", lambda *x, **k: [row]), mock.patch("cle.adapters.dispatch", lambda r: fake), \
                mock.patch("cle.adapters.neocp.write_json"), mock.patch("cle.adapters.gp.fetch", side_effect=fetch or (lambda *x: "ok")), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.states(args, WIN, None)
        return json.loads(build.joinpath("states_report.json").read_text()), build / "states"

    def test_other_windows_entries_are_kept_with_their_window(self):
        """DW-02: a run in a new window keeps the other adapters' entries, stamped with the window they were built in."""
        row = {"cid": "fake-bg", "draw": "Y", "primary_ephem": "bgtle:x.tle[2026-01-01..2026-12-31]", "norad": ""}
        args = SimpleNamespace(only=None, adapter=None, tier=2, fetch=False)
        this, other = export.window_of(WIN), export.window_of(OTHER)
        with tempfile.TemporaryDirectory() as d:
            rep, states = self.run_states(d, row, args, {"window": other, "now_utc": iso(OTHER.now_s), "targets": {
                "old-ok": {"status": "ok", "adapter": "gp"}, "old-failed": {"status": "failed", "error": "E: x"}}})
            self.assertEqual((rep["window"], rep["now_utc"]), (this, NOW))
            self.assertEqual(sorted(rep["targets"]), ["fake-bg", "old-failed", "old-ok"])
            self.assertEqual((rep["targets"]["old-ok"]["status"], rep["targets"]["old-ok"]["window"]), ("ok", other))
            self.assertEqual((rep["targets"]["old-failed"]["status"], rep["targets"]["old-failed"]["window"]), ("failed", other))
            self.assertEqual((rep["targets"]["fake-bg"]["status"], rep["targets"]["fake-bg"]["window"]), ("ok", this))
            self.assertEqual(StateTable.load(states, "fake-bg").now_utc, NOW)
            (states / "old-ok.json").write_text("{}")
            with mock.patch.object(export, "STATES", states):
                self.assertEqual(export.not_drawn({"cid": "old-ok"}, rep["targets"]["old-ok"], WIN), NOT_RUN)
                self.assertEqual(export.not_drawn({"cid": "fake-bg"}, rep["targets"]["fake-bg"], WIN), "")

    def test_failed_gp_fetch_is_recorded_and_the_run_continues(self):
        """DW-04(a): a Space-Track outage (CalledProcessError from the skill CLI) does not abort update-gp."""
        row = {"cid": "fake-gp", "draw": "Y", "primary_ephem": "spacetrack_gp:12345[2026-10-01..2026-10-20]", "norad": "12345"}
        args = SimpleNamespace(only=None, adapter=None, tier=2, fetch=True)
        boom = subprocess.CalledProcessError(1, ["st.py", "raw"], stderr="login failed: 401\n")
        with tempfile.TemporaryDirectory() as d:
            rep, states = self.run_states(d, row, args, {}, fetch=boom)
            self.assertEqual(rep["gp_fetch"]["status"], "failed")
            self.assertIn("CalledProcessError", rep["gp_fetch"]["error"])
            self.assertIn("login failed: 401", rep["gp_fetch"]["error"])
            self.assertEqual(rep["targets"]["fake-gp"]["status"], "ok")
            self.assertTrue((states / "fake-gp.json").exists())
            rep, _ = self.run_states(d, row, args, {})
            self.assertEqual(rep["gp_fetch"]["status"], "ok")


class RunShTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_sh(self, *args, **env_extra):
        env = {k: v for k, v in os.environ.items() if k != "CLE_NOW"} | {"CLE_WORK": str(self.work)} | env_extra
        return subprocess.run([str(ROOT / "ops/run.sh"), *args], cwd=ROOT, env=env, capture_output=True, text=True)

    def log(self, target):
        logs = sorted((self.work / "logs").glob(f"{target}-*.log"))
        self.assertEqual(len(logs), 1, logs)
        self.assertRegex(logs[0].name, rf"^{target}-\d{{4}}-\d\d-\d\d\.log$")
        return logs[0].read_text()

    def test_lock_log_and_pinned_window(self):
        r = self.run_sh("now", CLE_NOW=NOW)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.log("now")
        self.assertIn(f"CLE_NOW={NOW}", text)
        self.assertIn("now: ok rc=0", text)
        self.assertFalse((self.work / "make.lock").exists())
        lock = self.work / "make.lock"
        lock.mkdir()
        (lock / "pid").write_text(str(os.getpid()))                   # a live run holds the lock
        r = self.run_sh("now", CLE_NOW=NOW)
        self.assertEqual(r.returncode, 75)
        self.assertIn("skipped", self.log("now"))
        self.assertTrue(lock.exists())
        (lock / "pid").write_text("2147483646")                       # dead pid: stale, removed
        r = self.run_sh("now", CLE_NOW=NOW)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("removing stale lock", self.log("now"))
        self.assertFalse(lock.exists())
        lock.mkdir()
        (lock / "pid").write_text(str(os.getpid()))
        r = self.run_sh("now", CLE_NOW=NOW, CLE_LOCK_STALE_S="0")      # live pid but older than the limit
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(lock.exists())

    def test_failing_step_is_named(self):
        r = self.run_sh("test-browser", "NODE=false", CLE_NOW=NOW)
        self.assertNotEqual(r.returncode, 0)
        text = self.log("test-browser")
        self.assertRegex(text, r"test-browser: FAILED rc=\d+ at step test-browser")

    def test_update_neocp_uses_the_daily_window(self):
        rep = export.load_json(ROOT / "build/states_report.json", None)
        if not rep or not rep.get("window"):
            self.skipTest("no build/states_report.json (make states)")
        r = self.run_sh("update-neocp", "-n")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.log("update-neocp")
        t0, t1 = parse_iso(rep["window"][0]), parse_iso(rep["window"][1])
        if t0 <= time.time() <= t1:
            self.assertIn(f"CLE_NOW={rep['now_utc']} pinned", text)
            self.assertIn(f"== update-neocp  CLE_NOW={rep['now_utc']}", text)
        else:
            self.assertNotIn("pinned", text)
        self.assertIn("export --neocp-only", text)


class StatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("status", ROOT / "scripts/status.py")
        cls.status = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.status)

    def run_status(self, root, now_s):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.status.main(["--root", str(root), "--now", iso(now_s)])
        return rc, out.getvalue()

    def test_exit_codes(self):
        now_s = time.time()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rc, out = self.run_status(root, now_s)
            self.assertEqual(rc, 1)
            self.assertIn("NEOCP snapshot missing", out)
            (root / "work/cache/neocp").mkdir(parents=True)
            (root / "work/cache/neocp/parsed.json").write_text(json.dumps({"stats": {"neocp_txt_mtime": iso(now_s - 3600)}}))
            (root / "dist/data").mkdir(parents=True)
            (root / "dist/data/manifest.js").write_text(f'CLE.register("manifest", {{"built_utc":"{iso(now_s - 7200)}","files":{{}}}});\n')
            rc, out = self.run_status(root, now_s)
            self.assertEqual(rc, 0, out)
            self.assertIn("status: ok", out)
            self.assertRegex(out, r"neocp snapshot \(neocp\.txt\)\s+\S+\s+1\.0 h")
            (root / "work/logs").mkdir()
            log = root / "work/logs/daily-2026-10-22.log"
            log.write_text("2026-10-22T01:30:00Z daily: start\n2026-10-22T02:00:00Z daily: FAILED rc=2 at step catalog in 1800 s\n")
            rc, out = self.run_status(root, now_s)
            self.assertEqual(rc, 1)
            self.assertIn("last daily failed", out)
            log.write_text("2026-10-22T01:30:00Z daily: start\n2026-10-22T02:00:00Z daily: ok rc=0 in 1800 s\n")
            self.assertEqual(self.run_status(root, now_s)[0], 0)
            rc, out = self.run_status(root, now_s + 37 * 3600)
            self.assertEqual(rc, 1)
            self.assertIn("last daily 37.0 h old", out)
            (root / "work/cache/neocp/parsed.json").write_text(json.dumps({"stats": {"neocp_txt_mtime": iso(now_s - 4 * 3600)}}))
            rc, out = self.run_status(root, now_s)
            self.assertEqual(rc, 1)
            self.assertIn("NEOCP snapshot 4.0 h old", out)


class IntegrationFixesTest(unittest.TestCase):
    """Cross-owner fixes applied at integration: ephem_end/ephem_start at exactly one step (a Horizons fallback track
    on a moved window ends one step short), the validation report's fallback line and other-window status suffix,
    neocp_live.cached_fetch keeping the cached copy when the request raises."""

    def seg(self, t0, n, step=3600.0):
        t = t0 + step * np.arange(n)
        r = np.c_[1e5 + t / 1e3, np.zeros(n), np.zeros(n)]
        return Segment(t0, step, r, np.full((n, 3), 1e-3), "horizons:-170", "ref")

    def test_ephem_events_at_exactly_one_step(self):
        n = int((WIN.t1_s - WIN.t0_s) / 3600) + 1
        self.assertEqual([e["kind"] for e in export.events([self.seg(WIN.t0_s, n)], WIN)], [])
        short = export.events([self.seg(WIN.t0_s, n - 1)], WIN)              # ends one step before the window end
        self.assertEqual([e["kind"] for e in short], ["ephem_end"])
        self.assertEqual(short[0]["t_utc"], iso(WIN.t1_s - 3600))
        late = export.events([self.seg(WIN.t0_s + 3600, n - 1)], WIN)        # starts one step after the window start
        self.assertEqual([e["kind"] for e in late], ["ephem_start"])
        self.assertEqual([e["kind"] for e in export.events([self.seg(WIN.t0_s, n - 3)], WIN)], ["ephem_end"])

    def test_validation_report_shows_fallback_and_other_window(self):
        from cle import validate
        base = {"tier": 1, "regime": "SE_L2", "draw": "Y", "adapter": "horizons", "status": "ok", "segments": 1, "samples": 10,
                "quality": ["ref"], "start_utc": NOW, "end_utc": NOW, "covered_frac": 1.0, "covers_now": True,
                "midpoint_max_km": 0.1, "midpoint_method": "x", "self_est_km": 0.0, "r_geo_min_km": 1.0, "r_geo_max_km": 2.0}
        rep = {"built_utc": NOW, "now_utc": NOW, "window": export.window_of(WIN), "states_window": export.window_of(WIN),
               "targets": [base | {"cid": "jwst", "fallback": {"snapshot_utc": "2026-10-21", "regrid": True}, "midpoint_unchecked": 3,
                                   "unrepaired": 1},
                           base | {"cid": "tess", "status": "ok (built for window 2026-10-06T13..2026-11-05T13, not drawn)"},
                           base | {"cid": "lro"}]}
        md = validate.markdown(rep)
        self.assertIn("Horizons offline fallback (cached snapshot, caveat on the track): jwst (snapshot 2026-10-21, re-gridded, "
                      "3 midpoints unchecked, 1 pieces unrepaired).", md)
        self.assertIn("| tess | 1 | horizons | ok (built for window 2026-10-06T13..2026-11-05T13, not drawn) |", md)
        self.assertNotIn("lro (snapshot", md)
        rep["targets"] = [base | {"cid": "lro"}]
        self.assertNotIn("offline fallback", validate.markdown(rep))

    def test_string_fit_fallback_is_not_the_horizons_fallback(self):
        """The NEOCP adapter stores fallback="page_table" (§11); only the Horizons dict is the offline fallback.
        Regression: the first real `make daily` (2026-10-22) crashed in validate.markdown on TKO0003's string."""
        from cle import validate
        from cle.context import Context
        from cle.statetable import StateTable
        n = int((WIN.t1_s - WIN.t0_s) / 3600) + 1
        table = StateTable("tko0003", "earth", [self.seg(WIN.t0_s, n)], validation={"fallback": "page_table"})
        row = {"cid": "tko0003", "regime": "NEOCP_GEO", "staleness": {"flag": None}}
        out = validate.summary(table, row, Context(), WIN)
        self.assertIsNone(out["fallback"])
        self.assertEqual(out["fit_fallback"], "page_table")
        rep = {"built_utc": NOW, "now_utc": NOW, "window": export.window_of(WIN), "states_window": export.window_of(WIN),
               "targets": [out | {"cid": "tko0003", "tier": 1, "draw": "Y", "adapter": "neocp", "status": "ok"}]}
        self.assertNotIn("offline fallback", validate.markdown(rep))

    def test_neocp_live_cached_fetch_survives_a_failed_request(self):
        spec = importlib.util.spec_from_file_location("neocp_live", ROOT / "scripts/neocp_live.py")
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except ImportError as e:                                    # ../neocpsat or requests missing
            self.skipTest(str(e))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "neocp.txt")
            with mock.patch.object(mod, "http_get", side_effect=mod.requests.ConnectionError("reset")), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(mod.cached_fetch("https://x/neocp.txt", path, 0, False), (None, False))
                Path(path).write_bytes(b"cached")
                os.utime(path, (time.time() - 7200, time.time() - 7200))
                self.assertEqual(mod.cached_fetch("https://x/neocp.txt", path, 3600, False), (b"cached", False))
            self.assertIn("reset", err.getvalue())
            self.assertEqual(Path(path).read_bytes(), b"cached")
            self.assertFalse(Path(path + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
