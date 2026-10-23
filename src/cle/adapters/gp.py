"""Space-Track GP adapter (ARCHITECTURE.md §5.7, AM6).

Element sets come from every cached Space-Track reply: work/cache/gp/<stamp>/{gp_history,gp}*.json (fresh pulls,
`--fetch`), work/cache/rocketdebris/spacetrack/gp.json and work/cache/spacetrack/gp_*.json. Placeholder sets
(P < 1 d for these deep-space objects) are dropped. At t the latest epoch <= t is used (the first one before it);
skyfield EarthSatellite gives SGP4/SDP4 TEME rotated to GCRS. The track breaks at every element-set switch
(join 'splice', jump_km measured at the shared epoch); quality=gp. Earth–Moon CR3BP regimes (EM_*) use only the
state at the newest GP epoch and integrate it with cle.nbody (AM6); the lunar distance of that track is checked
against the target's newest Bill Gray TLE extrapolated the same way, and an EM_DRO track that comes within
30 000 km of the Moon is flagged in its caveat.

    python -I src/cle/adapters/gp.py [--fetch] <cid> ...      build (and save) targets
    python -I src/cle/adapters/gp.py --check                 GP vs Horizons (Titan-3C RB, XMM-Newton, Vela-2A, IMP-7, TESS)
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cle import nbody
from cle.adapters import primary
from cle.statetable import StateTable
from cle.timeutil import iso, parse_iso

ROOT = nbody.ROOT
ST = Path.home() / ".claude/skills/space-track/st.py"
PY = Path.home() / ".venvs/astro313/bin/python"
CACHE = ROOT / "work/cache/gp"
JUMP_WARN = 0.05            # a set switch moving the object by > 5 % of its distance is flagged in the caveat
ARC_SPAN_D = 25             # CR3BP targets: GP sets fitted together (arc_fit); DRO-A's Sep 22 + Oct 12 sets agree to
                            # 6,211 km rms, while spans reaching June/July do not (manoeuvres)
CHECKS = {2770: -102770, 25989: -125989, 836: -100836, 6197: -106197, 43435: -95}


class GPFetchError(RuntimeError):
    """The space-track skill CLI failed (login, network, HTTP, timeout): the cached GP sets stay in use (DW-04)."""


def fetch(norads, win, pad_days=30):
    """Fresh gp_history over [window start - pad, window end] plus current gp, via the space-track skill CLI.
    Any CLI failure raises GPFetchError (stderr tail logged); a reply that completed before it is kept."""
    folder = CACHE / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=True)
    ids = ",".join(str(n) for n in sorted(set(map(int, norads))))
    a, b = iso(win.t0_s - pad_days * 86400)[:10], iso(win.t1_s + 86400)[:10]
    queries = {"gp_history.json": f"class/gp_history/NORAD_CAT_ID/{ids}/EPOCH/{a}--{b}/orderby/EPOCH asc",
               "gp.json": f"class/gp/NORAD_CAT_ID/{ids}"}
    manifest = {"retrieved_utc": iso(datetime.now(timezone.utc).timestamp()), "tool": str(ST), "requests": {}}
    for name, query in queries.items():
        try:
            subprocess.run([str(PY), str(ST), "raw", query, "--json", str(folder / name)], check=True,
                           capture_output=True, text=True, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            err = getattr(e, "stderr", None) or ""
            tail = " | ".join((err.decode(errors="replace") if isinstance(err, bytes) else err).strip().splitlines()[-3:])
            (folder / name).unlink(missing_ok=True)                   # never leave a partial reply for records()
            manifest["error"] = {"request": name, "query": query, "error": f"{type(e).__name__}: {tail or e}"}
            if manifest["requests"]:
                (folder / "manifest.json").write_text(json.dumps(manifest, indent=1))
            elif not any(folder.iterdir()):
                folder.rmdir()
            print(f"  space-track {name} failed: {type(e).__name__}: {tail or e}", flush=True)
            raise GPFetchError(f"Space-Track fetch failed at {name} ({type(e).__name__}): {tail or e}") from e
        manifest["requests"][name] = {"query": query, "sha256": nbody.digest(folder / name)}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return folder


def sources():
    files = sorted(CACHE.glob("*/gp*.json")) + [ROOT / "work/cache/rocketdebris/spacetrack/gp.json"]
    return [f for f in files + sorted((ROOT / "work/cache/spacetrack").glob("gp_*.json")) if f.exists()]


def placeholder(g) -> bool:
    """Fresh-epoch LEO-period sets Space-Track carries for deep-space objects it no longer tracks (P < 1 d)."""
    return 1 / float(g["MEAN_MOTION"]) < 1.0


def records(norad):
    """Distinct usable element sets of one object, sorted by epoch then creation date; with their files."""
    norad, out = int(norad), {}
    for f in sources():
        data = json.loads(f.read_text())
        rows = data.values() if isinstance(data, dict) else data
        for g in rows:
            if int(g.get("NORAD_CAT_ID", -1)) != norad or not g.get("TLE_LINE1"):
                continue
            if placeholder(g):
                continue
            out.setdefault(g["TLE_LINE1"].strip(), (g, f))
    recs = sorted(out.values(), key=lambda gf: (gf[0]["EPOCH"], gf[0].get("CREATION_DATE", "")))
    return [g for g, _ in recs], sorted({str(f) for _, f in recs})


def epoch_s(g) -> float:
    return parse_iso(g["EPOCH"] + "Z")


def sgp4_at(g, ctx):
    from skyfield.api import EarthSatellite
    sat = EarthSatellite(g["TLE_LINE1"], g["TLE_LINE2"], g.get("OBJECT_NAME", ""), ctx.ts)

    def at(t):
        p = sat.at(ctx.times(t))
        rv = np.hstack([np.atleast_2d(p.position.km.T), np.atleast_2d(p.velocity.km_per_s.T)])
        if not np.isfinite(rv).all():
            raise ValueError(f"SGP4 failed for {g['NORAD_CAT_ID']} at epoch {g['EPOCH']}")
        return rv
    at.g = g
    return at


def pieces(recs, win, step):
    """(element set, start, stop): each set from the first grid epoch >= its epoch to the next set's."""
    return [(recs[k], a, b) for k, a, b in nbody.pieces([epoch_s(g) for g in recs], win.grid(step))]


