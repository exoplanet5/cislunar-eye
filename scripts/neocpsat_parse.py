#!/usr/bin/env python
"""Read-only parsers for the ../neocpsat NEOCP-artsat tracker (no side effects).

Used by ``extract_neocpsat.py``; importable on its own for ad-hoc inspection:

    ~/.venvs/astro313/bin/python -I scripts/neocpsat_parse.py [--pages|--data]

What is parsed (all paths under NEOCPSAT, never written):

* ``catalog.csv`` -- system of record. Columns ``Date, Temp Desig,
  Indentification`` (sic, intentional misspelling), ``link to projectpluto
  site, Orbit, H mag, Note``.
* ``data/<desig>.txt`` -- MPC 80-column observations (one ADES/PSV file,
  ``CDY3PM.txt``, is also understood).  trkSub = cols 6-12, date = cols 16-32.
* ``mpecs/*.html`` -- find_orb pseudo-MPECs (per-apparition ``<desig>.html``
  and combined-group ``<ident>_combined[_YYYY_MM_DD].html``).  The element
  block is find_orb's ``elements.txt`` layout; geocentric pages are
  "(J2000 equator)" with distances in km (switching to AU above ~1e6 km,
  e.g. Sar2901 ``a 0.00972340``), heliocentric pages "(J2000 ecliptic)" in AU.
* ``bill-2026-1-cache`` -- cached copy of Bill Gray's NEOCP artsat log lines
  ("X was not a minor planet (2026 Mon DD.dd UT) = ...").

Epochs in pages are TT; ``tt_to_utc_iso`` converts with TT-UTC = 69.184 s
(no leap second since 2017).
"""
from __future__ import annotations

import csv
import html
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

NEOCPSAT = Path("/Users/mickey/ciscluar/neocpsat")
AU_KM = 149597870.7
TT_MINUS_UTC = 69.184
MU_EARTH = 398600.4418  # km^3/s^2

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"], start=1)}


# ----------------------------------------------------------------- utilities
def frac_day_to_dt(y: int, m: int, dfrac: float) -> datetime:
    day = int(dfrac)
    return (datetime(y, m, day, tzinfo=timezone.utc)
            + timedelta(days=dfrac - day))


