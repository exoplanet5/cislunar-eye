#!/usr/bin/env python3
"""
helio_nbody.py - point-mass n-body propagation of a heliocentric object from one set of osculating
heliocentric ecliptic-J2000 elements (as JPL Horizons ELEMENTS tables print them), used by the cislunar-eye
extractors to estimate the minimum geocentric distance over a date window when the object's own ephemeris
(Horizons arc, TLE file) stops before that window.

Force model: Sun, Mercury..Neptune system barycentres, Earth and Moon separately (positions from the shared
JPL DE440s kernel in ~/JPLDE, via jplephem).  No solar radiation pressure, no non-gravitational terms, no
relativity: good to roughly 1e-3 AU over a few years for an inert object, much worse across a close Earth
encounter (the result then only says that an encounter happens, not its exact geometry).

Library use:
    from helio_nbody import min_earth_distance, min_earth_distance_geo_state
    d_au, when = min_earth_distance(el, jd_el, dt.datetime(2024,1,1), dt.datetime(2030,12,31))
      el = dict(e, q [km], i, om, w, ma [deg]) heliocentric ecliptic J2000 at Julian date jd_el (TDB ~ UT here)
    d_au, when = min_earth_distance_geo_state(r_km, v_kms, jd0, t0, t1)
      geocentric J2000-equatorial state (e.g. from Bill Gray's get_vect on a TLE) at jd0

Validated 2026-10-21 against Horizons: Lunar Trailblazer (-242) and the GRAIL Delta II stage (-176) propagated
from their 2026-10-21 osculating elements to 2030-12-30 agree with the Horizons geocentric distance to <= 3e-4 AU
(positions to ~0.05-0.16 million km).

CLI (quick check):
    ~/.venvs/astro313/bin/python -I scripts/helio_nbody.py <horizons ELEMENTS csv text file> [start end]
"""
from __future__ import annotations

import datetime as dt
import math
import re
import sys
from pathlib import Path

import numpy as np

KERNEL = Path.home() / "JPLDE" / "de440s.bsp"
AU = 149597870.7
GM_SUN = 1.32712440041279e11
# km^3/s^2 (DE440 values): Mercury..Neptune system barycentres, Earth, Moon
BODIES = [((0, 1), 22031.868551), ((0, 2), 324858.592), ((0, 4), 42828.375816), ((0, 5), 126712764.1),
          ((0, 6), 37940584.8418), ((0, 7), 5794556.4), ((0, 8), 6836527.10058)]
GM_EARTH, GM_MOON = 398600.435507, 4902.800118
EPS = math.radians(23.439279444)          # J2000 obliquity (IAU 1976, as Horizons uses for ECLIPTIC)

_K = None


def _kernel():
    global _K
    if _K is None:
        from jplephem.spk import SPK
        _K = SPK.open(str(KERNEL))
    return _K


def _pos(jd):
    """Barycentric ICRF positions (km) of the perturbers at Julian date jd (TDB)."""
    k = _kernel()
    sun = k[0, 10].compute(jd)
    out = [(sun, GM_SUN)]
    for seg, gm in BODIES:
        out.append((k[seg].compute(jd), gm))
    emb = k[0, 3].compute(jd)
    out.append((emb + k[3, 399].compute(jd), GM_EARTH))
    out.append((emb + k[3, 301].compute(jd), GM_MOON))
    return out


def earth_bary(jd):
    k = _kernel()
    return k[0, 3].compute(jd) + k[3, 399].compute(jd)


