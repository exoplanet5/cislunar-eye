"""NEOCP / astrometry tracklets in 3D (ARCHITECTURE.md §7).

For each 80-column observation: the station's geocentric ICRF position (ObsCodes parallax constants rotated
from ITRS with skyfield, no polar motion; satellite 'S/s' pairs give the vector directly; roving 'V/v' pairs give
WGS84 longitude, latitude and height), the observed J2000 direction u, and the fitted topocentric range
rho = |r(t - rho/c) - v_earth·rho/c - R(t)| (iterated twice; r geocentric, so the Earth's barycentric motion
during the light time is put back) from the object's trajectory. The dot is R + rho·u at t - rho/c;
O - C is the observed minus computed direction in arcsec. Observations are grouped per station and night
(a new tracklet after a 6 h gap).
"""
from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np

from cle.context import RE_KM

OBSCODES = Path.home() / ".find_orb/ObsCodes.htm"
C_KMS = 299792.458
AU = 149597870.7
NIGHT_GAP_S = 6 * 3600


@lru_cache(maxsize=1)
def obscodes():
    """code -> (east longitude deg, rho cos phi', rho sin phi'), or None for space-based / roving codes."""
    out = {}
    for ln in OBSCODES.read_text(errors="replace").splitlines():
        if re.match(r"^[0-9A-Z][0-9A-Z][0-9A-Z] ", ln):
            try:
                out[ln[:3]] = float(ln[4:13]), float(ln[13:21]), float(ln[21:30])
            except ValueError:
                out[ln[:3]] = None
    return out


def b64(a, dtype="<f8"):
    return base64.b64encode(np.ascontiguousarray(a, dtype=dtype).tobytes()).decode()


def parse(lines):
    """Observations of an 80-column file: one dict per optical line, with its satellite/roving 2nd line."""
    obs = []
    for k, ln in enumerate(lines):
        if len(ln) < 80 or ln[14] in "sv" or not re.match(r"\d{4} \d\d \d\d\.\d", ln[15:27]):
            continue
        y, m, d = int(ln[15:19]), int(ln[20:22]), float(ln[23:32])
        h, mi, s = (float(x) for x in ln[32:44].split())
        sign = -1 if ln[44] == "-" else 1
        dd, dm, ds = (float(x) for x in ln[45:56].split())
        mag = ln[65:70].strip()
        second = lines[k + 1] if k + 1 < len(lines) and len(lines[k + 1]) >= 70 and lines[k + 1][14] in "sv" else None
        obs.append({"t_s": datetime(y, m, 1, tzinfo=timezone.utc).timestamp() + (d - 1) * 86400,
                    "ra_deg": 15 * (h + mi / 60 + s / 3600), "dec_deg": sign * (dd + dm / 60 + ds / 3600),
                    "mag": float(mag) if re.match(r"^[\d.]+$", mag) else None, "stn": ln[77:80],
                    "kind": ln[14], "second": second, "line": ln[:80]})
    return sorted(obs, key=lambda o: (o["t_s"], o["stn"]))


def unit(ra_deg, dec_deg):
    a, d = np.radians(ra_deg), np.radians(dec_deg)
    return np.column_stack([np.cos(d) * np.cos(a), np.cos(d) * np.sin(a), np.sin(d)])


def earth_velocity(ctx, t):
    """(n, 3) barycentric velocity of the geocentre, km/s."""
    return np.atleast_2d(ctx.earth.at(ctx.times(t)).velocity.km_per_s.T)


def astrometric(rv_at, ctx, t, R):
    """(vector from R to the object as seen at t, light-time epoch), for geocentric trajectories rv_at."""
    ve = earth_velocity(ctx, t)
    rho = np.linalg.norm(rv_at(t)[:, :3] - R, axis=1)
    for _ in range(2):
        tau = rho / C_KMS
        x = rv_at(t - tau)[:, :3] - ve * tau[:, None] - R
        rho = np.linalg.norm(x, axis=1)
    tau = rho / C_KMS
    return rv_at(t - tau)[:, :3] - ve * tau[:, None] - R, t - tau


def stations(obs, ctx):
    """(n, 3) geocentric ICRF station positions, km (NaN where the station is unknown)."""
    from skyfield.api import wgs84
    from skyfield.framelib import itrs
    t = np.array([o["t_s"] for o in obs])
    rot = itrs.rotation_at(ctx.times(t))                  # ICRF -> ITRS, (3, 3, n)
    out = np.full((len(obs), 3), np.nan)
    codes = obscodes()
    for k, o in enumerate(obs):
        s = o["second"]
        if o["kind"] == "S" and s:
            scale = AU if s[32] == "2" else 1.0
            out[k] = [float(s[34:45].replace(" ", "")) * scale, float(s[46:57].replace(" ", "")) * scale,
                      float(s[58:69].replace(" ", "")) * scale]
            continue
        if o["kind"] == "V" and s:
            lon, lat, alt = float(s[34:44]), float(s[45:55]), float(s[56:61])
            p = wgs84.latlon(lat, lon, elevation_m=alt).itrs_xyz.km
        elif codes.get(o["stn"]):
            lon, rc, rs = codes[o["stn"]]
            p = RE_KM * np.array([rc * np.cos(np.radians(lon)), rc * np.sin(np.radians(lon)), rs])
        else:
            continue
        out[k] = rot[:, :, k].T @ p
    return out


