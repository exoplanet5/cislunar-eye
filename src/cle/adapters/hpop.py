"""State-vector adapter: a hand-supplied state vector propagated with ../hpop (ARCHITECTURE.md AM14).

A `statevec:<path>[<epoch date>..<epoch date>]` reference (catalog/sources/manual.tsv; path from the project root)
names a file in hpop's paste format: <EPOCH>ISO UTC</EPOCH> <X> <Y> <Z> <X_DOT> <Y_DOT> <Z_DOT>, in m and m/s,
Earth-centred ICRF (hpop's defaults for that format). hpop's own propagator (Moon-centred ICRF, DOP853 rtol 1e-12,
GRGM660 lunar harmonics to degree 10 inside 50 000 km, Earth and Sun point masses, DE440s through astropy) takes the
state from its epoch to both window ends (backward with a negative duration); its dense output is re-centred on the
Earth with the build Context's DE440s Moon and sampled on the regime grid (cle.nbody.segments). quality=fit within
FIT_DAYS of the epoch, extrap beyond. validation records the round trip at the epoch, the Moon distance in the window
and the offset from cle.nbody (Earth + Moon + Sun + J2) integrating the same state.

hpop is imported from $CLE_HPOP_DIR, default ../hpop next to this project.

    python -I src/cle/adapters/hpop.py <cid> ...      build (and save) targets
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cle import nbody
from cle.adapters import primary
from cle.statetable import StateTable
from cle.timeutil import iso, parse_iso

ROOT = nbody.ROOT
HPOP_DIR = Path(os.environ.get("CLE_HPOP_DIR") or ROOT.parent / "hpop")
TAGS = ("X", "Y", "Z", "X_DOT", "Y_DOT", "Z_DOT")
FIT_DAYS = 30               # quality=fit within this many days of the state epoch, extrap beyond (and the caveat says stale)
_HPOP = None


def hpop():
    """hpop's propagator module, imported once from HPOP_DIR (its modules import one another by bare name)."""
    global _HPOP
    if _HPOP is None:
        if not (HPOP_DIR / "propagator.py").exists():
            raise FileNotFoundError(f"hpop not found at {HPOP_DIR} (set CLE_HPOP_DIR)")
        sys.path.insert(0, str(HPOP_DIR))
        try:
            with contextlib.redirect_stdout(io.StringIO()):     # its "[HPOP] solar-system ephemeris: ..." line
                import propagator
        finally:
            sys.path.remove(str(HPOP_DIR))
        if Path(propagator.__file__).resolve().parent != HPOP_DIR.resolve():
            raise ImportError(f"'propagator' resolved to {propagator.__file__}, not hpop's")
        _HPOP = propagator
    return _HPOP


def read_state(path):
    """(epoch UTC seconds, epoch ISO text, geocentric ICRF state km & km/s) from an hpop paste-format file."""
    text = Path(path).read_text()

    def tag(name):
        m = re.search(rf"<{name}>\s*([^<]+?)\s*</{name}>", text, re.IGNORECASE)
        if not m:
            raise ValueError(f"{Path(path).name}: no <{name}> tag")
        return m[1]
    epoch = tag("EPOCH").rstrip("Zz")
    rv = np.array([float(tag(k)) for k in TAGS]) / 1e3
    r, v = np.linalg.norm(rv[:3]), np.linalg.norm(rv[3:])
    if not (np.isfinite(rv).all() and 1e4 < r < 3e6 and v < 20):
        raise ValueError(f"{Path(path).name}: |r| {r:.4g} km, |v| {v:.4g} km/s: not a cislunar state in m and m/s")
    return parse_iso(epoch + "Z"), epoch, rv