def epoch_state(norad, ctx, before_s=None):
    """(t_s, rv, element set) at the newest GP epoch (<= before_s if given), SDP4 -> GCRS."""
    recs, _ = records(norad)
    if before_s is not None:
        recs = [g for g in recs if epoch_s(g) <= before_s] or recs[:1]
    if not recs:
        raise LookupError(f"no usable GP for NORAD {norad}")
    g = recs[-1]
    t = epoch_s(g)
    return t, sgp4_at(g, ctx)(t)[0], g


def build(target, win, ctx):
    kind, ref, _ = primary(target)
    norad = int(target.get("norad") or ref)
    recs, files = records(norad)
    if not recs:
        raise LookupError(f"{target['cid']}: no usable GP for NORAD {norad}")
    step = nbody.step_for(target["regime"])
    prov = [nbody.raw(f, request="Space-Track GP via space-track skill CLI") for f in files]
    if target["regime"].startswith("EM_"):
        return cr3bp(target, win, ctx, norad, recs, step, prov)
    segs, switches, mid = [], [], 0.0
    prev = None
    for g, a, b in pieces(recs, win, step):
        at = sgp4_at(g, ctx)
        out, m = nbody.segments(at, win, step, a, b, f"gp:{norad}@{g['EPOCH'][:19]}Z", "gp",
                                "splice" if prev else "start", ctx)
        if prev:
            r0, r1 = at(a)[0, :3], prev(a)[0, :3]
            out[0].jump_km = float(np.linalg.norm(r0 - r1))
            switches.append({"t_utc": iso(a), "epoch": g["EPOCH"][:19] + "Z", "jump_km": round(out[0].jump_km, 3),
                             "jump_frac": round(out[0].jump_km / float(np.linalg.norm(r0)), 4),
                             "prev_age_d": round((a - epoch_s(prev.g)) / 86400, 1)})
        segs += out
        mid, prev = max(mid, m), at
    used = sorted({s.source for s in segs})
    newest = max(epoch_s(g) for g in recs if epoch_s(g) <= max(win.now_s, epoch_s(recs[0])))
    age = (win.now_s - newest) / 86400
    bad = max(switches, key=lambda s: s["jump_frac"], default=None)
    warn = (f"; consecutive GP sets disagree by {bad['jump_km']:,.0f} km at {bad['t_utc'][:10]} (the earlier set "
            f"{bad['prev_age_d']:.0f} d old)" if bad and bad["jump_frac"] > JUMP_WARN else "")
    return StateTable(cle_id=target["cid"], centre="earth", segments=segs, provenance=prov,
                      validation={"midpoint_max_km": mid, "gp_sets": len(used), "gp_switches": switches,
                                  "gp_age_d_now": round(age, 2), "epochs": [s.split("@")[1] for s in used]},
                      caveat=f"Space-Track GP (SGP4/SDP4), newest epoch <= now {iso(newest)[:16]}Z, age {age:.1f} d"
                             + warn)


