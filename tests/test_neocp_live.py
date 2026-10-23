"""scripts/neocp_live.py --offline replayed on the cached NEOCP snapshots with NOW injected (DW-03, DW-06).

    ~/.venvs/astro313/bin/python -I -m unittest tests.test_neocp_live

Each replay copies work/cache/neocp (pages, obs, history) into a scratch cache, installs one summary / neocp.txt /
sat_id2 snapshot as the "current" download and runs the script as a subprocess with CLE_NOW set, exactly as the
Makefile does; no network. Skipped when the 2026-10-21 snapshots or ../neocpsat are missing (work/ is not versioned).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SCRIPT = ROOT / "scripts/neocp_live.py"
CACHE = ROOT / "work/cache/neocp"
SNAPS = CACHE / "snapshots"
SUMMARY, NEOCP, SATID = "20261021T053501Z_summary.htm", "20261021T070226Z_neocp.txt", "20261021T020212Z_sat_id2.htm"
NEOCP_EARLIER = "20261020T210333Z_neocp.txt"           # P22rrti still listed
HAVE = all((SNAPS / n).exists() for n in (SUMMARY, NEOCP, SATID, NEOCP_EARLIER)) and \
    (ROOT.parent / "neocpsat/scraper.py").exists() and (CACHE / "history.json").exists()
PREV_LIVE = ["6K72D21", "A11IDud", "A11IIEo", "C46PP91", "P22rmMn", "P22rrti", "TKO0003"]


def stamp(name):
    return datetime.strptime(name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


class Replay:
    """A scratch cache seeded from work/cache/neocp with the given snapshots as the current downloads."""

    def __init__(self, tmp, prev_parsed=None, neocp=NEOCP):
        self.cache = Path(tmp) / "cache"
        self.cache.mkdir()
        t = stamp(neocp)
        for sub in ("mpecs", "obs", "mpc_obs", "pluto_mpecs", "hz_check"):
            if (CACHE / sub).exists():
                shutil.copytree(CACHE / sub, self.cache / sub, ignore=shutil.ignore_patterns("history"))
        # the hourly job overwrites mpecs/<desig>.htm and grows history.json: pin each page to its newest version
        # created by the neocp.txt stamp (mpecs/history/<desig>/<created>.htm) and drop objects first seen later
        for d in sorted((CACHE / "mpecs/history").glob("*/")):
            versions = sorted(v for v in d.glob("*.htm") if stamp(v.name) <= t)
            page = self.cache / "mpecs" / f"{d.name}.htm"
            if versions:
                shutil.copy2(versions[-1], page)
            else:
                page.unlink(missing_ok=True)
        hist = json.loads((CACHE / "history.json").read_text())
        (self.cache / "history.json").write_text(json.dumps(
            {d: h for d, h in hist.items() if (h.get("first_seen") or "") <= t.strftime("%Y-%m-%dT%H:%M:%SZ")}))
        (self.cache / "parsed.json").write_text(json.dumps(prev_parsed if prev_parsed is not None
                                                           else {"stats": {}, "objects": {}}))
        self.install(neocp)
        shutil.copy2(SNAPS / SUMMARY, self.cache / "summary.htm")
        (self.cache / "satid").mkdir()
        shutil.copy2(SNAPS / SATID, self.cache / "satid/sat_id2.htm")
        self.tsv = Path(tmp) / "neocp.tsv"

    def install(self, neocp):
        shutil.copy2(SNAPS / neocp, self.cache / "neocp.txt")
        t = stamp(neocp).timestamp()
        os.utime(self.cache / "neocp.txt", (t, t))      # the script stamps the listing with this mtime

    def run(self, now):
        env = {k: v for k, v in os.environ.items() if k != "CLE_NOW"}
        if now:
            env["CLE_NOW"] = now
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--offline", "--cache", str(self.cache),
                            "--tsv", str(self.tsv)], cwd=ROOT, env=env, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(r.stderr[-2000:])
        parsed = json.loads((self.cache / "parsed.json").read_text())
        rows = [ln.split("\t") for ln in self.tsv.read_text().splitlines()]
        tsv = {r[1]: dict(zip(rows[0], r)) for r in rows[1:]}
        return parsed, rows[0], tsv


@unittest.skipUnless(HAVE, "2026-10-21 NEOCP snapshots / ../neocpsat not available")
class NowInjectionTest(unittest.TestCase):
    """DW-03: NOW comes from CLE_NOW (floored to the day); selection, covers_now and the sigma note follow it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.runs = {}
        for now in ("2026-10-21T13:00:00Z", "2026-10-28T13:00:00Z", "2026-11-10T13:00:00Z", "2026-12-15T13:00:00Z"):
            cls.runs[now] = Replay(tempfile.mkdtemp(dir=cls.tmp.name)).run(now)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_now_is_the_cle_now_day(self):
        for now, (parsed, _, _) in self.runs.items():
            self.assertEqual(parsed["stats"]["now"], now[:10] + "T00:00:00Z")

    def test_sticky_satid_current_survives_november(self):
        """Bill's 'Oct. ??.??' entries stay current for 60 d after 2026-10-01: ABC0069 = Tiandu-1 and A11Ix7l =
        Falcon Heavy S2 keep their satid_current selection at +7 d and +20 d (after 2026-10-31)."""
        for now in ("2026-10-28T13:00:00Z", "2026-11-10T13:00:00Z"):
            parsed, _, tsv = self.runs[now]
            self.assertEqual(parsed["objects"]["ABC0069"]["selection"], ["satid_current"], now)
            self.assertEqual(tsv["ABC0069"]["name"], "Tiandu-1")
            self.assertIn("satid_current", parsed["objects"]["A11Ix7l"]["selection"], now)
            self.assertEqual(tsv["A11Ix7l"]["name"], "Falcon Heavy S2")
            self.assertEqual(len(parsed["stats"]["satid_current"]), 5)
        # control: 75 d after 2026-10-01 the '??.??' entries are no longer current, the rows survive on
        # --keep-days only (regen stamp 2026-10-21 vs last_selected 2026-10-21: still within 7 d)
        parsed, _, _ = self.runs["2026-12-15T13:00:00Z"]
        self.assertEqual(parsed["stats"]["satid_current"], [])
        self.assertEqual(parsed["objects"]["ABC0069"]["selection"], [])

    def test_covers_now_relative_to_now(self):
        """8NIQ001's pseudo-MPEC table (2026-10-21..23) covers the reference day only on 2026-10-21."""
        day0, week, _ = (self.runs[k] for k in ("2026-10-21T13:00:00Z", "2026-10-28T13:00:00Z", "2026-11-10T13:00:00Z"))
        self.assertEqual(day0[2]["8NIQ001"]["covers_now"], "Y")
        self.assertEqual(week[2]["8NIQ001"]["covers_now"], "?")
        # the row nearest NOW = 2026-10-21T00:00Z is the first hour of whichever table the cache holds (00:00 or 01:00)
        self.assertRegex(day0[2]["TKO0003"]["notes"], r"MPEC eph row nearest NOW 2026-10-21T0[0-2]:00:00Z")
        self.assertIn("covers 2026-10-21 but 1-sigma", day0[2]["TKO0003"]["notes"])
        self.assertNotIn("nearest NOW", week[2]["TKO0003"]["notes"])
        self.assertLess(week[0]["stats"]["rows_covers_now"], day0[0]["stats"]["rows_covers_now"])
        self.assertEqual(self.runs["2026-11-10T13:00:00Z"][0]["stats"]["rows_covers_now"], 0)

    def test_lifecycle_columns(self):
        parsed, header, tsv = self.runs["2026-10-21T13:00:00Z"]
        self.assertEqual(header[-2:], ["listed_now", "last_listed"])
        self.assertEqual(header[:-2][-1], "notes")
        self.assertEqual({d: r["listed_now"] for d, r in tsv.items()},
                         {"8NIQ001": "Y", "A11IIEI": "Y", "ZTF10Go": "Y", "ZTF10Gu": "Y", "A11IDud": "Y", "C46PP91": "Y",
                          "CEW2VG2": "Y", "TKO0003": "N", "ABC0069": "N", "A11Ix7l": "N"})
        self.assertEqual(tsv["8NIQ001"]["last_listed"], "2026-10-21T05:35:01Z")      # summary regeneration stamp
        self.assertEqual(tsv["TKO0003"]["last_listed"], "2026-10-20T21:03:33Z")
        self.assertIn("not in current NEOCP listings (last listed 2026-10-20T21:03:33Z", tsv["TKO0003"]["notes"])
        self.assertEqual(tsv["ABC0069"]["last_listed"], "")
        self.assertEqual(parsed["stats"]["rows_listed_now"], 7)


