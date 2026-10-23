"""Latest-orbit extrapolation for STALE primaries (ARCHITECTURE.md AM5).

The latest state of the stale source is integrated with Earth + Moon + Sun point masses (+ J2, cle.nbody) over
the window, quality=extrap, caveat "extrapolated N d from <date>":
    bgtle:<file>                 the end of the file's # Range: the n-body state least-squares fitted to the file's
                                 get_vect positions (J2000) over its last 20 d, or the last TLE's state if that fits better
    neocpsat_fit:<ref>           the refit's elements epoch (fo, as cle.adapters.fo)
    pluto_mpec:<url>             the same, from the target's neocpsat_fit of that night with the most obs
    spacetrack_gp:<norad>        the newest GP epoch <= now, SDP4 -> TEME -> GCRS
sigma_km for fo states combines fo's own 1-sigma (fo -E 16) with the difference between fo's one-leg propagation
and ours (SRP, planets), grown linearly from the epoch; otherwise it is a display-only growth s0 + 0.1 % of the
range per day since the epoch (s0 = max(the file's worst residual, the fit rms) for Bill Gray TLEs, 10 km for GP).

The bgtle least-squares start state depends on the TLE file, not on the window, so it is memoised in
work/cache/extrap/fits.json under fit_key() (file sha256, epoch, start state, fit settings, n-body constants, kernel):
a rebuild returns the identical state without the fit (REVIEW EFF-2); CLE_REFRESH=1 refits.

    python -I src/cle/adapters/extrap.py <cid> ...      build (and save)
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cle import nbody
from cle.adapters import bgtle, fo, gp, primary
from cle.context import RE_KM, RM_KM
from cle.statetable import StateTable
from cle.timeutil import iso

ROOT = nbody.ROOT
GROWTH = 1e-3            # display sigma growth, fraction of the geocentric range per day
GP_S0_KM = 10.0
FIT_SPAN_D = 20          # bgtle: the start state is fitted to the file's own positions over the last 20 d
FIT_POOR_KM = 1000.0
FITS = ROOT / "work/cache/extrap/fits.json"
FIT_VERSION = 1          # bump when fit_state's method changes: every memoised fit is then recomputed


def end_date(item):
    m = re.search(r"\.\.(\d{4}-\d\d-\d\d)\]", item)
    return m[1] if m else None


def neocpsat_ref(target):
    """The neocpsat_fit reference behind the primary: itself, or (pluto_mpec) the target's neocpsat_fit
    source of the same night with the most observations, i.e. the combined page."""
    kind, ref, _ = primary(target)
    if kind == "neocpsat_fit":
        return ref
    day, refs = end_date(target["primary_ephem"]), []
    for src in target.get("sources", []):
        for item in src.get("ephem_sources", "").split(";"):
            k, r, _ = primary({"primary_ephem": item})
            if k == "neocpsat_fit" and end_date(item) == day and (ROOT / r.split("@")[0]).exists() and r not in refs:
                refs.append(r)
    if not refs:
        raise LookupError(f"{target['cid']}: no neocpsat fit behind {ref}")
    return max(refs, key=lambda r: (len(fo.resolve(r)[0]), "combined" in r))


def fit_key(ref, ctx, t, rv, span_d, step):
    """sha256 over everything fit_state's result depends on."""
    parts = [nbody.digest(bgtle.TLES / ref), repr(float(t)), repr([float(x) for x in rv]), repr((span_d, step)),
             repr((FIT_VERSION, nbody.J2, nbody.RTOL, nbody.ATOL, nbody.BODY_STEP)), str(ctx.kernel_path)]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def memo_get(key):
    if os.environ.get("CLE_REFRESH") == "1":
        return None
    try:
        return json.loads(FITS.read_text()).get(key)
    except (OSError, ValueError):
        return None


def memo_put(key, ref, x, info):
    """Merge one fit into fits.json under a lock (states may fit several targets in parallel processes)."""
    FITS.parent.mkdir(parents=True, exist_ok=True)
    with open(FITS.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            cur = json.loads(FITS.read_text())
        except (OSError, ValueError):
            cur = {}
        cur[key] = {"file": ref, "x": [float(v) for v in x], "info": info, "cached_utc": iso(time.time())}
        tmp = FITS.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=1))
        tmp.replace(FITS)


