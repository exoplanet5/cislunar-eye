"""Per-target checks of build/states/* and build/validation_report.md (ARCHITECTURE.md §4.2 step 5, §5.3, §14).

For each target: adapter, source, quality, segments, samples, coverage against the window, the
maximum cubic-Hermite midpoint error, radius range and a regime check at "now". The midpoint error
comes from the adapter (Horizons re-requests the step/2 grid) or, when an adapter module exposes
`resample(target, win, ctx, t_utc_s) -> (n, 6)` in the table's centre, from a half-step re-sample.
Every target also gets a self-consistency estimate: each sample predicted from its two neighbours
(step 2h), divided by 16 for the h^4 law.
"""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

import numpy as np

from .context import GM_EARTH, GM_MOON
from .statetable import StateTable
from .timeutil import iso

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
STATES = BUILD / "states"
HILL_MOON_KM = 61300.0
SE_RANGE_KM = (1.2e6, 1.8e6)
TOL_KM = {"earth": 1.0, "moon": 0.1}                # Hermite midpoint tolerance (§5.3)


def hermite(seg, t):
    """Cubic Hermite position and velocity inside a uniform segment (t within its span)."""
    t = np.atleast_1d(np.asarray(t, dtype=float))
    x = (t - seg.t0_s) / seg.step_s
    k = np.clip(np.floor(x).astype(int), 0, seg.n - 2)
    u, h = (x - k)[:, None], seg.step_s
    p0, p1, v0, v1 = seg.r_km[k], seg.r_km[k + 1], seg.v_kms[k] * h, seg.v_kms[k + 1] * h
    u2, u3 = u * u, u * u * u
    r = (2 * u3 - 3 * u2 + 1) * p0 + (u3 - 2 * u2 + u) * v0 + (-2 * u3 + 3 * u2) * p1 + (u3 - u2) * v1
    dv = (6 * u2 - 6 * u) * p0 + (3 * u2 - 4 * u + 1) * v0 + (-6 * u2 + 6 * u) * p1 + (3 * u2 - 2 * u) * v1
    return r, dv / h


def state_at(table: StateTable, t_s: float):
    """(6,) state in the table centre at t, or None outside every segment."""
    for seg in table.segments:
        if seg.t0_s - 1e-6 <= t_s <= seg.t_s[-1] + 1e-6:
            r, v = hermite(seg, t_s)
            return np.r_[r[0], v[0]]
    return None


def skip_one(seg) -> float:
    if seg.n < 3:
        return float("nan")
    r, v = seg.r_km, seg.v_kms
    guess = (r[:-2] + r[2:]) / 2 + 2 * seg.step_s * (v[:-2] - v[2:]) / 8
    return float(np.linalg.norm(guess - r[1:-1], axis=1).max())


def elements(rv, gm):
    r, v = rv[:3], rv[3:]
    rn, vn = np.linalg.norm(r), np.linalg.norm(v)
    energy = vn ** 2 / 2 - gm / rn
    h = np.cross(r, v)
    e = float(np.linalg.norm(np.cross(v, h) / gm - r / rn))
    a = -gm / (2 * energy) if energy < 0 else float("inf")
    period_d = 2 * np.pi * np.sqrt(a ** 3 / gm) / 86400 if energy < 0 else float("inf")
    return {"r_km": float(rn), "energy": float(energy), "e": e, "a_km": float(a), "period_d": float(period_d),
            "apo_km": float(a * (1 + e)) if energy < 0 else float("inf")}


