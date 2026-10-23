#!/usr/bin/env python3
"""Merge the per-source extracts into the cislunar-eye master target list.

Inputs   catalog/sources/{neocpsat,billgray,rocketdebris,lunarmission,horizons,neocp,news,spacetrack,manual}.tsv
         catalog/sources/horizons_all_spacecraft.tsv   (distances at the sweep epochs: d_m15/d_0/d_p15_km
                                                        relative to its sweep_now column, or dated d_YYYYMMDD_km)
         catalog/overrides.tsv                         (hand corrections, applied last)
         catalog/verify_report.tsv                     (scripts/verify_master.py; used only when not older than --now)
Outputs  catalog/master_list.tsv, catalog/master_list.json, catalog/TARGETS.md
         (the standing decisions and open questions live in catalog/DECISIONS.md)

Space-Track GP primaries are graded by age: <= 30 d covers the window; 30-120 d still drives the GP
adapter but is marked covers_window 'stale-gp' with a "stale: ..." caveat; older sets are unusable.

Linking: rows are joined on NORAD, Horizons ID, COSPAR, NEOCP temporary designation and
normalised name, in that order. A join is refused when the two groups hold different real
NORAD numbers, or different Horizons IDs that the Horizons sweep itself verified; refusals
are listed under "Conflicts" in TARGETS.md. Bill Gray / analyst numbers 79000-99999 are
surrogates, not real NORAD numbers.

Usage:  ~/.venvs/astro313/bin/python -I scripts/build_master.py [--now YYYY-MM-DD] [--half-window DAYS]
        (defaults: today UTC, 15 d, i.e. a 30-day window now-15 d .. now+15 d)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "catalog/sources"
OUT_TSV = ROOT / "catalog/master_list.tsv"
OUT_JSON = ROOT / "catalog/master_list.json"
OUT_MD = ROOT / "catalog/TARGETS.md"
OVERRIDES = ROOT / "catalog/overrides.tsv"
VERIFY = ROOT / "catalog/verify_report.tsv"
DECISIONS = "catalog/DECISIONS.md"

HALF_WINDOW_D = 15
NOW = datetime.now(timezone.utc).date()          # reset in main() from --now
W0, W1 = NOW - timedelta(days=HALF_WINDOW_D), NOW + timedelta(days=HALF_WINDOW_D)
RECENT_END = NOW - timedelta(days=365)  # ended within a year: kept for replay (tier 2)
STALE_DAYS = 365
GP_FRESH_DAYS, GP_OK_DAYS = 30, 120    # GP age: <= 30 d 'window', <= 120 d 'stale-gp', older unusable
FIT_OK_DAYS = 30                       # a find_orb fit / obs arc ending this recently still counts
NEAR_KM = 1.0e7                        # "near Earth" during the window
P_THRESHOLD_D = 2.4
AU_KM = 149597870.7

SOURCES = ["news", "horizons", "lunarmission", "billgray", "neocpsat", "rocketdebris", "spacetrack", "neocp", "manual"]
NAME_RANK = {"manual": 0, "news": 1, "lunarmission": 2, "billgray": 3, "neocpsat": 4, "horizons": 5,
             "rocketdebris": 6, "spacetrack": 7, "neocp": 8}
# Vote weights for regime / status. Horizons rows that cover now are measured states.
WEIGHT = {"manual": 10, "news": 2.5, "lunarmission": 2, "billgray": 2, "neocpsat": 1.5,
          "rocketdebris": 1, "spacetrack": 0.5, "neocp": 1, "horizons": 1.5}
TERMINAL = {"decayed", "lunar_impact", "landed"}

GROUPS = [  # (group, regimes) in display order
    ("Lunar orbit", {"LUNAR_ORBIT"}),
    ("Earth–Moon libration, DRO & resonant", {"EM_L1", "EM_L2", "EM_DRO", "EM_OTHER"}),
    ("Cislunar HEO (apogee ≥ 100 000 km)", {"CISLUNAR_HEO"}),
    ("High Earth orbit (P > 2.4 d)", {"HEO"}),
    ("Sun–Earth L1", {"SE_L1"}),
    ("Sun–Earth L2", {"SE_L2"}),
    ("Transfer / translunar", {"TRANSLUNAR"}),
    ("Earth-return / near-Earth heliocentric", {"EARTH_NEAR_HELIO"}),
    ("NEOCP live", set()),
    ("NEOCP archive (unidentified apparitions)", set()),
    ("Natural companions (minimoons, quasi-satellites)", set()),
    ("Other", {"OTHER"}),
]

COLS = ["cid", "tier", "tier_reason", "group", "regime", "name", "aliases", "obj_type", "status", "status_date",
        "norad", "cospar", "horizons_id", "primary_ephem", "secondary_ephem", "covers_window", "dist_window_km",
        "tracklets", "draw", "caveat", "period_d", "a_km", "e", "incl_deg", "perigee_km", "apogee_km", "elem_epoch",
        "frame_center", "srcs", "notes"]

DESIG_RE = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9]{7,8}$")
COSPAR_RE = re.compile(r"^(\d{4})-?(\d{3})([A-Z]{1,3})$")
EPH_RE = re.compile(r"^(\w+):(.*?)\[([^\[\]]*?)\.\.([^\[\]]*?)\]$")


# ----------------------------------------------------------------------------- helpers
def read_tsv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def pdate(s):
    s = (s or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def real_norad(n):
    return n is not None and not (79000 <= n <= 99999)


def norm_name(s):
    s = s.lower().replace("chang'e", "change").replace("’", "'")
    s = re.sub(r"\(.*?\)", " ", s) if len(re.sub(r"\(.*?\)", "", s).strip()) >= 4 else s
    return re.sub(r"[^a-z0-9]", "", s)


def norm_cospar(s):
    m = COSPAR_RE.match((s or "").strip().upper())
    if not m or m[2] == "999" or m[3].startswith("ZZ"):
        return ""
    return f"{m[1]}-{m[2]}{m[3]}"


def slug(s):
    s = s.lower().replace("chang'e", "change").replace("'", "")
    s = re.sub(r"\(.*?\)", "", s)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s)).strip("-")[:40] or "obj"


def parse_ephem(field):
    out = []
    for item in filter(None, (x.strip() for x in (field or "").split(";"))):
        m = EPH_RE.match(item)
        if m:
            out.append({"kind": m[1], "ref": m[2], "start": pdate(m[3]), "end": pdate(m[4]), "raw": item})
        elif ":" in item:
            out.append({"kind": item.split(":")[0], "ref": item.split(":", 1)[1], "start": None, "end": None, "raw": item})
    return out


# ----------------------------------------------------------------------------- load
def load_rows():
    rows = []
    for src in SOURCES:
        path = SRC / f"{src}.tsv"
        if not path.exists():
            continue
        for r in read_tsv(path):
            r = {k: (v or "").strip() for k, v in r.items() if k}
            r["src"] = src
            n = r.get("norad", "")
            r["_norad"] = int(n) if n.isdigit() else None
            r["_hz"] = r.get("horizons_id", "")
            r["_cospar"] = "" if src == "horizons" else norm_cospar(r.get("cospar"))
            r["_eph"] = parse_ephem(r.get("ephem_sources"))
            names = [r.get("name", "")] + [a for a in r.get("aliases", "").split(";")]
            toks = set()
            for t in names + [r.get("src_ref", "")]:
                for w in re.split(r"[\s;,()=/]+", t or ""):
                    if DESIG_RE.match(w):
                        toks.add(w)
            r["_desig"] = toks
            nm = norm_name(r.get("name", ""))
            generic = src == "rocketdebris" and not r.get("src_ref", "").startswith("horizons_status")
            r["_name"] = nm if len(nm) >= 4 and not generic else ""
            rows.append(r)
    return rows


# ----------------------------------------------------------------------------- clustering
class Clusters:
    def __init__(self, rows):
        self.rows = rows
        self.parent = list(range(len(rows)))
        self.norads = [{r["_norad"]} if real_norad(r["_norad"]) else set() for r in rows]
        self.hzv = [{r["_hz"]} if r["src"] == "horizons" and r["_hz"] else set() for r in rows]
        self.conflicts = []
        self.weak = []

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j, why):
        a, b = self.find(i), self.find(j)
        if a == b:
            return
        if self.norads[a] and self.norads[b] and self.norads[a] != self.norads[b]:
            self.conflicts.append((why, self.rows[i], self.rows[j], "different NORAD"))
            return
        if self.hzv[a] and self.hzv[b] and self.hzv[a] != self.hzv[b]:
            self.conflicts.append((why, self.rows[i], self.rows[j], "different verified Horizons ID"))
            return
        self.parent[b] = a
        if why.split("=")[0] in ("name", "desig"):
            self.weak.append((why, self.rows[i], self.rows[j]))
        self.norads[a] |= self.norads[b]
        self.hzv[a] |= self.hzv[b]

    def link(self):
        for kind, keyf in [("norad", lambda r: [r["_norad"]] if r["_norad"] else []),
                           ("horizons", lambda r: [r["_hz"]] if r["_hz"] else []),
                           ("cospar", lambda r: [r["_cospar"]] if r["_cospar"] else []),
                           ("desig", lambda r: sorted(r["_desig"])),
                           ("name", lambda r: [r["_name"]] if r["_name"] else [])]:
            first = {}
            for i, r in enumerate(self.rows):
                for k in keyf(r):
                    if k in first:
                        if kind == "name" and self.rows[first[k]]["src"] == r["src"]:
                            continue          # one source never lists the same object twice by name
                        self.union(first[k], i, f"{kind}={k}")
                    else:
                        first[k] = i
        groups = defaultdict(list)
        for i in range(len(self.rows)):
            groups[self.find(i)].append(self.rows[i])
        return list(groups.values())


# ----------------------------------------------------------------------------- ephemeris choice
def judge(e, from_horizons_src):
    """Return (rank, coverage, note) for one ephemeris entry; rank None = unusable now."""
    k, s, t = e["kind"], e["start"], e["end"]
    if k == "horizons":
        if not from_horizons_src:
            return None, "unverified", ""
        if s and s > W1 or t and t < W0:
            return None, "outside", f"span {s}..{t}"
        full = (not s or s <= W0) and (not t or t >= W1)
        return (0 if full else 1), ("window" if full else f"partial {s}..{t}"), ""
    if k == "bgtle":
        if s and t and s <= W1 and t >= W0:
            full = s <= W0 and t >= W1
            return 2, ("window" if full else f"partial {s}..{t}"), ""
        return None, "outside", ""
    if k == "spacetrack_gp":
        ep = s or t
        age = abs((ep - NOW).days) if ep else None
        if age is not None and age <= GP_FRESH_DAYS:
            return 3, "window", f"GP {ep}"
        if age is not None and age <= GP_OK_DAYS:
            return 6, "stale-gp", f"GP {ep}, {age} d old"
        return None, "outside", ""
    if k in ("neocpsat_fit", "pluto_mpec", "neocp_obs"):
        last = t or s
        if last and last >= min(W0, NOW - timedelta(days=FIT_OK_DAYS)):
            return (4 if k == "neocpsat_fit" else 5), "fit", f"arc to {last}"
        return None, "outside", ""
    return None, "unknown", ""


def end_of(e):
    return e["end"] or e["start"]


def choose_ephem(cluster):
    usable, stale = [], []
    for r in cluster:
        for e in r["_eph"]:
            if e["kind"] == "neocp_obs":
                continue              # obs are tracklets, not an ephemeris on their own
            rank, cov, note = judge(e, r["src"] == "horizons")
            if rank is not None:
                usable.append((rank, cov, note, e))
            elif e["kind"] != "horizons" or r["src"] == "horizons":
                t = end_of(e)
                if t and t < W0 and (W0 - t).days <= STALE_DAYS:
                    stale.append((t, e))
    usable.sort(key=lambda x: (x[0], -(end_of(x[3]) or date.min).toordinal()))
    seen, picks = set(), []
    for rank, cov, note, e in usable:
        key = (e["kind"], e["ref"])
        if key not in seen:
            seen.add(key)
            picks.append((rank, cov, note, e))
    stale.sort(key=lambda x: x[0], reverse=True)
    return picks, stale


# ----------------------------------------------------------------------------- per-cluster attributes
def vote(cluster, field, ignore=("", "unknown"), covers_bonus=True):
    score, latest = Counter(), {}
    for r in cluster:
        v = r.get(field, "")
        if v in ignore:
            continue
        w = WEIGHT.get(r["src"], 1)
        if covers_bonus and r["src"] == "horizons" and r.get("covers_now") == "Y":
            w = 3.5
        score[v] += w
        d = pdate(r.get("status_date"))
        if d and (v not in latest or d > latest[v][0]):
            latest[v] = (d, r)
    if not score:
        return "unknown" if field == "status" else "OTHER", None
    best = max(score, key=lambda v: (score[v], latest.get(v, (date.min,))[0]))
    return best, latest.get(best, (None, None))[1]


def pick_status(cluster):
    # Terminal events recorded by SATCAT / the wikis with a past date win over "active" votes.
    terminal = [(pdate(r.get("status_date")), r) for r in cluster
                if r.get("status") in TERMINAL and pdate(r.get("status_date")) and pdate(r.get("status_date")) <= NOW]
    if terminal:
        d, r = max(terminal, key=lambda x: x[0])
        later = [x for x in cluster if x.get("status") not in TERMINAL | {"", "unknown"}
                 and pdate(x.get("status_date")) and pdate(x.get("status_date")) > d]
        if not later:
            return r["status"], r.get("status_date", "")
    s, r = vote(cluster, "status")
    return s, (r or {}).get("status_date", "")


def pick_elements(cluster):
    best = None
    for r in cluster:
        if not (fnum(r.get("a_km")) or fnum(r.get("period_d")) or fnum(r.get("apogee_km"))):
            continue
        ep = r.get("elem_epoch", "")
        if best is None or ep > best.get("elem_epoch", ""):
            best = r
    return best or {}


REL_EPOCH = {"m15": -15, "0": 0, "p15": 15}        # horizons_sweep.py window-relative distance columns


def load_distances():
    """{horizons_id: distances} plus the sweep metadata (epochs, counts) for TARGETS.md. Each audit row's
    distance epochs are its d_YYYYMMDD_km columns, or d_m15/d_0/d_p15_km around its sweep_now date; the
    window distance is the minimum over the epochs inside W0..W1, else the epoch nearest to now (frozen)."""
    d, meta = {}, {"ids": 0, "probes": 0, "sweep_now": set(), "in_window": 0, "frozen": 0}
    path = SRC / "horizons_all_spacecraft.tsv"
    if path.exists():
        for r in read_tsv(path):
            meta["ids" if r.get("kind") == "mb" else "probes"] += 1
            base, ep = pdate(r.get("sweep_now")), {}
            for k, v in r.items():
                m = re.fullmatch(r"d_(\d{8}|m15|0|p15)_km", k or "")
                if not m or fnum(v) is None:
                    continue
                if len(m[1]) == 8:
                    ep[datetime.strptime(m[1], "%Y%m%d").date()] = fnum(v)
                elif base:
                    ep[base + timedelta(days=REL_EPOCH[m[1]])] = fnum(v)
            if ep:
                meta["sweep_now"].add(base or sorted(ep)[len(ep) // 2])
            vals = [v for t, v in ep.items() if W0 <= t <= W1]
            frozen = not vals and bool(ep)
            if frozen:
                vals = [ep[min(ep, key=lambda t: abs((t - NOW).days))]]
            if r.get("in_scope") == "Y" and ep:
                meta["frozen" if frozen else "in_window"] += 1
            d[r["horizons_id"]] = {"min_window": min(vals) if vals else None, "frozen": frozen,
                                   "min_2024_2030": fnum(r.get("min_d_2024_2030_km")),
                                   "min_date": r.get("min_d_date", "")}
    return d, meta


def build():
    rows = load_rows()
    cl = Clusters(rows)
    clusters = cl.link()
    dist, sweep = load_distances()
    verified_hz = {r["_hz"] for r in rows if r["src"] == "horizons" and r["_hz"]}
    out, used_cids = [], set()

    for c in clusters:
        c.sort(key=lambda r: NAME_RANK.get(r["src"], 9))
        srcs = sorted({r["src"] for r in c}, key=lambda s: NAME_RANK.get(s, 9))
        name_row = c[0]
        name = name_row["name"]
        aliases = []
        for r in c:
            for a in [r["name"]] + r.get("aliases", "").split(";"):
                a = a.strip()
                if a and a != name and a not in aliases:
                    aliases.append(a)
        norads = Counter(r["_norad"] for r in c if real_norad(r["_norad"]))
        surrogates = Counter(r["_norad"] for r in c if r["_norad"] and not real_norad(r["_norad"]))
        norad = norads.most_common(1)[0][0] if norads else (surrogates.most_common(1)[0][0] if surrogates else "")
        cospars = Counter(r["_cospar"] for r in c if r["_cospar"])
        cospar = cospars.most_common(1)[0][0] if cospars else ""
        hz_v = [r["_hz"] for r in c if r["src"] == "horizons" and r["_hz"]]
        hz_cov = [r["_hz"] for r in c if r["src"] == "horizons" and r["_hz"] and r.get("covers_now") == "Y"]
        hz_other = [r["_hz"] for r in c if r["_hz"] and r["src"] != "horizons" and r["_hz"] not in verified_hz]
        horizons_id = (hz_cov or hz_v or hz_other or [""])[0]
        regime, _ = vote(c, "regime", ignore=("",))
        status, status_date = pick_status(c)
        obj_type, _ = vote(c, "obj_type", covers_bonus=False)
        el = pick_elements(c)

        picks, stale = choose_ephem(c)
        end_d = pdate(status_date)
        if status in TERMINAL and end_d and end_d < W0:
            # Gone before the window opens: nothing can cover it; keep the sources for replay only.
            stale = sorted(stale + [(end_of(x[3]), x[3]) for x in picks if end_of(x[3])], key=lambda x: x[0], reverse=True)
            picks = []
        tracklets = sorted({e["ref"] for r in c for e in r["_eph"] if e["kind"] == "neocp_obs"})
        live_obs = any(e["kind"] in ("neocp_obs", "pluto_mpec") and end_of(e)
                       and end_of(e) >= min(W0, NOW - timedelta(days=FIT_OK_DAYS))
                       for r in c for e in r["_eph"])
        primary = f"{picks[0][3]['raw']} ({picks[0][1]}{'; ' + picks[0][2] if picks[0][2] else ''})" if picks else ""
        secondary = picks[1][3]["raw"] if len(picks) > 1 else ""
        if not picks and stale:
            ended = sorted([x for x in stale if x[1]["kind"] in ("horizons", "bgtle")],
                           key=lambda x: x[1]["kind"] != "horizons") if status in TERMINAL else []
            primary = f"STALE {(ended or stale)[0][1]['raw']}"
        dw = dist.get(horizons_id, {})
        dmin = dw.get("min_window")

        period = fnum(el.get("period_d"))
        frame = el.get("frame_center", "")
        bound_geo = regime in {"CISLUNAR_HEO", "HEO", "LUNAR_ORBIT", "EM_L1", "EM_L2", "EM_DRO", "EM_OTHER"}
        near = (dmin is not None and dmin < NEAR_KM) or (dmin is None and (bound_geo or regime in {"SE_L1", "SE_L2"})) \
            or live_obs
        returning = dw.get("min_2024_2030") is not None and dw["min_2024_2030"] < 0.05 * AU_KM \
            and (pdate(dw.get("min_date")) or date.min) > W1
        replayable = [e for r in c for e in r["_eph"] if e["kind"] in ("horizons", "bgtle")
                      and (e["kind"] != "horizons" or r["src"] == "horizons")]
        future_only = all((e["start"] or date.min) > W1 for r in c for e in r["_eph"]) and any(r["_eph"] for r in c)
        below = regime in {"CISLUNAR_HEO", "HEO"} and period is not None and frame in ("earth", "") \
            and period <= P_THRESHOLD_D
        sd = pdate(status_date)
        neocp_only = all(r["src"] in ("neocp", "neocpsat") for r in c) and not norad
        listed = [r for r in c if r["src"] == "neocp" and r.get("listed_now") in ("Y", "N")]   # neocp_live.py columns
        left_neocp = max((r.get("last_listed", "")[:10] for r in listed), default="") \
            if listed and all(r["listed_now"] == "N" for r in listed) else ""

        if status in TERMINAL:
            if sd and sd >= RECENT_END and replayable:
                tier, why = 2, f"{status} {status_date}: recent, kept for replay"
            else:
                tier, why = 3, f"{status} {status_date}".strip()
        elif future_only:
            tier, why = 3, "ephemeris starts after the window (not launched)"
        elif regime == "OTHER":
            tier, why = 3, "outside the target regimes" + (f" ({dmin/1e6:.0f} Mkm in window)" if dmin else "")
        elif regime == "EARTH_NEAR_HELIO" or status == "escaped":
            if near and picks:
                tier, why = 1, "near Earth in window" + (f" (min {dmin/1e6:.2f} Mkm)" if dmin else " (observed)")
            elif returning and (picks or stale):
                tier, why = 2, f"returns {dw['min_date'][:10]} (min {dw['min_2024_2030']/1e6:.2f} Mkm)"
            elif near and stale:
                tier, why = 2, f"stale ephemeris, last {stale[0][0]} (near Earth in window)"
            else:
                tier, why = 3, "heliocentric, far in window" + (f" ({dmin/1e6:.0f} Mkm)" if dmin else "")
        elif below:
            tier, why = (2, f"P {period:.2f} d ≤ 2.4 d threshold") if picks else (3, f"P {period:.2f} d ≤ 2.4 d, no current ephemeris")
        elif picks:
            tier, why = 1, "ephemeris covers window"
        elif stale:
            tier, why = 2, f"stale ephemeris, last {stale[0][0]}"
        else:
            tier, why = 3, "no usable ephemeris"
        if obj_type == "natural":
            tier, why = 2, "natural object, kept: " + why
        if obj_type == "natural":
            group = "Natural companions (minimoons, quasi-satellites)"
        elif regime == "NEOCP_GEO" or (neocp_only and regime != "OTHER"):
            group = "NEOCP live" if live_obs and any(r["src"] == "neocp" for r in c) else "NEOCP archive (unidentified apparitions)"
            if group == "NEOCP live" and status not in TERMINAL:
                tier, why = 1, f"left the NEOCP {left_neocp}" if left_neocp else "on NEOCP now"
        else:
            group = next((g for g, regs in GROUPS if regime in regs), "Other")

        base = slug(name)
        cid, k = base, 2
        while cid in used_cids:
            cid, k = f"{base}-{k}", k + 1
        used_cids.add(cid)
        caveats = []
        if picks:
            k0, e0 = picks[0][3]["kind"], picks[0][3]
            if k0 == "spacetrack_gp":
                age = abs((NOW - (e0["start"] or e0["end"])).days)
                caveats.append(f"stale: Space-Track GP {age} d old (epoch {e0['start']})" if picks[0][1] == "stale-gp"
                               else f"Space-Track GP {e0['start']} (age {age} d)")
                if regime.startswith("EM_"):
                    caveats.append("CR3BP orbit: Earth+Moon+Sun trajectory fitted to recent GP sets, not SDP4")
            elif k0 in ("neocpsat_fit", "pluto_mpec"):
                caveats.append(f"find_orb fit, arc to {end_of(e0)}; extrapolated beyond it")
            elif k0 == "horizons" and e0["end"] and e0["end"] < W1:
                caveats.append(f"Horizons ephemeris ends {e0['end']}")
            elif k0 == "bgtle" and e0["end"] and e0["end"] < W1:
                caveats.append(f"Bill Gray TLE Range ends {e0['end']}")
        elif stale:
            caveats.append(f"stale: latest orbit ends {stale[0][0]}; extrapolated")
        if obj_type == "natural":
            caveats.append("natural object")
        if dw.get("frozen") and dmin is not None and sweep["sweep_now"]:
            caveats.append(f"Horizons distance from the sweep of {max(sweep['sweep_now'])}, not re-probed for this window")
        notes = []
        for r in c:
            if r.get("notes") and len("; ".join(notes)) < 260:
                notes.append(f"[{r['src']}] {r['notes'][:180]}")
        out.append({
            "cid": cid, "tier": tier, "tier_reason": why, "group": group, "regime": regime, "name": name,
            "aliases": ";".join(aliases[:12]), "obj_type": obj_type, "status": status, "status_date": status_date,
            "norad": norad, "cospar": cospar, "horizons_id": horizons_id, "primary_ephem": primary,
            "secondary_ephem": secondary,
            "covers_window": ("stale-gp" if picks[0][1] == "stale-gp" else "Y") if picks else ("stale" if stale else "N"),
            "dist_window_km": f"{dmin:.0f}" if dmin is not None else "", "tracklets": len(tracklets),
            "draw": "Y" if primary and status not in TERMINAL else "N",
            "caveat": "; ".join(caveats + (["ended before the window; replay needs history mode"] if status in TERMINAL else [])),
            "period_d": el.get("period_d", ""), "a_km": el.get("a_km", ""), "e": el.get("e", ""),
            "incl_deg": el.get("incl_deg", ""), "perigee_km": el.get("perigee_km", ""),
            "apogee_km": el.get("apogee_km", ""), "elem_epoch": el.get("elem_epoch", ""), "frame_center": frame,
            "srcs": ",".join(srcs), "notes": " | ".join(notes),
            "_rows": [{k: v for k, v in r.items() if not k.startswith("_")} for r in c],
            "_tracklet_files": tracklets, "_left_neocp": left_neocp,
        })
    return out, cl.conflicts, cl.weak, sweep


# ----------------------------------------------------------------------------- overrides
def apply_overrides(objs):
    if not OVERRIDES.exists():
        OVERRIDES.write_text("cid\tfield\tvalue\treason\n")
    log, by = [], {o["cid"]: o for o in objs}
    for r in read_tsv(OVERRIDES):
        cid = (r.get("cid") or "").strip()
        if not cid or cid.startswith("#"):
            continue
        o = by.get(cid)
        if not o:
            log.append(f"override for unknown cid {cid} ignored")
            continue
        field, value = r["field"].strip(), r["value"].strip()
        if field == "tier":
            o["tier"], o["tier_reason"] = int(value), f"override: {r.get('reason', '')}"
        elif field == "caveat":
            o["caveat"] = "; ".join(x for x in (value, o["caveat"]) if x)
        elif field == "group" or field in o:
            o[field] = value
        log.append(f"{cid}.{field} = {value} ({r.get('reason', '')})")
    return log


# ----------------------------------------------------------------------------- outputs
def fmt_km(s):
    v = fnum(s)
    return "" if v is None else (f"{v/1e6:.2f} Mkm" if v >= 1e6 else f"{v/1e3:.0f}k")


def source_facts(objs):
    """Dates and counts quoted in TARGETS.md, taken from the merged source rows."""
    rows = [r for o in objs for r in o["_rows"]]
    gp = [m[1] for r in rows for m in re.finditer(r"spacetrack_gp:[^\[;]*\[(\d{4}-\d\d-\d\d)", r.get("ephem_sources", ""))]
    live = [r.get("status_date", "") for r in rows if r["src"] == "neocp"]
    return {"gp_newest": max(gp, default=""), "neocp_rows": len(live), "neocp_latest": max(live, default=""),
            "srcs": Counter(r["src"] for r in rows)}


def write_outputs(objs, conflicts, weak, olog, sweep=None):
    sweep = sweep or {"ids": 0, "probes": 0, "sweep_now": set(), "in_window": 0, "frozen": 0}
    order = {g: i for i, (g, _) in enumerate(GROUPS)}
    objs.sort(key=lambda o: (o["tier"], order.get(o["group"], 99), o["name"].lower()))
    with open(OUT_TSV, "w", newline="") as f:
        w = csv.DictWriter(f, COLS, delimiter="\t", quoting=csv.QUOTE_NONE, escapechar="\\", lineterminator="\n")
        w.writeheader()
        for o in objs:
            w.writerow({k: str(o[k]).replace("\t", " ").replace("\n", " ") for k in COLS})
    OUT_JSON.write_text(json.dumps({"generated_for": str(NOW), "window": [str(W0), str(W1)],
                                    "objects": [{k: o[k] for k in COLS} | {"sources": o["_rows"], "tracklet_files": o["_tracklet_files"],
                                                                           "left_neocp": o["_left_neocp"] or None}
                                                for o in objs]}, indent=1, ensure_ascii=False, default=str))

    tiers = Counter((o["group"], o["tier"]) for o in objs)
    facts = source_facts(objs)
    sweep_now = ", ".join(str(x) for x in sorted(sweep["sweep_now"])) or "no sweep"
    L = ["# cislunar-eye target list", "",
         f"Generated by `scripts/build_master.py` for **{NOW}**, display window **{W0} → {W1} UTC**. "
         "Edit `catalog/overrides.tsv` (cid, field, value, reason) and re-run to change anything here. "
         f"Standing decisions and open questions: `{DECISIONS}`.", "",
         f"**Sources:** neocpsat catalog (P > 2.4 d fits, Spektr-RG history; {facts['srcs']['neocpsat']} rows), Bill Gray "
         f"`~/tles` ({facts['srcs']['billgray']}), rocketdebris wiki ({facts['srcs']['rocketdebris']}) and Space-Track GP sets "
         f"(newest epoch {facts['gp_newest'] or 'n/a'}; {facts['srcs']['spacetrack']} direct rows), lunarmission wiki "
         f"({facts['srcs']['lunarmission']}), JPL Horizons sweep ({sweep['ids']} spacecraft IDs + {sweep['probes']} probes, "
         f"{facts['srcs']['horizons']} in scope, distances for {sweep_now}), live NEOCP / Project Pluto `(geo)` objects "
         f"({facts['neocp_rows']}, latest {facts['neocp_latest'] or 'n/a'}), and a news sweep ({facts['srcs']['news']}).", "",
         "**Tiers:** 1 = shown by default (in regime, ephemeris covers the window; `draw=N` rows are listed "
         "but not drawn) · 2 = available, off by default (stale ephemeris, below the 2.4 d threshold, returning "
         "later, recent end kept for replay, NEOCP archive, natural companions) · 3 = excluded (decayed, "
         "impacted, landed, escaped far away, no ephemeris).", "",
         "**Ephemeris priority:** Horizons (sweep-verified span) > Bill Gray TLE inside its Range > Space-Track GP "
         "≤ 30 d old > neocpsat find_orb fit (arc ending ≥ window start) > Project Pluto pseudo-MPEC > "
         "Space-Track GP 30–120 d old (`covers_window` = `stale-gp`, caveat \"stale: …\"; older sets are unusable). "
         "The `caveat` column says what each track rests on.", ""]
    if sweep["frozen"]:
        L += [f"**Warning:** the Horizons sweep distances ({sweep_now}) fall outside this window for {sweep['frozen']} of "
              f"{sweep['frozen'] + sweep['in_window']} in-scope IDs; their window distance is the nearest probed epoch. "
              "Run `scripts/horizons_sweep.py --now <ISO> --in-scope-only` (make sweep) to re-probe.", ""]
    L += ["## Counts", "", "| Group | Tier 1 | Tier 2 | Tier 3 |", "|---|---:|---:|---:|"]
    for g, _ in GROUPS:
        if any(tiers[(g, t)] for t in (1, 2, 3)):
            L.append(f"| {g} | {tiers[(g, 1)]} | {tiers[(g, 2)]} | {tiers[(g, 3)]} |")
    L.append(f"| **Total** | **{sum(1 for o in objs if o['tier'] == 1)}** | **{sum(1 for o in objs if o['tier'] == 2)}** | "
             f"**{sum(1 for o in objs if o['tier'] == 3)}** |")
    L.append("")

    def idcell(o):
        bits = [f"NORAD {o['norad']}" if o["norad"] else "", o["cospar"], f"HZ {o['horizons_id']}" if o["horizons_id"] else ""]
        return " · ".join(b for b in bits if b)

    def ephcell(o):
        p = o["primary_ephem"]
        p = re.sub(r"\.\./neocpsat/mpecs/|https://www\.projectpluto\.com/neocp2/mpecs/|work/cache/\S*/", "", p)
        return p[:70]

    def orbcell(o):
        p, apo = fnum(o["period_d"]), o["apogee_km"]
        bits = []
        if p and o["frame_center"] in ("earth", "moon", ""):
            bits.append(f"P {p:.2f} d")
        if apo:
            bits.append(("Q " if o["frame_center"] != "sun" else "Q☉ ") + fmt_km(apo))
        if o["dist_window_km"]:
            bits.append(f"Δ {fmt_km(o['dist_window_km'])}")
        return " · ".join(bits)

    for t, title in ((1, "Tier 1 — shown by default"), (2, "Tier 2 — available, off by default")):
        L += [f"## {title}", ""]
        for g, _ in GROUPS:
            sel = [o for o in objs if o["tier"] == t and o["group"] == g]
            if not sel:
                continue
            L += [f"### {g} ({len(sel)})", "", "| Name | IDs | Status | Ephemeris | Covers | Orbit | Why | Caveat |",
                  "|---|---|---|---|---|---|---|---|"]
            for o in sel:
                st = o["status"] + (f" {o['status_date']}" if o["status_date"] else "")
                trk = f" · {o['tracklets']} obs file(s)" if o["tracklets"] else ""
                eph = ephcell(o) if o["draw"] == "Y" else "**listed, not drawn**"
                L.append(f"| **{o['name']}** `{o['cid']}` | {idcell(o)} | {st} | {eph}{trk} | {o['covers_window']} | "
                         f"{orbcell(o)} | {o['tier_reason']} | {o['caveat']} |")
            L.append("")

    L += ["## Tier 3 — excluded", "", "Grouped by reason; full rows are in `master_list.tsv`.", ""]
    reasons = defaultdict(list)
    for o in objs:
        if o["tier"] == 3:
            key = re.sub(r"\s*\(.*\)$", "", re.sub(r"\d{4}-\d{2}-\d{2}", "", o["tier_reason"])).strip()
            key = re.sub(r"P \d+\.\d+ d", "P", key)
            reasons[key].append(o["name"])
    for k in sorted(reasons, key=lambda k: -len(reasons[k])):
        names = sorted(reasons[k], key=str.lower)
        L.append(f"- **{k or 'n/a'}** ({len(names)}): " + ", ".join(names[:60]) + (" …" if len(names) > 60 else ""))
    L.append("")

    L += ["## Verification", ""]
    if VERIFY.exists():
        rep, known = read_tsv(VERIFY), {o["cid"] for o in objs}
        vdate = datetime.fromtimestamp(VERIFY.stat().st_mtime, timezone.utc).date()
        unknown = sorted({x["cid"] for x in rep if x["cid"] not in known})
        rep = [x for x in rep if x["cid"] in known]
        if vdate < NOW:
            L += [f"`catalog/verify_report.tsv` is from {vdate}, older than this master ({NOW}): ignored. Run "
                  "`scripts/verify_master.py` and the merge again.", ""]
        else:
            bad = [x for x in rep if x["verdict"] != "ok"]
            L += [f"`scripts/verify_master.py` checked the primary ephemeris of {len(rep)} tier-1/2 objects: "
                  f"{len(rep) - len(bad)} ok, {len(bad)} warn/fail (`catalog/verify_report.tsv` of {vdate}"
                  + (f"; {len(unknown)} rows for cids no longer in the master ignored: {', '.join(unknown[:8])}" if unknown else "")
                  + "). Horizons IDs must be ones the "
                  f"sweep resolved; Space-Track GP sets are propagated with SGP4/SDP4 to {NOW} and the radius must fall "
                  "inside perigee..apogee; Bill Gray, find_orb and pseudo-MPEC files must exist; every real NORAD number is "
                  "looked up in SATCAT for decay dates. GP periods agree with Bill Gray's to about 1 % for the HEO "
                  "derelicts. SATCAT gives IMAP, SWFO-L1 and Carruthers a DECAY of 2025-09-26: that is Space-Track's "
                  "mark for leaving Earth orbit, not a reentry.", ""]
            L += [f"- {x['verdict']} `{x['cid']}` ({x['kind']}): {x['detail']}" for x in bad]
            L.append("")
    else:
        L += ["No `catalog/verify_report.tsv`: run `scripts/verify_master.py` and the merge again.", ""]
    L += ["## Caveats the viewer must carry", "",
          "- **DRO-A / DRO-B** have only Space-Track GP sets. A DRO is not a Kepler orbit (the GP period swings "
          "between 26 and 47 d from fit to fit), so SDP4 over weeks is wrong: convert the GP state at its epoch "
          "and integrate with Earth + Moon + Sun.",
          "- **Chang'e-6 orbiter, Tiandu-1** manoeuvre; each neocpsat fit is coherent for about two weeks.",
          "- **Spektr-RG** has only neocpsat heliocentric fits; it station-keeps, so the fit drifts after the next burn.",
          "- **Space-Track GP tracks** are for this local page only (redistribution terms).",
          "- **NEOCP live** objects change hourly; `scripts/neocp_live.py` refreshes them.", "",
          f"Decisions, the watch list and the open questions about this list are in `{DECISIONS}`.", ""]
    L += ["## Conflicts between sources", "",
          "Joins the merge refused because the two sides carry different real NORAD numbers or different "
          "sweep-verified Horizons IDs. Each needs a source fix or an override.", ""]
    seen = set()
    for why, a, b, kind in conflicts:
        key = (why, a["name"], b["name"])
        if key in seen:
            continue
        seen.add(key)
        L.append(f"- `{why}` ({kind}): {a['src']} **{a['name']}** (NORAD {a.get('norad') or '–'}, HZ {a.get('horizons_id') or '–'}) "
                 f"vs {b['src']} **{b['name']}** (NORAD {b.get('norad') or '–'}, HZ {b.get('horizons_id') or '–'})")
    L.append("")
    L += ["## Weak joins (name / temporary designation only)", "",
          "These rows were joined without a shared NORAD, COSPAR or Horizons ID. Split any wrong one with an override.", ""]
    for why, a, b in weak:
        L.append(f"- `{why}`: {a['src']} **{a['name']}** + {b['src']} **{b['name']}**")
    L.append("")
    if olog:
        L += ["## Overrides applied", ""] + [f"- {x}" for x in olog] + [""]
    OUT_MD.write_text("\n".join(L) + "\n")


def set_now(now, half_window=HALF_WINDOW_D):
    global NOW, W0, W1, RECENT_END
    NOW = date.fromisoformat(str(now)[:10]) if now else datetime.now(timezone.utc).date()
    W0, W1 = NOW - timedelta(days=half_window), NOW + timedelta(days=half_window)
    RECENT_END = NOW - timedelta(days=365)


def run(now=None, half_window=HALF_WINDOW_D):
    set_now(now, half_window)
    objs, conflicts, weak, sweep = build()
    olog = apply_overrides(objs)
    write_outputs(objs, conflicts, weak, olog, sweep)
    return objs, conflicts


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--now", help="reference date YYYY-MM-DD (default: $CLE_NOW, else today UTC)")
    ap.add_argument("--half-window", type=int, default=HALF_WINDOW_D, help="days either side of now")
    a = ap.parse_args()
    objs, conflicts = run(a.now or (os.environ.get("CLE_NOW") or "")[:10], a.half_window)
    c = Counter(o["tier"] for o in objs)
    print(f"{len(objs)} objects: tier1={c[1]} tier2={c[2]} tier3={c[3]}; conflicts={len(conflicts)}")


if __name__ == "__main__":
    main()
