"""Numerical checks behind work/notes/architecture_review.md (review of ARCHITECTURE.md).

Pure computation: no network, no downloads, no sibling-project access. Re-running prints the same
numbers (idempotent). Each check is labelled with the ARCHITECTURE.md section it tests.

Checks
  A  window epoch t0_ms (§6.1)
  B  frame: angle between skyfield TEME->GCRS and IAU-1976 precession-only of-date->J2000 (§5.5),
     plus the IAU 2000A nutation magnitude sqrt(deps^2 + (dpsi sin eps)^2), at the window "now" and at
     the Artemis II TLE epoch used by §14 test 3
  C  cubic-Hermite midpoint error and straight-chord sag between native samples (§6.2, §9.4) for:
       LLO 100 km circular @ 5 min; LRO-like 30x180 km @ 5 min; NRHO-like selenocentric ellipse
       (rp 3,340 km, ra 71,700 km) carried by a circular Moon @ 30 min (Earth-centred, EM_L2 base
       step); HEO 200 km x 400,000 km at the omega-rule step; lunar flyby (rp 8,280 km, v_inf 1 km/s)
       carried by the Moon @ 1 h (TRANSLUNAR base step). Also the omega-rule value h*omega computed
       about Earth (what §6.2 specifies) and about the Moon.
  D  float32 spacing at representative radii and for trail time attributes (§6.2, §9.4)
  E  star appearance (§9.8) against the 6 px marker size (§9.3)
  F  find_orb state-vector JD print precision (%.5f, ephem0.cpp:2697) as a position error
  G  sidecar / GPU sizes (§15) and the cost of densifying trails to a chord tolerance

Models are two-body / kinematic superpositions: good for interpolation-error magnitudes, not for
ephemerides. Usage:
    ~/.venvs/astro313/bin/python -I scripts/arch_review_checks.py
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np

MU_E = 398600.4418      # km^3/s^2
MU_M = 4902.800066
R_MOON = 1737.4
A_MOON = 384400.0
ASEC = 180 / math.pi * 3600


# ---------------------------------------------------------------- A
def check_a():
    t0 = datetime(2026, 9, 21, tzinfo=timezone.utc)
    return {"t0_ms": int(t0.timestamp() * 1000)}


# ---------------------------------------------------------------- B
def iau1976_precession(jd_tt):
    """J2000 mean -> mean-of-date rotation (Lieske 1977), as Bill Gray's setup_precession."""
    t = (jd_tt - 2451545.0) / 36525.0
    zeta = (2306.2181 * t + 0.30188 * t * t + 0.017998 * t ** 3) / ASEC
    z = (2306.2181 * t + 1.09468 * t * t + 0.018203 * t ** 3) / ASEC
    theta = (2004.3109 * t - 0.42665 * t * t - 0.041833 * t ** 3) / ASEC

    def r3(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]])

    def r2(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, 0, -s], [0, 1, 0], [s, 0, c]])

    return r3(-z) @ r2(theta) @ r3(-zeta)


def rot_angle(m):
    return math.acos(max(-1.0, min(1.0, (np.trace(m) - 1) / 2)))


def check_b():
    from skyfield.api import load
    from skyfield.nutationlib import iau2000a_radians, mean_obliquity
    from skyfield.sgp4lib import TEME

    ts = load.timescale(builtin=True)
    out = {}
    for label, t in (("2026-10-21T00Z", ts.utc(2026, 10, 21)),
                     ("2026-04-06T23:58:50Z (26069a TLE epoch)", ts.utc(2026, 4, 6, 23, 58, 50.8)),
                     ("2026-07-02T00Z (26109a SMILE elements)", ts.utc(2026, 7, 2))):
        gcrs_to_teme = TEME.rotation_at(t)
        teme_to_gcrs = gcrs_to_teme.T
        p = iau1976_precession(t.tt)          # J2000 -> MOD
        resid = teme_to_gcrs @ p              # maps a J2000 vector (via MOD) back through TEME->GCRS
        dpsi, deps = iau2000a_radians(t)
        eps = mean_obliquity(t.tdb) / ASEC * math.pi / 180 * 3600 if False else math.radians(mean_obliquity(t.tdb) / 3600)
        nut = math.hypot(deps, dpsi * math.sin(eps)) * ASEC
        ang = rot_angle(resid) * ASEC
        out[label] = {
            "teme2gcrs_vs_precession_only_arcsec": round(ang, 3),
            "nutation_sqrt_deps2_dpsi_sin_eps2_arcsec": round(nut, 3),
            "dpsi_arcsec": round(dpsi * ASEC, 3), "deps_arcsec": round(deps * ASEC, 3),
            "km_at_121000": round(ang / ASEC * 121000, 2),
            "km_at_384400": round(ang / ASEC * 384400, 2),
            "km_at_1.5e6": round(ang / ASEC * 1.5e6, 2),
        }
    return out


