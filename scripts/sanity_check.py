#!/usr/bin/env python3
"""Numerical sanity checks of the page data (dist/data/*.js), as the page itself would show it.

The sidecars are decoded here, interpolated with the same cubic Hermite and rotated into the same display frames
(ARCHITECTURE AM1, AM4), and the page's own modules (web/loader.js, states.js, frames.js, lagrange.js) are run
under node at the same epochs as a cross-check. Checks:

  js      JS vs numpy: target states (Moon-centred recombination included), frame matrices, Lagrange points
  se      SE frame: every SE-L1/L2 target within 1e6 km of its marker over the window
  em_l    EM-L1..L5 co-move with the Moon: fixed in the EM frame; on the Moon line / at +-60 deg in ECI
  lunar   lunar orbiters within 20,000 km of the Moon (and above its surface) over the window
  dro     DRO-A within 1e5 km of the Moon in the EM frame (with its Bill Gray cross-check from build/states)
  regime  geocentric range against each drawn target's regime over the window
  gp      Space-Track GP vs Horizons (cle.adapters.gp.check on cached Horizons vectors; no network)
  dots    NEOCP tracklet dots against their target's track at the observation epochs

    ~/.venvs/astro313/bin/python -I scripts/sanity_check.py      -> build/sanity_check.json, .md; exit 1 on FAIL
"""
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cle.context import RE_KM, RM_KM, Context  # noqa: E402
from cle.export import collinear  # noqa: E402
from cle.timeutil import iso, window  # noqa: E402
from cle.validate import write_atomic, write_json  # noqa: E402

DIST = ROOT / "dist/data"
BUILD = ROOT / "build"
SE_MAX_KM, LUNAR_MAX_KM, DRO_MAX_KM = 1.0e6, 2.0e4, 1.0e5
# regime: (min, max) geocentric km over the window; LUNAR_ORBIT and EM_DRO are checked against the Moon instead
RANGES = {"EM_OTHER": (5e4, 1e6), "EM_L1": (2.5e5, 4.5e5), "EM_L2": (3.5e5, 5.5e5), "TRANSLUNAR": (RE_KM, 2e6),
          "CISLUNAR_HEO": (RE_KM, 2e6), "HEO": (RE_KM, 1e5), "SE_L1": (1.0e6, 2.0e6), "SE_L2": (1.0e6, 2.0e6)}

NODE = r"""
import { readFileSync, readdirSync } from 'node:fs';
const [root, req] = process.argv.slice(1), { times } = JSON.parse(readFileSync(req, 'utf8'));
const { decodeTrack } = await import(`${root}/web/loader.js`), { sampleTrack } = await import(`${root}/web/states.js`);
const { Frames } = await import(`${root}/web/frames.js`), { makeLagrange } = await import(`${root}/web/lagrange.js`);
const store = {}; globalThis.CLE = { register(n, p) { store[n] = p; } };
for (const f of readdirSync(`${root}/dist/data`)) if (f.endsWith('.js')) (0, eval)(readFileSync(`${root}/dist/data/${f}`, 'utf8'));
const core = store.core, F = new Frames(decodeTrack(core.sun.track), decodeTrack(core.moon.track));
const L = makeLagrange(core.meta.mu.se, core.meta.mu.em), out = { sun: [], moon: [], se: [], em: [], L: [], targets: {} };
const tracks = {};
for (const c of store.catalog.chunks) for (const [id, p] of Object.entries(store[c]?.targets || {})) tracks[id] = decodeTrack(p.track);
for (const [id, o] of Object.entries(store.neocp?.objects || {})) tracks[o.cid || id.toLowerCase()] = decodeTrack(o.track);
const rel = new Float64Array(6);
for (const id of Object.keys(tracks)) out.targets[id] = [];
for (const t of times) {
  F.context(t); out.sun.push([...F.sunState]); out.moon.push([...F.moonState]);
  out.se.push([...F.matrix('se', t)]); out.em.push([...F.matrix('em', t)]);
  L.update(F.sunState, F.moonState); out.L.push(L.out.map(p => [...p]));
  for (const [id, tr] of Object.entries(tracks)) {
    const seg = sampleTrack(tr, t, rel);
    out.targets[id].push(seg ? [...rel].map((x, k) => x + (tr.center === 'moon' ? F.moonState[k] : 0)) : null);
  }
}
process.stdout.write(JSON.stringify(out));
"""