def trajectory(ctx, epoch, te, rv, a_s, b_s, config=None):
    """Dense geocentric ICRF rv(t) on [min(a, te), max(b, te)]: hpop runs from the epoch state to each end."""
    from astropy.time import Time
    hp = hpop()
    t_ep = Time(epoch, scale="utc")
    s0 = hp.convert_earth_to_moon(rv, t_ep)              # hpop's Earth -> Moon conversion, as its web app does
    lo, hi = min(a_s, te), max(b_s, te)
    arcs, nfev = [], 0
    for end in (lo, hi):
        if end != te:
            res = hp.propagate(s0, t_ep, end - te, 3600.0, config or hp.HPOPConfig())
            arcs.append((min(te, end), max(te, end), res["sol"].sol))
            nfev += res["n_rhs_evals"]

    def at(t):
        t = np.atleast_1d(np.asarray(t, dtype=float))
        if t.size and (t.min() < lo - 1e-6 or t.max() > hi + 1e-6):
            raise ValueError("time outside the propagated span")
        sel = np.tile(s0, (len(t), 1))
        for a, b, sol in arcs:
            k = (t >= a) & (t <= b) & (t != te)
            if k.any():
                sel[k] = sol(t[k] - te).T
        return sel + ctx.moon_rv(t)                       # Moon-centred -> geocentric with the build's DE440s Moon
    at.span, at.nfev, at.s0 = (lo, hi), nfev, s0
    return at


def build(target, win, ctx):
    _, ref, _ = primary(target)
    path = ROOT / ref
    te, epoch, rv = read_state(path)
    hp = hpop()
    step = nbody.step_for(target["regime"])
    traj = trajectory(ctx, epoch, te, rv, win.t0_s, win.t1_s)
    fa, fb = te - FIT_DAYS * 86400, te + FIT_DAYS * 86400
    starts = [win.t0_s] + [s for s in (fa, fb) if win.t0_s < s < win.t1_s]
    qual = ["fit" if fa <= s < fb or (s == win.t0_s and fa <= s <= fb) else "extrap" for s in starts]
    src = f"hpop:{path.name}@{iso(te)}"
    segs, mid = [], 0.0
    for k, a, b in nbody.pieces(starts, win.grid(step)):
        out, m = nbody.segments(traj, win, step, a, b, src, qual[k], "start" if not segs else "continuous", ctx)
        segs += out
        mid = max(mid, m)
    t = win.grid(3600.0)
    moon = np.linalg.norm(traj(t)[:, :3] - ctx.moon_rv(t)[:, :3], axis=1)
    ref_nb = nbody.propagate(ctx, te, rv, win.t0_s, win.t1_s)          # same state, cle's Earth + Moon + Sun + J2
    vs_nb = np.linalg.norm(traj(t)[:, :3] - ref_nb(t)[:, :3], axis=1)
    back = traj(te)[0] - rv
    age = (win.now_s - te) / 86400
    k = int(np.argmin(moon))
    eph = hp.EPHEMERIS_NAME
    validation = {"midpoint_max_km": mid, "state_file": ref, "state_epoch": iso(te), "state_age_d_now": round(age, 2),
                  "hpop_dir": str(HPOP_DIR), "hpop_ephemeris": eph, "hpop_rhs_evals": traj.nfev,
                  "epoch_round_trip_m": round(float(np.linalg.norm(back[:3])) * 1e3, 4),
                  "moon_km_window": [round(float(moon.min())), round(float(moon.max()))],
                  "moon_closest_utc": iso(t[k]),
                  "hpop_vs_nbody_km": {"max": round(float(vs_nb.max()), 1), "at_window_ends": [round(float(vs_nb[0]), 1),
                                                                                              round(float(vs_nb[-1]), 1)]}}
    stale = abs(age) > FIT_DAYS
    caveat = (f"{'stale: ' if stale else ''}HPOP propagation of the state vector of {epoch[:16]}Z ({path.name}), "
              f"{abs(age):.0f} d {'before' if age >= 0 else 'after'} now; {moon.min():,.0f}-{moon.max():,.0f} km from "
              f"the Moon in the window" + ("; hpop used its analytic ephemeris, not DE440s" if eph == "builtin" else ""))
    prov = [nbody.raw(path, format="hpop paste text: m, m/s, Earth-centred ICRF, UTC epoch"),
            nbody.raw(HPOP_DIR / "propagator.py", tool="hpop propagator.propagate (HPOPConfig defaults)")]
    return StateTable(cle_id=target["cid"], centre="earth", segments=segs, provenance=prov, validation=validation,
                      caveat=caveat)


if __name__ == "__main__":
    nbody.run(build)
