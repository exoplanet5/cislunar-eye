"""Pure parsers for Bill Gray's neocp2 pages and MPC 80-column astrometry (on-request NEOCP helper, cle.request).

Ported from scripts/neocp_live.py (the manual catalog-source tool, left as it is) without its network code and without
the ../neocpsat imports: the redaction regex and looks_like_obs80 of ../neocpsat/pluto.py are inlined here. Nothing in
this module touches the network; case_safe_name only lists a directory.

  parse_mpec(page, desig)   pseudo-MPEC https://www.projectpluto.com/neocp2/mpecs/<desig>.htm -> dict: created, astrometry
                            (obs_lines, redaction), stations, elements, HTML-comment state vectors (heliocentric
                            equatorial J2000 always; "relative to central body" for geocentric fits; AU and mAU/day at
                            the elements epoch, JD TT), scores, TLE comments, the 48-h geocentric ephemeris table
  parse_summary(text)       https://www.projectpluto.com/neocp2/summary.htm -> regenerated stamp + rows ("(geo)" in
                            the MOID column = find_orb's best fit is geocentric)
  obs80_lines(text)         80-column optical lines (+ their satellite / roving second lines) of an MPC OBS80 block
  merge_obs(*groups)        union de-duplicated by astrometric_key (first copy wins), sorted by time and station
  obs_stats, obs80_time, astrometric_key, page_center, derive_geocentric, regime_for_bound, case_safe_name, valid_desig
"""
from __future__ import annotations

import datetime as dt
import html
import math
import os
import re

AU_KM = 149597870.7
MU_EARTH = 398600.4418                # km^3/s^2 (neocp_live.py's value, for derive_geocentric)
TEMP_DESIG = re.compile(r"^[A-Za-z0-9]{5,8}$")
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
FAR = dt.datetime(9999, 1, 1, tzinfo=dt.timezone.utc)

# ../neocpsat/pluto.py: astrometry Bill Gray blanks while an object is on the NEOCP (black-on-black class="neocp"), and
# the fixed RA "HH MM SS.sss" [32:44] / Dec "sDD MM SS.ss" [44:56] columns that separate real lines from that junk.
REDACTED_RE = re.compile(r'<code\s+class="neocp"', re.IGNORECASE)
_RA_RE = re.compile(r"^\d{2} \d{2} \d{2}\.\d+\s*$")
_DEC_RE = re.compile(r"^[+-]\d{2} \d{2} \d{2}\.?\d*\s*$")


def valid_desig(desig) -> bool:
    """A NEOCP temporary designation: 5-8 letters and digits, case-sensitive (ZTF10Gu and ZTF10GU differ)."""
    return isinstance(desig, str) and TEMP_DESIG.fullmatch(desig) is not None      # fullmatch: no trailing newline


def iso(t, sec=True):
    if t is None:
        return ""
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ" if sec else "%Y-%m-%dT%H:%MZ")


def jd_to_dt(jd):
    """JD (any scale, treated as UTC) -> aware datetime."""
    return dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc) + dt.timedelta(days=jd - 2451545.0)


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def looks_like_obs80(line):
    """True if `line` has the fixed-column structure of MPC 80-col astrometry."""
    return len(line) >= 56 and bool(_RA_RE.match(line[32:44])) and bool(_DEC_RE.match(line[44:56]))


def second_line(line, prev):
    """True for the second line of a satellite (S/s) or roving (V/v) observation following `prev`."""
    return bool(prev) and len(line) >= 80 and line[14:15] in ("s", "v") and prev[14:15] in ("S", "V") \
        and line[15:32] == prev[15:32] and line[77:80] == prev[77:80]


def obs80_lines(text):
    """80-column optical lines of an OBS80 block (MPC get-obs-neocp, showobsorbs, a cached obs file), each with its
    satellite / roving second line when it has one; anything else is dropped."""
    out = []
    for raw in (text or "").splitlines():
        ln = raw.rstrip("\r\n")
        if looks_like_obs80(ln):
            out.append(ln[:80])
        elif out and second_line(ln, out[-1]):
            out.append(ln[:80])
    return out


def optical(lines):
    """The first lines of the observations (drops satellite / roving second lines)."""
    return [ln for ln in lines if looks_like_obs80(ln) and ln[14:15] not in ("s", "v")]