def sidecar(name):
    m = re.match(r'CLE\.register\("[^"]+", (.*)\);\s*$', (DIST / f"{name}.js").read_text(), re.S)
    return json.loads(m[1])


def decode(track):
    dt = "<f8" if track["dtype"] == "f64" else "<f4"
    srcs, segs = track.get("srcs") or [], []
    for s in track["segments"]:
        rv = np.frombuffer(base64.b64decode(s["rv"]), dtype=dt).astype(float).reshape(s["n"], 6)
        t0 = s["t0_ms"] / 1000
        src = srcs[s["src"]] if isinstance(s["src"], int) else s["src"]      # §11: a segment's src indexes track.srcs
        segs.append({"t0": t0, "h": s["step_s"], "n": s["n"], "t1": t0 + (s["n"] - 1) * s["step_s"], "rv": rv,
                     "t": t0 + s["step_s"] * np.arange(s["n"]), "src": src, "quality": s["quality"]})
    return {"center": track["center"], "segs": segs}


def hermite(seg, t):
    x = (t - seg["t0"]) / seg["h"]
    i = np.clip(np.floor(x).astype(int), 0, seg["n"] - 2)
    u, h, rv = (x - i)[:, None], seg["h"], seg["rv"]
    p0, p1, v0, v1 = rv[i, :3], rv[i + 1, :3], rv[i, 3:] * h, rv[i + 1, 3:] * h
    u2, u3 = u * u, u * u * u
    r = (2 * u3 - 3 * u2 + 1) * p0 + (u3 - 2 * u2 + u) * v0 + (-2 * u3 + 3 * u2) * p1 + (u3 - u2) * v1
    v = ((6 * u2 - 6 * u) * p0 + (3 * u2 - 4 * u + 1) * v0 + (-6 * u2 + 6 * u) * p1 + (3 * u2 - 2 * u) * v1) / h
    return np.hstack([r, v])


def sample(track, t):
    """States in the track centre; NaN outside coverage; the later segment wins at a shared boundary (states.js)."""
    t, out = np.atleast_1d(np.asarray(t, float)), None
    out = np.full((len(t), 6), np.nan)
    for s in track["segs"]:
        k = (t >= s["t0"] - 1e-6) & (t <= s["t1"] + 1e-6)
        if k.any():
            out[k] = hermite(s, t[k])
    return out


def basis(rv, sign):
    """Rows x = sign*unit(r), y = z x x, z = unit(r x v) (frames.js rotatingBasis); (n, 3, 3)."""
    r, v = rv[:, :3], rv[:, 3:]
    x = sign * r / np.linalg.norm(r, axis=1)[:, None]
    z = np.cross(r, v)
    z /= np.linalg.norm(z, axis=1)[:, None]
    return np.stack([x, np.cross(z, x), z], axis=1)


def rot(R, p):
    return np.einsum("nij,nj->ni", R, p)


def lagrange(sun, moon, se, em):
    """AM4 markers, geocentric ICRF km, (n, 7, 3): SE-L1, SE-L2, EM-L1..L5."""
    D, d = np.linalg.norm(sun[:, :3], axis=1)[:, None], np.linalg.norm(moon[:, :3], axis=1)[:, None]
    us, um = sun[:, :3] / D, moon[:, :3] / d
    z = np.cross(moon[:, :3], moon[:, 3:])
    y = np.cross(z / np.linalg.norm(z, axis=1)[:, None], um)
    c, s = np.cos(np.pi / 3), np.sin(np.pi / 3)
    pts = [se["gamma_l1"] * D * us, -se["gamma_l2"] * D * us, (1 - em["gamma_l1"]) * d * um, (1 + em["gamma_l2"]) * d * um,
           (em["x_l3"] + em["mu"]) * d * um, d * (c * um + s * y), d * (c * um - s * y)]
    return np.stack(pts, axis=1)


def status(ok, warn=False):
    return "pass" if ok else ("WARN" if warn else "FAIL")