def elements_to_state(el, mu=GM_SUN):
    """Heliocentric ecliptic-J2000 elements -> heliocentric ICRF (equatorial) state, km and km/s."""
    e, q = el["e"], el["q"]
    a = q / (1 - e)
    i, om, w, M = (math.radians(el[k]) for k in ("i", "om", "w", "ma"))
    E = M
    for _ in range(50):
        E -= (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
    r = a * (1 - e * math.cos(E))
    xo, yo = a * (math.cos(E) - e), a * math.sqrt(1 - e * e) * math.sin(E)
    n = math.sqrt(mu / a ** 3)
    vxo, vyo = -a * n * math.sin(E) / (1 - e * math.cos(E)), a * n * math.sqrt(1 - e * e) * math.cos(E) / (1 - e * math.cos(E))
    cO, sO, cw, sw, ci, si = math.cos(om), math.sin(om), math.cos(w), math.sin(w), math.cos(i), math.sin(i)
    R = np.array([[cO * cw - sO * sw * ci, -cO * sw - sO * cw * ci],
                  [sO * cw + cO * sw * ci, -sO * sw + cO * cw * ci],
                  [sw * si, cw * si]])
    rec, vec = R @ np.array([xo, yo]), R @ np.array([vxo, vyo])
    ce, se = math.cos(EPS), math.sin(EPS)
    rot = np.array([[1, 0, 0], [0, ce, -se], [0, se, ce]])
    return rot @ rec, rot @ vec


def _acc(jd, x):
    a = np.zeros(3)
    for p, gm in _pos(jd):
        d = p - x
        a += gm * d / np.linalg.norm(d) ** 3
    return a


def propagate(el, jd0, jd1, step_d=0.5):
    """scipy DOP853 integration in the barycentric ICRF frame from heliocentric elements `el` at jd0;
    returns (jd array (daily), barycentric position array)."""
    r_h, v_h = elements_to_state(el)
    k = _kernel()
    sun_p, sun_v = k[0, 10].compute_and_differentiate(jd0)
    return _integrate(np.concatenate([r_h + sun_p, v_h + sun_v / 86400.0]), jd0, jd1, step_d)


def propagate_geo_state(r_geo, v_geo, jd0, jd1, step_d=0.5):
    """Same, from a geocentric ICRF (J2000 equatorial) state in km, km/s (e.g. Bill Gray get_vect output)."""
    k = _kernel()
    ep, ev = k[0, 3].compute_and_differentiate(jd0)
    gp, gv = k[3, 399].compute_and_differentiate(jd0)
    y0 = np.concatenate([np.asarray(r_geo) + ep + gp, np.asarray(v_geo) + (ev + gv) / 86400.0])
    return _integrate(y0, jd0, jd1, step_d)


def min_earth_distance_geo_state(r_geo, v_geo, jd0, t0, t1):
    """Minimum geocentric distance (AU) and date over [t0, t1] starting from a geocentric state at jd0."""
    jds, xs = propagate_geo_state(r_geo, v_geo, jd0, _jd(t1))
    return _min_over(jds, xs, t0)


def _jd(d):
    return 2440587.5 + (d - dt.datetime(1970, 1, 1)).total_seconds() / 86400.0


def _min_over(jds, xs, t0):
    m = jds >= _jd(t0)
    if not m.any():
        return None
    d = np.array([np.linalg.norm(x - earth_bary(j)) for j, x in zip(jds[m], xs[m])]) / AU
    k = int(np.argmin(d))
    return float(d[k]), dt.datetime(1970, 1, 1) + dt.timedelta(days=float(jds[m][k] - 2440587.5))


def _integrate(y0, jd0, jd1, step_d):
    """Barycentric DOP853 integration from jd0 to jd1 (either direction), output once per day."""
    from scipy.integrate import solve_ivp

    def f(t, y):           # t in seconds from jd0
        return np.concatenate([y[3:], _acc(jd0 + t / 86400.0, y[:3])])

    T = (jd1 - jd0) * 86400.0
    n = int(abs(jd1 - jd0)) + 1
    ts = np.linspace(0.0, T, n)
    sol = solve_ivp(f, (0.0, T), y0, method="DOP853", t_eval=ts, rtol=1e-10, atol=1e-3, max_step=step_d * 86400.0)
    return jd0 + sol.t / 86400.0, sol.y[:3].T


def min_earth_distance(el, jd_el, t0, t1):
    """Minimum geocentric distance (AU) and its date over [t0, t1] (datetimes, UT ~ TDB)."""
    jds, xs = propagate(el, jd_el, _jd(t1))
    return _min_over(jds, xs, t0)


def parse_horizons_elements(txt):
    body = txt.split("$$SOE", 1)[1].split("$$EOE", 1)[0]
    r = [c.strip() for c in body.strip().splitlines()[0].split(",")]
    ec, qr, inc, om, w, tp, n, ma = (float(x) for x in r[2:10])
    return dict(e=ec, q=qr, i=inc, om=om, w=w, ma=ma), float(r[0])


if __name__ == "__main__":
    el, jd0 = parse_horizons_elements(Path(sys.argv[1]).read_text())
    t0 = dt.datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else dt.datetime(2024, 1, 1)
    t1 = dt.datetime.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else dt.datetime(2030, 12, 31)
    res = min_earth_distance(el, jd0, t0, t1)
    print(f"min geocentric distance {res[0]:.4f} AU on {res[1]:%Y-%m-%d}" if res else "window not reached")