def obs80_time(line):
    """UTC datetime of an MPC 80-col line (cols 16-32 'YYYY MM DD.dddddd')."""
    try:
        y, m, d = int(line[15:19]), int(line[20:22]), float(line[23:32])
    except ValueError:
        return None
    return dt.datetime(y, m, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=d - 1)


def astrometric_key(line):
    """Same dedup key as neocpsat scraper.save_observations._astrometric_key (time, RA, Dec, station): the MPC and
    Pluto copies of one observation differ only in the discovery '*' and the 'NEOCP' note columns."""
    if len(line) < 80:
        return line.rstrip()
    return line[15:32] + "|" + line[32:44].strip() + "|" + line[44:56].strip() + "|" + line[77:80]


def merge_obs(*groups):
    """Union of 80-column line lists de-duplicated by astrometric_key (the first copy wins: pass the Pluto page's lines
    first), sorted by time and station (a stable sort keeps second lines after their first lines)."""
    out, seen = [], set()
    for group in groups:
        for ln in group or ():
            k = astrometric_key(ln)
            if k not in seen:
                seen.add(k)
                out.append(ln)
    out.sort(key=lambda ln: (obs80_time(ln) or FAR, ln[77:80]))
    return out


def obs_stats(lines):
    """n (optical lines), first / last (ISO), stations, arc_h."""
    first_lines = [ln for ln in lines if ln[14:15] not in ("s", "v")]
    ts = [t for t in (obs80_time(ln) for ln in first_lines) if t]
    stns = sorted({ln[77:80] for ln in first_lines if len(ln) >= 80})
    if not ts:
        return {"n": len(first_lines), "first": None, "last": None, "stations": stns}
    return {"n": len(first_lines), "first": iso(min(ts)), "last": iso(max(ts)), "stations": stns,
            "arc_h": round((max(ts) - min(ts)).total_seconds() / 3600, 3)}


def case_safe_name(desig, directory, ext=""):
    """File name for `desig` that cannot collide on a case-insensitive volume: plain '<desig><ext>' unless a
    different-case sibling already exists, then '<desig>~<hex uppercase-mask><ext>' (deterministic)."""
    plain = f"{desig}{ext}"
    try:
        names = set(os.listdir(directory))
    except FileNotFoundError:
        names = set()
    if plain in names or not any(n.lower() == plain.lower() for n in names):
        return plain
    mask = sum(1 << i for i, c in enumerate(desig) if c.isupper())
    return f"{desig}~{mask:x}{ext}"


# ------------------------------------------------------------------ summary.htm

SUMMARY_ROW = re.compile(
    r'^(?P<desig>\S+)\s+'
    r'(?:(?P<a>[\d.]+) \+/-\s?(?P<sa>\S*)\s+)?'
    r'(?P<e>[\d.]+) \+/-\s?(?P<se>\S*)\s+'
    r'(?P<i>[\d.]+) \+/-\s?(?P<si>\S*)\s+'
    r'(?P<moid>\(geo\)|[\d.]+)\s+'
    r'(?P<ra>\d\d \d\d \d\d)\s+(?P<dec>[+-]\d\d \d\d \d\d)\s+'
    r'(?P<elong>[\d.]+)\s+(?P<mag>\S+)\s+(?P<sig1>\S+)\s+(?P<sig2>\S+)\s*$')


def sig_to_arcsec(s):
    """Pluto sigma: plain = arcsec, trailing ' = arcmin, d = degrees."""
    if not s:
        return None
    m = re.match(r"^([\d.]+)([d']?)$", s)
    if not m:
        return None
    return float(m.group(1)) * {"": 1.0, "'": 60.0, "d": 3600.0}[m.group(2)]


def tisserand_earth(a, e, i_deg):
    """Tisserand parameter w.r.t. Earth (a in AU, heliocentric ecliptic i)."""
    if a is None or e is None or i_deg is None or a <= 0 or e >= 1:
        return None
    return 1.0 / a + 2.0 * math.sqrt(a * (1 - e * e)) * math.cos(math.radians(i_deg))


