"""StateTables + Context -> dist/data/*.js classic-script sidecars (ARCHITECTURE.md §11).

Every file is `CLE.register("<name>", {...});`, written atomically. Arrays are base64 of little-endian
float32/float64 (f64 when max |r| > 1e6 km, §6.2), rv interleaved per sample as x,y,z,vx,vy,vz.
Consecutive segments joined "continuous" share their boundary sample; other joins never interpolate
across. Tracks are clipped to the window. Moon-centred (lunar) tracks carry "center": "moon".
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

from . import catalog, earthrot
from .context import GM_EARTH, GM_MOON, GM_SUN, MU_EM, MU_SE, RE_KM, RM_KM
from .statetable import Segment, StateTable
from .timeutil import iso
from .validate import BUILD, STATES, TOL_KM, load_json, regime_now

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist/data"
CONTEXT_STEP_S = 3600
F64_ABOVE_KM = 1.0e6


def b64(a, dtype: str) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype="<f8" if dtype == "f64" else "<f4").tobytes()).decode()


def ms(t_s: float) -> int:
    return int(round(t_s * 1000))


def clip(seg: Segment, t0_s: float, t1_s: float) -> Segment | None:
    t = seg.t_s
    keep = np.flatnonzero((t >= t0_s - 1e-6) & (t <= t1_s + 1e-6))
    if len(keep) < 2:
        return None
    i, j = keep[0], keep[-1] + 1
    return Segment(t0_s=float(t[i]), step_s=seg.step_s, r_km=seg.r_km[i:j], v_kms=seg.v_kms[i:j], source=seg.source,
                   quality=seg.quality, join_before=seg.join_before, jump_km=seg.jump_km,
                   sigma_km=None if seg.sigma_km is None else seg.sigma_km[i:j])


def track(segments: list[Segment], centre: str, dtype: str | None = None) -> dict:
    """§11 Track: segment `src` indexes the track's `srcs`; sigma_km / jump_km only when there is one (EFF-6)."""
    big = max(np.linalg.norm(s.r_km, axis=1).max() for s in segments) > F64_ABOVE_KM
    dtype = dtype or ("f64" if big else "f32")
    srcs, out = [], []
    for k, s in enumerate(segments):
        if s.source not in srcs:
            srcs.append(s.source)
        seg = {"t0_ms": ms(s.t0_s), "step_s": float(s.step_s), "n": s.n, "rv": b64(np.hstack([s.r_km, s.v_kms]), dtype),
               "src": srcs.index(s.source), "quality": s.quality, "join_before": "start" if k == 0 else s.join_before}
        if s.sigma_km is not None:
            seg["sigma_km"] = b64(s.sigma_km, "f32")
        if s.jump_km:
            seg["jump_km"] = float(s.jump_km)
        out.append(seg)
    return {"center": centre, "dtype": dtype, "srcs": srcs, "segments": out}


def span(trk: dict) -> tuple[str, str]:
    """ISO start and end of a Track (from t0_ms, as the page computes t1_ms)."""
    a, z = trk["segments"][0], trk["segments"][-1]
    return iso(a["t0_ms"] / 1000), iso((z["t0_ms"] + 1000 * z["step_s"] * (z["n"] - 1)) / 1000)


def events(segments: list[Segment], win) -> list[dict]:
    out = []
    if segments[0].t0_s >= win.t0_s + segments[0].step_s:      # a step or more missing at either end (a Horizons
        out.append({"t_utc": iso(segments[0].t0_s), "kind": "ephem_start", "note": f"{segments[0].source} starts"})
    for s in segments[1:]:                                       # fallback track ends exactly one step short)
        if s.join_before in ("splice", "gap", "source"):
            out.append({"t_utc": iso(s.t0_s), "kind": s.join_before, "note": f"jump {s.jump_km:.1f} km" if s.jump_km else ""})
    last = segments[-1]
    if last.t_s[-1] <= win.t1_s - last.step_s:
        out.append({"t_utc": iso(last.t_s[-1]), "kind": "ephem_end", "note": f"{last.source} ends"})
    return out


