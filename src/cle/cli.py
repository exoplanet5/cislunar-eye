"""cislunar-eye pipeline commands (ARCHITECTURE.md §13).

    states   [--only cid,..] [--adapter name] [--tier N] [--offline] [--refresh] [--fetch] [--jobs N]
             dispatch() per drawn target -> build/states/<cid>.npz/.json, build/states_report.json,
             build/moon_check.json, then build/neocp.json; a failing or missing adapter is recorded and the run
             continues. --fetch first pulls a fresh Space-Track gp/gp_history snapshot for the GP targets (a
             failed pull is recorded as report['gp_fetch'] and the cached sets are used). Every report entry and
             every saved table carries the window it was built in; export draws only from the current window.
             --jobs N (CLE_JOBS, default 8; REVIEW C2): gp / bgtle / extrap targets build in N spawned worker
             processes (one Context each), the find_orb targets (fo, neocp) in order in one more worker, Horizons
             targets in this process (one Snapshots: politeness, breaker, counters); tables are saved by the
             process that built them, the report only here. --jobs 1 builds everything here in catalog order.
             An entry's `seconds` is the target's own work; time queued on work/fo.lock behind another process's
             find_orb run is recorded apart as `lock_wait_s`, so the per-target seconds compare across --jobs.
    export   [--neocp-only]   build/states + Context -> dist/data/*.js
    validate                  -> build/validation_report.md, build/validation.json
    all      states (same options), export, validate

The window comes from $CLE_NOW (or the wall clock), floored to the hour, +/- 15 d.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

from . import catalog
from .timeutil import iso, window
from .validate import BUILD, STATES, load_json, write_json

SERIAL = ("fo", "neocp")          # find_orb lanes: one worker, in order (they serialise on work/fo.lock anyway)
_W = {}                           # a worker's window and Context (set once per process by _init)


def adapter_of(row) -> str:
    from .adapters import KIND_TO_MODULE, primary
    kind, _, stale = primary(row)
    return "extrap" if stale else KIND_TO_MODULE.get(kind, "")


def _init(stamp):
    from .adapters import fo
    from .context import Context
    fo.STAMP = stamp              # one work/cache/fo/<stamp>/ per states run, whichever process runs fo
    _W["win"], _W["ctx"] = window(), Context()


def build_one(row, this, win=None, ctx=None, states=None):
    """One target -> (cid, report entry); the table is saved here (atomically), an error is recorded, never raised."""
    from .adapters import dispatch, fo
    if win is None:
        win, ctx = _W["win"], _W["ctx"]
    name = adapter_of(row)
    item = {"adapter": name, "primary": row.get("primary_ephem", ""), "run_utc": iso(time.time()), "window": this}
    t, wait = time.time(), fo.lock_wait()
    try:
        mod = dispatch(row)
        if mod is None:
            raise LookupError(f"no adapter for {row.get('primary_ephem')!r}")
        table = mod.build(row, win, ctx)
        if table is None:
            item["status"] = "skipped"
        else:
            table.now_utc, table.t0_utc, table.t1_utc = iso(win.now_s), this[0], this[1]
            table.save(STATES if states is None else states)
            item |= {"status": "ok", "segments": len(table.segments), "samples": sum(s.n for s in table.segments)}
    except Exception as e:
        item |= {"status": "failed", "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
    wait = fo.lock_wait() - wait
    item["seconds"] = round(time.time() - t - wait, 1)      # own work, comparable between --jobs 1 and a pool run
    if wait >= 0.05:
        item["lock_wait_s"] = round(wait, 1)
    return row["cid"], item


def results(rows, this, win, ctx, jobs, prev=None):
    """(cid, item) per target as they finish. jobs == 1, or nothing to pool: every target here, in order.
    Otherwise the independent targets run in a pool of `jobs` spawned workers (longest previous build first),
    the find_orb targets in one more worker, Horizons targets here; a worker that dies (BrokenProcessPool) costs
    its target a rebuild here, never the run."""
    pool = [r for r in rows if adapter_of(r) not in SERIAL + ("horizons",)]
    if jobs <= 1 or not pool:
        for r in rows:
            yield build_one(r, this, win, ctx, STATES)
        return
    from .adapters import fo
    pool.sort(key=lambda r: -(prev or {}).get(r["cid"], {}).get("seconds", 0))
    kw = dict(mp_context=multiprocessing.get_context("spawn"), initializer=_init, initargs=(fo.STAMP,))
    with ProcessPoolExecutor(min(jobs, len(pool)), **kw) as ex, ProcessPoolExecutor(1, **kw) as ex1:
        futs = {ex.submit(build_one, r, this, states=STATES): r for r in pool}
        futs |= {ex1.submit(build_one, r, this, states=STATES): r for r in rows if adapter_of(r) in SERIAL}
        for r in rows:
            if adapter_of(r) == "horizons":
                yield build_one(r, this, win, ctx, STATES)
        for f in as_completed(futs):
            try:
                yield f.result()
            except Exception as e:
                print(f"  worker lost {futs[f]['cid']} ({type(e).__name__}: {e}); rebuilding here", flush=True)
                yield build_one(futs[f], this, win, ctx, STATES)


def states(args, win, ctx) -> dict:
    from .adapters import primary
    rows = [r for r in catalog.targets(tiers=tuple(range(1, args.tier + 1))) if r["draw"] == "Y"]
    if args.only:
        only = set(args.only.split(","))
        rows = [r for r in rows if r["cid"] in only]
    if args.adapter:
        rows = [r for r in rows if adapter_of(r) == args.adapter]
    jobs = max(1, int(getattr(args, "jobs", None) or 1))
    this = [iso(win.t0_s), iso(win.t1_s)]
    report = load_json(BUILD / "states_report.json", {})
    report.setdefault("targets", {})
    for v in report["targets"].values():          # every entry keeps the window it was built in (DW-02): export
        v.setdefault("window", report.get("window"))   # draws a target only from an entry of the current window
    report |= {"window": this, "now_utc": iso(win.now_s)}
    if getattr(args, "fetch", False):
        from .adapters import gp
        norads = [r["norad"] for r in rows if primary(r)[0] == "spacetrack_gp" and r.get("norad")]
        if norads:
            try:
                print(f"gp snapshot: {gp.fetch(norads + list(gp.CHECKS), win)}", flush=True)
                report["gp_fetch"] = {"status": "ok", "run_utc": iso(time.time())}
            except Exception as e:                # DW-04(a): a Space-Track outage keeps the cached sets
                err = f"{type(e).__name__}: {e} {(getattr(e, 'stderr', None) or '').strip()[-300:]}".strip()
                report["gp_fetch"] = {"status": "failed", "error": err, "run_utc": iso(time.time())}
                write_json(BUILD / "states_report.json", report)
                print(f"gp snapshot failed, continuing with the cached sets: {err}", flush=True)
    done, t_run = {"ok": 0, "failed": 0, "skipped": 0}, time.time()
    for i, (cid, item) in enumerate(results(rows, this, win, ctx, jobs, report["targets"]), 1):
        report["targets"][cid] = item
        done[item["status"] if item["status"] in done else "failed"] += 1
        print(f"[{i}/{len(rows)}] {cid:34s} {item['adapter']:9s} {item['status']:7s} {item['seconds']:6.1f}s "
              + (f"(+{item['lock_wait_s']:.0f}s on fo.lock) " if item.get("lock_wait_s") else "")
              + f"{item.get('samples', '') or item.get('error', '')[:90]}", flush=True)
        report["states_run"] = {"jobs": jobs, "targets": len(rows), "wall_s": round(time.time() - t_run, 1)}
        write_json(BUILD / "states_report.json", report)
    if any(v.get("adapter") == "horizons" and v.get("status") == "ok" and v.get("window") == this for v in report["targets"].values()):
        from .adapters import horizons
        try:
            write_json(BUILD / "moon_check.json", horizons.moon_check(win, ctx))
        except Exception as e:
            print(f"moon check failed: {type(e).__name__}: {e}", flush=True)
        print(f"horizons requests: {horizons.SNAPSHOTS.fetched} fetched, {horizons.SNAPSHOTS.reused} from cache")
    if rows:                                  # live NEOCP objects + tracklets of identified targets (§7, §11)
        from .adapters import neocp
        try:
            neocp.write_json(win, ctx)
            print(f"wrote {BUILD / 'neocp.json'}", flush=True)
        except Exception as e:
            report["neocp_json"] = {"status": "failed", "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
            write_json(BUILD / "states_report.json", report)
            print(f"neocp.json failed: {type(e).__name__}: {e}", flush=True)
    print(json.dumps(done))
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cle", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("states", "all"):
        p = sub.add_parser(name)
        p.add_argument("--only", help="comma-separated cle ids")
        p.add_argument("--adapter", help="only targets dispatched to this adapter module (horizons, gp, fo, ...)")
        p.add_argument("--tier", type=int, default=2, help="highest tier to build (default 2)")
        p.add_argument("--offline", action="store_true", help="never use the network (CLE_OFFLINE=1)")
        p.add_argument("--refresh", action="store_true", help="ignore cached raw responses (CLE_REFRESH=1)")
        p.add_argument("--fetch", action="store_true", help="pull a fresh Space-Track GP snapshot first (network)")
        p.add_argument("--jobs", type=int, default=int(os.environ.get("CLE_JOBS") or 8),
                       help="worker processes for independent targets (CLE_JOBS, default 8; 1 = sequential)")
    sub.add_parser("export").add_argument("--neocp-only", action="store_true")
    sub.add_parser("validate")
    args = ap.parse_args(argv)
    if getattr(args, "offline", False):
        os.environ["CLE_OFFLINE"] = "1"
    if getattr(args, "refresh", False):
        os.environ["CLE_REFRESH"] = "1"
    from .context import Context
    win, ctx = window(), Context()
    print(f"window {iso(win.t0_s)} .. {iso(win.t1_s)} (now {iso(win.now_s)})", flush=True)
    if args.cmd in ("states", "all"):
        states(args, win, ctx)
    if args.cmd in ("export", "all"):
        from . import export
        print(json.dumps(export.run(win, ctx, neocp_only=getattr(args, "neocp_only", False))))
    if args.cmd in ("validate", "all"):
        from . import validate
        rep = validate.run(win, ctx, catalog.targets())
        ok = sum(bool(r.get("segments")) for r in rep["targets"])
        print(f"validated {ok}/{len(rep['targets'])} targets -> {BUILD / 'validation_report.md'}")
    return 0