def build(obs, rv_at, ctx, t_min=None, t_max=None):
    """Tracklets (§11 shape) for observations whose light-time epoch the trajectory rv_at covers."""
    if not obs:
        return []
    R = stations(obs, ctx)
    t = np.array([o["t_s"] for o in obs])
    u = unit([o["ra_deg"] for o in obs], [o["dec_deg"] for o in obs])
    ok = np.isfinite(R).all(axis=1)
    if t_min is not None:
        ok &= t - 10 > t_min
    if t_max is not None:
        ok &= t <= t_max
    if not ok.any():
        return []
    calc, te = astrometric(rv_at, ctx, t[ok], R[ok])
    good = np.isfinite(calc).all(axis=1)                 # trajectories with gaps give NaN there
    ok[ok] = good
    calc, te = calc[good], te[good]
    if not len(calc):
        return []
    rho = np.linalg.norm(calc, axis=1)
    ra_c = np.degrees(np.arctan2(calc[:, 1], calc[:, 0])) % 360
    dec_c = np.degrees(np.arcsin(calc[:, 2] / np.linalg.norm(calc, axis=1)))
    sel = [o for o, g in zip(obs, ok) if g]
    dra = ((np.array([o["ra_deg"] for o in sel]) - ra_c + 180) % 360 - 180) * np.cos(np.radians(dec_c)) * 3600
    ddec = (np.array([o["dec_deg"] for o in sel]) - dec_c) * 3600
    # In the geocentric scene the object at its light-time epoch sits v_earth * tau beyond the astrometric line of sight
    dots = R[ok] + rho[:, None] * u[ok] + earth_velocity(ctx, t[ok]) * (rho / C_KMS)[:, None]
    groups, cur = [], []
    order = sorted(range(len(sel)), key=lambda i: (sel[i]["stn"], sel[i]["t_s"]))
    for i in order:
        if cur and (sel[i]["stn"] != sel[cur[-1]]["stn"] or sel[i]["t_s"] - sel[cur[-1]]["t_s"] > NIGHT_GAP_S):
            groups.append(cur)
            cur = []
        cur.append(i)
    groups.append(cur)
    out = []
    for g in sorted(groups, key=lambda g: te[g[0]]):
        out.append({"stn": sel[g[0]]["stn"], "n": len(g), "t_ms": [round(float(te[i]) * 1000) for i in g],
                    "ra_deg": [round(sel[i]["ra_deg"], 7) for i in g], "dec_deg": [round(sel[i]["dec_deg"], 7) for i in g],
                    "mag": [sel[i]["mag"] for i in g], "range_km": [round(float(rho[i]), 3) for i in g],
                    "stn_km": b64(R[ok][g]), "dot_km": b64(dots[g]),
                    "resid_arcsec": [[round(float(dra[i]), 3), round(float(ddec[i]), 3)] for i in g]})
    return out


def rms(tracklets):
    """Per-coordinate RMS of O - C (arcsec), find_orb's "mean residual": sqrt(sum(dRA cos d^2 + dDec^2) / 2n)."""
    r = np.array([x for tr in tracklets for x in tr["resid_arcsec"]])
    return float(np.sqrt(np.mean(r**2))) if len(r) else None


def track_at(st, ctx=None):
    """Geocentric Hermite position/velocity callable over a saved StateTable (Moon-centred tables are recombined
    with the DE440s Moon from ctx); NaN outside its segments."""
    from scipy.interpolate import CubicHermiteSpline
    parts = [(s.t_s[0], s.t_s[-1], CubicHermiteSpline(s.t_s, s.r_km, s.v_kms)) for s in st.segments]

    def at(t):
        t = np.atleast_1d(np.asarray(t, dtype=float))
        out = np.full((len(t), 6), np.nan)
        for a, b, f in parts:
            k = (t >= a) & (t <= b)
            if k.any():
                out[k] = np.hstack([f(t[k]), f(t[k], 1)])
        if st.centre == "moon":
            out += ctx.moon_rv(t)
        return out
    at.span = (parts[0][0], parts[-1][1]) if parts else (np.inf, -np.inf)
    return at