def arc_fit(recs, ctx, t_e, rv0, span_d=ARC_SPAN_D):
    """One Earth + Moon + Sun trajectory through the SDP4 positions of every GP set within span_d days before t_e,
    each taken only within ±12 h of its own epoch (where SDP4 is best). A DRO is not a Kepler orbit, so a single
    set's SDP4 velocity is off by up to ~0.1 km/s; the positions of a run of sets are not."""
    from scipy.optimize import least_squares
    use = [r for r in recs if t_e - span_d * 86400 <= epoch_s(r) <= t_e]
    ts, obs = [], []
    for r in use:
        t = epoch_s(r) + np.array([-43200.0, 0.0, 43200.0])
        ts.append(t), obs.append(sgp4_at(r, ctx)(t)[:, :3])
    order = np.argsort(np.concatenate(ts))
    ts, obs = np.concatenate(ts)[order], np.vstack(obs)[order]
    a, b = min(ts[0], t_e), max(ts[-1], t_e)
    res = lambda x: (nbody.propagate(ctx, t_e, x, a, b)(ts)[:, :3] - obs).ravel() / 100
    rms = lambda f: float(np.sqrt(np.mean(np.sum(f.reshape(-1, 3) ** 2, axis=1))) * 100)
    sol = least_squares(res, rv0, x_scale=np.r_[[1000.0] * 3, [0.01] * 3], diff_step=1e-7, max_nfev=80)
    per = np.linalg.norm(sol.fun.reshape(-1, 3), axis=1) * 100
    info = {"arc_sets": len(use), "arc_span": [iso(ts[0]), iso(ts[-1])], "single_state_rms_km": round(rms(res(rv0))),
            "arc_rms_km": round(rms(sol.fun)), "arc_max_km": round(float(per.max())),
            "arc_dr_km": round(float(np.linalg.norm(sol.x[:3] - rv0[:3]))),
            "arc_dv_ms": round(float(np.linalg.norm(sol.x[3:] - rv0[3:])) * 1e3, 1)}
    return (sol.x, info) if len(use) >= 3 and info["arc_rms_km"] < info["single_state_rms_km"] else (rv0, info)


def cr3bp(target, win, ctx, norad, recs, step, prov):
    """AM6: GP sets give positions, not a usable CR3BP velocity: fit one Earth + Moon + Sun trajectory through the
    recent sets (arc_fit) and integrate it across the window."""
    g = [r for r in recs if epoch_s(r) <= win.t1_s][-1]
    t_e = epoch_s(g)
    rv0, fit = arc_fit(recs, ctx, t_e, sgp4_at(g, ctx)(t_e)[0])
    traj = nbody.propagate(ctx, t_e, rv0, win.t0_s, win.t1_s)
    segs, mid = nbody.segments(traj, win, step, win.t0_s, win.t1_s, f"gp+nbody:{norad}@{g['EPOCH'][:19]}Z",
                               "gp", "start", ctx)
    consistency = []
    for r in recs:
        t = epoch_s(r)
        if r is not g and abs(t - t_e) < 120 * 86400:
            far = nbody.propagate(ctx, t_e, rv0, t, t)
            consistency.append({"epoch": r["EPOCH"][:19] + "Z", "period_d": round(1 / float(r["MEAN_MOTION"]), 2),
                                "dr_km": round(float(np.linalg.norm(far(t)[0, :3] - sgp4_at(r, ctx)(t)[0, :3])), 1)})
    age = (win.now_s - t_e) / 86400
    t = win.grid(3600.0)
    moon = np.linalg.norm(traj(t)[:, :3] - ctx.moon_rv(t)[:, :3], axis=1)
    check = {"moon_km_window": [round(float(moon.min())), round(float(moon.max()))], "arc_fit": fit,
             **bg_check(target, ctx, traj, t_e, rv0, t)}
    warn = ""
    if target["regime"] in ("EM_DRO", "EM_OTHER") and moon.min() < 30000:
        k = int(np.argmin(moon))
        warn = (f"; lunar-bound and eccentric in the GP data, not a DRO: {moon.min():,.0f}-{moon.max():,.0f} km from the "
                f"Moon in the window, closest {iso(t[k])[:10]} (extrapolated beyond the last GP set, uncertain)")
        if "bg_moon_km_window" in check:
            warn += (f" (the Bill Gray TLE {check['bg_file']} to {check['bg_end'][:10]}, extrapolated, stays "
                     f"{check['bg_moon_km_window'][0]:,}-{check['bg_moon_km_window'][1]:,} km away)")
    return StateTable(cle_id=target["cid"], centre="earth", segments=segs, provenance=prov,
                      validation={"midpoint_max_km": mid, "gp_epoch": g["EPOCH"][:19] + "Z",
                                  "gp_period_d": round(1 / float(g["MEAN_MOTION"]), 3),
                                  "nbody_vs_other_gp": consistency, **check},
                      caveat=f"Earth + Moon + Sun trajectory fitted to {fit['arc_sets']} Space-Track GP sets "
                             f"({fit['arc_span'][0][:10]}..{g['EPOCH'][:10]}, rms {fit['arc_rms_km']:,} km), newest "
                             f"{age:.0f} d old; CR3BP orbit, SDP4 used only near each epoch" + warn)