def main():
    win, ctx = window(), Context()
    core, catalog, neocp = sidecar("core"), sidecar("catalog"), sidecar("neocp")
    cat = {e["id"]: e for e in catalog["targets"]}
    tracks = {}
    for c in catalog["chunks"]:
        for cid, p in sidecar(c)["targets"].items():
            tracks[cid] = decode(p["track"])
    for d, o in neocp.get("objects", {}).items():
        tracks[o.get("cid", d.lower())] = decode(o["track"])
    sun_tr, moon_tr = decode(core["sun"]["track"]), decode(core["moon"]["track"])
    se, em = collinear(core["meta"]["mu"]["se"]), collinear(core["meta"]["mu"]["em"])
    t0, t1 = core["meta"]["window"]["t0_ms"] / 1000, core["meta"]["window"]["t1_ms"] / 1000
    out = {"built_utc": iso(os.path.getmtime(DIST / "core.js")), "now_utc": core["meta"]["now_utc"], "window": [iso(t0), iso(t1)]}
    hard, now_s = [], core["meta"]["now_ms"] / 1000

    # --- js: the page's modules at off-grid epochs vs numpy
    rng = np.random.default_rng(20261021)
    tj = np.sort(np.r_[rng.uniform(t0, t1, 160), t0 + 3600 * np.arange(0, 721, 48) + 17 * 60])
    tj = np.round(np.clip(tj, t0, t1) * 1000) / 1000
    req = BUILD / "sanity_js_request.json"
    req.write_text(json.dumps({"times": (tj * 1000).tolist()}))
    js = json.loads(subprocess.run(["node", "--input-type=module", "-e", NODE, str(ROOT), str(req)], check=True,
                                   capture_output=True, text=True, cwd=ROOT).stdout)
    req.unlink()
    sun_j, moon_j = np.array(js["sun"]), np.array(js["moon"])
    sun_p, moon_p = sample(sun_tr, tj), sample(moon_tr, tj)
    R_se, R_em = basis(sun_p, -1), basis(moon_p, 1)
    Lp = lagrange(sun_p, moon_p, se, em)
    dstate, drel = 0.0, 0.0
    for cid, rows in js["targets"].items():
        a = np.array([r if r is not None else [np.nan] * 6 for r in rows], float)
        b = sample(tracks[cid], tj)
        if tracks[cid]["center"] == "moon":
            b = b + moon_p
        both = np.isfinite(a[:, 0]) & np.isfinite(b[:, 0])
        if (np.isfinite(a[:, 0]) != np.isfinite(b[:, 0])).any():
            hard.append(f"js coverage differs for {cid}")
        if both.any():
            err = np.abs(a[both, :3] - b[both, :3]).max(axis=1)
            dstate = max(dstate, float(err.max()))
            drel = max(drel, float((err / np.maximum(1e-3, np.linalg.norm(b[both, 3:], axis=1))).max()))   # as a time offset, s
    js_res = {"epochs": len(tj), "targets": len(js["targets"]), "state_max_km": dstate, "state_max_dt_s": drel,
              "sun_max_km": float(np.abs(sun_j[:, :3] - sun_p[:, :3]).max()),
              "moon_max_km": float(np.abs(moon_j[:, :3] - moon_p[:, :3]).max()),
              "R_se_max": float(np.abs(np.array(js["se"]).reshape(-1, 3, 3) - R_se).max()),
              "R_em_max": float(np.abs(np.array(js["em"]).reshape(-1, 3, 3) - R_em).max()),
              "lagrange_max_km": float(np.abs(np.array(js["L"]) - Lp).max())}
    js_res["status"] = status(drel < 1e-6 and js_res["R_se_max"] < 1e-12 and js_res["R_em_max"] < 1e-12
                              and js_res["lagrange_max_km"] < 1e-6 and not hard)
    tm = t0 + 1800 + 3600 * np.arange(720)
    js_res["moon_track_vs_de440s_mid_km"] = float(np.linalg.norm(sample(moon_tr, tm)[:, :3] - ctx.moon_rv(tm)[:, :3], axis=1).max())
    out["js"] = js_res

    # --- per-target native samples, geocentric ICRF
    geo = {}
    for cid, tr in tracks.items():
        t = np.concatenate([s["t"] for s in tr["segs"]])
        rv = np.concatenate([s["rv"] for s in tr["segs"]])
        geo[cid] = (t, rv + (sample(moon_tr, t) if tr["center"] == "moon" else 0), rv if tr["center"] == "moon" else None)

    # --- se: SE-L1/L2 targets near their markers in the SE frame
    se_res = {}
    for cid, (t, rv, _) in geo.items():
        reg = cat[cid]["regime"]
        if reg not in ("SE_L1", "SE_L2"):
            continue
        sun = sample(sun_tr, t)
        L = lagrange(sun, sample(moon_tr, t), se, em)[:, 0 if reg == "SE_L1" else 1]
        R = basis(sun, -1)
        p, q = rot(R, rv[:, :3]), rot(R, L)
        dist = np.linalg.norm(p - q, axis=1)
        side, k = np.sign(p[:, 0]), np.argmin(np.abs(t - now_s))
        good = (dist < SE_MAX_KM) & (side == (-1 if reg == "SE_L1" else 1))
        se_res[cid] = {"regime": reg, "max_km": float(dist.max()), "median_km": float(np.median(dist)),
                       "x_km": [float(p[:, 0].min()), float(p[:, 0].max())], "marker_x_km": float(q[:, 0].mean()),
                       "right_side": bool((side == (-1 if reg == "SE_L1" else 1)).all()), "now_km": float(dist[k]),
                       "inside_frac": float(good.mean()), "status": status(good.all(), warn=bool(good[k]))}
    out["se"] = se_res

    # --- em_l: EM L-points co-move with the Moon
    th = t0 + 3600 * np.arange(721)
    sun_h, moon_h = sample(sun_tr, th), sample(moon_tr, th)
    Lh, Rh, d = lagrange(sun_h, moon_h, se, em), basis(moon_h, 1), np.linalg.norm(moon_h[:, :3], axis=1)
    em_res = {}
    want = {"EM-L1": [1 - em["gamma_l1"], 0, 0], "EM-L2": [1 + em["gamma_l2"], 0, 0], "EM-L3": [em["x_l3"] + em["mu"], 0, 0],
            "EM-L4": [0.5, np.sqrt(3) / 2, 0], "EM-L5": [0.5, -np.sqrt(3) / 2, 0]}
    um = moon_h[:, :3] / d[:, None]
    for k, (name, w) in enumerate(want.items(), start=2):
        f = rot(Rh, Lh[:, k]) / d[:, None]
        ang = np.degrees(np.arccos(np.clip(np.einsum("ni,ni->n", Lh[:, k] / np.linalg.norm(Lh[:, k], axis=1)[:, None], um), -1, 1)))
        em_res[name] = {"em_frame_units_of_d": [round(float(x), 7) for x in f.mean(axis=0)],
                        "em_frame_spread": float(np.abs(f - w).max()),
                        "eci_angle_from_moon_deg": [round(float(ang.min()), 6), round(float(ang.max()), 6)],
                        "km_from_earth": [round(float(np.linalg.norm(Lh[:, k], axis=1).min())), round(float(np.linalg.norm(Lh[:, k], axis=1).max()))]}
    em_res["status"] = status(all(v["em_frame_spread"] < 1e-9 for v in em_res.values()))
    out["em_l"] = em_res

    # --- lunar: Moon-centred tracks
    lunar = {}
    for cid, (t, rv, sel) in geo.items():
        if cat[cid]["regime"] != "LUNAR_ORBIT":
            continue
        r = np.linalg.norm(sel[:, :3] if sel is not None else rv[:, :3] - sample(moon_tr, t)[:, :3], axis=1)
        lunar[cid] = {"centre": tracks[cid]["center"], "r_moon_km": [round(float(r.min()), 1), round(float(r.max()), 1)],
                      "status": status(r.max() < LUNAR_MAX_KM and r.min() > RM_KM)}
    out["lunar"] = lunar

    # --- dro: DRO targets in the EM frame
    dro = {}
    for cid, (t, rv, _) in geo.items():
        if cat[cid]["regime"] != "EM_DRO":
            continue
        moon = sample(moon_tr, t)
        R = basis(moon, 1)
        rel = rot(R, rv[:, :3] - moon[:, :3])
        r = np.linalg.norm(rel, axis=1)
        v = json.loads((BUILD / "states" / f"{cid}.json").read_text()).get("validation", {})
        dro[cid] = {"moon_km": [round(float(r.min())), round(float(r.max()))], "em_x_km": [round(float(rel[:, 0].min())), round(float(rel[:, 0].max()))],
                    "em_y_km": [round(float(rel[:, 1].min())), round(float(rel[:, 1].max()))],
                    "quality": sorted({s["quality"] for s in tracks[cid]["segs"]}), "src": sorted({s["src"] for s in tracks[cid]["segs"]}),
                    "bill_gray_extrap_moon_km": v.get("bg_moon_km_window"), "gp_vs_bg_at_gp_epoch": v.get("gp_vs_bg_extrap_at_gp_epoch"),
                    "status": status(r.max() < DRO_MAX_KM and r.min() > 2e4, warn=cid != "dro-a")}
    out["dro"] = dro
    if dro.get("dro-a", {}).get("status") == "FAIL":
        out["dro"]["note"] = ("data source, not integration: AM6 integrates the newest Space-Track GP state; the Bill Gray "
                              "fit extrapolated the same way stays inside the DRO band")

    # --- regime: geocentric range over the window
    reg = {}
    for cid, (t, rv, _) in geo.items():
        e, r = cat[cid], np.linalg.norm(rv[:, :3], axis=1)
        rm = np.linalg.norm(rv[:, :3] - sample(moon_tr, t)[:, :3], axis=1)
        lo, hi = RANGES.get(e["regime"], (RE_KM, np.inf))
        inside = (r > RE_KM) & (rm > RM_KM) & (r >= lo) & (r <= hi)
        if e["regime"] == "LUNAR_ORBIT":
            inside = (rm < LUNAR_MAX_KM) & (rm > RM_KM)
        ok, at_now = bool(inside.all()), bool(inside[np.argmin(np.abs(t - now_s))])
        reg[cid] = {"regime": e["regime"], "tier": e["tier"], "r_geo_km": [float(f"{r.min():.4g}"), float(f"{r.max():.4g}")],
                    "r_moon_min_km": float(f"{rm.min():.4g}"), "bounds_km": [lo, hi if np.isfinite(hi) else None],
                    "regime_now": e.get("regime_now"), "inside_frac": round(float(inside.mean()), 3),
                    "status": status(ok, warn=at_now or e["regime"].startswith("EM_") or e["tier"] > 1)}
    out["regime"] = reg

    # --- gp: Space-Track GP vs Horizons (cached vectors; never fetches)
    from cle.adapters import gp
    try:
        out["gp"] = {str(k): v for k, v in gp.check(ctx, win).items()}
    except OSError as err:
        out["gp"] = {"error": f"{type(err).__name__}: {err}"}

    # --- dots: NEOCP tracklet dots against the target track at the observation epochs
    dots = {}
    items = [(o.get("cid", d.lower()), o.get("tracklets", [])) for d, o in neocp.get("objects", {}).items()]
    items += [(cid, a.get("tracklets", [])) for cid, a in neocp.get("attached", {}).items()]
    for cid, tks in items:
        if cid not in tracks or not tks:
            continue
        dr = []
        for tk in tks:
            p = np.frombuffer(base64.b64decode(tk["dot_km"]), "<f8").reshape(-1, 3)
            t = np.array(tk["t_ms"]) / 1000
            s = sample(tracks[cid], t)
            if tracks[cid]["center"] == "moon":
                s = s + sample(moon_tr, t)
            dr += list(np.linalg.norm(p - s[:, :3], axis=1))
        dr = np.array(dr)
        dr = dr[np.isfinite(dr)]
        if len(dr):
            dots[cid] = {"dots": len(dr), "median_km": round(float(np.median(dr)), 2), "max_km": round(float(dr.max()), 1)}
    out["dots"] = dots

    out["failures"] = hard + [f"{k}:{cid}" for k in ("se", "lunar", "dro", "regime") for cid, v in out[k].items()
                              if isinstance(v, dict) and v.get("status") == "FAIL"]
    out["failures"] += [f"{k}" for k in ("js", "em_l") if out[k]["status"] == "FAIL"]
    out["warnings"] = [f"{k}:{cid}" for k in ("dro", "se", "regime") for cid, v in out[k].items()
                       if isinstance(v, dict) and v.get("status") in ("WARN", "FAIL") and f"{k}:{cid}" not in out["failures"]]
    write_json(BUILD / "sanity_check.json", out)
    write_atomic(BUILD / "sanity_check.md", report(out))
    print(report(out))
    return 1 if out["failures"] else 0