def fit_state(ref, ctx, t, rv, span_d=FIT_SPAN_D, step=3 * 3600.0):
    """Least-squares n-body state at t through the file's own positions over the last span_d days of its Range.
    A single TLE's velocity is ~6 m/s off the arc the file traces (IBEX), so it is kept only when it fits better.
    Memoised in FITS: a hit returns the stored state and info unchanged."""
    key = fit_key(ref, ctx, t, rv, span_d, step)
    hit = memo_get(key)
    if hit:
        return np.array(hit["x"], dtype=float), dict(hit["info"])
    trk, (_, r0, r1) = bgtle.Track(ref, ctx), bgtle.entry(ref)
    ts = np.arange(max(t - span_d * 86400, trk.covered(range_=(r0, r1))[0]), t - 1, step)
    obs = trk(ts)[:, :3]
    res = lambda x: (nbody.propagate(ctx, t, x, ts[0], t)(ts)[:, :3] - obs).ravel() / 10
    rms = lambda f: float(np.sqrt(np.mean(np.sum(f.reshape(-1, 3) ** 2, axis=1)) * 100))
    try:
        sol = least_squares(res, rv, x_scale=np.r_[[100.0] * 3, [0.01] * 3], diff_step=1e-7, max_nfev=60)
    except RuntimeError:
        sol = None
    info = {"single_state_rms_km": round(rms(res(rv)), 1), "fit_rms_km": round(rms(sol.fun), 1) if sol else None,
            "fit_span": [iso(ts[0]), iso(t)], "fit_samples": len(ts)}
    if sol and info["fit_rms_km"] < info["single_state_rms_km"]:
        info |= {"fit_dr_km": round(float(np.linalg.norm(sol.x[:3] - rv[:3])), 1),
                 "fit_dv_ms": round(float(np.linalg.norm(sol.x[3:] - rv[3:])) * 1e3, 2)}
        x = sol.x
    else:
        info["fit_rms_km"] = info["single_state_rms_km"]
        x = np.asarray(rv, dtype=float)
    memo_put(key, ref, x, info)
    return x, info


def latest(target, win, ctx):
    """(t_utc_s, rv (6,), source, sigma(t, r_km) or None, provenance, info) of the stale source."""
    kind, ref, _ = primary(target)
    if kind == "bgtle":
        t, rv, info = bgtle.end_state(ref, ctx)
        rv, fit = fit_state(ref, ctx, t, rv)
        info |= fit
        s0 = max(info["worst_km"] or GP_S0_KM, fit["fit_rms_km"])
        return t, rv, f"bgtle:{ref}", lambda tt, r: s0 + GROWTH * r * np.abs(tt - t) / 86400, \
            [nbody.raw(bgtle.TLES / ref, range=info["range"], tool=str(bgtle.GET_VECT))], info
    if kind == "spacetrack_gp":
        norad = int(target.get("norad") or ref)
        t, rv, g = gp.epoch_state(norad, ctx, before_s=win.now_s)
        _, files = gp.records(norad)
        info = {"epoch": g["EPOCH"][:19] + "Z", "period_d": round(1 / float(g["MEAN_MOTION"]), 3),
                "sdp4_at": gp.sgp4_at(g, ctx)}
        return t, rv, f"gp:{norad}@{info['epoch']}", lambda tt, r: GP_S0_KM + GROWTH * r * np.abs(tt - t) / 86400, \
            [nbody.raw(f, request="Space-Track GP via space-track skill CLI") for f in files], info
    if kind in ("neocpsat_fit", "pluto_mpec"):
        r = neocpsat_ref(target)
        t, rv, info = fo.epoch_state(r, ctx, until_s=win.t1_s, cid=target["cid"])
        page = Path(r.split("@")[0])
        return t, rv, f"fo:{page.parent.name if page.name == 'elements.txt' else page.name}", None, \
            info.pop("provenance"), info
    raise LookupError(f"{target['cid']}: no extrapolation from {target.get('primary_ephem')!r}")