def bg_check(target, ctx, traj, t_e, rv0, t):
    """The target's newest Bill Gray TLE, extrapolated from the end of its Range with cle.nbody: its lunar
    distance over the window and its offset from the GP state at the GP epoch (a DRO sanity check)."""
    from cle.adapters import bgtle
    items = [i for s in target.get("sources", []) for i in s.get("ephem_sources", "").split(";") if i.startswith("bgtle:")]
    ends = {}
    for name in {primary({"primary_ephem": i})[1] for i in items}:
        try:
            ends[name] = bgtle.entry(name)[2] or 0
        except LookupError:
            pass
    for name in sorted(ends, key=ends.get, reverse=True)[:1]:
        try:
            tb, rvb, info = bgtle.end_state(name, ctx)
            far = nbody.propagate(ctx, tb, rvb, min(tb, t[0], t_e), max(t[-1], t_e))
        except (LookupError, ValueError, RuntimeError):
            return {}
        moon = np.linalg.norm(far(t)[:, :3] - ctx.moon_rv(t)[:, :3], axis=1)
        return {"bg_file": name, "bg_end": iso(tb), "bg_moon_km_window": [round(float(moon.min())), round(float(moon.max()))],
                "gp_vs_bg_extrap_at_gp_epoch": {"dr_km": round(float(np.linalg.norm(rv0[:3] - far(t_e)[0, :3]))),
                                                "dv_ms": round(float(np.linalg.norm(rv0[3:] - far(t_e)[0, 3:])) * 1000, 1)}}
    return {}


def check(ctx, win):
    """SGP4 -> GCRS against Horizons over the window for objects that have both (no assertion)."""
    out = {}
    for norad, hz in CHECKS.items():
        recs, _ = records(norad)
        try:
            t, ref = nbody.hz_vectors(hz, iso(win.t0_s)[:16].replace("T", " "), iso(win.t1_s)[:16].replace("T", " "),
                                      "1 h", CACHE / "hz")
        except ValueError as err:
            out[norad] = {"horizons": hz, "n_gp": len(recs), "error": str(err)}
            continue
        dr = np.full(len(t), np.nan)
        age = np.full(len(t), np.nan)
        for g, a, b in pieces(recs, win, 3600.0):
            k = (t >= a) & (t <= b)
            dr[k] = np.linalg.norm(sgp4_at(g, ctx)(t[k])[:, :3] - ref[k, :3], axis=1)
            age[k] = (t[k] - epoch_s(g)) / 86400
        past = t <= win.now_s
        out[norad] = {"horizons": hz, "n_gp": len(recs), "max_km": round(float(np.nanmax(dr)), 1),
                      "median_km": round(float(np.nanmedian(dr)), 1),
                      "max_km_past": round(float(np.nanmax(dr[past])), 1),
                      "max_km_future": round(float(np.nanmax(dr[~past])), 1),
                      "max_age_d": round(float(np.nanmax(np.abs(age))), 1)}
    return out


if __name__ == "__main__":
    from cle.context import Context
    from cle.timeutil import window
    args = sys.argv[1:]
    if "--check" in args:
        print(json.dumps(check(Context(), window()), indent=1))
        sys.exit()
    if "--fetch" in args:
        args.remove("--fetch")
        objs = [nbody.target(c) for c in args]
        try:
            print(fetch([o["norad"] for o in objs] + list(CHECKS), window()))
        except GPFetchError as e:
            print(f"gp fetch failed, building from the cached sets: {e}", flush=True)
    nbody.run(build, args)