@unittest.skipUnless(HAVE, "2026-10-21 NEOCP snapshots / ../neocpsat not available")
class RemovedTest(unittest.TestCase):
    """DW-06: removed = live in the previous parsed.json and absent from both listings now."""

    def test_membership(self):
        prev = {"stats": {}, "objects": {d: {"live": True} for d in PREV_LIVE},
                "removed": {"OLD0001": {"last_listed": "2026-10-01T05:35:01Z", "removed_utc": "2026-10-01T07:00:00Z"}}}
        with tempfile.TemporaryDirectory() as tmp:
            rp = Replay(tmp, prev)
            parsed, _, _ = rp.run("2026-10-21T13:00:00Z")
            # 6K72D21, A11IDud, A11IIEo, C46PP91, P22rmMn are in the 2026-10-21 listings; only P22rrti and TKO0003 left
            self.assertEqual(sorted(parsed["removed"]), ["P22rrti", "TKO0003"])
            self.assertEqual(parsed["removed"]["P22rrti"],
                             {"last_listed": "2026-10-20T21:03:33Z", "removed_utc": "2026-10-21T07:02:26Z"})
            self.assertNotIn("OLD0001", parsed["removed"])                      # older than --keep-days
            # a second run on the same listings keeps the record; the 21:03:33Z neocp.txt, in which both were
            # last listed, drops them again (listed again)
            again, _, _ = rp.run("2026-10-21T13:00:00Z")
            self.assertEqual(again["removed"], parsed["removed"])
            rp.install(NEOCP_EARLIER)
            back, _, tsv = rp.run("2026-10-21T13:00:00Z")
            self.assertEqual(back["removed"], {})
            self.assertEqual(tsv["TKO0003"]["listed_now"], "Y")

    def test_seeded_from_history_when_previous_parsed_json_has_no_removed(self):
        """Today's cached data through a pre-fix parsed.json: history.json last_listed gives the same two."""
        with tempfile.TemporaryDirectory() as tmp:
            parsed, _, tsv = Replay(tmp, {"stats": {}, "objects": {}}).run("2026-10-21T13:00:00Z")
            self.assertEqual(sorted(parsed["removed"]), ["P22rrti", "TKO0003"])
            self.assertEqual(tsv["TKO0003"]["listed_now"], "N")

    def test_adapter_removed_list(self):
        from cle.adapters import neocp
        parsed = {"removed": {"P22rrti": {"last_listed": "2026-10-20T21:03:33Z", "removed_utc": "2026-10-21T07:02:26Z"},
                              "TKO0003": {"last_listed": "2026-10-20T21:03:33Z", "removed_utc": "2026-10-21T07:02:26Z"}}}
        out = neocp.removed_list(parsed, {"TKO0003": {}, "8NIQ001": {}})        # TKO0003 is still a live object
        self.assertEqual(out, [{"desig": "P22rrti", "removed_utc": "2026-10-21T07:02:26Z",
                                "last_listed": "2026-10-20T21:03:33Z", "reason": "no longer on the NEOCP"}])
        self.assertEqual(neocp.removed_list({"stats": {}, "objects": {}}, {}), [])