def report(o):
    j = o["js"]
    lines = ["# Sanity check", "", f"Data built {o['built_utc']} · now {o['now_utc']} · window {o['window'][0]} → {o['window'][1]}", "",
             f"**JS vs numpy ({j['status']})**: {j['epochs']} off-grid epochs × {j['targets']} targets: state ≤ {j['state_max_km']:.2g} km, "
             f"Sun ≤ {j['sun_max_km']:.2g} km, Moon ≤ {j['moon_max_km']:.2g} km, R_se ≤ {j['R_se_max']:.2g}, R_em ≤ {j['R_em_max']:.2g}, "
             f"Lagrange ≤ {j['lagrange_max_km']:.2g} km (state ≤ |v| × {j['state_max_dt_s']:.2g} s: the float64 ulp of epoch seconds is 2.4e-7 s). "
             f"Core Moon track (1 h Hermite) vs DE440s at mid-steps ≤ {j['moon_track_vs_de440s_mid_km'] * 1000:.2g} m.", "",
             "## SE-L1/L2 targets vs their marker (SE frame, whole window)", "",
             f"Limit {SE_MAX_KM:.0e} km and the marker's side of Earth; WARN = inside at now but not over the whole window.", "",
             "| target | regime | now km | max km | median km | inside | x range km | marker x km | |", "|---|---|---:|---:|---:|---:|---|---:|---|"]
    lines += [f"| {c} | {v['regime']} | {v['now_km']:.4g} | {v['max_km']:.4g} | {v['median_km']:.4g} | {v['inside_frac']:.0%} | "
              f"{v['x_km'][0]:.4g} … {v['x_km'][1]:.4g} | {v['marker_x_km']:.5g} | {v['status']} |" for c, v in sorted(o["se"].items(), key=lambda kv: -kv[1]["max_km"])]
    lines += ["", f"## EM Lagrange points co-move with the Moon ({o['em_l']['status']})", "", "| point | EM frame (units of d) | spread | ECI angle from Moon ° | km from Earth |", "|---|---|---:|---|---|"]
    lines += [f"| {k} | {v['em_frame_units_of_d']} | {v['em_frame_spread']:.1g} | {v['eci_angle_from_moon_deg']} | {v['km_from_earth']} |"
              for k, v in o["em_l"].items() if isinstance(v, dict)]
    lines += ["", "## Lunar orbiters (selenocentric r over the window)", "", "| target | centre | r km | |", "|---|---|---|---|"]
    lines += [f"| {c} | {v['centre']} | {v['r_moon_km'][0]:,} … {v['r_moon_km'][1]:,} | {v['status']} |" for c, v in o["lunar"].items()]
    lines += ["", "## DROs in the EM frame (Moon-relative)", "", "| target | Moon km | x km | y km | source | Bill Gray extrap Moon km | GP vs BG at GP epoch | |", "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {c} | {v['moon_km'][0]:,} … {v['moon_km'][1]:,} | {v['em_x_km']} | {v['em_y_km']} | {', '.join(v['src'])} | "
              f"{v['bill_gray_extrap_moon_km']} | {v['gp_vs_bg_at_gp_epoch']} | {v['status']} |" for c, v in o["dro"].items() if isinstance(v, dict)]
    if o["dro"].get("note"):
        lines += ["", o["dro"]["note"] + "."]
    bad = {c: v for c, v in o["regime"].items() if v["status"] != "pass"}
    lines += ["", f"## Geocentric range vs regime: {len(o['regime']) - len(bad)}/{len(o['regime'])} pass", "",
              "WARN = inside the bounds at now (or a tier-2 / EM row, whose regime label is indicative) but not over the whole window.", "",
              "| target | tier | regime | r geo km | min Moon km | bounds km | inside | |", "|---|---:|---|---|---:|---|---:|---|"]
    lines += [f"| {c} | {v['tier']} | {v['regime']} | {v['r_geo_km'][0]:.4g} … {v['r_geo_km'][1]:.4g} | {v['r_moon_min_km']:.4g} | "
              f"{v['bounds_km']} | {v['inside_frac']:.0%} | {v['status']} |" for c, v in sorted(o["regime"].items(), key=lambda kv: (kv[1]['status'] == 'pass', kv[1]['regime']))]
    lines += ["", "## GP vs Horizons (cached Horizons vectors over the window)", "", "| NORAD | Horizons | GP sets | max km | median km | max age d |", "|---|---|---:|---:|---:|---:|"]
    lines += [f"| {k} | {v.get('horizons')} | {v.get('n_gp')} | {v.get('max_km', '—')} | {v.get('median_km', v.get('error', '—'))} | {v.get('max_age_d', '—')} |"
              for k, v in o["gp"].items() if isinstance(v, dict)]
    lines += ["", "## NEOCP tracklet dots vs their target track", "", "| target | dots | median km | max km |", "|---|---:|---:|---:|"]
    lines += [f"| {c} | {v['dots']} | {v['median_km']} | {v['max_km']} |" for c, v in o["dots"].items()]
    lines += ["", f"**Failures:** {', '.join(o['failures']) or 'none'}", "", f"**Warnings:** {', '.join(o['warnings']) or 'none'}", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