def tt_to_utc_iso(dt_tt: datetime) -> str:
    dt = dt_tt - timedelta(seconds=TT_MINUS_UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def period_days_from_a_km(a_km: float) -> float:
    return 2 * math.pi * math.sqrt(a_km ** 3 / MU_EARTH) / 86400.0


# ------------------------------------------------------------------ catalog
def read_catalog(path: Path = NEOCPSAT / "catalog.csv") -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for i, r in enumerate(rows):
        r["_line"] = i + 2  # 1-based line number in the CSV (header = 1)
    return rows


_ORBIT_P = re.compile(r"P\s*=\s*([\d.]+)\s*day")
_ORBIT_I = re.compile(r"i\s*=\s*([\d.]+)")
_ORBIT_O = re.compile(r"Ω\s*=\s*([\d.]+)")


def parse_orbit_cell(cell: str) -> dict:
    """``"i =83, Ω = 315, P = 8.9 day"`` / ``"... (heliocentric)"`` /
    ``"a =1.007 AU, e =0.066, i =4.0°, P = 369 day"``."""
    out = {"raw": cell, "P": None, "i": None, "node": None,
           "helio": ("heliocentric" in cell) or (" AU" in cell)}
    if m := _ORBIT_P.search(cell):
        out["P"] = float(m.group(1))
    if m := _ORBIT_I.search(cell):
        out["i"] = float(m.group(1))
    if m := _ORBIT_O.search(cell):
        out["node"] = float(m.group(1))
    return out


# --------------------------------------------------------------- obs files
def parse_obs80_line(line: str):
    """Return (trksub, datetime_utc) for a valid MPC 80-col optical line.

    Also accepts find_orb's extended-precision layout used on some Project
    Pluto pages for ADES-sourced obs: date field ``KYYMMDD:HHMMSSs`` (cols
    16-32) with RA/Dec in decimal degrees (e.g. the P13 lines of
    Chandrayaan-3_combined_2026_06_20.html)."""
    if len(line) < 32:
        return None
    if line[15] == "K":
        m = re.match(r"K(\d\d)(\d\d)(\d\d):(\d\d)(\d\d)(\d\d)(\d*)",
                     line[15:32])
        if not m:
            return None
        y, mo, d, hh, mi, ss, fr = m.groups()
        try:
            dt = datetime(2000 + int(y), int(mo), int(d), int(hh), int(mi),
                          int(ss), tzinfo=timezone.utc) + timedelta(
                seconds=float("0." + fr) if fr else 0.0)
        except ValueError:
            return None
        return line[5:12].strip(), dt
    try:
        y = int(line[15:19]); mo = int(line[20:22]); d = float(line[23:32])
    except ValueError:
        return None
    if not (1990 < y < 2100 and 1 <= mo <= 12 and 1 <= d < 32):
        return None
    if line[14] in "sv":  # second line of satellite / roving obs pair
        return None
    return line[5:12].strip(), frac_day_to_dt(y, mo, d)


def parse_obs_file(path: Path) -> dict:
    """Count obs, span and trkSubs of one data/<desig>.txt file.

    Handles plain MPC80, ADES PSV and files that mix both (e.g. Sar2901.txt:
    several PSV blocks with different headers between 80-col blocks).
    Redacted Project-Pluto filler (no valid RA/Dec columns) is counted
    separately as ``n_redacted``.
    """
    text = path.read_text(errors="replace")
    lines = [l.rstrip("\n") for l in text.splitlines() if l.strip()]
    obs = []
    kinds = set()
    hdr = None
    n_redacted = 0
    red_dates = []                     # YYYY-MM-DD of redacted lines
    psv_set = set()
    for l in lines:
        if l.startswith(("#", "!")):
            continue
        if l.count("|") >= 5:          # PSV row (an 80-col note1 may be '|')
            cols = [c.strip() for c in l.split("|")]
            if "obsTime" in cols:
                hdr = cols
                continue
            if hdr and len(cols) >= hdr.index("obsTime") + 1:
                t = cols[hdr.index("obsTime")]
                trk = cols[hdr.index("trkSub")] if "trkSub" in hdr else ""
                try:
                    dt = datetime.strptime(t.rstrip("Z")[:19],
                                           "%Y-%m-%dT%H:%M:%S").replace(
                        tzinfo=timezone.utc)
                except ValueError:
                    continue
                obs.append((trk, dt))
                psv_set.add((trk, dt))
                kinds.add("ADES_PSV")
            continue
        p = parse_obs80_line(l)
        if not p and len(l) >= 80 and re.match(r"\d{4} \d\d \d\d", l[15:25]):
            n_redacted += 1             # Pluto redaction filler in RA/Dec
            red_dates.append(l[15:25].replace(" ", "-"))
            continue
        if p:
            if (re.match(r"\d\d \d\d \d\d", l[32:40]) and
                    re.match(r"[-+ ]\d\d \d\d", l[44:50])) or \
                    (l[15] == "K" and re.match(r"\s*\d+\.\d+", l[32:44])):
                obs.append(p)
                kinds.add("OBS80")
            else:
                n_redacted += 1
                red_dates.append(l[15:25].replace(" ", "-"))
    fmt = "+".join(sorted(kinds)) or ("REDACTED" if n_redacted else "EMPTY")
    seen = set(); uniq = []
    for o in obs:  # files are line-deduped already; keep exact dupes out
        if o not in seen:
            seen.add(o); uniq.append(o)
    trk, trk_first, trk_last = {}, {}, {}
    for t, d in uniq:
        trk[t] = trk.get(t, 0) + 1
        trk_first[t] = min(d, trk_first.get(t, d))
        trk_last[t] = max(d, trk_last.get(t, d))
    dts = [d for _, d in uniq]
    n_psv = sum(1 for o in uniq if o in psv_set)
    return {"path": path, "format": fmt, "n": len(uniq), "n_psv": n_psv,
            "n_redacted": n_redacted,
            "redacted_span": (min(red_dates), max(red_dates)) if red_dates
            else None,
            "first": min(dts) if dts else None,
            "last": max(dts) if dts else None, "trksubs": trk,
            "trk_first": trk_first, "trk_last": trk_last}


def read_data_dir(d: Path = NEOCPSAT / "data") -> dict:
    out = {}
    for p in sorted(d.glob("*.txt")):
        out[p.stem] = parse_obs_file(p)
    return out


# ------------------------------------------------------------- mpec pages
def _strip(htmltext: str) -> list[str]:
    t = re.sub(r"<[^>]+>", "", htmltext)
    t = html.unescape(t)
    return t.splitlines()


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_date_tt(txt: str):
    """'2026 Jan 18.0' or '2026 Mar  9.0' -> datetime (TT)."""
    m = re.match(r"(\d{4})\s+([A-Za-z]+)\.?\s+([\d.]+)", txt.strip())
    if not m:
        return None
    mo = MONTHS.get(m.group(2)[:3].lower())
    if not mo:
        return None
    return frac_day_to_dt(int(m.group(1)), mo, float(m.group(3)))


def parse_mpec_page(path: Path) -> dict:
    lines = _strip(path.read_text(errors="replace"))
    full = "\n".join(lines)
    rec = {"path": path, "name": path.name}
    if m := re.search(r"Pseudo-MPEC\"? for (\S+)", full):
        rec["combine_as"] = m.group(1)
    if m := re.search(r"Created (\d{4} \w+ +\d+ +[\d:]+) UT", full):
        rec["created"] = re.sub(r"\s+", " ", m.group(1))
    # ---- astrometry
    obs = []
    in_ast = False
    for l in lines:
        if l.strip().startswith("Astrometry:"):
            in_ast = True
            continue
        if in_ast and (l.strip().startswith("Station data") or
                       l.strip().startswith("Orbital elements")):
            break
        if in_ast:
            p = parse_obs80_line(l)
            if p:
                obs.append(p)
    rec["n_ast"] = len(obs)
    rec["ast_first"] = min((d for _, d in obs), default=None)
    rec["ast_last"] = max((d for _, d in obs), default=None)
    trk, trk_first = {}, {}
    for t, d in obs:
        trk[t] = trk.get(t, 0) + 1
        trk_first[t] = min(d, trk_first.get(t, d))
    rec["ast_trksubs"] = trk
    rec["ast_trk_first"] = trk_first
    # ---- elements block
    try:
        k = next(i for i, l in enumerate(lines)
                 if l.startswith("Orbital elements:"))
    except StopIteration:
        rec["has_elements"] = False
        return rec
    rec["has_elements"] = True
    blk = []
    for l in lines[k:k + 60]:
        if l.startswith("Residuals in arcseconds"):
            break
        blk.append(l)
    b = "\n".join(blk)
    rec["elem_block"] = b
    rec["frame"] = ("ecliptic" if "J2000 ecliptic" in b else
                    "equator" if "J2000 equator" in b else "")
    rec["center"] = ("sun" if re.search(r"^\s*Perihelion", b, re.M) else
                     "earth" if re.search(r"^\s*Perigee", b, re.M) else
                     ("sun" if rec["frame"] == "ecliptic" else
                      "earth" if rec["frame"] == "equator" else ""))
    if m := re.search(r"^\s*Peri(?:gee|helion)\s+(\d{4} \w+ +[\d.]+)", b, re.M):
        rec["peri_time_tt"] = _parse_date_tt(m.group(1))
    if m := re.search(r"^Epoch\s+(\d{4} \w+ +[\d.]+) TT", b, re.M):
        rec["epoch_tt"] = _parse_date_tt(m.group(1))
    if m := re.search(r"AMR\s+([-\d.e]+)(?:\s*\+/-\s*([\d.e-]+))?\s*m\^2/kg", b):
        rec["amr"] = _num(m.group(1)); rec["amr_sigma"] = _num(m.group(2))
        if rec["amr"] == 0 and rec["amr_sigma"] is None:
            # find_orb prints a bare "AMR 0 m^2/kg" when SRP was not fitted
            rec["amr"] = None
            rec["amr_not_fitted"] = True
    if m := re.search(r"Earth MOID:\s*([\d.]+)", b):
        rec["earth_moid_au"] = _num(m.group(1))

    def dist(tag):
        # a / q / Q value; km unless it has no 'km' and is < 10 (then AU)
        mm = re.search(rf"(?:^|\s){tag}\s*([\d.]+)(km)?", b, re.M)
        if not mm:
            return None
        v = float(mm.group(1))
        if rec["center"] == "sun":
            return v * AU_KM
        if mm.group(2) == "km" or v > 10:
            return v
        return v * AU_KM

    rec["a_km"] = dist("a")
    rec["q_km"] = dist("q")
    rec["Q_km"] = dist("Q")
    if m := re.search(r"^e\s*([\d.]+)", b, re.M):
        rec["e"] = float(m.group(1))
    if m := re.search(r"Incl\.\s+([\d.]+)", b):
        rec["incl"] = float(m.group(1))
    if m := re.search(r"Node\s+([\d.]+)", b):
        rec["node"] = float(m.group(1))
    if m := re.search(r"Peri\.\s+([\d.]+)", b):
        rec["argp"] = float(m.group(1))
    if m := re.search(r"^M\s+([\d.]+)", b, re.M):
        rec["M"] = float(m.group(1))
    if m := re.search(r"^P\s*(\S+)", b, re.M):
        tok = m.group(1)              # 6479.281m/4.500d | 11.7571d | 1.086/396.595d
        if mm := re.search(r"([\d.]+)d$", tok):
            rec["P_d"] = float(mm.group(1))
        elif mm := re.search(r"^([\d.]+)m$", tok):
            rec["P_d"] = float(mm.group(1)) / 1440.0
        elif mm := re.search(r"^([\d.]+)h$", tok):
            rec["P_d"] = float(mm.group(1)) / 24.0
    if rec.get("P_d") is None and rec.get("a_km") and rec["center"] == "earth" \
            and (rec.get("e") is not None and rec["e"] < 1):
        rec["P_d_from_a"] = period_days_from_a_km(rec["a_km"])
    if m := re.search(r"\bH\s+([\d.]+)\s+G", b):
        rec["H"] = float(m.group(1))
    if m := re.search(r"\bU\s+([\d.]+)", b):
        rec["U"] = float(m.group(1))
    if m := re.search(r"(\d+) of (\d+) observations", b):
        rec["n_used"] = int(m.group(1)); rec["n_total"] = int(m.group(2))
    elif m := re.search(r"From (\d+) observations", b):
        rec["n_used"] = rec["n_total"] = int(m.group(1))
    if m := re.search(r"mean residual\s+([\d]*)\"\.(\d+)", b):
        rec["resid"] = float(f"{m.group(1) or 0}.{m.group(2)}")
    if m := re.search(r"# Full range of obs: ([^\n]+)", full):
        rec["full_range"] = m.group(1).strip()
    if m := re.search(r"Perturbers: ([^\n]+)", full):
        rec["perturbers"] = m.group(1).strip()
    # first geocentric ephemeris row -> delta (AU, or km when large number)
    for i, l in enumerate(lines):
        if l.startswith("Ephemerides (geocentric)"):
            for l2 in lines[i + 1:i + 8]:
                mm = re.match(r"(\d{4} \d\d \d\d \d\d:\d\d)\s+\d\d \d\d [\d.]+\s+"
                              r"[-+]\d\d \d\d [\d.]+\s+([\d.]+)\s", l2)
                if mm:
                    v = float(mm.group(2))
                    rec["eph_start"] = mm.group(1)
                    rec["eph_delta_km"] = v * AU_KM if v < 100 else v
                    break
            break
    return rec


def read_mpecs(d: Path = NEOCPSAT / "mpecs") -> dict:
    return {p.name: parse_mpec_page(p) for p in sorted(d.glob("*.html"))}


# ----------------------------------------------- per-observation usage
def obs_key(line: str):
    """Identity key of one MPC 80-col optical line.

    (date cols 16-32, RA hh mm cols 33-37, Dec sdd mm cols 45-50, obscode).
    The coarse position is needed because surveys report several objects
    from the same exposure with identical timestamps (e.g. A11H0cP =
    Spektr-RG and A11H0cX = Multijunk, both W68 2026 Sep 16.214472;
    SE000CB / SE000CC, R43 Sep 16.950556); date+station alone conflated them.
    """
    return (line[15:32].strip(), line[32:37], line[44:50].strip(),
            line[77:80].strip())


def page_obs_usage(path: Path) -> list[dict]:
    """Astrometry of a find_orb pseudo-MPEC with the per-obs used/excluded flag.

    find_orb prints the residual block column-major (three columns, read top
    to bottom, then left to right) in the same time order as the sorted
    astrometry; an excluded obs has its residuals in parentheses.  Each
    pairing is checked against the residual's YYMMDD date and obscode
    (``match`` False flags a misalignment; it has not happened on any
    neocpsat page so far).  Returns [] if either block is missing.
    """
    lines = _strip(Path(path).read_text(errors="replace"))
    ast, in_ast = [], False
    for l in lines:
        if l.strip().startswith("Astrometry:"):
            in_ast = True
            continue
        if in_ast and (l.strip().startswith("Station data") or
                       l.strip().startswith("Orbital elements")):
            break
        if in_ast:
            p = parse_obs80_line(l)
            if p:
                ast.append({"trksub": p[0], "dt": p[1], "key": obs_key(l),
                            "stn": l[77:80].strip()})
    try:
        k = next(i for i, l in enumerate(lines)
                 if l.startswith("Residuals in arcseconds"))
    except StopIteration:
        return []
    grid = []
    for l in lines[k + 1:]:
        if not l.strip():
            if grid:
                break
            continue
        if l.startswith("Ephemerides"):
            break
        cells = re.findall(r"(\d{6}) (\S{3})(.{12})", l)
        if cells:
            grid.append(cells)
    if not grid:
        return []
    res = []
    for c in range(max(len(r) for r in grid)):
        for r in grid:
            if c < len(r):
                res.append((r[c][0], r[c][1], "(" in r[c][2]))
    ast.sort(key=lambda o: o["dt"])
    if len(res) != len(ast):
        return []
    for o, (ymd, stn, excl) in zip(ast, res):
        o["used"] = not excl
        o["match"] = (o["dt"].strftime("%y%m%d") == ymd and o["stn"] == stn)
    return ast


def data_obs_index(d: Path = NEOCPSAT / "data") -> dict:
    """{obs_key: set(data-file stems)} over all 80-col lines in data/*.txt."""
    idx = {}
    for p in sorted(d.glob("*.txt")):
        for l in p.read_text(errors="replace").splitlines():
            if parse_obs80_line(l) and (re.match(r"\d\d \d\d \d\d", l[32:40])
                                        or l[15:16] == "K"):
                idx.setdefault(obs_key(l), set()).add(p.stem)
    return idx


# ---------------------------------------------------------- Bill Gray log
_BILL = re.compile(
    r"^(?P<desig>\S+)\s+(?:was|is)\s+(?P<verdict>[^(]+?)\s*"
    r"\((?:(?P<y>\d{4})\s+)?(?P<mon>[A-Za-z]+)\.?\s+(?P<d>[\d.]+)\s*(?:UT)?\)"
    r"\s*=\s*(?P<rest>.*)$")


def read_bill_cache(path: Path = NEOCPSAT / "bill-2026-1-cache") -> list[dict]:
    out = []
    for n, l in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = _BILL.match(l.strip())
        if m:
            g = m.groupdict()
            g["line"] = n
            mo = MONTHS.get(g["mon"][:3].lower())
            try:
                g["date"] = frac_day_to_dt(int(g["y"] or 2026), mo,
                                           float(g["d"])).date().isoformat()
            except (TypeError, ValueError):
                g["date"] = ""
            out.append(g)
    return out


if __name__ == "__main__":
    if "--data" in sys.argv:
        for k, v in read_data_dir().items():
            print(k, v["format"], v["n"], v["first"], v["last"],
                  dict(v["trksubs"]))
    else:
        for k, r in read_mpecs().items():
            print(f"{k:42s} {r.get('combine_as',''):9s} c={r.get('center','')} "
                  f"P={r.get('P_d')} a={r.get('a_km')} e={r.get('e')} "
                  f"i={r.get('incl')} q={r.get('q_km')} Q={r.get('Q_km')} "
                  f"ep={r.get('epoch_tt')} n={r.get('n_used')}/{r.get('n_total')}"
                  f" ast={r.get('n_ast')} [{r.get('ast_first')}..{r.get('ast_last')}]"
                  f" rms={r.get('resid')} U={r.get('U')} H={r.get('H')}")