def parse_summary(text):
    """The 'Sorted by designation' table of summary.htm: {"regenerated": ISO, "rows": {desig: row}, "unparsed": [...]}."""
    out = {"regenerated": None, "rows": {}, "unparsed": []}
    m = re.search(r"Last regenerated (\d{4}) (\w{3})\.? +(\d+) +(\d+):(\d+):(\d+) UTC", text)   # hour space-padded
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        out["regenerated"] = iso(dt.datetime(int(y), MONTHS[mo.lower()], int(d), int(hh), int(mm), int(ss),
                                             tzinfo=dt.timezone.utc))
    sec = re.search(r'name="desig">(.*?)(?:<h3>|</pre>)', text, re.DOTALL)
    for raw in (sec.group(1) if sec else "").splitlines():
        if 'href="mpecs/' not in raw:
            continue
        hm = re.search(r'href="mpecs/([^"]+)\.htm"', raw)
        line = html.unescape(re.sub(r"<[^>]+>", "", raw)).rstrip()
        rm = SUMMARY_ROW.match(line)
        if not rm:
            out["unparsed"].append(line)
            continue
        g = rm.groupdict()
        desig = hm.group(1) if hm else g["desig"]
        row = {"desig": desig, "a_au": fnum(g["a"]), "e": fnum(g["e"]), "incl": fnum(g["i"]),
               "moid_au": None if g["moid"] == "(geo)" else fnum(g["moid"]), "geo": g["moid"] == "(geo)",
               "ra": g["ra"], "dec": g["dec"], "elong": fnum(g["elong"]), "mag": fnum(g["mag"]), "mag_raw": g["mag"],
               "sig1_arcsec": sig_to_arcsec(g["sig1"]), "sig2_arcsec": sig_to_arcsec(g["sig2"]),
               "sig1_raw": g["sig1"], "sig2_raw": g["sig2"], "line": line}
        row["tisserand_earth"] = None if row["geo"] else tisserand_earth(row["a_au"], row["e"], row["incl"])
        out["rows"][desig] = row
    return out


# ------------------------------------------------------------------ pseudo-MPEC

def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s))


_STD_HEADER = re.compile(r"Pseudo-MPEC\" for|Created \d{4}|explanation of pseudo-MPECs|Orbit Simulator View", re.I)
_ID_PAT = re.compile(
    r"(artificial|satellite|spacecraft|rocket|booster|upper stage|\bstage\b|"
    r"identif|NORAD|\b(?:19|20)\d\d-\d{3}[A-Z]{0,3}\b|\bR/B\b|probe|"
    r"\bJWST\b|Spektr|Chang.?e|Tiandu|Falcon|Starship|Centaur|TESS|Gaia)", re.I)