# ---------------------------------------------------------------- C
def kepler_ellipse(mu, rp, ra, t):
    """Perifocal 3D state at times t (s from periapsis)."""
    a = (rp + ra) / 2
    e = (ra - rp) / (ra + rp)
    n = math.sqrt(mu / a ** 3)
    m = n * np.asarray(t, float)
    ea = m.copy()
    for _ in range(50):
        ea = ea - (ea - e * np.sin(ea) - m) / (1 - e * np.cos(ea))
    cE, sE = np.cos(ea), np.sin(ea)
    b = a * math.sqrt(1 - e * e)
    x = a * (cE - e)
    y = b * sE
    edot = n / (1 - e * cE)
    vx = -a * sE * edot
    vy = b * cE * edot
    z = np.zeros_like(x)
    return np.stack([x, y, z], -1), np.stack([vx, vy, z], -1), 2 * math.pi / n


def kepler_hyper(mu, rp, vinf, t):
    a = mu / vinf ** 2                      # a > 0 convention
    e = 1 + rp / a
    n = math.sqrt(mu / a ** 3)
    m = n * np.asarray(t, float)
    h = np.arcsinh(m / e)
    for _ in range(80):
        h = h - (e * np.sinh(h) - h - m) / (e * np.cosh(h) - 1)
    ch, sh = np.cosh(h), np.sinh(h)
    b = a * math.sqrt(e * e - 1)
    x = a * (e - ch)
    y = b * sh
    hdot = n / (e * ch - 1)
    vx = -a * sh * hdot
    vy = b * ch * hdot
    z = np.zeros_like(x)
    return np.stack([x, y, z], -1), np.stack([vx, vy, z], -1)


def moon_circ(t, phase=0.0, incl_deg=5.0):
    w = math.sqrt((MU_E + MU_M) / A_MOON ** 3)
    th = w * np.asarray(t, float) + phase
    ci = math.cos(math.radians(incl_deg))
    si = math.sin(math.radians(incl_deg))
    r = np.stack([A_MOON * np.cos(th), A_MOON * np.sin(th) * ci, A_MOON * np.sin(th) * si], -1)
    v = np.stack([-A_MOON * w * np.sin(th), A_MOON * w * np.cos(th) * ci, A_MOON * w * np.cos(th) * si], -1)
    return r, v