def regime_now(table: StateTable, ctx, win, declared: str) -> dict:
    """Classify the state at now and check it against the declared regime (§4.2 step 5).
    LUNAR_ORBIT: selenocentric bound and r < 61,300 km. SE_L1/L2: |r| 1.2-1.8e6 km on the right side
    of Earth. CISLUNAR_HEO: bound, apogee >= 1e5 km; HEO: P > 2.4 d, apogee < 1e5 km. Others: reported only."""
    s = state_at(table, win.now_s)
    if s is None:
        return {"regime_now": "", "check": "no data at now", "pass": None}
    moon, sun = ctx.moon_rv(win.now_s)[0], ctx.sun_rv(win.now_s)[0]
    geo = s + moon if table.centre == "moon" else s
    sel = geo - moon
    eg, em = elements(geo, GM_EARTH), elements(sel, GM_MOON)
    side = "SE_L1" if np.dot(geo[:3], sun[:3]) > 0 else "SE_L2"
    if em["energy"] < 0 and em["r_km"] < HILL_MOON_KM:
        now = "LUNAR_ORBIT"
    elif SE_RANGE_KM[0] <= eg["r_km"] <= SE_RANGE_KM[1]:
        now = side
    elif eg["energy"] < 0 and eg["apo_km"] >= 1e5:
        now = "CISLUNAR_HEO"
    elif eg["energy"] < 0:
        now = "HEO" if eg["period_d"] > 2.4 else "OUT_OF_SCOPE"          # P < 2.4 d and apogee < 1e5 km (§1)
    else:
        now = "EARTH_NEAR_HELIO" if eg["r_km"] > 1e6 else "TRANSLUNAR"
    if declared == "LUNAR_ORBIT":
        ok, why = now == "LUNAR_ORBIT", f"selenocentric r {em['r_km']:.0f} km, E {em['energy']:+.3f}"
    elif declared in ("SE_L1", "SE_L2"):
        ok = SE_RANGE_KM[0] <= eg["r_km"] <= SE_RANGE_KM[1] and side == declared
        why = f"|r| {eg['r_km']:.4g} km, {side} side"
    elif declared in ("CISLUNAR_HEO", "HEO"):
        ok, why = now == declared, f"P {eg['period_d']:.2f} d, apogee {eg['apo_km']:.4g} km"
    elif declared == "EARTH_NEAR_HELIO":
        ok, why = eg["energy"] > 0 or eg["r_km"] > 1e6, f"|r| {eg['r_km']:.4g} km, geocentric E {eg['energy']:+.3f}"
    else:
        ok, why = None, f"|r| {eg['r_km']:.4g} km, Moon {em['r_km']:.4g} km"
    if ok is None:
        why += f" (classified {now})"
    return {"regime_now": declared if ok in (True, None) else now, "classified": now, "check": why, "pass": ok,
            "r_geo_km": eg["r_km"], "r_moon_km": em["r_km"]}


def summary(table: StateTable, row: dict, ctx, win, mod=None) -> dict:
    segs = table.segments
    t0, t1 = segs[0].t0_s, segs[-1].t_s[-1]
    covered = sum(s.t_s[-1] - s.t0_s for s in segs)
    geo = []
    for s in segs:
        r = s.r_km + (ctx.moon_rv(s.t_s)[:, :3] if table.centre == "moon" else 0)
        geo.append(np.linalg.norm(r, axis=1))
    geo = np.concatenate(geo)
    v = table.validation or {}
    mid, method = v.get("midpoint_max_km"), v.get("midpoint_method", "adapter" if "midpoint_max_km" in v else "")
    if mid is None and mod is not None and hasattr(mod, "resample"):
        errs = []
        for s in segs:
            tm = s.t_s[:-1] + s.step_s / 2
            ref = np.asarray(mod.resample(row, win, ctx, tm))
            errs.append(np.linalg.norm(hermite(s, tm)[0] - ref[:, :3], axis=1))
        mid, method = float(np.concatenate(errs).max()), "adapter half-step re-sample"
    out = {
        "segments": len(segs), "samples": int(sum(s.n for s in segs)),
        "steps_s": sorted({float(s.step_s) for s in segs}), "quality": sorted({s.quality for s in segs}),
        "sources": sorted({s.source for s in segs}), "joins": [s.join_before for s in segs[1:] if s.join_before != "continuous"],
        "start_utc": iso(t0), "end_utc": iso(t1), "covered_frac": covered / (win.t1_s - win.t0_s),
        "covers_now": state_at(table, win.now_s) is not None,
        "r_geo_min_km": float(geo.min()), "r_geo_max_km": float(geo.max()),
        "midpoint_max_km": mid, "midpoint_method": method,
        "midpoint_tol_km": v.get("tol_km", TOL_KM[table.centre]),
        "self_est_km": float(np.nanmax([skip_one(s) for s in segs] + [0.0])) / 16,
        "gp_age_d": v.get("gp_age_d_now"),                 # cle.adapters.gp: newest GP epoch <= now, days before now
        "staleness": (row.get("staleness") or {}).get("flag"),
        # horizons offline fallback is a dict; the NEOCP adapter's "page_table" / "page_tle" fallback is a string (§11)
        "fallback": v.get("fallback") if isinstance(v.get("fallback"), dict) else None,
        "fit_fallback": v.get("fallback") if isinstance(v.get("fallback"), str) else None,
        "midpoint_unchecked": v.get("midpoint_unchecked"),
        "unrepaired": sum(n.get("action") == "unrepaired" for n in v.get("repairs") or []),
        "solution": v.get("solution"),                     # horizons: {id, note ('Revised:' date), checked_utc} of the daily probe
        "fetched": v.get("fetched"),                       # horizons: requests fetched for this target in the run that built it
    }
    if table.centre == "moon":
        sel = np.concatenate([np.linalg.norm(s.r_km, axis=1) for s in segs])
        out |= {"r_moon_min_km": float(sel.min()), "r_moon_max_km": float(sel.max())}
    return out | regime_now(table, ctx, win, row["regime"])