def parse_mpec(page, desig):
    """Parse a Project Pluto pseudo-MPEC (find_orb HTML) into a dict (scripts/neocp_live.py parse_mpec; obs_lines also
    keep the second lines of satellite / roving observations)."""
    r = {"desig": desig, "found": True}
    tm = re.search(r'"Pseudo-MPEC" for\s*(.*?)\s*</', page, re.DOTALL | re.I)
    r["title"] = " ".join(strip_tags(tm.group(1)).split()) if tm else ""
    nm = re.search(r"NORAD\s+(\d+)", r["title"])
    r["norad"] = nm.group(1) if nm else ""
    m = re.search(r"Created (\d{4}) (\w{3}) +(\d+) +(\d+):(\d+):(\d+) UT", page)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        r["created"] = iso(dt.datetime(int(y), MONTHS[mo.lower()], int(d), int(hh), int(mm), int(ss), tzinfo=dt.timezone.utc))
    # ---- header paragraphs beyond the boilerplate = Bill Gray's own text
    head = page.split('<a name="ast">')[0]
    head = head.split("<BODY>", 1)[-1] if "<BODY>" in head else head
    extra = []
    for p in re.findall(r"<p>(.*?)</p>", head, re.DOTALL | re.I):
        t = " ".join(strip_tags(p).split())
        if t and not _STD_HEADER.search(t):
            extra.append(t)
    r["header_text"] = extra

    # ---- astrometry
    am = re.search(r'<a name="ast"></a>(.*?)<a name="(?:stations|elements)"></a>', page, re.DOTALL)
    ablock = am.group(1) if am else ""
    r["astrometry_redacted"] = bool(REDACTED_RE.search(ablock))
    txt = re.sub(r"<a [^>]*>|</a>", "", ablock)
    txt = re.sub(r"<b>.*?</b>", "", txt, flags=re.DOTALL)
    obs = []
    if not r["astrometry_redacted"]:
        for raw in txt.splitlines():
            ln = html.unescape(re.sub(r"<[^>]+>", "", raw)).rstrip()
            if looks_like_obs80(ln):
                obs.append(ln)
            elif obs and second_line(ln, obs[-1]):
                obs.append(ln)
    r["obs_lines"] = obs
    r["astrometry_withheld"] = not obs
    # ---- stations
    sm = re.search(r'<a name="stations"></a>(.*?)<a name="elements"></a>', page, re.DOTALL)
    stations = {}
    if sm:
        for code, nm_ in re.findall(r"\((\w{3})\)\s+([^(\n]+?)\s+\(", strip_tags(sm.group(1))):
            stations[code] = " ".join(nm_.split())
    r["station_names"] = stations

    # ---- elements block
    em = re.search(r'<a name="elements"></a>(.*?)</pre>', page, re.DOTALL)
    eb = strip_tags(em.group(1)) if em else ""
    r["elements_text"] = eb.strip()
    el = {}
    for ln in eb.splitlines():
        s = ln.strip()
        mm = re.match(r"^(Peri\w+)\s+(\d{4}) (\w{3})\s+([\d.]+)(?: \+/- (\S+))? TT.*?\(JD ([\d.]+)\)", s)
        if mm:
            el["peri_word"] = mm.group(1)
            el["tp_jd_tt"] = float(mm.group(6))
            el["tp_sigma_d"] = fnum(mm.group(5))
        mm = re.match(r"^Epoch (\d{4}) (\w{3})\s+([\d.]+) TT(?: = JDT ([\d.]+))?", s)
        if mm:
            if mm.group(4):
                el["epoch_jd_tt"] = float(mm.group(4))
            else:
                t0 = dt.datetime(int(mm.group(1)), MONTHS[mm.group(2).lower()], 1, tzinfo=dt.timezone.utc)
                el["epoch_jd_tt"] = 2451545.0 + (t0 + dt.timedelta(days=float(mm.group(3)) - 1)
                                                 - jd_to_dt(2451545.0)).total_seconds() / 86400.0
            last_tok = s.split()[-1]
            el["fit_tag"] = last_tok if re.match(r"^[A-Za-z_]+$", last_tok) else ""
            am_ = re.search(r"AMR\s+([-\d.]+)", s)
            if am_:
                el["amr"] = float(am_.group(1))
        mm = re.search(r"Earth MOID:\s*([\d.]+)", s)
        if mm:
            el["earth_moid_au"] = float(mm.group(1))
        mm = re.search(r"\((J2000(?:\.0)?(?: (?:equator|ecliptic))?)\)", s)
        if mm:
            el["frame"] = mm.group(1)
        for key, pat in (("M", r"^M\s*([-\d.]+)"), ("n", r"^n\s*([\d.]+)"), ("a", r"^a\s*([\d.]+)"),
                         ("e", r"^e\s*([\d.]+)"), ("q", r"^q\s*([\d.]+)")):
            mm = re.match(pat + r"(?: \+/- (\S+))?", s)
            if mm:
                el[key] = float(mm.group(1))
                el[key + "_sigma"] = fnum(mm.group(2))
        mm = re.search(r"\bQ\s+([\d.]+)(?: \+/- (\S+))?", s)
        if mm:
            el["Q"] = float(mm.group(1))
            el["Q_sigma"] = fnum(mm.group(2))
        for key, pat in (("peri", r"Peri\.\s+([\d.]+)"), ("node", r"Node\s+([\d.]+)"), ("incl", r"Incl\.\s+([\d.]+)")):
            mm = re.search(pat + r"(?: \+/- (\S+))?", s)
            if mm:
                el[key] = float(mm.group(1))
                el[key + "_sigma"] = fnum(mm.group(2))
        if s.startswith("P"):
            for pat, conv in ((r"^P\s*([\d.]+)m/([\d.]+)d", lambda g: float(g[1])),
                              (r"^P\s*([\d.]+)/([\d.]+)d", lambda g: float(g[1])),
                              (r"^P\s*([\d.]+)d", lambda g: float(g[0])),
                              (r"^P\s*([\d.]+)y", lambda g: float(g[0]) * 365.25),
                              (r"^P\s*([\d.]+)h", lambda g: float(g[0]) / 24)):
                mm = re.match(pat, s)
                if mm:
                    el["P_d"] = conv(mm.groups())
                    break
        mm = re.search(r"\bH\s+([\d.]+)\s+G\s+([\d.]+)", s)
        if mm:
            el["H"], el["G"] = float(mm.group(1)), float(mm.group(2))
        mm = re.search(r"\bU\s+([\d.]+)\s*$", s)
        if mm:
            el["U"] = float(mm.group(1))
        mm = re.search(r"(?:From (\d+) observations|(\d+) of (\d+) observations)"
                       r"\s+(.*?)(?:\s+\(([^)]*)\))?;\s*mean residual\s+(\d*)\"\.?(\d*)", s)
        if mm:
            if mm.group(1):
                el["n_used"] = el["n_obs"] = int(mm.group(1))
            else:
                el["n_used"], el["n_obs"] = int(mm.group(2)), int(mm.group(3))
            el["obs_dates_text"] = mm.group(4).strip()
            el["arc_text"] = mm.group(5) or ""
            el["mean_resid_arcsec"] = float(f"{mm.group(6) or 0}.{mm.group(7) or 0}")
    if "P_d" not in el and el.get("n"):
        el["P_d"] = 360.0 / el["n"]
    r["elements"] = el

    # ---- HTML comments (state vectors, scores, TLE, heliocentric $-elements)
    comments = [c.strip() for c in re.findall(r"<!--(.*?)-->", page, re.DOTALL)]
    cm, tle = {}, []
    for k, c in enumerate(comments):
        if c.startswith("State vector (heliocentric equatorial J2000)") and k + 2 < len(comments):
            cm["sv_helio_au"] = [float(x) for x in comments[k + 1].split()[:3]]
            cm["sv_helio_mau_d"] = [float(x) for x in comments[k + 2].split()[:3]]
        if c.startswith("State vector relative to central body") and k + 2 < len(comments):
            cm["sv_central_au"] = [float(x) for x in comments[k + 1].split()[:3]]
            cm["sv_central_mau_d"] = [float(x) for x in comments[k + 2].split()[:3]]
        if re.match(r"^1 \d{5}U ", c) or re.match(r"^2 \d{5} ", c):
            tle.append(c)
        for key, pat in (("geo_score", r"GEO score\s+(-?\d+)"),
                         ("earth_enc_kms", r"Earth encounter velocity\s+([-\d.]+) km/s"),
                         ("barbee_kms", r"Barbee-style encounter velocity:\s*([-\d.]+) km/s"),
                         ("tisserand_earth", r"Tisserand relative to Earth:\s*([-\d.]+)"),
                         ("diam_m", r"Diameter\s+([\d.]+) meters"),
                         ("score", r"^Score:\s*([-\d.]+)")):
            mm = re.search(pat, c)
            if mm:
                cm[key] = float(mm.group(1))
        for key, pat in (("elements_written", r"Elements written:\s*(.*)"), ("full_range", r"Full range of obs:\s*(.*)"),
                         ("perturbers", r"Perturbers:\s*(.*)"), ("find_orb_ver", r"Find_Orb ver:\s*(.*)")):
            mm = re.search(pat, c)
            if mm:
                cm[key] = mm.group(1).strip()
        for key in ("a", "ecc", "Incl", "Node", "Peri", "q", "EpJD", "T", "H"):
            mm = re.search(r"\$" + key + r"=([-\d.]+)", c)
            if mm:
                cm.setdefault("helio_el", {})[key] = float(mm.group(1))
        if "MOIDs:" in c:
            for pl, v in re.findall(r"(\w\w)\s+([\d.]+)", c.split("MOIDs:", 1)[1]):
                cm.setdefault("moids", {})[pl] = float(v)
    if tle:
        cm["tle"] = tle
    idc = [c for c in comments if _ID_PAT.search(c) and not re.search(r"State vector|Perturbers|Find_Orb ver|\$Name", c)]
    r["comments"] = cm
    r["id_text"] = [t for t in extra if _ID_PAT.search(t)] + idc
    std = re.compile(r"^(Orbital elements|Peri\w+ \d{4}|Epoch |M |n |a|e |q|P|H |Node|Incl|From |\d+ of \d+ obs|Peri\.)")
    r["elements_extra"] = [ln.strip() for ln in eb.splitlines() if ln.strip() and not std.match(ln.strip())]

    # ---- ephemeris table (geocentric, astrometric)
    eph = []
    xm = re.search(r'<a name="eph"></a>(.*?)</pre>', page, re.DOTALL)
    if xm:
        eh = strip_tags(xm.group(1))
        r["eph_header"] = eh.strip().splitlines()[0] if eh.strip() else ""
        for ln in eh.splitlines():
            mm = re.match(r"^(\d{4}) (\d\d) (\d\d)(?: (\d\d))?(?::(\d\d))?\s+"
                          r"(\d\d) (\d\d) (\d\d\.\d+)\s+([+-])(\d\d) (\d\d) (\d\d\.\d+)\s+"
                          r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\S+)\s+(\S+)\s+(\S+)", ln)
            if not mm:
                continue
            g = mm.groups()
            t = dt.datetime(int(g[0]), int(g[1]), int(g[2]), int(g[3] or 0), int(g[4] or 0), tzinfo=dt.timezone.utc)
            ra = (int(g[5]) + int(g[6]) / 60 + float(g[7]) / 3600) * 15
            de = int(g[9]) + int(g[10]) / 60 + float(g[11]) / 3600
            de = -de if g[8] == "-" else de
            # find_orb prints delta in AU with a decimal point ('.05523'), but in whole km for close objects ('182991')
            if "." in g[12]:
                delta = float(g[12])
                rk = delta * AU_KM
            else:
                rk = float(g[12])
                delta = rk / AU_KM
            cr, sr = math.cos(math.radians(ra)), math.sin(math.radians(ra))
            cd, sd = math.cos(math.radians(de)), math.sin(math.radians(de))
            eph.append({"t": iso(t), "ra_deg": round(ra, 6), "dec_deg": round(de, 6), "delta_au": delta,
                        "delta_km": round(rk, 1), "delta_raw": g[12], "r_au": float(g[13]), "elong": float(g[14]),
                        "mag": fnum(g[15]), "sig_arcsec": sig_to_arcsec(g[16]) if g[16] != "?" else None,
                        "sig_pa": fnum(g[17]),
                        "xyz_km": [round(rk * cd * cr, 1), round(rk * cd * sr, 1), round(rk * sd, 1)]})
    r["ephemeris"] = eph
    return r


