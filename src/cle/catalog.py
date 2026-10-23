"""Targets of the viewer: tier 1-2 rows of catalog/master_list.json (ARCHITECTURE.md §4.4, AM3, AM8).

`targets()` returns the master-list rows (the dicts adapters receive) with a few derived keys added:
label (<= 14 chars), chunk (targets-<chunk>.js), centre (interpolation centre), default_visible, staleness.
`entry()` turns one of them into a catalog.js target (§11 shape plus draw/caveat/tier_reason/staleness/left_neocp).
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "catalog/master_list.json"

# Seven colour families (§9.3, review V3): lunar blue; Earth–Moon + translunar lavender; HEO + cislunar HEO teal;
# SE-L1 gold; SE-L2 orange; near-Earth heliocentric magenta; NEOCP rose. OTHER stays neutral. Chosen on #04060c for
# OKLCH chroma ≥ 0.1, list-order adjacent ΔE ≥ 13 under protan/deutan simulation and ≥ 15 normal, and ≥ 8 from the
# Sun, star and Lagrange colours; OKLCH L 0.62–0.81, above the dataviz dark band on purpose (1.5-px lines on
# near-black). Checked by tests/palette.test.mjs; web/theme.css holds the status and reference tokens.
FAMILY = {"LUNAR": "#4f81ff", "EM": "#d1a9ff", "HEO": "#4db99a", "SE_L1": "#e4bb1b", "SE_L2": "#e66c04",
          "HELIO": "#d13ec7", "NEOCP": "#d15a62", "OTHER": "#90a4ae"}
REGIMES = [  # key, label, colour (§9.3), chunk
    ("LUNAR_ORBIT", "Lunar orbit", FAMILY["LUNAR"], "lunar"),
    ("EM_L1", "Earth–Moon L1", FAMILY["EM"], "em"),
    ("EM_L2", "Earth–Moon L2", FAMILY["EM"], "em"),
    ("EM_DRO", "Earth–Moon DRO", FAMILY["EM"], "em"),
    ("EM_OTHER", "Earth–Moon other", FAMILY["EM"], "em"),
    ("TRANSLUNAR", "Translunar", FAMILY["EM"], "em"),
    ("CISLUNAR_HEO", "Cislunar HEO", FAMILY["HEO"], "cislunar"),
    ("HEO", "HEO", FAMILY["HEO"], "heo"),
    ("SE_L1", "Sun–Earth L1", FAMILY["SE_L1"], "se"),
    ("SE_L2", "Sun–Earth L2", FAMILY["SE_L2"], "se"),
    ("EARTH_NEAR_HELIO", "Near-Earth heliocentric", FAMILY["HELIO"], "helio"),
    ("NEOCP_GEO", "NEOCP geocentric", FAMILY["NEOCP"], "neocp"),
    ("OTHER", "Other", FAMILY["OTHER"], "helio"),
]
CHUNK = {k: c for k, _, _, c in REGIMES}
CHUNKS = ("lunar", "em", "cislunar", "heo", "se", "helio", "neocp")
DAY = 86400

LABELS = {  # hand labels where the rules below give a poor short name
    "artemis-p1": "ARTEMIS P1", "artemis-p2": "ARTEMIS P2", "chandrayaan-2-orbiter": "CH-2 orbiter",
    "chandrayaan-3-propulsion-module": "CH-3 PM", "change-3-cz-3b-y23-stage-3": "CE-3 R/B",
    "change-6-orbiter": "CE-6 orbiter", "chandra-x-ray-observatory": "Chandra",
    "astron-proton-blok-dm-stage": "Astron R/B", "beidou-yuanzheng-1-y1-upper-stage": "YZ-1 Y1 R/B",
    "prognoz-6-blok-so-l-stage": "Prognoz-6 R/B", "solrad-11-debris": "SOLRAD 11 deb",
    "solrad-11a-b-pkm": "SOLRAD 11 PKM", "ops-6679": "Vela 4B", "carruthers-geocorona-observatory": "Carruthers",
    "solar-1": "SWFO-L1", "nancy-grace-roman-space-telescope": "Roman", "falcon-heavy-013-stage-2": "FH-013 S2",
    "falcon-9-427-stage-2": "F9-427 S2", "1969-046-fragment": "1969-046 frag", "cluster-ii-rumba": "Cluster Rumba",
    "cluster-ii-tango": "Cluster Tango", "cluster-ii-fm7": "Cluster Samba",
    "vela-4-titan-3c-transtage-10": "Vela 4 R/B", "vela-5-spin-interstage": "Vela 5 istage",
    "vela-5-titan-3c-transtage": "Vela 5 R/B", "vela-6-titan-3c-transtage": "Vela 6 R/B",
    "xmm-newton-ariane-5-stage": "XMM R/B", "yuanzheng-1s-y11": "YZ-1S Y11", "artemis-ii-orion": "Artemis II",
    "cert-2-centaur-v-upper-stage": "Cert-2 Centaur", "vela-2b-fragment": "Vela 2B frag",
}


def load(path: Path = MASTER) -> dict:
    return json.loads(Path(path).read_text())


def num(text):
    try:
        x = float(text)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def label(row: dict) -> str:
    if row["cid"] in LABELS:
        return LABELS[row["cid"]]
    name = re.sub(r"\s*\(.*\)\s*$", "", row["name"]).strip()
    name = re.sub(r"^ETBO\s+", "", name).replace("Chang'e ", "CE-").replace("Chang'e-", "CE-")
    if len(name) <= 14:
        return name
    short = [a for a in row.get("aliases", "").split(";") if 2 <= len(a) <= 14 and not re.search(r"\d{4}-\d{3}", a)]
    return min(short, key=len) if short else name[:13] + "…"


def trail(row: dict) -> dict:
    """Default past/future trail spans in seconds (§9.4, HEO shortened); the window is ±15 d. Live NEOCP objects get
    first obs -> now + 1 d from cle.adapters.neocp."""
    regime, p = row["regime"], num(row.get("period_d"))
    if regime == "LUNAR_ORBIT":
        p = p or 2 / 24
        past, future = min(p, 1), 0.25 * p
    elif regime.startswith("EM_"):
        past, future = 14, 7
    elif regime == "TRANSLUNAR":
        past, future = 15, 3
    elif regime in ("CISLUNAR_HEO", "HEO", "NEOCP_GEO"):      # half a period back, a quarter ahead: no Earth tangle
        past, future = (min(p, 14) * 0.5, min(0.25 * p, 1.5)) if p else (5, 1.5)
    else:
        past, future = 30, 30
    return {"past_s": round(past * DAY), "future_s": round(future * DAY)}


def ephem_sources(row: dict) -> list[dict]:
    """Union of the `kind:ref[start..end]` items of every source row; the primary_ephem one first."""
    from .adapters import primary
    kind, ref, _ = primary(row)
    out, seen = [], set()
    items = [s.get("ephem_sources", "") for s in row.get("sources", [])]
    for item in ";".join(items).split(";"):
        m = re.match(r"^(\w+):(.*?)(?:\[(.*?)\.\.(.*?)\])?$", item.strip())
        if not m or (m[1], m[2]) in seen:
            continue
        seen.add((m[1], m[2]))
        out.append({"kind": m[1], "ref": m[2], "start": m[3] or "", "end": m[4] or "",
                    "primary": (m[1], m[2]) == (kind, ref)})
    if kind and (kind, ref) not in seen:
        out.append({"kind": kind, "ref": ref, "start": "", "end": "", "primary": True})
    return sorted(out, key=lambda s: not s["primary"])


def compact(s: dict) -> str:
    """catalog.js form of an ephem source: `kind:ref[start..end]`, `*` first on the primary one (web/loader.js parseSource)."""
    return ("*" if s["primary"] else "") + f"{s['kind']}:{s['ref']}" + (f"[{s['start']}..{s['end']}]" if s["start"] or s["end"] else "")


def links(row: dict, sources: list[dict]) -> list[dict]:
    out = []
    if row.get("horizons_id"):
        out.append({"label": "Horizons", "url": "https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND="
                    f"'{row['horizons_id']}'&OBJ_DATA=YES&MAKE_EPHEM=NO"})
    for s in sources:
        if s["kind"] == "pluto_mpec" and s["ref"].startswith("http"):
            out.append({"label": "Project Pluto", "url": s["ref"]})
            break
    return out


def staleness(row: dict, now: date | None) -> dict:
    """The catalog's own staleness flag (ARCHITECTURE.md §6.4, review DW-07), one wording for list, page and
    TARGETS.md: 'gp-stale' for a Space-Track GP primary 30-120 d old (covers_window 'stale-gp'), 'extrap' for a
    STALE primary (latest-orbit extrapolation, AM5), else None. age_d counts from the primary's end date to the
    master's generated_for date."""
    from .adapters import primary
    kind, _, stale = primary(row)
    m = re.search(r"\.\.(\d{4}-\d\d-\d\d)\]", row.get("primary_ephem", ""))
    age = (now - date.fromisoformat(m[1])).days if m and now else None
    flag = "extrap" if stale else ("gp-stale" if row.get("covers_window") == "stale-gp" else None)
    return {"flag": flag, "age_d": age if flag or kind == "spacetrack_gp" else None}