def node_hooks():
    """node >= 23.5 with module.registerHooks (to map the page's bare 'three' import to web/vendor as build_site.mjs does)."""
    node = shutil.which("node")
    return node and subprocess.run([node, "-e", "process.exit(typeof require('node:module').registerHooks === 'function' ? 0 : 1)"],
                                   capture_output=True).returncode == 0


class ListBadgeTest(unittest.TestCase):
    """web/ui/list.js renders "left NEOCP <date>" from a catalog entry's left_neocp (ISO date) and nothing without it."""

    @unittest.skipUnless(node_hooks(), "node with module.registerHooks not available")
    def test_badge(self):
        hooks = """
            import { registerHooks } from 'node:module';
            import { pathToFileURL } from 'node:url';
            registerHooks({ resolve(spec, ctx, next) {
              if (spec === 'three') return { url: pathToFileURL('%s/web/vendor/three/three.module.min.js').href, shortCircuit: true };
              return next(spec, ctx); } });
        """ % ROOT
        js = """
            import { TargetList } from '%s/web/ui/list.js';
            const T = (id, cat) => ({ id, cat, label: id, tier: 1, natural: false, group: 'NEOCP live', regime: { order: 1 },
                                      visible: true, draw: true, css: '#fff', seg: null });
            const targets = [T('a11idud', { name: 'Multijunk', left_neocp: '2026-10-20T21:03:33Z' }), T('x', { name: 'x' })];
            const el = { addEventListener() {}, querySelectorAll() { return []; }, innerHTML: '' };
            const w = { targets, byId: new Map(targets.map(t => [t.id, t])), groupOrder: new Map(), t: 0 };
            new TargetList(el, w, { chipOn: () => true, selected: null, loaded: true }).render();
            console.log(el.innerHTML);
        """ % ROOT
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "hooks.mjs").write_text(hooks)
            r = subprocess.run(["node", "--import", str(Path(tmp) / "hooks.mjs"), "--input-type=module", "-e", js],
                               capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr[-1000:])
        html = r.stdout
        self.assertIn('<span class="tier left" title="no longer in the NEOCP listings (last listed 2026-10-20)">left NEOCP 2026-10-20</span>', html)
        self.assertEqual(html.count("left NEOCP"), 1)
        self.assertIn("left the NEOCP 2026-10-20", html)                        # row tooltip


if __name__ == "__main__":
    unittest.main()