def page_center(el):
    """'earth' (Perigee / J2000 equator), 'sun' (Perihelion), 'moon' (Perilune) or '' for a page's elements."""
    pw = el.get("peri_word", "")
    if pw == "Perigee" or el.get("frame") == "J2000 equator":
        return "earth"
    if pw == "Perihelion":
        return "sun"
    if pw in ("Perilune", "Periselene"):
        return "moon"
    return ""


def derive_geocentric(el):
    """Geocentric q/e -> a, apogee, period, v_inf (km, d, km/s)."""
    q, e = el.get("q"), el.get("e")
    d = {}
    if q is None or e is None:
        return d
    d["perigee_km"] = q
    if e < 1:
        a = el.get("a") or q / (1 - e)
        d["a_km"] = a
        d["apogee_km"] = el.get("Q") or a * (1 + e)
        d["period_d"] = el.get("P_d") or 2 * math.pi * math.sqrt(a ** 3 / MU_EARTH) / 86400
    else:
        d["a_km"] = q / (1 - e)  # negative for a hyperbola
        d["v_inf_kms"] = math.sqrt(MU_EARTH * (e - 1) / q) if e > 1 else 0.0
    return d


def regime_for_bound(period_d, apo_km):
    """Regime of a bound geocentric orbit (neocp_live.py): period <= 2.4 d -> OTHER, else apogee >= 100 000 km ->
    CISLUNAR_HEO, else HEO."""
    if period_d is None or apo_km is None:
        return None
    if period_d <= 2.4:
        return "OTHER"
    return "CISLUNAR_HEO" if apo_km >= 100000 else "HEO"