def run(win, ctx, rows: list[dict]) -> dict:
    from .adapters import KIND_TO_MODULE, dispatch, primary
    states_report = load_json(BUILD / "states_report.json", {})
    results, t_start = [], time.time()
    for row in rows:
        cid = row["cid"]
        kind, ref, stale = primary(row)
        item = {"cid": cid, "tier": row["tier"], "regime": row["regime"], "draw": row["draw"],
                "primary": row.get("primary_ephem", ""), "adapter": "extrap" if stale else KIND_TO_MODULE.get(kind, ""),
                "status": ""}
        rep = states_report.get("targets", {}).get(cid, {})
        item |= {k: rep[k] for k in ("error", "traceback", "seconds") if k in rep}
        if row["draw"] != "Y":
            results.append(item | {"status": "not drawn"})
            continue
        if rep.get("status") == "failed" or not (STATES / f"{cid}.json").exists():
            results.append(item | {"status": "failed (not drawn)" if rep.get("status") == "failed" else rep.get("status", "missing")})
            continue
        try:
            table = StateTable.load(STATES, cid)
            try:
                mod = dispatch(row)
            except Exception:
                mod = None
            item |= summary(table, row, ctx, win, mod)
            v = table.validation or {}
            if "fit_rms_km" in (v.get("latest") or {}):        # AM5 bgtle start state fitted to the file's last 20 d
                item["start_fit"] = {k: v["latest"].get(k) for k in ("file", "single_state_rms_km", "fit_rms_km", "fit_dr_km", "fit_dv_ms",
                                                                     "fit_span")} | {"epoch_utc": v.get("epoch_utc"), "sigma_km_now": v.get("sigma_km_now")}
            item["status"] = "ok" if rep.get("status", "ok") == "ok" else f"{rep['status']} (previous states kept)"
            built = rep.get("window") or states_report.get("window")        # DW-02: export draws only this window
            if built and built != [iso(win.t0_s), iso(win.t1_s)]:
                item["status"] += f" (built for window {built[0][:13]}..{built[1][:13]}, not drawn)"
        except Exception as e:
            item |= {"status": "validate error", "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
        results.append(item)
    report = {"built_utc": iso(time.time()), "now_utc": iso(win.now_s), "window": [iso(win.t0_s), iso(win.t1_s)],
              "states_window": states_report.get("window"), "moon_check": load_json(BUILD / "moon_check.json", None),
              "targets": results, "seconds": round(time.time() - t_start, 1)}
    write_json(BUILD / "validation.json", report)
    write_atomic(BUILD / "validation_report.md", markdown(report))
    return report


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_atomic(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def write_json(path: Path, value):
    write_atomic(path, json.dumps(value, indent=1, default=float, allow_nan=True))


def fmt(x, nd=3):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}g}"
    return str(x)