def impact(traj, ctx, a_s, b_s, step=600.0):
    """First epoch in [a_s, b_s] where the trajectory is inside the Earth or the Moon, or None."""
    t = np.arange(a_s, b_s + step, step)
    rv = traj(np.clip(t, a_s, b_s))
    hit = (np.linalg.norm(rv[:, :3], axis=1) < RE_KM) | \
          (np.linalg.norm(rv[:, :3] - ctx.moon_rv(np.clip(t, a_s, b_s))[:, :3], axis=1) < RM_KM)
    return float(t[np.argmax(hit)]) if hit.any() else None


def build(target, win, ctx):
    t_e, rv, src, sig, prov, info = latest(target, win, ctx)
    try:
        traj = nbody.propagate(ctx, t_e, rv, win.t0_s, win.t1_s)
    except RuntimeError as err:
        raise RuntimeError(f"{target['cid']}: integration from {iso(t_e)} failed ({err})") from err
    a, b = win.t0_s, win.t1_s
    hit = impact(traj, ctx, min(t_e, a), b)
    if hit is not None:
        if hit <= a + nbody.step_for(target["regime"]):
            raise RuntimeError(f"{target['cid']}: the extrapolation from {iso(t_e)[:10]} hits the Earth or Moon "
                               f"near {iso(hit)[:10]}, before the window")
        b = hit - 600.0
    step = nbody.step_for(target["regime"])
    check, note = {}, ""
    if "fit" in info:
        fit = info.pop("fit")
        diff = {t: float(np.linalg.norm(fit.single(t)[:3] - traj(t)[0, :3])) for t in (win.now_s, win.t1_s)}
        check = {"fo_one_leg_vs_nbody_km": {iso(t): round(d, 1) for t, d in diff.items()}}
        grow = max(d / abs(t - t_e) for t, d in diff.items())          # model difference (SRP, planets), km/s
        fo_sig = fit.sigma or (lambda tt: 0.0)
        sig = lambda tt, r: np.hypot(fo_sig(tt), grow * np.abs(tt - t_e))
        info["sigma_model"] = "hypot(fo -E 16 sigma, |fo one-leg - nbody| growing linearly from the epoch)"
        c = info["check"]
        if not c["accepted"]:
            note = f"; refit not accepted ({c['n_used']}/{c['n_obs']} obs, rms {c['rms_arcsec']}\")"
    else:
        if (info.get("fit_rms_km") or 0) > FIT_POOR_KM:
            note = (f"; no single n-body orbit reproduces the file's last {FIT_SPAN_D} d (rms {info['fit_rms_km']:,.0f} km), "
                    "so the position is indicative only")
        if "sdp4_at" in info:
            f = info.pop("sdp4_at")
            check = {"sdp4_vs_nbody_km_now": round(float(np.linalg.norm(f(win.now_s)[0, :3] - traj(win.now_s)[0, :3])), 1)}
        info["sigma_model"] = f"display: s0 + {GROWTH:g} r per day since the epoch"
    sigma = None if sig is None else (lambda tt: sig(tt, np.linalg.norm(traj(tt)[:, :3], axis=1)))
    segs, mid = nbody.segments(traj, win, step, a, b, f"{src}+nbody", "extrap", "start", ctx, sigma=sigma)
    if not segs:
        raise RuntimeError(f"{target['cid']}: no samples inside the window")
    days = (win.now_s - t_e) / 86400
    what = {"bgtle": "Bill Gray TLE Range end", "spacetrack_gp": "Space-Track GP", "neocpsat_fit": "find_orb refit",
            "pluto_mpec": "find_orb refit"}[primary(target)[0]]
    return StateTable(cle_id=target["cid"], centre="earth", segments=segs, provenance=prov,
                      validation={"midpoint_max_km": mid, "epoch_utc": iso(t_e), "extrap_days_now": round(days, 1),
                                  "impact_utc": iso(hit) if hit else None, "sigma_km_now": float(np.atleast_1d(sigma(win.now_s))[0])
                                  if sigma and a <= win.now_s <= b else None, **check,
                                  "latest": {k: v for k, v in info.items() if k not in ("fo_table",)}},
                      caveat=f"extrapolated {days:.0f} d from {iso(t_e)[:10]} ({what}, {src}); Earth + Moon + Sun "
                             f"point masses, no manoeuvres or radiation pressure" + note
                             + (f"; hits the Earth or Moon near {iso(hit)[:10]}" if hit else ""))


if __name__ == "__main__":
    nbody.run(build)