def write_js(path: Path, name: str, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"CLE.register({json.dumps(name)}, " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                                                              allow_nan=False) + ");\n"
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def read_js(path: Path):
    """Payload of a CLE.register sidecar, or None when it is missing or not one."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        return json.loads(text[text.index(", ") + 2:text.rindex(")")])
    except (OSError, ValueError):
        return None


def collinear(mu: float) -> dict:
    """CR3BP collinear points as x/separation from the barycentre (primary at -mu, secondary at 1-mu)."""
    f = lambda x: x - (1 - mu) * (x + mu) / abs(x + mu) ** 3 - mu * (x - 1 + mu) / abs(x - 1 + mu) ** 3
    h = (mu / 3) ** (1 / 3)
    l1 = brentq(f, 1 - mu - 3 * h, 1 - mu - 1e-6 * h)
    l2 = brentq(f, 1 - mu + 1e-6 * h, 1 - mu + 3 * h)
    l3 = brentq(f, -mu - 1.5, -mu - 0.5)
    return {"mu": mu, "x_l1": l1, "x_l2": l2, "x_l3": l3, "gamma_l1": 1 - mu - l1, "gamma_l2": l2 - 1 + mu}


def core(win, ctx) -> dict:
    t = win.grid(CONTEXT_STEP_S)
    q = earthrot.track(win, ctx.ts, CONTEXT_STEP_S)
    body = {}
    for name, rv, src in (("moon", ctx.moon_rv(t), "de440s:301"), ("sun", ctx.sun_rv(t), "de440s:10")):
        seg = Segment(t0_s=float(t[0]), step_s=CONTEXT_STEP_S, r_km=rv[:, :3], v_kms=rv[:, 3:], source=src, quality="ref")
        body[name] = track([seg], "earth", "f64")
    meta = {
        "built_utc": iso(time.time()), "now_utc": iso(win.now_s), "now_ms": ms(win.now_s),
        "window": {"start_utc": iso(win.t0_s), "stop_utc": iso(win.t1_s), "t0_ms": ms(win.t0_s), "t1_ms": ms(win.t1_s)},
        "frame": "ICRF", "state_type": "geometric", "time": "UTC (no leap second in window)",
        "units": {"pos": "km", "vel": "km/s"},
        "scene": {"mapping": "(X,Y,Z) -> (X,Z,-Y) / 6378.137 km; right handed", "unit_km": RE_KM},
        "frames": {"default": "se_rot", "options": ["se_rot", "eci", "em_rot"],
                   "se_rot": "Earth-centred; x = unit(r_earth - r_sun), z = unit((r_earth - r_sun) x (v_earth - v_sun))",
                   "em_rot": "Earth-centred; x = unit(r_moon), z = unit(r_moon x v_moon)"},
        "earth_orientation": "Skyfield ITRS, builtin timescale, no polar motion; scene-frame quaternion xyzw (MMX)",
        "kernel": {"path": "~/JPLDE/de440s.bsp", "sha256": ctx.kernel_sha256},
        "constants": {"gm_earth": GM_EARTH, "gm_moon": GM_MOON, "gm_sun": GM_SUN, "mu_em": MU_EM, "mu_se": MU_SE,
                      "re_km": RE_KM, "rm_km": RM_KM},
        "mu": {"em": MU_EM, "se": MU_SE},
        "lagrange": {"em": collinear(MU_EM), "se": collinear(MU_SE),
                     "note": "x in units of the instantaneous primary-secondary distance from the barycentre; "
                             "EM L4/L5 at +/-60 deg from the Moon in its instantaneous orbit plane (AM4)"},
        "segment_rule": "continuous joins share the boundary sample; never interpolate across splice/gap/source",
        "moon_check": load_json(BUILD / "moon_check.json", None),
        "software": {"python": sys.version.split()[0], **{k: version(k) for k in ("numpy", "scipy", "skyfield", "jplephem")},
                     "fo": "2026 Mar 01", "three": "r170"},
    }
    return {"schema": "cle/core@1", "meta": meta,
            "earth": {"radius_km": RE_KM, "quat": {"t0_ms": ms(q["t0_s"]), "step_s": q["step_s"], "n": len(q["xyzw"]),
                                                   "xyzw": b64(q["xyzw"], "f64")}},
            "moon": {"radius_km": RM_KM, "track": body["moon"]}, "sun": {"track": body["sun"]}}


def neocp(built: str) -> dict:
    data = load_json(BUILD / "neocp.json", None)
    if not isinstance(data, dict):
        return {"schema": "cle/neocp@1", "built_utc": built, "neocp_snapshot_utc": None, "objects": {}, "removed": []}
    return {"schema": "cle/neocp@1", "built_utc": built, "neocp_snapshot_utc": None, "objects": {}, "removed": []} | data


def manifest(built: str, win, only: tuple[str, ...] = ()) -> Path:
    """dist/data/manifest.js over every sidecar. With `only`, an existing manifest keeps its built_utc, now_utc and
    every other file entry (§13: `export --neocp-only` touches nothing but neocp.js)."""
    old = read_js(DIST / "manifest.js") if only else None
    files = dict(old.get("files", {})) if old else {}
    for p in sorted(DIST.glob("*.js")):
        if p.name != "manifest.js" and (old is None or p.name in only):
            files[p.name] = {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    head = {"schema": "cle/manifest@1", "built_utc": built, "now_utc": iso(win.now_s)}
    if old:
        head = {k: old.get(k, v) for k, v in head.items()}
    return write_js(DIST / "manifest.js", "manifest", head | {"files": files})


def window_of(win) -> list[str]:
    return [iso(win.t0_s), iso(win.t1_s)]


def not_drawn(row: dict, rep: dict, win=None) -> str:
    """Why a draw=Y target has no track in this build ('' when it should have one): a short reason for the page;
    the full error stays in build/validation_report.md. `rep` is the target's build/states_report.json entry; without
    one for the current window the states on disk are never drawn (DW-02)."""
    if rep.get("status") == "failed":
        why = re.sub(rf"^\w+: (?:{re.escape(row['cid'])}: )?", "", rep.get("error", "")).split(" (")[0]
        return f"build failed: {why[:120]} (details: build/validation_report.md)"
    if not (STATES / f"{row['cid']}.json").exists():
        return f"no states built ({rep.get('status') or 'adapter not run'})"
    if not rep or (win is not None and rep.get("window") not in (None, window_of(win))):
        return "not drawn (adapter not run in this window)"
    return ""


def other_window(table: StateTable, win) -> str:
    """Reason when the states were built for another window ('' when they match or carry no window stamp)."""
    if table.t0_utc and [table.t0_utc, table.t1_utc] != window_of(win):
        return f"not drawn (states built for now {table.now_utc}, not this window)"
    return ""


def run(win, ctx, neocp_only: bool = False) -> dict:
    built = iso(time.time())
    live = neocp(built)
    write_js(DIST / "neocp.js", "neocp", live)
    if neocp_only:
        manifest(built, win, only=("neocp.js",))
        return {"files": ["neocp.js"]}
    rows = catalog.targets()
    report = load_json(BUILD / "states_report.json", {})
    report = {k: {"window": report.get("window")} | v for k, v in report.get("targets", {}).items()}
    override = {o["cid"]: o["catalog"] for o in live["objects"].values() if o.get("cid")}
    chunks = {c: {} for c in catalog.CHUNKS}
    entries, skipped = [], []
    for row in rows:
        cid, drawn, rnow = row["cid"], False, ""
        why = not_drawn(row, report.get(cid, {}), win) if row["draw"] == "Y" else ""
        if row["draw"] == "Y" and not why:
            try:
                table = StateTable.load(STATES, cid)
            except Exception as e:
                skipped.append(f"{cid}: {type(e).__name__}: {e}")
                entries.append(catalog.entry(row))
                continue
            why = other_window(table, win)
            segs = [] if why else [s for s in (clip(s, win.t0_s, win.t1_s) for s in table.segments) if s is not None]
            if segs:
                table.segments = segs
                check = regime_now(table, ctx, win, row["regime"])
                rnow, drawn = check["regime_now"], True
                v, trk = table.validation or {}, track(segs, table.centre)
                start, end = span(trk)
                data = {"start_utc": start, "end_utc": end, "quality": sorted({s.quality for s in segs}), "src": sorted(trk["srcs"]),
                        "retrieved_utc": max((p.get("retrieved_utc", "") for p in table.provenance), default="")}
                if cid not in override:          # a live NEOCP track travels in neocp.js only (the page's applyNeocp overrides)
                    chunks[row["chunk"]][cid] = {
                        "track": trk, "events": events(segs, win),
                        "validation": {"midpoint_max_km": v.get("midpoint_max_km"), "midpoint_method": v.get("midpoint_method"),
                                       "tol_km": v.get("tol_km") or TOL_KM[table.centre], "moon_check_km": None,
                                       "regime_check": check.get("check"), "regime_pass": check.get("pass")},
                        "caveat": table.caveat or row.get("caveat", ""), "quality_note": v.get("pred_note", ""),
                    }                            # provenance stays in build/states/<cid>.json (§11)
            else:
                why = why or "no samples inside the window"
                skipped.append(f"{cid}: {why}")
        e = catalog.entry(row, rnow, drawn)
        if why:
            e["not_drawn_reason"] = why
        if drawn and cid in override:            # live NEOCP: flyby subgroup, default visibility, trail (neocp.json entry)
            e |= {k: override[cid][k] for k in ("subgroup", "default_visible", "trail") if k in override[cid]}
            e["chunk"] = "neocp"
        if drawn:
            e["data"] = data
        entries.append(e)
    write_js(DIST / "core.js", "core", core(win, ctx))
    write_js(DIST / "catalog.js", "catalog", {"schema": "cle/catalog@1", "built_utc": built, "now_utc": iso(win.now_s),
                                              "regimes": catalog.regimes(), "groups": catalog.groups(rows),
                                              "chunks": [f"targets-{c}" for c in catalog.CHUNKS], "targets": entries})
    for c, targets in chunks.items():
        write_js(DIST / f"targets-{c}.js", f"targets-{c}", {"schema": "cle/targets@1", "built_utc": built,
                                                            "targets": targets})
    manifest(built, win)
    return {"targets": len(entries), "drawn": sum(e["drawn"] for e in entries), "skipped": skipped,
            "chunks": {c: len(t) for c, t in chunks.items()}}