def markdown(rep: dict) -> str:
    rows = rep["targets"]
    drawn = [r for r in rows if r["draw"] == "Y"]
    ok = [r for r in drawn if r.get("segments")]
    failed = [r for r in drawn if r.get("error")]
    by_adapter = {}
    for r in drawn:
        by_adapter.setdefault(r.get("adapter") or "?", [0, 0])
        by_adapter[r.get("adapter") or "?"][0] += 1
        by_adapter[r.get("adapter") or "?"][1] += bool(r.get("segments"))
    lines = [f"# Validation report", "",
             f"Built {rep['built_utc']} · now {rep['now_utc']} · window {rep['window'][0]} → {rep['window'][1]}", ""]
    if rep.get("states_window") and rep["states_window"] != rep["window"]:
        lines += [f"**Warning:** build/states were made for window {rep['states_window']}.", ""]
    mc = rep.get("moon_check")
    if mc:
        lines += [f"Moon check (Horizons 301 vs DE440s, {mc['samples']} daily samples): max {mc['max_km']:.4f} km "
                  f"({'pass' if mc['pass'] else 'FAIL'}, limit 1 km).", ""]
    lines += [f"Targets: {len(rows)} (draw=Y {len(drawn)}, with states {len(ok)}, failed {len(failed)}).", "",
              "| adapter | targets | with states |", "|---|---:|---:|"]
    lines += [f"| {k} | {a} | {b} |" for k, (a, b) in sorted(by_adapter.items())]
    worst = sorted((r for r in ok if r.get("midpoint_max_km") is not None),
                   key=lambda r: -r["midpoint_max_km"] / (r.get("midpoint_tol_km") or 1))[:10]
    lines += ["", "## Worst midpoint errors (relative to tolerance)", "", "| target | max km | tol km | method |",
              "|---|---:|---:|---|"]
    lines += [f"| {r['cid']} | {fmt(r['midpoint_max_km'])} | {fmt(r.get('midpoint_tol_km'))} | {r['midpoint_method']} |"
              for r in worst]
    gp_stale = [r for r in rows if r.get("staleness") == "gp-stale"]
    if gp_stale:
        lines += ["", f"Space-Track GP primaries 30–120 d old (catalog `stale-gp`, drawn as stale): "
                  + ", ".join(f"{r['cid']} ({fmt(r.get('gp_age_d'), 3)} d)" for r in gp_stale) + "."]
    fb = [r for r in rows if r.get("fallback")]
    if fb:                                                 # DW-04(c): Horizons served from the newest cached snapshot
        lines += ["", "Horizons offline fallback (cached snapshot, caveat on the track): "
                  + ", ".join(f"{r['cid']} (snapshot {r['fallback'].get('snapshot_utc')}"
                              + (", re-gridded" if r['fallback'].get('regrid') else "")
                              + (f", {r['midpoint_unchecked']} midpoints unchecked" if r.get('midpoint_unchecked') else "")
                              + (f", {r['unrepaired']} pieces unrepaired" if r.get('unrepaired') else "") + ")" for r in fb) + "."]
    hz = [r for r in rows if r.get("solution")]
    if hz:                                                 # EFF-4: solution probe per target per UTC day; tiles refetched on a new one
        got = [r for r in hz if r.get("fetched")]
        lines += ["", f"Horizons solutions probed for {len(hz)} targets (checked "
                  + (min(r['solution'].get('checked_utc') or 'n/a' for r in hz)[:16] + "Z") + "); requests fetched when built: "
                  + (", ".join(f"{r['cid']} {r['fetched']}" for r in got) if got else "none, all from cache") + "."]
    lines += ["", "## Targets", "",
              "| target | tier | adapter | status | quality | seg | samples | span | cover | now | GP age d | mid km | est km "
              "| r geo km | regime now | check |", "|---|---:|---|---|---|---:|---:|---|---:|---|---:|---:|---:|---|---|---|"]
    for r in rows:
        if not r.get("segments"):
            lines.append(f"| {r['cid']} | {r['tier']} | {r.get('adapter') or ''} | {r['status']} | | | | | | | | | | | | |")
            continue
        span = f"{r['start_utc'][5:16]}→{r['end_utc'][5:16]}"
        rr = f"{r['r_geo_min_km']:.3g}–{r['r_geo_max_km']:.3g}"
        chk = {True: "pass", False: "**FAIL**", None: "n/a"}[r.get("pass")]
        age = fmt(r.get("gp_age_d"), 3) if r.get("gp_age_d") is not None else ""
        lines.append(f"| {r['cid']} | {r['tier']} | {r.get('adapter', '')} | {r['status']} | {','.join(r['quality'])} | "
                     f"{r['segments']} | {r['samples']} | {span} | {r['covered_frac']:.0%} | "
                     f"{'Y' if r['covers_now'] else 'N'} | {age}{' stale' if r.get('staleness') == 'gp-stale' else ''} | "
                     f"{fmt(r['midpoint_max_km'])} | {fmt(r['self_est_km'])} | {rr} | "
                     f"{r.get('regime_now', '')} | {chk}: {r.get('check', '')} |")
    fits = [r for r in ok if r.get("start_fit")]
    if fits:
        lines += ["", "## Extrapolation start states (AM5, Bill Gray TLEs)", "",
                  "One n-body state least-squares fitted to the file's own get_vect positions over its last 20 d (3 h samples); "
                  "the last TLE's single state is kept only when it fits better. rms > 1,000 km is flagged in the caveat.", "",
                  "| target | file | epoch | single-TLE rms km | fitted rms km | dr km | dv m/s | sigma now km |",
                  "|---|---|---|---:|---:|---:|---:|---:|"]
        for r in fits:
            f = r["start_fit"]
            lines.append(f"| {r['cid']} | {f['file']} | {f['epoch_utc']} | {fmt(f['single_state_rms_km'], 5)} | "
                         f"{fmt(f['fit_rms_km'], 5)} | {fmt(f.get('fit_dr_km'))} | {fmt(f.get('fit_dv_ms'))} | {fmt(f['sigma_km_now'])} |")
    if failed:
        lines += ["", "## Failures", ""]
        for r in failed:
            lines += [f"### {r['cid']} ({r.get('adapter') or r['primary']})", "", f"`{r['error']}`", ""]
            if r.get("traceback"):
                lines += ["```", r["traceback"].rstrip(), "```", ""]
    return "\n".join(lines) + "\n"