def targets(path: Path = MASTER, tiers=(1, 2)) -> list[dict]:
    rows, master = [], load(path)
    now = date.fromisoformat(master["generated_for"]) if master.get("generated_for") else None
    for row in master["objects"]:
        if row["tier"] not in tiers:
            continue
        rows.append(row | {"label": label(row), "chunk": CHUNK.get(row["regime"], "helio"),
                           "centre": "moon" if row["regime"] == "LUNAR_ORBIT" else "earth",
                           "default_visible": row["tier"] == 1 and row["draw"] == "Y",
                           "staleness": staleness(row, now)})
    return rows


def entry(row: dict, regime_now: str = "", drawn: bool = False) -> dict:
    """catalog.js target (§11)."""
    sources = ephem_sources(row)
    norad = num(row.get("norad"))
    return {
        "id": row["cid"], "name": row["name"], "label": row["label"],
        "aliases": [a for a in row.get("aliases", "").split(";") if a],
        "norad": int(norad) if norad else None, "cospar": row.get("cospar") or None,
        "horizons_id": row.get("horizons_id") or None, "group": row["group"],
        "obj_type": row["obj_type"], "natural": row["obj_type"] == "natural",
        "regime": row["regime"], "regime_now": regime_now,
        "status": row["status"], "status_date": row.get("status_date") or None,
        "tier": row["tier"], "tier_reason": row.get("tier_reason", ""), "draw": row["draw"] == "Y",
        "drawn": drawn, "default_visible": row["default_visible"] and drawn,
        "caveat": row.get("caveat", ""), "chunk": f"targets-{row['chunk']}" if drawn else None,
        "staleness": row.get("staleness") or {"flag": None, "age_d": None},
        "left_neocp": row.get("left_neocp") or None, "subgroup": "left" if row.get("left_neocp") else None,
        "centre": row["centre"], "primary_ephem": row.get("primary_ephem", ""), "sources": [compact(s) for s in sources],
        "elements": {"epoch": row.get("elem_epoch") or None, "center": row.get("frame_center") or None,
                     "a_km": num(row.get("a_km")), "e": num(row.get("e")), "i_deg": num(row.get("incl_deg")),
                     "peri_km": num(row.get("perigee_km")), "apo_km": num(row.get("apogee_km")),
                     "period_d": num(row.get("period_d"))},
        "trail": trail(row), "notes": row.get("notes", ""), "links": links(row, sources),
    }


def regimes() -> list[dict]:
    return [{"key": k, "label": lab, "order": i, "color": c, "chunk": ch} for i, (k, lab, c, ch) in enumerate(REGIMES)]


def groups(rows: list[dict]) -> list[dict]:
    out = {}
    for r in rows:
        g = out.setdefault(r["group"], {"key": r["group"], "order": len(out), "count": 0, "tier1": 0})
        g["count"] += 1
        g["tier1"] += r["tier"] == 1
    return list(out.values())
