"""Earth + Moon + Sun point-mass integrator in geocentric ICRF, and the regime sampler (ARCHITECTURE.md AM5, AM6, §6.2).

propagate(ctx, t_s, rv, a_s, b_s) integrates one geocentric geometric ICRF state (km, km/s at UTC seconds t_s)
forward and backward to cover [a_s, b_s] with scipy DOP853 (rtol 1e-11, atol 1e-6 km) and returns a dense
callable rv(t) -> (n, 6). Moon and Sun come from the shared Context (DE440s) on a 10-minute Hermite grid; the
indirect terms make the frame geocentric; Earth J2 (pole of date) is applied at every radius: cutting it at
50 000 km, as first planned, kicks every orbit at the cut and cost 1 400 km in 15 d on Chandra (21 km without).

segments(...) samples any such callable on the window-aligned regime grid and refines it where h·omega > 0.3
(§6.2), returning StateTable segments that share their boundary epochs.

Check against Horizons:  python -I src/cle/nbody.py            (TESS -95, Chandra -151, Spektr-R -557)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
from scipy.integrate import solve_ivp
from scipy.interpolate import CubicHermiteSpline

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cle.context import GM_EARTH, GM_MOON, GM_SUN, RE_KM
from cle.statetable import Segment

ROOT = Path(__file__).resolve().parents[2]
J2 = 1.08262668e-3
BODY_STEP = 600.0
RTOL, ATOL = 1e-11, 1e-6
STEP = {"LUNAR_ORBIT": 300, "EM_L1": 1800, "EM_L2": 1800, "SE_L1": 21600, "SE_L2": 21600,
        "EARTH_NEAR_HELIO": 21600}
HILL_MOON = 61300.0
API = "https://ssd.jpl.nasa.gov/api/horizons.api"


def step_for(regime: str) -> float:
    return float(STEP.get(regime, 3600))


class Bodies:
    """Moon and Sun geocentric positions on a 10-minute Hermite grid, and the Earth pole of date."""

    def __init__(self, ctx, a_s, b_s):
        t = np.arange(a_s - 2 * BODY_STEP, b_s + 3 * BODY_STEP, BODY_STEP)
        m, s = ctx.moon_rv(t), ctx.sun_rv(t)
        self.moon = CubicHermiteSpline(t, m[:, :3], m[:, 3:])
        self.sun = CubicHermiteSpline(t, s[:, :3], s[:, 3:])
        from skyfield.framelib import itrs
        self.pole = itrs.rotation_at(ctx.times(0.5 * (a_s + b_s))[0])[2]

    def accel(self, t, y):
        r = y[:3]
        rn = np.sqrt(r @ r)
        a = -GM_EARTH * r / rn**3
        for p, gm in ((self.moon(t), GM_MOON), (self.sun(t), GM_SUN)):
            d = p - r
            a += gm * (d / np.sqrt(d @ d)**3 - p / np.sqrt(p @ p)**3)
        z = r @ self.pole
        a -= 1.5 * J2 * GM_EARTH * RE_KM**2 / rn**5 * ((1 - 5 * z * z / rn**2) * r + 2 * z * self.pole)
        return np.concatenate([y[3:], a])


def propagate(ctx, t_s, rv, a_s, b_s):
    """Dense geocentric ICRF trajectory through state rv at t_s, valid on [min(a, t), max(b, t)]."""
    rv = np.asarray(rv, dtype=float).ravel()
    lo, hi = min(a_s, t_s), max(b_s, t_s)
    bodies = Bodies(ctx, lo, hi)
    f = lambda t, y: bodies.accel(t, y)
    arcs = []
    for end in (lo, hi):
        if end != t_s:
            sol = solve_ivp(f, (t_s, end), rv, method="DOP853", rtol=RTOL, atol=ATOL, dense_output=True)
            if sol.status != 0:
                raise RuntimeError(sol.message)
            arcs.append((min(t_s, end), max(t_s, end), sol.sol))

    def at(t):
        t = np.atleast_1d(np.asarray(t, dtype=float))
        if t.size and (t.min() < lo - 1e-6 or t.max() > hi + 1e-6):
            raise ValueError("time outside the integrated span")
        out = np.tile(rv, (len(t), 1))
        for a, b, s in arcs:
            k = (t >= a) & (t <= b) & (t != t_s)
            if k.any():
                out[k] = s(t[k]).T
        return out
    at.span = (lo, hi)
    return at


def omega(rv, moon=None):
    """Angular rate (rad/s) about the Earth, or about the Moon inside its Hill sphere, per sample."""
    r, v = rv[:, :3], rv[:, 3:]
    w = np.linalg.norm(np.cross(r, v), axis=1) / np.sum(r * r, axis=1)
    if moon is not None:
        d, u = r - moon[:, :3], v - moon[:, 3:]
        dn2 = np.sum(d * d, axis=1)
        near = dn2 < HILL_MOON**2
        w[near] = np.maximum(w[near], np.linalg.norm(np.cross(d, u), axis=1)[near] / dn2[near])
    return w


def peak_omega(rv, w):
    """Per-interval rate: the osculating perigee rate where r has a minimum inside the interval."""
    r, v = rv[:, :3], rv[:, 3:]
    rn = np.linalg.norm(r, axis=1)
    rdot = np.sum(r * v, axis=1) / rn
    h = np.linalg.norm(np.cross(r, v), axis=1)
    e = np.linalg.norm(np.cross(v, np.cross(r, v)) / GM_EARTH - r / rn[:, None], axis=1)
    rp = h**2 / GM_EARTH / (1 + e)
    out = np.maximum(w[:-1], w[1:])
    peri = (rdot[:-1] < 0) & (rdot[1:] > 0)
    out[peri] = np.maximum(out[peri], (h / rp**2)[:-1][peri])
    return out


def pieces(starts, t):
    """(k, a, b) on grid t for items that take over at times starts[k]: item k runs from the first grid epoch
    >= starts[k] to the next item's (item 0 also covers the grid before it); items replaced within one step
    are skipped. Neighbouring pieces share their boundary epoch."""
    edge = np.maximum.accumulate([t[0]] + [t[min(np.searchsorted(t, s - 1e-6), len(t) - 1)] for s in starts[1:]]
                                 + [t[-1]])
    return [(k, edge[k], edge[k + 1]) for k in range(len(starts)) if edge[k + 1] > edge[k]]


def hermite_mid(r0, v0, r1, v1, h):
    return 0.5 * (r0 + r1) + h * (v0 - v1) / 8


def substep(m, step):
    """Smallest m' >= m dividing the step into whole seconds >= 60 s (or the 60 s floor)."""
    ok = [d for d in range(1, int(step // 60) + 1) if step % d == 0 and (step / d) % 1 == 0]
    return np.array([next((d for d in ok if d >= x), ok[-1]) for x in np.atleast_1d(m)], dtype=int)


def segments(rv_at, win, step, a_s, b_s, source, quality, join="start", ctx=None, sigma=None, wmax=0.3, tol=1.0):
    """Window-aligned samples of rv_at on [a_s, b_s]: base `step`, divided by m where h·omega > wmax (§6.2) and
    again wherever the Hermite midpoint error exceeds tol km. Returns (segments, midpoint_max_km); refined
    pieces join 'continuous' and share their boundary epochs."""
    t = win.grid(step, a_s, b_s)
    if len(t) < 2:
        return [], 0.0
    rv = rv_at(t)
    w = peak_omega(rv, omega(rv, ctx.moon_rv(t) if ctx is not None else None))
    m = substep(np.ceil(step * w / wmax), step)
    for _ in range(5):
        m = np.maximum(m, np.maximum(np.r_[m[1:], 1], np.r_[1, m[:-1]]))      # one interval of margin each side
        out, err = sample(rv_at, t, rv, m, step, source, quality, join, sigma)
        bad = (err > tol) & (m < substep(1e9, step)[0])
        if not bad.any():
            break
        m = np.where(bad, substep(2 * m, step), m)
    return out, float(err.max())


def sample(rv_at, t, rv, m, step, source, quality, join, sigma):
    """Segments for per-interval divisors m; returns them and the midpoint error per base interval."""
    out, err, k = [], np.zeros(len(m)), 0
    while k < len(m):
        j = k
        while j + 1 < len(m) and m[j + 1] == m[k]:
            j += 1
        h = step / m[k]
        tt = t[k:j + 2] if m[k] == 1 else t[k] + h * np.arange(round((t[j + 1] - t[k]) / h) + 1)
        x = rv[k:j + 2] if m[k] == 1 else rv_at(tt)
        tm = 0.5 * (tt[:-1] + tt[1:])
        e = np.linalg.norm(hermite_mid(x[:-1, :3], x[:-1, 3:], x[1:, :3], x[1:, 3:], h) - rv_at(tm)[:, :3], axis=1)
        np.maximum.at(err, k + ((tm - t[k]) // step).astype(int), e)
        sg = None if sigma is None else np.asarray(sigma(tt), dtype=np.float32)
        out.append(Segment(t0_s=float(tt[0]), step_s=float(h), r_km=x[:, :3], v_kms=x[:, 3:], source=source,
                           quality=quality, join_before=join if not out else "continuous", sigma_km=sg))
        k = j + 1
    return out, err


def digest(path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def raw(path, **extra) -> dict:
    """Provenance entry for one raw input file."""
    path = Path(path)
    rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    return {"raw": rel, "sha256": digest(path), **extra}


def target(cid: str) -> dict:
    objs = json.loads((ROOT / "catalog/master_list.json").read_text())["objects"]
    return next(o for o in objs if o["cid"] == cid)


def run(build, argv=None):
    """Adapter __main__: build the cids named on the command line and save them to build/states/."""
    from cle.context import Context
    from cle.timeutil import window
    win, ctx = window(), Context()
    for cid in argv if argv is not None else sys.argv[1:]:
        st = build(target(cid), win, ctx)
        st.save(ROOT / "build/states")
        n = sum(s.n for s in st.segments)
        print(f"{cid}: {len(st.segments)} segments, {n} samples, {st.validation.get('midpoint_max_km', 0):.3f} km "
              f"midpoint, {st.caveat}", flush=True)


def hz_vectors(command, start, stop, step, cache, center=399):
    """Horizons geocentric ICRF vectors (MMX request), cached as JSON; returns (t_utc_s, (n, 6))."""
    from cle.timeutil import parse_iso
    values = dict(COMMAND=str(command), EPHEM_TYPE="VECTORS", CENTER=f"500@{center}", START_TIME=start,
                  STOP_TIME=stop, STEP_SIZE=step, REF_SYSTEM="ICRF", REF_PLANE="FRAME", TIME_TYPE="UT",
                  VEC_CORR="NONE", OUT_UNITS="KM-S", VEC_TABLE="2", CSV_FORMAT="YES", CAL_FORMAT="JD", OBJ_DATA="NO")
    params = {"format": "json", **{k: f"'{v}'" for k, v in values.items()}}
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"hz_{str(command).strip('-')}_{start[:10]}_{stop[:10]}_{step.replace(' ', '')}.json"
    if not dest.exists():
        with urlopen(API + "?" + urlencode(params), timeout=180) as response:
            dest.write_bytes(response.read())
    result = json.loads(dest.read_text())
    if result.get("error"):
        raise ValueError(result["error"])
    text = result["result"]
    if "Reference frame : ICRF" not in text or f"({str(command)})" not in text.split("$$SOE")[0]:
        raise ValueError(f"unexpected Horizons header in {dest}")
    rows = [r.split(",") for r in text.split("$$SOE")[1].split("$$EOE")[0].strip().splitlines()]
    jd = np.array([float(r[0]) for r in rows])
    return (jd - 2440587.5) * 86400.0, np.array([[float(x) for x in r[2:8]] for r in rows])


def check(ctx, win):
    """Integrate Horizons states from the window start; max |dr| over 15 d and 30 d. Spektr-R's Horizons
    record is TLE-based since 2011-11-16 (its OBJ_DATA, work/cache/nbody_check/hz_557_objdata.json), so it
    measures SDP4 and TLE switching, not this integrator; TESS and Chandra are limited by their unmodelled SRP."""
    from cle.timeutil import iso
    cache = ROOT / "work/cache/nbody_check"
    report = {}
    for name, cmd, stop in (("TESS", -95, win.t1_s), ("Chandra", -151, win.t1_s),
                            ("Spektr-R", -557, win.t0_s + 28 * 86400 + 39600)):
        a, b = iso(win.t0_s)[:16].replace("T", " "), iso(stop)[:16].replace("T", " ")
        t, ref = hz_vectors(cmd, a, b, "1 h", cache)
        traj = propagate(ctx, t[0], ref[0], t[0], t[-1])
        dr = np.linalg.norm(traj(t)[:, :3] - ref[:, :3], axis=1)
        d = (t - t[0]) / 86400
        report[name] = {"horizons": cmd, "start": iso(t[0]), "end": iso(t[-1]),
                        "max_km_15d": float(dr[d <= 15].max()), "max_km_30d": float(dr.max()),
                        "r_km_range": [float(np.linalg.norm(ref[:, :3], axis=1).min()),
                                       float(np.linalg.norm(ref[:, :3], axis=1).max())]}
    return report


if __name__ == "__main__":
    from cle.context import Context
    from cle.timeutil import window
    out = json.dumps(check(Context(), window()), indent=1)
    (ROOT / "work/cache/nbody_check/report.json").write_text(out)
    print(out)