def tilt(r, deg=80.0):
    """Rotate perifocal plane about x so the selenocentric orbit is near-polar wrt the Moon's orbit."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    return r @ m.T


def hermite_mid(p0, v0, p1, v1, h, u):
    h00 = 2 * u ** 3 - 3 * u ** 2 + 1
    h10 = u ** 3 - 2 * u ** 2 + u
    h01 = -2 * u ** 3 + 3 * u ** 2
    h11 = u ** 3 - u ** 2
    return (h00[:, None] * p0 + h10[:, None] * h * v0 + h01[:, None] * p1 + h11[:, None] * h * v1)


def interp_errors(state_fn, t_nodes, n_sub=21):
    """Max Hermite error and max chord sag (true path vs straight node-to-node segment)."""
    r, v = state_fn(t_nodes)
    h = t_nodes[1] - t_nodes[0]
    worst_h, worst_c, where_h = 0.0, 0.0, None
    u = np.linspace(0, 1, n_sub)[1:-1]
    for i in range(len(t_nodes) - 1):
        tt = t_nodes[i] + u * h
        rt, _ = state_fn(tt)
        rh = hermite_mid(r[i], v[i], r[i + 1], v[i + 1], h, u)
        eh = np.linalg.norm(rh - rt, axis=1).max()
        rc = r[i] + u[:, None] * (r[i + 1] - r[i])
        ec = np.linalg.norm(rc - rt, axis=1).max()
        if eh > worst_h:
            worst_h, where_h = eh, float(t_nodes[i])
        worst_c = max(worst_c, ec)
    return worst_h, worst_c, where_h


def check_c():
    out = {}

    def sel_case(name, rp, ra, h):
        def fn(t):
            r, v, _ = kepler_ellipse(MU_M, rp, ra, t)
            return r, v
        _, _, period = kepler_ellipse(MU_M, rp, ra, [0.0])
        tn = np.arange(-period / 2, period / 2 + h, h)
        eh, ec, _ = interp_errors(fn, tn)
        rv = fn(np.array([0.0]))
        om = np.linalg.norm(np.cross(rv[0][0], rv[1][0])) / np.linalg.norm(rv[0][0]) ** 2
        out[name] = {"period_h": round(period / 3600, 3), "step_s": h, "hermite_max_km": round(eh, 4),
                     "chord_sag_max_km": round(ec, 3), "h_omega_peri_moon": round(om * h, 3),
                     "analytic_r_wh4_384_at_peri_km": round(rp * (om * h) ** 4 / 384, 4)}

    sel_case("LLO_100km_circ_5min", R_MOON + 100, R_MOON + 100, 300.0)
    sel_case("LRO_like_30x180km_5min", R_MOON + 30, R_MOON + 180, 300.0)
    sel_case("ELFO_200x16000km_5min", R_MOON + 200, R_MOON + 16000, 300.0)

    # NRHO-like: selenocentric ellipse near-polar, carried by a circular Moon, Earth-centred nodes @1800 s
    rp, ra = 3340.0, 71700.0
    _, _, per = kepler_ellipse(MU_M, rp, ra, [0.0])

    def nrho(t):
        rs, vs, _ = kepler_ellipse(MU_M, rp, ra, t)
        rs, vs = tilt(rs), tilt(vs)
        rm, vm = moon_circ(t)
        return rm + rs, vm + vs

    for h in (1800.0, 600.0, 300.0):
        tn = np.arange(-4 * 3600.0, 4 * 3600.0 + h, h) + 0.37 * h     # perilune between nodes
        eh, ec, _ = interp_errors(nrho, tn)
        out[f"NRHO_like_perilune_step_{int(h)}s_earth_centred"] = {
            "selenocentric_period_d": round(per / 86400, 2),
            "hermite_max_km": round(eh, 3), "chord_sag_max_km": round(ec, 2)}
    rE, vE = nrho(np.array([0.0]))
    om_e = np.linalg.norm(np.cross(rE[0], vE[0])) / np.linalg.norm(rE[0]) ** 2
    rs, vs, _ = kepler_ellipse(MU_M, rp, ra, [0.0])
    om_m = np.linalg.norm(np.cross(rs[0], vs[0])) / np.linalg.norm(rs[0]) ** 2
    out["NRHO_like_omega_rule_at_perilune"] = {
        "h_omega_about_earth_at_1800s": round(om_e * 1800, 4),
        "h_omega_about_moon_at_1800s": round(om_m * 1800, 3),
        "omega_rule_triggers_about_earth": bool(om_e * 1800 > 0.3),
        "step_needed_by_rule_about_moon_s": round(0.3 / om_m, 1)}

    # HEO with LEO perigee at the omega-rule step
    rp, ra = 6578.0, 400000.0

    def heo(t):
        r, v, _ = kepler_ellipse(MU_E, rp, ra, t)
        return r, v
    r0, v0 = heo(np.array([0.0]))
    om_p = np.linalg.norm(v0[0]) / rp
    h = max(60.0, 0.3 / om_p)
    tn = np.arange(-6 * h, 6 * h + h, h) + 0.5 * h
    eh, ec, _ = interp_errors(heo, tn)
    out["HEO_200x400000km_omega_rule_step"] = {
        "step_s": round(h, 1), "hermite_max_km": round(eh, 3), "chord_sag_max_km": round(ec, 2),
        "doc_claim_bound_km": round(rp * 0.3 ** 4 / 384, 3)}

    # Lunar flyby on a TRANSLUNAR / CISLUNAR_HEO object, 1 h Earth-centred nodes
    rpf, vinf = 8280.0, 1.0

    def flyby(t):
        rs, vs = kepler_hyper(MU_M, rpf, vinf, t)
        rs, vs = tilt(rs, 30.0), tilt(vs, 30.0)
        rm, vm = moon_circ(t)
        return rm + rs, vm + vs
    for h in (3600.0, 900.0):
        tn = np.arange(-12 * 3600.0, 12 * 3600.0 + h, h) + 0.41 * h
        eh, ec, _ = interp_errors(flyby, tn)
        out[f"lunar_flyby_rp8280_vinf1_step_{int(h)}s"] = {
            "hermite_max_km": round(eh, 3), "chord_sag_max_km": round(ec, 2)}
    rE, vE = flyby(np.array([0.0]))
    out["lunar_flyby_omega_about_earth_x3600"] = round(
        float(np.linalg.norm(np.cross(rE[0], vE[0])) / np.linalg.norm(rE[0]) ** 2 * 3600), 4)

    # Moon itself, 1 h
    tn = np.arange(0, 2 * 86400.0 + 3600, 3600.0)
    eh, ec, _ = interp_errors(lambda t: moon_circ(t), tn)
    out["Moon_1h"] = {"hermite_max_km": float(f"{eh:.3g}"), "chord_sag_max_km": round(ec, 3)}
    return out


# ---------------------------------------------------------------- D
def check_d():
    def ulp(x):
        return float(np.spacing(np.float32(x)))
    return {"f32_ulp_km": {str(x): ulp(x) for x in (1837.0, 61300.0, 384400.0, 448900.0, 1.5e6, 7.48e6)},
            "f32_ulp_s": {"60 d": ulp(60 * 86400), "7.3 yr (Spektr-RG history)": ulp(7.3 * 365.25 * 86400)}}


# ---------------------------------------------------------------- E
def star(v):
    area = 0.30 * max(0.0, 6.9 - v) ** 2.0
    d = max(0.6, math.sqrt(area) * 96 / 72)
    op = min(0.60, max(0.12, 0.12 + 0.075 * (6.6 - v)))
    return round(d, 2), round(op, 3)


def check_e():
    rows = {str(v): star(v) for v in (-1.44, -0.5, 0.0, 0.5, 1.0, 1.6, 2.0, 3.0, 4.0, 5.0, 6.0)}
    # how many Hipparcos stars render >= 4 px / >= 3 px, using MMX's computed cumulative counts
    return {"diam_px_opacity": rows, "marker_px": 6,
            "V_where_diam_ge_4px": round(6.9 - (4 * 72 / 96) / math.sqrt(0.30), 2),
            "V_where_diam_ge_3px": round(6.9 - (3 * 72 / 96) / math.sqrt(0.30), 2)}


# ---------------------------------------------------------------- F
def check_f():
    q = 0.5e-5 * 86400  # half of the %.5f quantum, seconds
    return {"max_time_error_s": q,
            "km_at_speed_kms": {str(v): round(q * v, 3) for v in (0.2, 1.0, 1.7, 3.0, 10.9)}}


# ---------------------------------------------------------------- G
def check_g():
    b = {"1h_f32_350": 350 * 1441 * 24, "lunar_5min_f32_15": 15 * 17281 * 24,
         "se_helio_6h_f64_25": 25 * 241 * 48}
    tot = sum(b.values()) + 0.6e6 + 1.9e6 + 0.2e6
    # chord-sag densification for lunar trails: subdivide to sag <= 0.5 km at 100 km LLO
    r = R_MOON + 100
    w = 2 * math.pi / 7067.0
    dth = 2 * math.acos(1 - 0.5 / r)
    n_sub = math.ceil(w * 300 / dth)
    lunar_gpu_full = 15 * 17280 * n_sub * 36
    lunar_gpu_1period = 15 * math.ceil(7067 / 300) * n_sub * 36 * 2
    return {"sidecar_binary_MB": round(tot / 1e6, 1), "base64_MB": round(tot * 4 / 3 / 1e6, 1),
            "lunar_subdiv_per_5min_for_0.5km_sag": n_sub,
            "lunar_trail_gpu_MB_if_densified_whole_window": round(lunar_gpu_full / 1e6, 1),
            "lunar_trail_gpu_MB_if_densified_2_periods_only": round(lunar_gpu_1period / 1e6, 3),
            "earth_texture_4096x2048_rgba_mips_MB": round(4096 * 2048 * 4 * 4 / 3 / 1e6, 1)}


def main():
    res = {"A": check_a(), "B": check_b(), "C": check_c(), "D": check_d(), "E": check_e(),
           "F": check_f(), "G": check_g()}
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
