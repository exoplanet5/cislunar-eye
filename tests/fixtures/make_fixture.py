#!/usr/bin/env python3
"""Viewer fixture: build/fixture/data/{manifest,core,catalog,targets-*}.js in the ARCHITECTURE.md §11 schema, plus
build/fixture/request/C46FX01.json, a canned answer of the on-request NEOCP helper (GET /api/neocp/C46FX01).

The Moon, Sun and Earth orientation are real (cle.context, DE440s; Skyfield ITRS); the targets are analytic
test objects chosen to exercise every viewer path: a Moon-centred 2 h lunar orbiter, an SE-L2 halo-like loop,
an SE-L1 object, a DRO, a refined cislunar HEO with two astrometry tracklets in its chunk payload (W68, M22), an
EM-L2 object with a gap, a heliocentric flyby (f64), a draw=N listed entry, a tier-2 stale rocket body and a
derelict cislunar HEO stage with a fresh GP (hollow = extrap/stale only, derelict = dim). The NEOCP object C46FX01
(bound geocentric fit, two tracklets G96 / T05) is not in the catalog: it is only the helper response, in the
`cle/neocp-request@1` shape {ok, schema, desig, built_utc, window, object: {cid, catalog, track, fit, caveat,
tracklets, rms_arcsec, request}}, for tests/browser.mjs to route-fulfil http://127.0.0.1:8765/api/neocp/C46FX01?...

built_utc is the window's now, so two runs with the same CLE_NOW are byte-identical (tests/browser.mjs keys its
screenshot baselines on the manifest).

Usage:  CLE_NOW=2026-10-21T13:00:00Z ~/.venvs/astro313/bin/python -I tests/fixtures/make_fixture.py [--out DIR]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cle import catalog  # noqa: E402
from cle.context import GM_EARTH, GM_MOON, MU_EM, MU_SE, RE_KM, RM_KM, Context  # noqa: E402
from cle.timeutil import iso, window  # noqa: E402

MAPPING = np.array([[1., 0., 0.], [0., 0., 1.], [0., -1., 0.]])
DAY = 86400.0
REGIMES = [(k, lab, c) for k, lab, c, _ in catalog.REGIMES]                 # the export's palette (V3)


def b64(a, dtype) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype=np.dtype(dtype).newbyteorder("<")).tobytes()).decode()


def unit(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def collinear(mu: float) -> tuple[float, float, float]:
    """CR3BP collinear gammas: L1, L2 from the secondary; L3 from the primary."""
    def newton(f, df, g):
        for _ in range(60):
            g -= f(g) / df(g)
        return g
    g0 = (mu / 3) ** (1 / 3)
    l1 = newton(lambda g: g**5 - (3 - mu) * g**4 + (3 - 2 * mu) * g**3 - mu * g**2 + 2 * mu * g - mu,
                lambda g: 5 * g**4 - 4 * (3 - mu) * g**3 + 3 * (3 - 2 * mu) * g**2 - 2 * mu * g + 2 * mu, g0)
    l2 = newton(lambda g: g**5 + (3 - mu) * g**4 + (3 - 2 * mu) * g**3 - mu * g**2 - 2 * mu * g - mu,
                lambda g: 5 * g**4 + 4 * (3 - mu) * g**3 + 3 * (3 - 2 * mu) * g**2 - 2 * mu * g - 2 * mu, g0)
    l3 = newton(lambda g: g**5 + (2 + mu) * g**4 + (1 + 2 * mu) * g**3 - (1 - mu) * g**2 - 2 * (1 - mu) * g - (1 - mu),
                lambda g: 5 * g**4 + 4 * (2 + mu) * g**3 + 3 * (1 + 2 * mu) * g**2 - 2 * (1 - mu) * g - 2 * (1 - mu), 1 - 7 * mu / 12)
    return l1, l2, l3


class Fixture:
    def __init__(self, ctx: Context, win):
        self.ctx, self.win = ctx, win

    def basis(self, frame: str, t):
        """(n, 3, 3) rows x̂, ŷ, ẑ of the rotating frame (AM1) in ICRF, and the scale distance (km)."""
        rv = self.ctx.sun_rv(t) if frame == "se" else self.ctx.moon_rv(t)
        r, v = rv[:, :3], rv[:, 3:]
        x = unit(-r) if frame == "se" else unit(r)
        z = unit(np.cross(r, v))
        return np.stack([x, np.cross(z, x), z], axis=1), np.linalg.norm(r, axis=1)

    def rotating(self, frame: str, fun):
        """Geocentric ICRF position function from a rotating-frame one fun(t, d) -> (n, 3)."""
        def pos(t):
            R, d = self.basis(frame, t)
            return np.einsum("nji,nj->ni", R, fun(t, d))
        return pos

    @staticmethod
    def rv(pos, t, h=30.0):
        return np.hstack([pos(t), (pos(t + h) - pos(t - h)) / (2 * h)])


def kepler(t, mu, rp, ra, inc, raan, argp, tp):
    a, e = (rp + ra) / 2, (ra - rp) / (ra + rp)
    n = np.sqrt(mu / a**3)
    M = n * (t - tp)
    E = M.copy()
    for _ in range(40):
        E -= (E - e * np.sin(E) - M) / (1 - e * np.cos(E))
    c, s = np.cos(E), np.sin(E)
    r = a * (1 - e * c)
    pq = np.stack([a * (c - e), a * np.sqrt(1 - e * e) * s, 0 * c], axis=1)
    vq = np.stack([-s, np.sqrt(1 - e * e) * c, 0 * c], axis=1) * (np.sqrt(mu * a) / r)[:, None]
    return np.hstack([pq @ rot(inc, raan, argp).T, vq @ rot(inc, raan, argp).T]), 2 * np.pi / n


def hyperbola(t, mu, rp, vinf, inc, raan, argp, tp):
    a = -mu / vinf**2
    e = 1 - rp / a
    n = np.sqrt(mu / -a**3)
    M = n * (t - tp)
    H = np.arcsinh(M / e)
    for _ in range(60):
        H -= (e * np.sinh(H) - H - M) / (e * np.cosh(H) - 1)
    ch, sh = np.cosh(H), np.sinh(H)
    r = a * (1 - e * ch)
    pq = np.stack([a * (ch - e), -a * np.sqrt(e * e - 1) * sh, 0 * ch], axis=1)
    vq = np.stack([-sh, np.sqrt(e * e - 1) * ch, 0 * ch], axis=1) * (np.sqrt(-mu * a) / r)[:, None]
    return np.hstack([pq @ rot(inc, raan, argp).T, vq @ rot(inc, raan, argp).T])


def rot(inc, raan, argp):
    def rz(a):
        c, s = np.cos(a), np.sin(a)
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    c, s = np.cos(inc), np.sin(inc)
    return rz(np.radians(raan)) @ np.array([[1, 0, 0], [0, c, -s], [0, s, c]]) @ rz(np.radians(argp))


def segment(t, rv, src, quality, join="start", dtype="f32", sigma=None, jump=0.0):
    return {"_f64": dtype == "f64", "t0": iso(t[0]), "t0_ms": int(round(t[0] * 1000)), "step_s": float(t[1] - t[0]), "n": len(t),
            "rv": b64(rv, np.float32 if dtype == "f32" else np.float64),
            "sigma_km": None if sigma is None else b64(sigma, np.float32),
            "src": src, "quality": quality, "join_before": join, "jump_km": jump}


def track(center, segs):
    kinds = {s.pop("_f64") for s in segs}
    assert len(kinds) == 1, "mixed f32/f64 segments"
    dtype = "f64" if kinds.pop() else "f32"
    return {"center": center, "dtype": dtype, "segments": segs}


def register(path: Path, name: str, payload: dict) -> dict:
    text = f'CLE.register({json.dumps(name)}, {json.dumps(payload, separators=(",", ":"), ensure_ascii=False)});\n'
    tmp = path.with_name(f".{path.name}")
    tmp.write_text(text)
    tmp.replace(path)
    return {"name": name, "file": path.name, "bytes": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest()}


def tracklets_of(ctx, pos, stations, t_obs, mags, resid):
    """§7 tracklets of the geocentric orbit pos(t) -> (n, 3) km seen from ObsCodes (lon, rho cos phi', rho sin phi')
    stations at the epochs t_obs (one array per station): light-time epochs, observed RA/Dec, stn_km / dot_km."""
    from skyfield.framelib import itrs
    out = []
    for (code, (lon, rc, rs)), te, mag, res in zip(stations.items(), t_obs, mags, resid):
        R = itrs.rotation_at(ctx.times(te))                             # (3, 3, n) ICRF -> ITRS
        lam = np.radians(lon)
        itrs_km = RE_KM * np.array([rc * np.cos(lam), rc * np.sin(lam), rs])
        stn = np.einsum("jin,j->ni", R, itrs_km)
        obj = pos(te)
        rho = np.linalg.norm(obj - stn, axis=1)
        tl = te - rho / 299792.458
        dot = pos(tl)
        u = unit(dot - stn)
        out.append({"stn": code, "n": len(te), "t_ms": [int(x * 1000) for x in tl],
                    "ra_deg": list(np.degrees(np.arctan2(u[:, 1], u[:, 0])) % 360), "dec_deg": list(np.degrees(np.arcsin(u[:, 2]))),
                    "mag": mag, "range_km": [round(float(x), 3) for x in np.linalg.norm(dot - stn, axis=1)],
                    "stn_km": b64(stn, np.float64), "dot_km": b64(dot, np.float64), "resid_arcsec": res})
    return out


def target(cid, name, label, group, regime, obj_type, tier, chunk, *, status="active", draw=True, caveat="",
           sources=(), elements=None, trail=None, notes="", aliases=(), norad=None, cospar="", horizons_id=""):
    return {"id": cid, "name": name, "label": label, "aliases": list(aliases), "norad": norad, "cospar": cospar,
            "horizons_id": horizons_id, "group": group, "obj_type": obj_type, "regime": regime, "regime_now": regime,
            "status": status, "status_date": "2026-10-20", "tier": tier, "default_visible": tier == 1 and draw,
            "draw": draw, "caveat": caveat, "chunk": chunk if draw else None, "sources": list(sources),
            "elements": elements, "trail": trail, "notes": notes, "links": []}


def neocp_request(ctx, win, built: str, window: dict) -> dict:
    """GET /api/neocp/C46FX01 as the on-request NEOCP helper answers it (`cle/neocp-request@1`): a bound geocentric
    find_orb fit (fit.mode "fo") of two tracklets (G96, T05), first obs - 2 d .. now + 7 d, quality fit over the arc
    +/- 1 d and extrap beyond, the fo sigma growing to 9,000 km; the track in the export.track shape (§11), the catalog
    entry in catalog.entry semantics (id = desig.lower(), exact-case name / label, chunk None: the track travels here);
    fit = cle.adapters.neocp.fit_block keys; request = the helper's provenance block (cle.request.build).
    tests/test_request.py FixtureShapeTest checks these key sets against a real cle.request.respond answer."""
    from cle import tracklets as tk
    from cle.export import track as export_track
    from cle.statetable import Segment
    now, desig, cid = win.now_s, "C46FX01", "c46fx01"
    url = f"https://www.projectpluto.com/neocp2/mpecs/{desig}.htm"
    t_obs = [now - 2 * DAY - 5 * 3600 + 600 * np.arange(5), now - 1 * DAY - 3 * 3600 + 900 * np.arange(5)]
    first, last = t_obs[0][0], t_obs[-1][-1]
    tn_a = np.arange(first - 2 * DAY, first - DAY + 1, 3600.0)
    tn_b = np.arange(first - DAY, last + DAY + 1, 600.0)
    tn_c = np.arange(tn_b[-1], now + 7 * DAY + 1, 3600.0)
    orbit = dict(mu=GM_EARTH, rp=60e3, ra=500e3, inc=np.radians(12), raan=300, argp=40, tp=now - 0.5 * DAY)

    def seg(t, quality, join, sigma):
        rv = kepler(t, **orbit)[0]
        return Segment(float(t[0]), float(t[1] - t[0]), rv[:, :3], rv[:, 3:], f"fo:neocp:{desig}", quality, join,
                       sigma_km=np.asarray(sigma, np.float32))
    sig_c = np.linspace(25, 9000, len(tn_c))
    trk = export_track([seg(tn_a, "extrap", "start", np.linspace(400, 40, len(tn_a))),
                        seg(tn_b, "fit", "continuous", np.full(len(tn_b), 25.0)),
                        seg(tn_c, "extrap", "continuous", sig_c)], "earth")
    stations = {"G96": (249.21128, 0.845111, 0.533614), "T05": (203.7424, 0.936241, 0.351543)}
    resid = [[0.21, -0.12], [-0.30, 0.08], [0.05, 0.27], [0.11, -0.18], [-0.09, 0.02]]
    tracklets = tracklets_of(ctx, lambda t: kepler(t, **orbit)[0][:, :3], stations, t_obs,
                             [[20.1, 20.0, 20.2, 20.1, 19.9]] * 2, [resid] * 2)
    rms = round(tk.rms(tracklets), 3)
    created = iso(now - 25 * 60 + 6)                                       # Bill Gray regenerates at hh:35
    span = f"[{iso(first)[:10]}..{iso(last)[:10]}]"
    # the helper's caveat (adapters/neocp.py fo_track) states the fit's rms (fit.rms_arcsec, as the NEOCP REQUEST card)
    # and where the track turns extrapolated (the end of the "fit" segment tn_b below)
    caveat = (f"find_orb fit of 10 NEOCP obs {iso(first)[:16]}..{iso(last)[:16]}Z (rms 0.19″); "
              f"extrapolated beyond {iso(last + DAY)[:10]}")
    row = {"cid": cid, "name": desig, "label": desig, "aliases": "", "norad": "", "cospar": "", "horizons_id": "",
           "group": "NEOCP · requested", "obj_type": "OTHER", "regime": "NEOCP_GEO", "status": "unknown",
           "status_date": iso(last)[:10], "tier": 1, "tier_reason": "requested from the NEOCP", "draw": "Y", "chunk": catalog.CHUNK["NEOCP_GEO"],
           "centre": "earth", "default_visible": True, "caveat": caveat, "primary_ephem": f"pluto_mpec:{url}{span}",
           "sources": [{"ephem_sources": f"pluto_mpec:{url}{span};neocp_obs:mpc-api"}], "elem_epoch": iso(now),
           "frame_center": "earth", "a_km": "280000", "e": "0.785714", "incl_deg": "12", "perigee_km": "60000",
           "apogee_km": "500000", "period_d": "17.07"}
    entry = catalog.entry(row, "NEOCP_GEO", True)
    entry |= {"chunk": None, "trail": {"past_s": round(now - first + 0.5 * DAY), "future_s": round(DAY)},
              "links": entry["links"] + [{"label": "MPC NEOCP",
                                          "url": f"https://cgi.minorplanetcenter.net/cgi-bin/showobsorbs.cgi?Obj={desig}&obs=y"}]}
    jd_tt = 2440587.5 + (now + 69.184) / DAY
    elements = {"epoch_jd_tt": round(jd_tt, 6), "peri_word": "Perigee", "frame": "J2000 equator", "q": 60000.0,
                "e": 0.785714, "incl": 12.0, "node": 300.0, "peri": 40.0, "n_obs": 10, "n_used": 10, "mean_resid_arcsec": 0.19}
    fit = {"n_obs": 10, "n_used": 10, "rms_arcsec": 0.19, "arc_h": round((last - first) / 3600, 3),
           "arc_d": round((last - first) / DAY, 4), "stations": sorted(stations),
           "obs_first": iso(first), "obs_last": iso(last), "epoch": iso(now), "epoch_jd_tt": elements["epoch_jd_tt"],
           "elements": elements, "page": {"n_obs": 10, "n_used": 10, "rms_arcsec": 0.19, "created": created},
           "geo_score": None, "geo": True, "flyby": False, "srp": False, "pluto_url": url, "find_orb_ver": "2026 Mar 01",
           "fo_args": ["-h", "3"], "page_check": {"fit_disagrees": False, "dr_page_sv_km": 0.4, "table_max_sigma_ratio": 0.6,
                                                   "table_max_sep_arcsec": 0.31, "dq_sigma": 0.01, "de_sigma": 0.0, "di_sigma": 0.01},
           "fo_consistency": None, "sigma_km_now": round(float(np.interp(now, tn_c, sig_c)), 1),
           "sigma_limit_utc": iso(tn_c[-1]), "fallback": None, "mode": "fo"}
    return {"ok": True, "schema": "cle/neocp-request@1", "desig": desig, "built_utc": built,
            "window": {"start_utc": window["start_utc"], "stop_utc": window["stop_utc"]},
            "object": {"cid": cid, "catalog": entry, "track": trk, "fit": fit, "caveat": caveat, "tracklets": tracklets,
                       "rms_arcsec": rms,
                       "request": {"desig": desig, "fetched_utc": built, "page_created_utc": created, "n_obs_mpc": 10,
                                   "n_obs_page": 10, "n_obs_used": 10, "on_neocp": True, "mode": "fo", "seconds": 2.1,
                                   "cached": False, "warnings": [], "find_orb": True}}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "build/fixture/data")
    a = ap.parse_args()
    win, ctx = window(), Context()
    fx = Fixture(ctx, win)
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    now = win.now_s
    built = iso(now)

    # core: Moon, Sun on the 1 h grid; Earth orientation quaternions (MMX ephemeris.py:122-127)
    from scipy.spatial.transform import Rotation
    from skyfield.framelib import itrs
    tc = win.grid(3600)
    tt = ctx.times(tc)
    fixed_to_icrf = np.moveaxis(itrs.rotation_at(tt), 2, 0).transpose(0, 2, 1)
    q = Rotation.from_matrix(MAPPING @ fixed_to_icrf @ MAPPING.T).as_quat()
    for i in range(1, len(q)):
        if np.dot(q[i - 1], q[i]) < 0:
            q[i] *= -1
    core = {"schema": "cle/core@1", "meta": {
        "built_utc": built, "now_utc": iso(now), "window": {"start_utc": iso(win.t0_s), "stop_utc": iso(win.t1_s)},
        "frame": "ICRF", "state_type": "geometric", "time": "UTC (no leap second in window)",
        "units": {"pos": "km", "vel": "km/s"},
        "scene": {"mapping": "(X,Y,Z) -> (X,Z,-Y) / 6378.137 km; right handed", "unit_km": RE_KM},
        "earth_orientation": "Skyfield ITRS, builtin timescale, no polar motion",
        "kernel": {"path": "~/JPLDE/de440s.bsp", "sha256": ctx.kernel_sha256},
        "software": {"python": sys.version.split()[0], "fixture": "tests/fixtures/make_fixture.py", "three": "r170"},
        "mu": {"em": MU_EM, "se": MU_SE}, "gm": {"earth": GM_EARTH, "moon": GM_MOON}},
        "earth": {"radius_km": RE_KM, "quat": {"t0_ms": int(tc[0] * 1000), "step_s": 3600.0, "n": len(tc), "xyzw": b64(q, np.float64)}},
        "moon": {"radius_km": RM_KM, "track": track("earth", [segment(tc, ctx.moon_rv(tc), "de440s:301", "ref", dtype="f64")])},
        "sun": {"track": track("earth", [segment(tc, ctx.sun_rv(tc), "de440s:10", "ref", dtype="f64")])}}

    targets, chunks, catalog = {}, {}, []
    g1se, g2se, _ = collinear(MU_SE)
    g1em, g2em, _ = collinear(MU_EM)

    def add(entry, trk, chunk, events=(), tracklets=()):
        catalog.append(entry)
        chunks.setdefault(chunk, {})[entry["id"]] = {
            "track": trk, "events": list(events), "validation": {"midpoint_max_km": 0.0, "moon_check_km": None},
            "provenance": [{"src": s["src"], "retrieved_utc": built, "raw": "tests/fixtures/make_fixture.py", "sha256": None}
                           for s in trk["segments"]]}
        if tracklets:                                                   # identified target's astrometry (cle.astrometry)
            chunks[chunk][entry["id"]]["tracklets"] = list(tracklets)

    # 1. Moon-centred 2 h circular polar lunar orbiter, 5 min grid, ref -> pred at now
    T = 7200.0
    r = (GM_MOON * (T / 2 / np.pi) ** 2) ** (1 / 3)
    w = 2 * np.pi / T

    def lunar(t):
        ph = w * (t - win.t0_s)
        return np.stack([r * np.cos(ph), 0 * ph, r * np.sin(ph), -r * w * np.sin(ph), 0 * ph, r * w * np.cos(ph)], axis=1)
    ta, tb = win.grid(300, win.t0_s, now), win.grid(300, now, win.t1_s)
    add(target("fx-lunar-2h", "Fixture lunar orbiter (2 h polar)", "FX-LLO", "Lunar orbit", "LUNAR_ORBIT", "payload", 1, "targets-lunar",
               horizons_id="-9001", sources=[{"kind": "horizons", "ref": "-9001", "start": iso(win.t0_s), "end": iso(win.t1_s), "primary": True}],
               elements={"epoch": iso(now), "center": "moon", "a_km": r, "e": 0.0, "i_deg": 90.0, "peri_km": r, "apo_km": r, "period_d": T / DAY}),
        track("moon", [segment(ta, lunar(ta), "horizons:-9001", "ref"), segment(tb, lunar(tb), "horizons:-9001", "pred", "continuous")]), "targets-lunar")

    # 2. SE-L2 halo-like Lissajous loop (28 d) in the Sun–Earth rotating frame, 6 h, f64
    def halo(t, d, gam=g2se, P=28 * DAY, A=(120e3, 400e3, 250e3), sign=1):
        ph = 2 * np.pi * (t - win.t0_s) / P
        return np.stack([sign * gam * d + A[0] * np.cos(ph), A[1] * np.sin(ph), A[2] * np.cos(ph + 0.6)], axis=1)
    pos = fx.rotating("se", halo)
    ta, tb = win.grid(21600, win.t0_s, now), win.grid(21600, now, win.t1_s)
    add(target("fx-se-l2", "Fixture SE-L2 halo", "FX-L2", "Sun–Earth L2", "SE_L2", "payload", 1, "targets-se", horizons_id="-9002",
               sources=[{"kind": "horizons", "ref": "-9002", "start": iso(win.t0_s), "end": iso(win.t1_s), "primary": True}]),
        track("earth", [segment(ta, fx.rv(pos, ta), "horizons:-9002", "ref", dtype="f64"),
                        segment(tb, fx.rv(pos, tb), "horizons:-9002", "pred", "continuous", dtype="f64")]), "targets-se")

    # 3. SE-L1 object: fit to now - 2 d, then extrapolated (find_orb style), 6 h, f64
    pos = fx.rotating("se", lambda t, d: halo(t, d, g1se, 25 * DAY, (60e3, 260e3, 120e3), -1))
    ta, tb = win.grid(21600, win.t0_s, now - 2 * DAY), win.grid(21600, now - 2 * DAY, win.t1_s)
    sig = 2.0 + 0.8 * (tb - tb[0]) / 3600
    add(target("fx-se-l1", "Fixture SE-L1 object", "FX-L1", "Sun–Earth L1", "SE_L1", "payload", 1, "targets-se",
               caveat="find_orb fit, arc to 2026-10-19",
               sources=[{"kind": "neocpsat_fit", "ref": "FX-L1@2026-10-19", "start": iso(win.t0_s), "end": iso(now - 2 * DAY), "primary": True}]),
        track("earth", [segment(ta, fx.rv(pos, ta), "fo:FX-L1@2026-10-19", "fit", dtype="f64"),
                        segment(tb, fx.rv(pos, tb), "fo:FX-L1@2026-10-19", "extrap", "continuous", dtype="f64", sigma=sig)]), "targets-se")

    # 4. DRO: retrograde 2:1 ellipse about the Moon in the Earth–Moon rotating frame (13.6 d), GP-state integration, 1 h
    def dro(t, d, P=13.6 * DAY):
        ph = 2 * np.pi * (t - win.t0_s) / P
        return np.stack([d + 35e3 * np.cos(ph), -70e3 * np.sin(ph), 4e3 * np.sin(ph)], axis=1)
    pos = fx.rotating("em", dro)
    tt1 = win.grid(3600)
    add(target("fx-dro", "Fixture DRO", "FX-DRO", "Earth–Moon libration, DRO & resonant", "EM_DRO", "payload", 1, "targets-em", norad=99003,
               caveat="Space-Track GP 2026-10-12 (age 9 d); CR3BP orbit: integrated from the GP state",
               sources=[{"kind": "spacetrack_gp", "ref": "99003", "start": "2026-10-12T00:00:00Z", "end": "2026-10-12T00:00:00Z", "primary": True}],
               elements={"epoch": "2026-10-12T00:00:00Z", "center": "earth", "a_km": 384e3, "e": 0.1, "i_deg": 5.0, "peri_km": 3.5e5,
                         "apo_km": 4.2e5, "period_d": 27.3}),
        track("earth", [segment(tt1, fx.rv(pos, tt1), "gp:99003", "gp")]), "targets-em")

    # 5. Cislunar HEO, extrapolated latest orbit (IBEX-like), 1 h with 10 min refinement around perigee
    mu = GM_EARTH
    tp0 = now - 1.3 * DAY
    _, P = kepler(np.array([0.0]), mu, 25e3, 320e3, np.radians(30), 40, 70, tp0)
    peris = [tp for tp in tp0 + P * np.arange(-5, 6) if win.t0_s < tp < win.t1_s]
    hour = lambda x: min(max(win.t0_s + 3600 * round((x - win.t0_s) / 3600), win.t0_s), win.t1_s)
    edges = sorted({win.t0_s, win.t1_s, *(hour(tp + s) for tp in peris for s in (-6 * 3600, 6 * 3600))})
    segs = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        step = 600.0 if any(lo <= tp <= hi for tp in peris) else 3600.0
        t = lo + step * np.arange(round((hi - lo) / step) + 1)
        segs.append(segment(t, kepler(t, mu, 25e3, 320e3, np.radians(30), 40, 70, tp0)[0], "extrap:bgtle:fx08051a25.tle", "extrap",
                            "continuous" if segs else "start"))
    heo_obs = [now - 1.6 * DAY + 900 * np.arange(4), now - 0.7 * DAY + 900 * np.arange(4)]       # ATLAS south, two nights
    heo_tk = tracklets_of(ctx, lambda t: kepler(t, mu, 25e3, 320e3, np.radians(30), 40, 70, tp0)[0][:, :3],
                          {"W68": (289.23502, 0.862845, -0.504269), "M22": (20.81059, 0.845564, -0.532612)}, heo_obs,
                          [[18.4, 18.5, 18.3, 18.4], [18.9, 18.8, 19.0, 18.9]],
                          [[[0.31, -0.22], [-0.12, 0.18], [0.05, -0.09], [-0.21, 0.14]], [[0.42, 0.11], [-0.35, -0.27], [0.08, 0.30], [0.15, -0.06]]])
    add(target("fx-heo", "Fixture cislunar HEO (IBEX-like)", "FX-HEO", "Cislunar HEO (apogee ≥ 100 000 km)", "CISLUNAR_HEO", "payload", 1,
               "targets-cislunar", norad=99005,
               caveat="latest orbit = Bill Gray TLE fit ending 2026-01-01, integrated (Earth+Moon+Sun); position uncertain",
               sources=[{"kind": "bgtle", "ref": "fx08051a25.tle", "start": "2025-01-01T00:00:00Z", "end": "2026-01-01T00:00:00Z", "primary": True}],
               elements={"epoch": "2026-01-01T00:00:00Z", "center": "earth", "a_km": 172.5e3, "e": 0.855, "i_deg": 30.0, "peri_km": 25e3,
                         "apo_km": 320e3, "period_d": P / DAY}),
        track("earth", segs), "targets-cislunar", tracklets=heo_tk)

    # 6. EM-L2 object: fit, a 1-day gap, then GP (EM rotating frame halo, 14 d)
    def eml2(t, d, P=14 * DAY):
        ph = 2 * np.pi * (t - win.t0_s) / P
        return np.stack([(1 - MU_EM + g2em + MU_EM) * d + 12e3 * np.cos(ph), 30e3 * np.sin(ph), 50e3 * np.cos(ph)], axis=1)
    pos = fx.rotating("em", eml2)
    ta, tb = win.grid(1800, win.t0_s, now - 5 * DAY), win.grid(1800, now - 4 * DAY, win.t1_s)
    add(target("fx-em-l2", "Fixture EM-L2 halo", "FX-EML2", "Earth–Moon libration, DRO & resonant", "EM_L2", "payload", 1, "targets-em",
               caveat="fit to 2026-10-16, gap, then Space-Track GP"),
        track("earth", [segment(ta, fx.rv(pos, ta), "fo:FX-EML2@2026-10-16", "fit"),
                        segment(tb, fx.rv(pos, tb), "gp:99006", "gp", "gap")]), "targets-em")

    # 7. Heliocentric Earth flyby (returning stage): geocentric hyperbola, perigee 9e5 km at now + 4 d, 6 h, f64
    t6 = win.grid(21600)
    add(target("fx-flyby", "Fixture returning stage (flyby)", "FX-RET", "Earth-return / near-Earth heliocentric", "EARTH_NEAR_HELIO", "rocket_body", 1,
               "targets-helio", status="returning", horizons_id="DES=FX1",
               sources=[{"kind": "horizons", "ref": "DES=FX1", "start": iso(win.t0_s), "end": iso(win.t1_s), "primary": True}]),
        track("earth", [segment(t6, hyperbola(t6, mu, 9e5, 0.8, np.radians(20), 120, 30, now + 4 * DAY), "horizons:DES=FX1", "pred", dtype="f64")]),
        "targets-helio")

    # 8. draw=N listed entry
    catalog.append(target("fx-listed", "Fixture listed relay (no ephemeris)", "FX-LIST", "Lunar orbit", "LUNAR_ORBIT", "payload", 1, None, draw=False,
                          caveat="no public ephemeris (lunar ELFO, 24 h); listed, not drawn",
                          elements={"epoch": "2026-09-01T00:00:00Z", "center": "moon", "a_km": 8000, "e": 0.68, "i_deg": 55,
                                    "peri_km": 2560, "apo_km": 13440, "period_d": 1.0}))

    # 10. Tier-2 stale rocket body
    rv, _ = kepler(tt1, mu, 8e3, 380e3, np.radians(28), 200, 180, now - 3 * DAY)
    add(target("fx-stale-rb", "Fixture stale R/B", "FX-RB", "Cislunar HEO (apogee ≥ 100 000 km)", "CISLUNAR_HEO", "rocket_body", 2, "targets-cislunar",
               status="derelict", caveat="stale: latest orbit ends 2026-06-01; extrapolated",
               elements={"epoch": "2026-06-01T00:00:00Z", "center": "earth", "a_km": 194e3, "e": 0.959, "i_deg": 28, "peri_km": 8e3,
                         "apo_km": 380e3, "period_d": 9.9}),
        track("earth", [segment(tt1, rv, "extrap:gp:99010", "extrap")]), "targets-cislunar")

    # 11. Derelict cislunar HEO stage with a fresh GP (9 d): a second HEO trail for the idle-dim / hover tests, drawn dim, not hollow
    rv, P = kepler(tt1, mu, 10e3, 250e3, np.radians(45), 150, 300, now - 0.4 * DAY)
    add(target("fx-derelict", "Fixture derelict stage (HEO)", "FX-DEREL", "Cislunar HEO (apogee ≥ 100 000 km)", "CISLUNAR_HEO", "rocket_body", 1,
               "targets-cislunar", norad=99012, status="derelict",
               sources=[{"kind": "spacetrack_gp", "ref": "99012", "start": "2026-10-12T00:00:00Z", "end": "2026-10-12T00:00:00Z", "primary": True}],
               elements={"epoch": "2026-10-12T00:00:00Z", "center": "earth", "a_km": 130e3, "e": 0.923, "i_deg": 45.0, "peri_km": 10e3,
                         "apo_km": 250e3, "period_d": P / DAY}),
        track("earth", [segment(tt1, rv, "gp:99012", "gp")]), "targets-cislunar")

    # 12. NEOCP C46FX01, on request only: bound geocentric fit with two tracklets (G96, T05) as the helper's answer
    request = neocp_request(ctx, win, built, core["meta"]["window"])

    trail = {"LUNAR_ORBIT": lambda e: (min(DAY, e["period_d"] * DAY if e else DAY), 0.25 * min(DAY, e["period_d"] * DAY if e else DAY)),
             "SE_L1": lambda e: (30 * DAY, 30 * DAY), "SE_L2": lambda e: (30 * DAY, 30 * DAY), "EARTH_NEAR_HELIO": lambda e: (30 * DAY, 30 * DAY)}
    for e in catalog:
        if e["regime"] in trail:
            p, f = trail[e["regime"]](e["elements"])
            e["trail"] = {"past_s": p, "future_s": f}
    order = {k: i for i, (k, *_) in enumerate(REGIMES)}
    catalog.sort(key=lambda e: (order[e["regime"]], e["tier"], e["name"]))
    files = [register(out / "core.js", "core", core),
             register(out / "catalog.js", "catalog", {"schema": "cle/catalog@1", "built_utc": built,
                                                      "regimes": [{"key": k, "label": lab, "order": i, "color": c} for i, (k, lab, c) in enumerate(REGIMES)],
                                                      "targets": catalog})]
    for name, objs in sorted(chunks.items()):
        files.append(register(out / f"{name}.js", name, {"schema": "cle/targets@1", "built_utc": built, "targets": objs}))
    (out / "neocp.js").unlink(missing_ok=True)                          # retired sidecar of older fixtures
    register(out / "manifest.js", "manifest", {"schema": "cle/manifest@1", "built_utc": built, "now_utc": iso(now),
                                               "window": core["meta"]["window"], "files": files, "chunks": sorted(chunks)})
    req = out.parent / "request" / "C46FX01.json"
    req.parent.mkdir(parents=True, exist_ok=True)
    req.write_text(json.dumps(request, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(f"fixture {out}: window {iso(win.t0_s)} .. {iso(win.t1_s)}, {len(catalog)} catalog rows, chunks {sorted(chunks)}, "
          f"{sum(f['bytes'] for f in files) / 1e6:.2f} MB; helper response {req}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
