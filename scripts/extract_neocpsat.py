#!/usr/bin/env python
"""Extract cislunar / Sun-Earth-L1/L2 / high-Earth-orbit / Earth-near heliocentric
candidates from the ../neocpsat NEOCP artsat tracker (read-only).

Outputs (overwritten on every run, so the script is idempotent):

* ``catalog/sources/neocpsat.tsv`` -- one row per physical object, columns and
  vocabularies exactly as ``catalog/sources/SCHEMA.md``.
* ``catalog/sources/neocpsat_spektr_rg_history.tsv`` -- every Spektr-RG
  (2019-040A, not in JPL Horizons) fit / combined batch / apparition recorded
  by neocpsat, one row each.
* ``work/notes/neocpsat_extract_summary.json`` -- machine-readable run report
  for the merge step: regime counts, excluded catalog groups (P <= 2.4 d,
  incl. high-apogee ones and '?' rows with no period), data/ files that no
  catalog row owns, and geocentric/heliocentric fits of unconfirmed_obs/.

Validate with ``scripts/validate_source_tsv.py catalog/sources/neocpsat.tsv``.

Run::

    ~/.venvs/astro313/bin/python -I scripts/extract_neocpsat.py

Selection (the user's rule): every catalog.csv row whose fitted geocentric
period P (``Orbit`` cell) is > 2.4 d, every row with a heliocentric fit, and
every row whose ``Indentification`` is a known cislunar / L1 / L2 /
translunar object even when the Orbit cell has no P (JWST, Euclid,
Artemis II ...).  Rows are grouped by physical object:

* named rows by their ``Indentification`` value;
* ``?`` / ``ETBO`` rows by the ``X = Y`` designation chains in their ``Note``
  (neocpsat's own confirmed linkages; "Possibly = X" is ignored), so
  different unidentified ETBOs never collapse into one row.

Per object, the elements are those of the most recent *well-determined* fit
that actually uses that object's observations: candidate pages are every
``mpecs/*.html`` whose astrometry contains used (non-excluded) obs of the
group -- the per-obs used flag is read from the residual block, so e.g.
``C45LDC1_combined.html`` (TESS obs all rejected at ~130 deg) is attributed to
IMP-7 and not to TESS.  "Well determined" = bound geocentric fit (or
heliocentric fit for heliocentric objects) with find_orb U < 15 (pages in
the short no-sigma layout have no U and are accepted).  A newer but
rejected fit is reported in the notes.  Objects with no acceptable page are
refitted from their own neocpsat obs file with find_orb (``fo -h 3``,
gravity only), cached under ``work/cache/neocpsat_fo/<desig>/`` and reused
if present (``--refit`` forces a rerun).

``covers_now`` (the find_orb fits have no fixed ephemeris span) is a
validity judgement for propagating the selected fit to 2026-10-21:
``Y`` = fit arc ended <= 14 d before with a well-determined orbit and no
later manoeuvre/flyby noted; ``?`` = arc ended 14-60 d before; ``N`` = older,
known manoeuvres/flybys since, object departed/decayed, or no fit at all.

``ephem_sources`` items: ``neocpsat_fit:<page>@<fit epoch UTC date>[obs
first..last]``, ``neocp_obs:<obs file>#<n obs>[first..last]``,
``pluto_mpec:<catalog URL>[first..last of that desig]``.  Paths are relative
to the cislunar-eye project root.

Facts that are not in neocpsat (NORAD numbers, mission status) come from
~/tles/tle_list.txt, ../highartsat/satid/data/artsats.csv, or memory; the
``notes`` field says which.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import neocpsat_parse as P  # noqa: E402
from _now import now  # noqa: E402

ROOT = HERE.parent
NEO = P.NEOCPSAT
OUT = ROOT / "catalog" / "sources" / "neocpsat.tsv"
OUT_SRG = ROOT / "catalog" / "sources" / "neocpsat_spektr_rg_history.tsv"
OUT_JSON = ROOT / "work" / "notes" / "neocpsat_extract_summary.json"
FO_CACHE = ROOT / "work" / "cache" / "neocpsat_fo"
FO_UNCONF = ROOT / "work" / "cache" / "neocpsat_fo_unconfirmed"
FO_BIN = "/Users/mickey/Desktop/billgray/local/bin/fo"
FO_LOCK = ROOT / "work" / "fo.lock"
NOW = now("day")             # CLE_NOW floored to the day, else today (scripts/_now.py)
P_CUT = 2.4
REL = "../neocpsat"          # neocpsat path relative to the project root

COLS = ["src", "src_ref", "name", "aliases", "norad", "cospar", "horizons_id",
        "obj_type", "regime", "status", "status_date", "period_d", "a_km",
        "e", "incl_deg", "perigee_km", "apogee_km", "elem_epoch",
        "frame_center", "ephem_sources", "covers_now", "notes"]

# --------------------------------------------------------------------------
# Per-object facts that neocpsat itself does not carry.  Keyed by the
# catalog ``Indentification`` value (or the canonical desig of an
# unidentified linked group).  'src' says where each fact comes from.
TLE = "~/tles/tle_list.txt"
ART = "../highartsat/satid/data/artsats.csv"
META = {
    "Spektr-RG": dict(
        name="Spektr-RG", cospar="2019-040A", norad="44432", obj_type="payload",
        regime="SE_L2", status="active", helio=True, force="known SE-L2 object",
        bill_re=r"2019-040A|Spektr-RG", other=["SRG"],
        note=f"NORAD/COSPAR {TLE} (ID: 44432 19040A). Not in JPL Horizons "
             "(user statement); Bill Gray hex TLEs 19040a.tle..19040a25.tle "
             f"cover only 2019-07-14..2026-01-01 ({TLE}), so the neocpsat "
             "heliocentric batch fits are the only 2026 ephemeris (each "
             "Spektr-RG_combined_*.html carries the fo state vector, "
             "heliocentric equatorial J2000 AU & mAU/day, at the fit epoch, "
             "plus a geocentric ephemeris from the last obs). Status "
             "active: neocpsat sees station-keeping dVs (after Sep 7; "
             "between Sep 21 and Oct 4 2026). Bill-cache L225 labels "
             "CE23EA2 '2011-037A = Spektr-RG' (COSPAR typo for 2019-040A; "
             "CE23EA2 fits in the heliocentric Jan-Mar batch)."),
    "Spektr-R": dict(
        name="Spektr-R", cospar="2011-037A", norad="37755", obj_type="payload",
        regime="auto", status="derelict", bill_re=r"2011-037A|Spektr-R(?!G)",
        other=["RadioAstron"],
        note=f"NORAD {TLE}. Derelict: RadioAstron contact lost Jan 2019 "
             "(memory)."),
    "JWST": dict(
        name="JWST", cospar="2021-130A", norad="50463", obj_type="payload",
        regime="SE_L2", status="active", helio=True, force="known SE-L2 object",
        bill_re=r"2021-130A|JWST", other=["James Webb Space Telescope"],
        note=f"NORAD {TLE}. No neocpsat obs or fit (catalog rows carry only "
             "desigs; Pluto ephemeris page projectpluto.com/temp/jwst.htm)."),
    "Euclid": dict(
        name="Euclid", cospar="2023-092A", norad="57209", obj_type="payload",
        regime="SE_L2", status="active", helio=True, force="known SE-L2 object",
        bill_re=r"2023-092A|Euclid",
        note=f"NORAD {TLE}. No neocpsat obs or fit."),
    "TESS": dict(
        name="TESS", cospar="2018-038A", norad="43435", obj_type="payload",
        regime="EM_OTHER", status="active", bill_re=r"2018-038A|TESS",
        force="known P/2 lunar-resonant orbit",
        note=f"NORAD {TLE}. P/2 lunar resonance (catalog note: 2:1, Q~lunar "
             "distance). C45LDC1_combined.html is NOT a TESS fit: all 15 "
             "C45LDC1 obs are excluded there (~130 deg residuals) and the "
             "elements are IMP-7's (C45PQ51). Catalog: 'JPL Horizons verified: "
             "0.79 deg miss'. Status active (memory)."),
    "IMP-7": dict(
        name="IMP-7", cospar="1972-073A", norad="6197", obj_type="payload",
        regime="auto", status="derelict", bill_re=r"1972-073A|IMP-7",
        other=["Explorer 47"],
        note=f"NORAD {TLE}. Fit page is C45LDC1_combined.html (mislabelled "
             "TESS+IMP-7 combine; only the 48 C45PQ51 obs are used). "
             "Derelict since 1978 (memory)."),
    "IMP-8": dict(
        name="IMP-8", cospar="1973-078A", norad="6893", obj_type="payload",
        regime="auto", status="derelict", bill_re=r"1973-078A|IMP-8",
        other=["Explorer 50"],
        note=f"NORAD {TLE}. Derelict since 2006 (memory)."),
    "Vela 5b": dict(
        name="Vela 5B", cospar="1969-046E", norad="3955", obj_type="payload",
        regime="auto", status="derelict", bill_re=r"1969-046E|Vela 5b",
        note=f"NORAD {TLE} (03955 69046E)."),
    "Vela 1B": dict(
        name="Vela 1B", cospar="1963-039C", norad="692", obj_type="payload",
        regime="auto", status="derelict",
        bill_re=r"1963-039C|1969-039C|Vela 1B",
        note=f"NORAD {TLE} (00692 63039C); Bill-cache L159/L177 write "
             "'1969-039C' (typo). Bill L190 'ST26A71 ... = 1967-040A = Vela "
             "1B' has a COSPAR/name conflict (1967-040A is Vela IV-A); kept "
             "here by name (TF26A87 = 1963-039C = Vela 1B 3 d earlier); no "
             "neocpsat obs for either."),
    "Vela 3a": dict(
        name="Vela 3A", cospar="1965-058A", norad="1458", obj_type="payload",
        regime="auto", status="derelict", bill_re=r"1965-058A|Vela 3a",
        note=f"NORAD {TLE}. Identification is neocpsat's highartsat "
             "cross-match (README table); Bill cache only says 'unid, "
             "i=29.4, P=4.7d'."),
    "Vela IV-A": dict(
        name="Vela IV-A", cospar="1967-040A", norad="2765", obj_type="payload",
        regime="auto", status="derelict", bill_re=r"1967-040A|Vela IV-A",
        other=["Vela 7"],
        note=f"NORAD {TLE}; {ART} names it 'Vela 7' (= 4th-pair Vela 4A). "
             "C461U31 row says '(suspected)', corrected from Vela 3B."),
    "Vela 2B": dict(
        name="Vela 2B fragment (1964-040B bit)", cospar="", norad="",
        obj_type="debris", regime="auto", status="derelict",
        bill_re=r"1964-040B",
        note="Bill Gray (bill-2026-1-cache): 'unid (P=4.17d) ... piece of "
             "1964-040B, probably' -- a small bit near the Vela 2B plane, "
             "~2 mag fainter, not the parent; no catalogued NORAD. The "
             "C461VP1 row's 'NORAD 837 = 1964-040B' is the parent "
             "(highartsat cross-match)."),
    "Blok SO-L": dict(
        name="Blok SO-L (1977-093E)", cospar="1977-093E", norad="32705",
        obj_type="rocket_body", regime="auto", status="derelict",
        bill_re=r"1977-093E|Blok SO-L",
        note=f"NORAD {TLE}. 'Blok SO-L_combined.html' includes ST26A84 but "
             "excludes all 4 of its obs (separate Vela-2B bit)."),
    "Multijunk": dict(
        name="Multijunk", cospar="", norad="89983", obj_type="debris",
        regime="auto", status="derelict", bill_re=r"Multijunk|C1AUGE1",
        other=["C1AUGE1", "ZTF0Edd", "C0Z1JL1", "ZTF0Csc", "C21KXA2",
               "ZTF0Czu", "00000AAQ"],
        note=f"Unidentified junk; NORAD 89983 = 00000AAQ is a Bill Gray "
             f"surrogate, not a real NORAD number ({TLE}: c05ghg1.tle 2019, "
             "c0z1jl1.tle 2020, c1auge1_20..25.tle 2020-06..2026-01-01; "
             "../highartsat/catalog/data/orbits.json names it 'C0Z1JL1 = "
             "ZTF0Csc = C21KXA2 = ZTF0Czu = Multijunk'). Successive NEOCP "
             "apparitions are fitted separately (SRP/AMR ~20 m^2/kg in the "
             "Oct 6-9 batch), so the elements are only the latest short-arc "
             "snapshot."),
    "THEMIS-E": dict(
        name="THEMIS-E", cospar="2007-004E", norad="30798", obj_type="payload",
        regime="OTHER", status="active", bill_re=r"2007-004E|THEMIS[ -]?E",
        note=f"Regime OTHER: included only because the ZTF10Cw single-night "
             "fit (catalog P=3.9 d) is poorly constrained (U=16.0, 49-min "
             f"arc); the real orbit is P=1639.08 min = 1.138 d, apogee "
             f"altitude 78,227 km (Q ~84,600 km from centre), e=0.837 "
             f"({ART}, 07004e19.tle epoch 2020-07-13) -- below the 2.4 d cut, "
             f"not cislunar. NORAD {TLE}. Catalog-case collision: data/"
             "ZTF10Di.txt and mpecs/ZTF10Di.html hold ZTF10DI (THEMIS-E, Apr "
             "8) obs on the case-insensitive file system; the unrelated "
             "'?' row ZTF10Di (May 7) has its obs in unconfirmed_obs/"
             "ZTF10Di_2026May7.txt. Status active (memory)."),
    "Chang'e 6": dict(
        name="Chang'e-6 orbiter", cospar="2024-083A", norad="59627",
        obj_type="payload", regime="auto", status="active",
        bill_re=r"2024-083A|Chang'e-6", other=["Chang'e-6 Guidao Qi"],
        force="known lunar-mission spacecraft",
        note=f"NORAD {TLE}. CHANGE6_HISTORY.md: 'high lunar-distance "
             "geocentric orbit', i~74-85 deg, P 33-36 d, q 356-402k km, "
             "distant lunar flybys every ~13-14 d and frequent maintenance "
             "burns (>=30 m/s between Jul 6 and Aug 1 2026); no two windows "
             "link. Mid-Dec 2025 reconfiguration i 29->84 deg."),
    "Chandrayaan-3": dict(
        name="Chandrayaan-3 Propulsion Module", cospar="2023-098C",
        norad="57770", obj_type="payload", regime="auto", status="unknown",
        bill_re=r"2023-098C|Chandrayaan-3", force="known lunar-mission spacecraft",
        note=f"NORAD {TLE} (57770 23098C, Bill hex TLEs 23098c.tle / "
             "23098c25.tle to 2026-01-01; Bill log: '2023-098C = "
             "Chandrayaan-3 propulsion module'; ../lunarmission/"
             "spacetrack_status.tsv: 57770 'CH 3 PROPULSION MODULE' "
             "2023-098C). CHANDRAYAAN3_ANALYSIS.md L85 says 'NORAD 57320 = "
             "2023-098A' -- that is the pre-separation integrated craft / "
             "lander (tle_list chandra3.tle 2023-07-15..08-04), not the PM. "
             "Horizons -169 (PM) ends 2023-08-18 (../lunarmission/"
             "horizons_status.tsv), so not used. Status unknown: catalog "
             "ZTF10FG note (L210) says the June orbit passes the Moon at "
             "~28,000 km on 2026-08-19 and leaves near escape (~1 Mkm by Aug "
             "30), 'probably the last apparition'. ../neocpsat/alt_eph.txt "
             "(unlabelled daily geocentric ephemeris 2026-08-11..09-14, "
             "file dated Sep 15) matches ZTF10FG Aug 11.40 to ~1.4 deg "
             "(parallax-size at 0.0023 AU) and runs delta 0.00228 AU Aug 11 "
             "-> min 0.00101 AU (151,000 km) Aug 15 -> 0.00257 AU Aug 19 "
             "(lunar distance) -> 0.0114 AU (1.71 Mkm) Sep 14, still "
             "receding: probably departed the Earth-Moon system by mid-Sep "
             "2026 (inference, not a neocpsat statement)."),
    "Tiandu-1": dict(
        name="Tiandu-1", cospar="2024-051C", norad="59278", obj_type="payload",
        regime="auto", status="active", bill_re=r"2024-051C|Tiandu",
        note=f"NORAD {TLE}. Active: manoeuvre between Sep 20 and Oct 1 2026 "
             "(Aug 2-Sep 20 orbit leaves Oct 11-12 obs 3-9\" off)."),
    "CAPSTONE": dict(
        name="CAPSTONE", cospar="2022-070A", norad="52914", obj_type="payload",
        regime="auto", status="unknown", bill_re=r"2022-070A|CAPSTONE",
        force="known lunar-mission spacecraft",
        note="NORAD from catalog note (not in tle_list). Catalog: completed "
             "its lunar NRHO mission and departed the Moon at the end of Aug "
             "2026, now high Earth orbit; whether it still operates is not "
             "stated."),
    "Falcon Heavy S2": dict(
        name="Falcon Heavy S2 (NGRST launch)", cospar="2026-199B", norad="",
        obj_type="rocket_body", regime="EARTH_NEAR_HELIO", status="escaped",
        helio=True, bill_re=r"2026-199B|Falcon Heavy",
        note=f"Launched 2026-08-30 with NGRST/Roman (2026-199A = NORAD "
             f"100532 per {TLE}); 2026-199B NORAD not in local sources. "
             "Heliocentric, leaving Earth (synodic period ~34 yr for P=376.5 "
             "d)."),
    "DART upper stage": dict(
        name="DART Falcon 9 upper stage", cospar="2021-110B", norad="49498",
        obj_type="rocket_body", regime="EARTH_NEAR_HELIO", status="escaped",
        helio=True, bill_re=r"2021-110B|DART",
        note=f"NORAD {TLE}. Earth co-orbital (a=1.007 AU); NEOCP apparition "
             "2026-05-19 = its Earth approach; synodic period ~35 yr, so "
             "status escaped (not returning soon)."),
    "Artemis II": dict(
        name="Artemis II Orion", cospar="2026-069A", norad="68538",
        obj_type="payload", regime="TRANSLUNAR", status="decayed",
        status_date="2026-04-10", bill_re=r"2026-069A|Artemis II",
        force="known translunar mission",
        note=f"NORAD {TLE} (ID: 68538 26069A, TLE range 2026-04-03..04-10.9). "
             "Crewed free-return mission; decayed = Orion re-entry/splashdown "
             "~2026-04-10 (end of Bill's TLE range; date from memory). No "
             "archive obs to fit (catalog)."),
    "C45ZJD1": dict(
        name="C45ZJD1 (unidentified HEO object)", obj_type="unknown",
        regime="auto", status="unknown",
        note="Bill Gray: 'oddball ... AMR 0.1725 +/- 0.0028 m^2/kg, well above "
             "most junk (~0.01), well below ETBOs (20-100)'; not an ETBO."),
    "ETBO:ST26B21": dict(
        name="ETBO ST26B21 (unidentified)", obj_type="unknown", regime="auto",
        status="unknown",
        note="Unid ETBO, AMR=26 +/- 1 m^2/kg (Bill: P=4318m)."),
    "ETBO:A11DZEE": dict(
        name="ETBO A11DZEE (unidentified)", obj_type="unknown", regime="auto",
        status="unknown", note="Unid ETBO (Bill: A11DZEE = A11DnOA = A11DIrA)."),
    "?:ST26AA3": dict(
        name="ST26AA3 (unidentified HEO debris)", obj_type="debris",
        regime="auto", status="unknown",
        note="Bill: 'ST26C63 = ST26AA3'; 'Can't even come up with something "
             "to which this is related.' No single fo arc links the two "
             "tracklets (lunisolar evolution over the 53-d gap)."),
    "?:Sar2901": dict(
        name="Sar2901 (unidentified temporarily captured object)",
        obj_type="unknown", regime="EARTH_NEAR_HELIO", status="unknown",
        note="Regime EARTH_NEAR_HELIO = temporary capture (SCHEMA); the "
             "elements are the current bound geocentric osculating fit "
             "(Q beyond the ~1.5 Mkm Hill sphere). SAR2901_ANALYSIS.md: MPC "
             "'confirmed artificial' (no designation, identity not "
             "disclosed); weak-stability-boundary capture (Round 3: median "
             "2026 Apr 28, v_inf 0.075 km/s; SAR2901_RETURNS.md: ~Apr 21); "
             "single perigee 2026-08-18 at 536,012 km (33-obs Aug 24 fit); "
             "Round 3 (14-obs fit, not restated later) predicts escape "
             "2026 Dec 29 +/- 2 d. Pre-capture heliocentric fingerprint "
             "a=0.94275 AU e=0.05429 i=0.318 deg (ecl, epoch 2020-05-22); "
             "previous returns 2015 Aug 4 (11.3 Mkm) ... 1965 Apr (3.6 Mkm "
             "median, the only plausible space-age departure). Photon "
             "(2022-070C) excluded (Aug 20); DSCOVR-S2-debris hypothesis "
             "retracted (Round 5). H=31.4 -> meter-class."),
    "?:P22qGC1": dict(
        name="P22qGC1 (unidentified Earth-flyby object)", obj_type="unknown",
        regime="EARTH_NEAR_HELIO", status="unknown",
        note="Catalog: 'Earth-flyby object, not an Earth satellite': "
             "perigee 2026-09-22 (~54k km, lunar pass 52k km), outbound "
             "again; pre-flyby heliocentric orbit a=1.0171 AU e=0.0088 "
             "i=0.33 deg (ecl) q=1.0081 Q=1.0261 AU P=374.7 d. Elements "
             "below are the geocentric osculating fit (bound only "
             "formally, Q beyond the Hill sphere)."),
    "?:C466EJ1": dict(
        name="C466EJ1 (NEOCP geocentric, unidentified in neocpsat)",
        obj_type="unknown", regime="NEOCP_GEO", status="unknown",
        note="Bill (2026 Apr 12.18): 'C466EJ1 ... = C45WGK1 = C45VHJ1' i.e. "
             "the C45VHJ1 ETBO (i=17.55, P=1921m=1.33 d, AMR=51) -- if so the "
             "19.7 d single-night period (U=15.9) is spurious and the object "
             "is below the 2.4 d cut."),
    "?:C466QN1": dict(
        name="C466QN1 (NEOCP geocentric, unidentified in neocpsat)",
        obj_type="unknown", regime="NEOCP_GEO", status="unknown",
        note="Bill (2026 Apr 16.50): 'C466QN1 ... = C45WGK1 = C45VHJ1' i.e. "
             "the C45VHJ1 ETBO (P~1.33 d) -- if so the 2.7 d period is "
             "spurious and the object is below the 2.4 d cut."),
    "?:A11C39L": dict(
        name="A11C39L (NEOCP geocentric, unidentified)", obj_type="unknown",
        regime="NEOCP_GEO", status="unknown",
        note="Bill (2026 May 08.57): 'unid, i~19, Omega~29'."),
}
# Objects whose neocpsat fits must not be propagated to "now" (frequent
# manoeuvres between every observation window; CHANGE6_HISTORY.md).
NO_PROPAGATE = {"Chang'e 6"}
# Groups selected although the catalog has no P > 2.4 d / helio cell.
FORCE = {k for k, v in META.items() if v.get("force")}

# Extra aliases that are not catalog rows, with provenance.
EXTRA_ALIASES = {
    "Falcon Heavy S2": {"6JE1921": "data/6JE1921.txt (not in catalog.csv, "
                        "2026-09-15 W94/734, 9 obs): linked by a joint fo "
                        "fit of data/FalconH.txt + data/6JE1921.txt "
                        "(work/cache/neocpsat_fo/FH_plus_6JE1921, this "
                        "extract)"},
}
# Joint fits run by this extract (label -> obs stems, combine-as, fo args).
JOINT = {
    "Falcon Heavy S2": ("FH_plus_6JE1921", ["FalconH", "6JE1921"], "FalconH",
                        ["-h", "0", "-g", "17"]),
}


# ------------------------------------------------------------------ helpers
def iso(dt):
    return dt.strftime("%Y-%m-%d") if dt else ""


def fmt(v, nd=6):
    if v is None:
        return ""
    if isinstance(v, float):
        s = f"{v:.{nd}f}"
        if "." in s:                      # never strip an integer's zeros
            s = s.rstrip("0").rstrip(".")
        return s
    return str(v)


DESIG_TOKEN = re.compile(r"(?<![\w-])([A-Za-z0-9]{5,9})(?![\w-])")


def chain_tokens(note: str) -> list[str]:
    """Designation-like tokens that sit in an 'X = Y = Z' chain of a Note.

    Left neighbours are matched with a look-ahead so that the '=' is not
    consumed (the old single-pass regex lost the last member of every
    chain, e.g. A11DIrA in 'A11DZEE = A11DnOA = A11DIrA').  'Possibly = X'
    is not a confirmed linkage and is ignored.
    """
    note = re.sub(r"(?i)possibly\s*=\s*\S+", " ", note)
    hits = []
    for m in re.finditer(r"(?<![\w-])([A-Za-z0-9]{5,9})\)?\s*(?==)", note):
        hits.append((m.start(1), m.group(1)))
    for m in re.finditer(r"=\s*\(?([A-Za-z0-9]{5,9})(?![\w-])", note):
        hits.append((m.start(1), m.group(1)))
    return list(dict.fromkeys(t for _, t in sorted(hits)))


def trksub_first(d: str, D: dict, M: dict):
    """Earliest observation time of temp desig / trkSub ``d``: its own
    data/<d>.txt, else the earliest line carrying that trkSub in any data
    file (merged files hold several trkSubs), else in any mpecs page."""
    if d in D and D[d]["first"]:
        return D[d]["first"]
    c = [v["trk_first"][d] for v in D.values() if d in v.get("trk_first", {})]
    if c:
        return min(c)
    c = [r["ast_trk_first"][d] for r in M.values()
         if d in r.get("ast_trk_first", {})]
    return min(c) if c else None


def looks_desig(t: str) -> bool:
    return (6 <= len(t) <= 8 and re.search(r"\d", t) and
            re.search(r"[A-Za-z]", t) and not re.match(r"^\d{4}[A-Z]{1,3}$", t))


def read_bill(path=NEO / "bill-2026-1-cache"):
    """Every desig-headed line of Bill Gray's log cache (+ continuations)."""
    out = []
    for n, l in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not l.strip():
            continue
        if l.startswith(" ") and out:
            out[-1]["comment"] += " " + l.strip()
            continue
        m = re.match(r"(\S+)", l)
        d = m.group(1)
        mm = re.search(r"\((?:(\d{4})\s+)?([A-Za-z]+)\.?\s+([\d.]+)\s*(?:UT)?\)",
                       l)
        date = None
        if mm:
            mo = P.MONTHS.get(mm.group(2)[:3].lower())
            if mo:
                date = P.frac_day_to_dt(int(mm.group(1) or 2026), mo,
                                        float(mm.group(3)))
        out.append({"line": n, "desig": d, "text": l.strip(), "date": date,
                    "comment": "",
                    "tokens": set(DESIG_TOKEN.findall(l))})
    return out


# --------------------------------------------------------------- find_orb
def refit(desig: str, force=False):
    """fo -h 3 gravity-only refit of data/<desig>.txt; cached, read-only on
    neocpsat.  Returns the parsed elements record (or None)."""
    src = NEO / "data" / f"{desig}.txt"
    if not src.exists() or P.parse_obs_file(src)["n"] == 0:
        return None                       # missing, or redaction filler only
    d = FO_CACHE / desig
    rec = cached_fo(d, src.read_bytes(), ["-c", desig, "-h", "3"], force)
    if rec is None:
        return None
    o = P.parse_obs_file(src)
    rec["ast_first"], rec["ast_last"] = o["first"], o["last"]
    return rec


def cached_fo(d: Path, obs_bytes: bytes, args: list[str], force=False):
    """Run ``fo <d>/obs.txt <args> -O <d>`` unless <d> already holds a fit
    of exactly these obs with exactly these args (fresh -O dir every run,
    so ~/.find_orb caching cannot leak in).  Returns the parsed elements.
    A cached failure (stamp + obs present, no elements.txt) is not rerun."""
    el = d / "elements.txt"
    stamp = d / "fo_args.txt"
    want = " ".join(args)
    same_obs = (d / "obs.txt").exists() and \
        (d / "obs.txt").read_bytes() == obs_bytes
    fresh = (not force and same_obs and (
        (el.exists() and (not stamp.exists() or
                          stamp.read_text().strip() == want)) or
        (not el.exists() and stamp.exists() and
         stamp.read_text().strip() == want)))
    if not fresh:
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        (d / "obs.txt").write_bytes(obs_bytes)
        FO_LOCK.parent.mkdir(parents=True, exist_ok=True)
        with open(FO_LOCK, "w") as lk:          # one find_orb at a time (cle.adapters.fo holds the same lock)
            fcntl.flock(lk, fcntl.LOCK_EX)
            r = subprocess.run([FO_BIN, str(d / "obs.txt"), *args, "-O", str(d)],
                               capture_output=True, text=True, timeout=300)
            fcntl.flock(lk, fcntl.LOCK_UN)
        (d / "fo_stdout.txt").write_text(r.stdout + r.stderr)
        stamp.write_text(want + "\n")
    if not el.exists():
        return None
    rec = P.parse_mpec_page(el)
    rec["refit_dir"] = d
    return rec


def joint_fit(label: str, stems: list[str], combine_as: str, extra: list[str],
              force=False):
    """Cached joint fo fit of several neocpsat obs files (read-only)."""
    data = b"".join((NEO / "data" / f"{s}.txt").read_bytes() for s in stems)
    return cached_fo(FO_CACHE / label, data, ["-c", combine_as, *extra], force)


def fit_unconfirmed(force=False):
    """Geocentric (fo -h 3) fits of every ../neocpsat/unconfirmed_obs/*.txt,
    cached in work/cache/neocpsat_fo_unconfirmed/<stem>/; plus the heliocentric
    neocpsat page next to it.  Only used for the console report: none of
    these objects is in catalog.csv."""
    out = []
    for src in sorted((NEO / "unconfirmed_obs").glob("*.txt")):
        rec = cached_fo(FO_UNCONF / src.stem, src.read_bytes(), ["-h", "3"],
                        force) or {}
        page = src.with_suffix(".html")
        helio = P.parse_mpec_page(page) if page.exists() else {}
        out.append((src, P.parse_obs_file(src), rec, helio))
    return out


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refit", action="store_true",
                    help="force find_orb refits even if cached")
    args = ap.parse_args()

    cat = P.read_catalog()
    D = P.read_data_dir()
    # ADES file CDY3PM.txt: use its 80-col copy for spans
    if "CDY3PM.mpc80" in D:
        D["CDY3PM"] = D["CDY3PM.mpc80"]
    M = P.read_mpecs()
    USE = {n: P.page_obs_usage(P.NEOCPSAT / "mpecs" / n) for n in M}
    IDX = P.data_obs_index()
    BILL = read_bill()
    bill_by_desig = {}
    for b in BILL:
        bill_by_desig.setdefault(b["desig"], []).append(b)

    catset = {r["Temp Desig"] for r in cat}
    known = catset | set(D) | set(bill_by_desig)
    for v in D.values():
        known |= set(v["trksubs"])

    # ------------------------------------------------ group catalog rows
    LINK = {"?", "ETBO"}
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    linkrows = {r["Temp Desig"]: r for r in cat
                if r["Indentification"].strip() in LINK}
    for d in linkrows:
        parent[d] = d
    for d, r in linkrows.items():
        for t in chain_tokens(r["Note"]):
            if t in linkrows:
                union(d, t)
    groups = {}
    for r in cat:
        ident = r["Indentification"].strip()
        if ident in LINK:
            key = ("U", find(r["Temp Desig"]))
        else:
            key = ("N", ident)
        groups.setdefault(key, []).append(r)

    def first_obs(d):
        t = trksub_first(d, D, M)
        if t:
            return t
        for b in bill_by_desig.get(d, []):
            if b["date"]:
                return b["date"]
        return None

    G = []
    for key, rows in groups.items():
        members = [r["Temp Desig"] for r in rows]
        members.sort(key=lambda d: (first_obs(d) or datetime(2100, 1, 1,
                                    tzinfo=timezone.utc), d))
        if key[0] == "N":
            label = key[1]
            mkey = label
        else:
            ident = rows[0]["Indentification"].strip()
            label = f"{ident}:{members[0]}"
            mkey = label
        G.append({"key": key, "label": label, "mkey": mkey, "rows": rows,
                  "members": members})

    # ------------------------------------------- selection per group
    sel, excl = [], []
    boundary = []          # catalog cells that round to exactly the cut
    for g in G:
        cells = [P.parse_orbit_cell(r["Orbit"]) for r in g["rows"]]
        Ps = []
        for r, c in zip(g["rows"], cells):
            if not c["P"] or c["helio"]:
                continue
            p = c["P"]
            if round(p, 1) == P_CUT:   # '2.4 day' is rounded: use the page
                pg = M.get(f"{r['Temp Desig']}.html", {})
                if pg.get("P_d"):
                    boundary.append((r["Temp Desig"], g["label"], p,
                                     pg["P_d"]))
                    p = pg["P_d"]
            Ps.append(p)
        g["maxP"] = max(Ps) if Ps else None
        g["helio_cell"] = any(c["helio"] for c in cells)
        reasons = []
        if g["maxP"] and g["maxP"] > P_CUT:
            reasons.append(f"catalog P max {g['maxP']:g} d > {P_CUT}")
        if g["helio_cell"]:
            reasons.append("heliocentric fit in catalog")
        if g["mkey"] in FORCE:
            reasons.append(META[g["mkey"]]["force"])
        g["reasons"] = reasons
        (sel if reasons else excl).append(g)

    # ------------------------------------------- aliases / data / bill
    claimed_bill = set()
    for g in sel + excl:
        gset = set(g["members"])
        extras = {}
        for r in g["rows"]:
            for t in chain_tokens(r["Note"]):
                if t not in gset and (t in known or looks_desig(t)):
                    extras.setdefault(t, f"catalog Note L{r['_line']}")
        for t, why in EXTRA_ALIASES.get(g["mkey"], {}).items():
            extras.setdefault(t, why)
        g["gset"] = gset | set(extras)
        # data files: member/extra stems, or merged files whose trksubs hit
        stems = []
        for s, v in D.items():
            if s == "CDY3PM.mpc80":
                continue
            if s in g["gset"] or (set(v["trksubs"]) & g["gset"]):
                stems.append(s)
        for s in stems:
            for t in D[s]["trksubs"]:
                if t not in g["gset"]:
                    extras.setdefault(t, f"trksub in data/{s}.txt")
        g["gset"] = gset | set(extras)
        g["stems"] = sorted(set(stems))
        g["extras"] = extras
        g["bill"] = []
    meta_order = [g for g in sel + excl]
    for b in BILL:
        owner = None
        for g in meta_order:              # catalog membership first
            if b["desig"] in g["gset"]:
                owner = g
                break
        if owner is None:
            for g in meta_order:
                rx = META.get(g["mkey"], {}).get("bill_re")
                if (rx and re.search(rx, b["text"])) or \
                        (b["tokens"] & set(g["members"])):
                    owner = g
                    break
        if owner is not None:
            owner["bill"].append(b)
            if b["desig"] not in owner["gset"]:
                owner["extras"].setdefault(
                    b["desig"], f"bill-2026-1-cache L{b['line']} only")
                owner["gset"].add(b["desig"])
    for g in sel + excl:                  # obs files of Bill-only desigs
        for s_, v in D.items():
            if s_ != "CDY3PM.mpc80" and s_ not in g["stems"] and (
                    s_ in g["gset"] or set(v["trksubs"]) & g["gset"]):
                g["stems"].append(s_)
        g["stems"].sort()

    # ------------------------------------------- fits per group
    def page_group_usage(name, g):
        """(used, total, per-desig [used,total], first, last used obs time)
        of this group's observations on one pseudo-MPEC page."""
        use = USE.get(name) or []
        tot = used = 0
        per = {}
        t_used = []
        for o in use:
            stems = IDX.get(o["key"], set())
            mine = (o["trksub"] in g["gset"]) or bool(stems & set(g["stems"]))
            if mine:
                lab = next((s for s in sorted(stems) if s in g["members"]),
                           o["trksub"])
                rr = per.setdefault(lab, [0, 0])
                rr[1] += 1
                tot += 1
                if o["used"]:
                    used += 1
                    rr[0] += 1
                    t_used.append(o["dt"])
        if not use:                       # no residual block: trust trksubs
            rec = M[name]
            for t, n in rec.get("ast_trksubs", {}).items():
                if t in g["gset"]:
                    per[t] = [n, n]
                    tot += n
                    used += n
            if used:
                t_used = [rec.get("ast_first"), rec.get("ast_last")]
        t_used = [t for t in t_used if t]
        return (used, tot, per, min(t_used) if t_used else None,
                max(t_used) if t_used else None)

    for g in sel:
        meta = META.get(g["mkey"], {})
        fits = []
        for name, rec in M.items():
            used, tot, per, g0, g1 = page_group_usage(name, g)
            if used == 0:
                continue
            f = dict(rec)
            f.update(page=name, g_used=used, g_tot=tot, per=per,
                     combined="_combined" in name, g_first=g0, g_last=g1,
                     foreign=(tot < (rec.get("n_total") or rec.get(
                         "n_ast") or tot)))
            fits.append(f)
        fits.sort(key=lambda f: (f.get("ast_last") or NOW, f["combined"],
                                 f.get("n_used") or 0), reverse=True)
        g["fits"] = fits

        def ok(f):
            if not f.get("has_elements"):
                return False
            if meta.get("helio"):
                return f.get("center") == "sun"
            if f.get("center") != "earth" or f.get("e") is None or f["e"] >= 1:
                return False
            return f.get("U") is None or f["U"] < 15

        best = next((f for f in fits if ok(f)), None)
        g["refits"] = []
        if best is None and not meta.get("helio"):
            for d in sorted([m for m in g["members"] if m in D],
                            key=lambda m: D[m]["last"], reverse=True):
                rf = refit(d, force=args.refit)
                if rf:
                    rf.update(page=f"refit:{d}", combined=False, g_used=rf.get(
                        "n_used"), g_tot=rf.get("n_total"), per={},
                        g_first=rf.get("ast_first"), g_last=rf.get("ast_last"))
                    g["refits"].append(rf)
                    if ok(rf):
                        best = rf
                        break
        g["flag_poor"] = False
        if best is None:
            pool = [f for f in fits + g["refits"] if f.get("has_elements") and
                    f.get("center") in ("earth", "sun") and
                    (f.get("e") or 9) < 1]
            if pool:
                best = pool[0]
                g["flag_poor"] = True
        g["best"] = best
        g["newer_rejected"] = [f for f in fits if best and f is not best and
                               (f.get("ast_last") or NOW) >
                               (best.get("ast_last") or NOW)]
        g["joint"] = []
        if g["mkey"] in JOINT:
            lab, stems, cas, extra = JOINT[g["mkey"]]
            jf = joint_fit(lab, stems, cas, extra, force=args.refit)
            if jf:
                spans = [D[s] for s in stems if s in D]
                jf.update(page=f"joint:{lab}", combined=True, per={},
                          ast_first=min(v["first"] for v in spans),
                          ast_last=max(v["last"] for v in spans),
                          g_first=min(v["first"] for v in spans),
                          g_last=max(v["last"] for v in spans),
                          joint_desc=f"fo {' '.join(['-c', cas, *extra])} "
                          f"on data/{'.txt + data/'.join(stems)}.txt")
                g["joint"].append(jf)

    for g in sel:                         # unique obs over the group's files
        st = set(g["stems"])
        if "CDY3PM" in st:                # ADES file: its 80-col copy is indexed
            st.add("CDY3PM.mpc80")
        keys = {k for k, v in IDX.items() if v & st}
        g["n_obs_unique"] = len(keys) + sum(
            D[s]["n_psv"] for s in g["stems"] if s != "CDY3PM")

    rows_out = [build_row(g, D, M, NOW) for g in sel]
    rows_out.sort(key=lambda r: (REGIME_ORDER.get(r["regime"], 99),
                                 r["name"].lower()))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(COLS) + "\n")
        for r in rows_out:
            fh.write("\t".join(clean(r[c]) for c in COLS) + "\n")

    srg = next(g for g in sel if g["mkey"] == "Spektr-RG")
    n_srg = write_spektr_history(srg, cat, D, M, USE, IDX, BILL)

    # ------------------------------------------- console + JSON report
    from collections import Counter
    rep = {"generated_utc": datetime.now(timezone.utc).strftime(
               "%Y-%m-%dT%H:%M:%SZ"),
           "now_reference": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "tsv": str(OUT.relative_to(ROOT)), "rows": len(rows_out),
           "spektr_rg_history_tsv": str(OUT_SRG.relative_to(ROOT)),
           "spektr_rg_history_rows": n_srg,
           "regime_counts": dict(Counter(r["regime"] for r in rows_out)),
           "covers_now_counts": dict(Counter(r["covers_now"]
                                             for r in rows_out)),
           "rows_brief": [{k: r[k] for k in ("src_ref", "name", "regime",
                                              "status", "period_d",
                                              "elem_epoch", "covers_now")}
                          for r in rows_out]}
    print(f"wrote {OUT} rows={len(rows_out)}")
    print(f"wrote {OUT_SRG} rows={n_srg}")
    print("regimes", rep["regime_counts"])
    print("covers_now", rep["covers_now_counts"])
    rep["excluded_groups"] = []
    print("EXCLUDED groups (max catalog P):")
    for g in sorted(excl, key=lambda g: -(g["maxP"] or 0)):
        print(f"   {g['label']:28s} P={g['maxP']} n={len(g['members'])}")
        rep["excluded_groups"].append({
            "group": g["label"], "max_catalog_P_d": g["maxP"],
            "members": g["members"],
            "catalog_lines": sorted(r["_line"] for r in g["rows"])})
    rep["boundary_cells"] = []
    print("catalog P cells that round to the 2.4 d cut (page P used):")
    for d, lab, pc, pp in boundary:
        print(f"   {d} ({lab}): catalog {pc} -> page {pp:.4f} d "
              f"{'IN' if pp > P_CUT else 'out'}")
        rep["boundary_cells"].append({"desig": d, "group": lab,
                                      "catalog_P": pc, "page_P_d": pp,
                                      "selected": pp > P_CUT})
    rep["excluded_high_apogee"] = []
    print("excluded groups with a fitted apogee >= 100,000 km (P <= 2.4 d):")
    for g in excl:
        hit = None
        for r in g["rows"]:
            for name in [f"{g['label']}_combined.html",
                         f"{r['Temp Desig']}.html"]:
                pg = M.get(name, {})
                if pg.get("center") == "earth" and (pg.get("e") or 9) < 1 \
                        and (pg.get("Q_km") or 0) >= 1e5:
                    hit = (name, pg)
                    break
            if hit:
                break
        if hit:
            name, pg = hit
            print(f"   {g['label']:20s} {name:30s} P={pg.get('P_d')} "
                  f"Q={pg['Q_km']:.0f} km U={pg.get('U')}")
            rep["excluded_high_apogee"].append({
                "group": g["label"], "page": f"{REL}/mpecs/{name}",
                "P_d": pg.get("P_d"), "Q_km": round(pg["Q_km"]),
                "U": pg.get("U")})
    rep["excluded_no_period"] = []
    print("excluded unidentified rows with no catalog period (page fit "
          "hyperbolic / undetermined):")
    for g in excl:
        if g["key"][0] != "U" or g["maxP"]:
            continue
        for r in g["rows"]:
            pg = M.get(f"{r['Temp Desig']}.html", {})
            print(f"   {r['Temp Desig']:9s} L{r['_line']:<4d} "
                  f"'{r['Orbit']}' page e={fmt(pg.get('e'), 2)}")
            rep["excluded_no_period"].append({
                "desig": r["Temp Desig"], "catalog_line": r["_line"],
                "orbit_cell": r["Orbit"], "page_e": pg.get("e"),
                "page_q_km": pg.get("q_km")})
    used = set()
    for g in sel + excl:
        used |= set(g["stems"])
    rep["unattributed_data_files"] = []
    print("data/ files attributed to no catalog group (fo -h 3 refit):")
    for s_ in sorted(set(D) - used - {"CDY3PM.mpc80"}):
        v = D[s_]
        rf = refit(s_, force=args.refit) or {}
        print(f"   {s_:14s} {v['format']:8s} n={v['n']} {iso(v['first'])}.."
              f"{iso(v['last'])} trksubs={','.join(v['trksubs'])} | "
              f"{geo_desc(rf)}")
        rep["unattributed_data_files"].append({
            "file": f"{REL}/data/{s_}.txt", "n_obs": v["n"],
            "first": iso(v["first"]), "last": iso(v["last"]),
            "trksubs": list(v["trksubs"]), "geocentric_refit": geo_desc(rf),
            "refit_dir": f"work/cache/neocpsat_fo/{s_}" if rf else ""})
    rep["unconfirmed_obs"] = []
    print("unconfirmed_obs/ (not in catalog.csv): geocentric fo -h 3 refit | "
          "neocpsat heliocentric page")
    for src, o, rec, helio in fit_unconfirmed(force=args.refit):
        hel = (f"a={fmt((helio.get('a_km') or 0) / P.AU_KM, 4)} AU "
               f"e={fmt(helio.get('e'), 3)} i={fmt(helio.get('incl'), 2)} "
               f"Earth MOID={helio.get('earth_moid_au')} AU H={helio.get('H')}"
               if helio.get("has_elements") else "no page")
        print(f"   {src.name:22s} n={o['n']} {iso(o['first'])} | "
              f"{geo_desc(rec)} | {hel}")
        rep["unconfirmed_obs"].append({
            "file": f"{REL}/unconfirmed_obs/{src.name}", "n_obs": o["n"],
            "first": iso(o["first"]), "last": iso(o["last"]),
            "geocentric_refit": geo_desc(rec),
            "refit_dir": f"work/cache/neocpsat_fo_unconfirmed/{src.stem}",
            "heliocentric_page": f"{REL}/unconfirmed_obs/{src.stem}.html"
            if helio else "", "heliocentric": hel})
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(rep, indent=1, default=str) + "\n")
    print(f"wrote {OUT_JSON}")


def geo_desc(rec):
    """One-line description of a geocentric fo fit (bound or hyperbolic)."""
    if not rec or not rec.get("has_elements"):
        return "no fit"
    e, q = rec.get("e"), rec.get("q_km")
    s = (f"e={fmt(e, 3)} q={fmt(q, 0)} km i={fmt(rec.get('incl'), 1)} "
         f"{rec.get('n_used')}/{rec.get('n_total')} obs U="
         f"{fmt(rec.get('U')) or 'n/a'}")
    if e is not None and e < 1:
        s += f" P={fmt(rec.get('P_d') or rec.get('P_d_from_a'), 3)} d"
    elif e is not None and q:
        vinf = math.sqrt(P.MU_EARTH * (e - 1) / q)
        s += f" hyperbolic v_inf={vinf:.2f} km/s"
    return s


REGIME_ORDER = {k: i for i, k in enumerate(
    ["LUNAR_ORBIT", "EM_L1", "EM_L2", "EM_DRO", "EM_OTHER", "SE_L1", "SE_L2",
     "TRANSLUNAR", "CISLUNAR_HEO", "HEO", "EARTH_NEAR_HELIO", "NEOCP_GEO",
     "OTHER"])}


def clean(v):
    """TSV-safe text: no tab/CR/LF, and no double quote (SCHEMA: no quoting;
    arcsec marks become two apostrophes so naive CSV readers cannot choke)."""
    return str(v if v is not None else "").replace("\t", " ").replace(
        "\n", " ").replace("\r", " ").replace('"', "''")


def build_row(g, D, M, now):
    meta = META.get(g["mkey"], {})
    best = g["best"]
    rows = g["rows"]
    # ---------------- aliases: temp desigs by first obs, then other names
    def fo_date(d):
        t = trksub_first(d, D, M)
        if t:
            return t
        for b in g["bill"]:
            if b["desig"] == d and b["date"]:
                return b["date"]
        return None
    desigs = list(dict.fromkeys(g["members"] + [
        t for t in g["extras"] if looks_desig(t) or t in D]))
    desigs.sort(key=lambda d: (fo_date(d) or datetime(2100, 1, 1,
                               tzinfo=timezone.utc), d))
    name = meta.get("name") or g["label"]
    aliases = [d for d in desigs if d != name]
    for o in meta.get("other", []):
        if o not in aliases:
            aliases.append(o)
    # ---------------- obs span / apparitions
    firsts = [D[s]["first"] for s in g["stems"] if D[s]["first"]]
    lasts = [D[s]["last"] for s in g["stems"] if D[s]["last"]]
    for f in g["fits"]:                    # this object's used obs only
        if f.get("g_first"):
            firsts.append(f["g_first"])
            lasts.append(f["g_last"])
    bdates = [b["date"] for b in g["bill"] if b["date"]]
    span0 = min(firsts + bdates) if firsts + bdates else None
    span1 = max(lasts + bdates) if lasts + bdates else None
    # observation windows (gap > 3 d) over member first-obs dates
    pts = sorted({fo_date(d) for d in desigs if fo_date(d)})
    windows = 0
    prev = None
    for t in pts:
        if prev is None or (t - prev).days > 3:
            windows += 1
        prev = t
    n_obs = g["n_obs_unique"]
    # ---------------- elements
    el = dict(period_d="", a_km="", e="", incl_deg="", perigee_km="",
              apogee_km="", elem_epoch="", frame_center="")
    elnote = ""
    if best:
        center = best.get("center")
        el["frame_center"] = center
        a = best.get("a_km")
        q, Q = best.get("q_km"), best.get("Q_km")
        e = best.get("e")
        if a is None and q is not None and e is not None and e < 1:
            a = q / (1 - e)
        if Q is None and a is not None and e is not None:
            Q = a * (1 + e)
        Pd = best.get("P_d") or best.get("P_d_from_a")
        if center == "earth":
            el["period_d"] = fmt(Pd, 4)
        el["a_km"] = fmt(a, 1)
        el["e"] = fmt(e, 7)
        el["incl_deg"] = fmt(best.get("incl"), 5)
        el["perigee_km"] = fmt(q, 1)
        el["apogee_km"] = fmt(Q, 1)
        if best.get("epoch_tt"):
            el["elem_epoch"] = P.tt_to_utc_iso(best["epoch_tt"])
        frame = "J2000 ecliptic" if best.get("frame") == "ecliptic" else \
            "J2000 equator"
        src = (f"{REL}/mpecs/{best['page']}" if not best["page"].startswith(
            "refit:") else f"work/cache/neocpsat_fo/{best['page'][6:]}/"
            "elements.txt (fo -h 3 refit of "
            f"{REL}/data/{best['page'][6:]}.txt, this extract)")
        elnote = (f"elements: {src}, {frame}, epoch "
                  f"{iso(best.get('epoch_tt'))} TT, Node={fmt(best.get('node'), 5)}"
                  f" Peri={fmt(best.get('argp'), 5)} M={fmt(best.get('M'), 5)}, "
                  f"{best.get('n_used')}/{best.get('n_total')} obs "
                  f"{iso(best.get('ast_first'))}..{iso(best.get('ast_last'))}"
                  + (f" (page; this object's {best.get('g_used')} used obs "
                     f"{iso(best.get('g_first'))}..{iso(best.get('g_last'))})"
                     if best.get("foreign") else "")
                  + f", rms {fmt(best.get('resid'), 2)}\", "
                  f"U={fmt(best.get('U')) or 'n/a (short no-sigma page)'}")
        if best.get("amr_not_fitted"):
            elnote += ", AMR not fitted (gravity only)"
        if best.get("amr") is not None:
            elnote += f", AMR={fmt(best['amr'], 4)}"
            if best.get("amr_sigma") is not None:
                elnote += f"+/-{fmt(best['amr_sigma'], 4)}"
            elnote += " m^2/kg"
        if center == "sun":
            elnote += (f"; heliocentric P={fmt(Pd, 3)} d (period_d left empty:"
                       " not geocentric), a/q/Q from the Sun")
            if best.get("earth_moid_au") is not None:
                elnote += f", Earth MOID {best['earth_moid_au']} AU"
        if best.get("eph_delta_km") and best.get("eph_start") and \
                eph_near_arc(best):
            elnote += (f"; geocentric distance {best['eph_delta_km']:.0f} km at "
                       f"{best['eph_start']} UTC (page ephemeris)")
        if g["flag_poor"]:
            elnote += "; POORLY CONSTRAINED (no fit passed U<15)"
    else:
        cells = [P.parse_orbit_cell(r["Orbit"]) for r in rows]
        withP = [c for c in cells if c["P"]]
        if withP:
            c = withP[-1]
            el["period_d"] = fmt(c["P"], 3)
            el["incl_deg"] = fmt(c["i"], 3)
            el["frame_center"] = "earth"
            elnote = f"elements: catalog Orbit cell only ('{c['raw']}')"
    # ---------------- regime
    regime = meta.get("regime", "auto")
    if regime == "auto":
        Q = float(el["apogee_km"]) if el["apogee_km"] else None
        Pn = float(el["period_d"]) if el["period_d"] else None
        if Q is not None and el["frame_center"] == "earth":
            regime = "CISLUNAR_HEO" if Q >= 100000 else (
                "HEO" if (Pn or 0) > P_CUT else "OTHER")
        elif g["key"][0] == "U":
            regime = "NEOCP_GEO"
        else:
            regime = "CISLUNAR_HEO"
    # ---------------- status
    status = meta.get("status", "unknown")
    status_date = meta.get("status_date") or iso(span1)
    # ---------------- covers_now
    cov = "N"
    if best and status not in ("decayed", "lunar_impact", "landed") and \
            not g["flag_poor"]:
        age = (now - best["ast_last"]).total_seconds() / 86400 if \
            best.get("ast_last") else 999
        arc = ((best["ast_last"] - best["ast_first"]).total_seconds() / 86400
               if best.get("ast_last") and best.get("ast_first") else 0)
        if g["mkey"] in NO_PROPAGATE:
            cov = "N"
        elif age <= 14:
            cov = "Y"
        elif age <= 60:
            # a long-arc fit of a passive object extrapolates over a gap
            # shorter than its own arc; anything else is a judgement call
            cov = "Y" if (status == "derelict" and arc >= age) else "?"
    # ---------------- ephem sources
    es = []
    for f in sorted(g["fits"] + [r for r in g["refits"] if r is best]
                    + g.get("joint", []),
                    key=lambda f: f.get("ast_last") or now, reverse=True):
        if f["page"].startswith(("refit:", "joint:")):
            ref = f"work/cache/neocpsat_fo/{f['page'][6:]}/elements.txt"
        else:
            ref = f"{REL}/mpecs/{f['page']}"
        ep = iso(f.get("epoch_tt")) if f.get("has_elements") else "noelem"
        # coverage = arc of this object's obs actually used by the fit
        a0 = f.get("g_first") or f.get("ast_first")
        a1 = f.get("g_last") or f.get("ast_last")
        es.append(f"neocpsat_fit:{ref}@{ep}[{iso(a0)}..{iso(a1)}]")
    redacted = []
    stems = list(g["stems"])
    for r in rows:                         # redacted-only files are not stems
        d = r["Temp Desig"]
        if d in D and d not in stems and D[d]["n"] == 0 and \
                D[d]["n_redacted"]:
            stems.append(d)
    for s in sorted(stems, key=lambda s: D[s]["first"] or
                    datetime.fromisoformat((D[s]["redacted_span"] or
                                            ("2100-01-01",))[0]).replace(
                        tzinfo=timezone.utc)):
        v = D[s]
        fn = "CDY3PM.txt" if s == "CDY3PM" else f"{s}.txt"
        if v["n"] == 0 and v["redacted_span"]:
            a0, a1 = v["redacted_span"]
            redacted.append(f"{REL}/data/{fn} holds {v['n_redacted']} lines "
                            f"({a0}..{a1}) of Project Pluto redaction filler "
                            "(no usable astrometry; not listed as neocp_obs)")
            continue
        a0, a1 = iso(v["first"]), iso(v["last"])
        es.append(f"neocp_obs:{REL}/data/{fn}#{v['n']}[{a0}..{a1}]")
    other_links = []
    for r in sorted(rows, key=lambda r: fo_date(r["Temp Desig"]) or now):
        url = r["link to projectpluto site"].strip()
        if not url:
            continue
        d = r["Temp Desig"]
        if "projectpluto.com" not in url:
            other_links.append(f"{d}: {url}")
            continue
        if "/neocp2/mpecs/" in url:
            a0 = D[d]["first"] if d in D and D[d]["first"] else fo_date(d)
            a1 = D[d]["last"] if d in D and D[d]["last"] else fo_date(d)
            es.append(f"pluto_mpec:{url}[{iso(a0)}..{iso(a1)}]")
        else:                              # e.g. projectpluto.com/temp/jwst.htm
            es.append(f"pluto_mpec:{url}[..]")    # coverage not stated
            other_links.append(f"{d}: {url} is a Project Pluto ephemeris page "
                               "with no stated span (listed with empty bounds)")
    # ---------------- notes
    lines = sorted(r["_line"] for r in rows)
    hm = sorted({r["H mag"] for r in rows if r["H mag"].strip()}, key=float)
    nb = [d for d, w in g["extras"].items() if "bill-2026-1-cache" in w]
    ndat = [d for d, w in g["extras"].items() if w.startswith("data/")]
    notes = [
        f"catalog.csv {len(rows)} rows (lines {compress(lines)}); selected: "
        + "; ".join(g["reasons"]),
        f"apparitions: {len(g['members'])} catalog desigs"
        + (f" + {len(nb)} Bill-cache-only ({', '.join(nb)})" if nb else "")
        + (f" + {len(ndat)} data-only ({', '.join(ndat)})" if ndat else "")
        + f", {windows} obs windows, span {iso(span0)}..{iso(span1)}"
        + (f", {n_obs} obs in neocpsat data files" if g["stems"] else
           ", no neocpsat obs files"),
    ]
    if elnote:
        notes.append(elnote)
    for f in g["newer_rejected"][:3]:
        notes.append(f"newer fit not used: {f['page']} "
                     f"({iso(f.get('ast_first'))}..{iso(f.get('ast_last'))}, "
                     f"{'e=' + fmt(f.get('e'), 3) if f.get('e') else 'no elements'}"
                     f", U={fmt(f.get('U')) or 'n/a'}, i={fmt(f.get('incl'), 2)}, "
                     f"Node={fmt(f.get('node'), 2)})")
    combs = [f for f in g["fits"] if f["combined"]]
    if combs and g["mkey"] != "Spektr-RG":
        notes.append("combined fits: " + "; ".join(
            f"{f['page']} {iso(f.get('ast_first'))}..{iso(f.get('ast_last'))} "
            f"{f.get('n_used')}/{f.get('n_total')} P={fmt(f.get('P_d'), 3)} "
            f"e={fmt(f.get('e'), 3)} i={fmt(f.get('incl'), 2)}"
            for f in sorted(combs, key=lambda f: (f.get("ast_first") or now,
                                                  f.get("ast_last") or now))))
    for f in g.get("joint", []):
        notes.append(
            f"joint fit (this extract): work/cache/neocpsat_fo/{f['page'][6:]}"
            f" ({f['joint_desc']}): {f.get('n_used')}/{f.get('n_total')} obs "
            f"{iso(f['ast_first'])}..{iso(f['ast_last'])}, rms "
            f"{fmt(f.get('resid'), 2)}\", {f.get('center')}-centred a="
            f"{fmt((f.get('a_km') or 0) / P.AU_KM, 6)} AU e={fmt(f.get('e'), 5)}"
            f" i={fmt(f.get('incl'), 3)}"
            + (f" AMR={fmt(f['amr'], 4)}+/-{fmt(f.get('amr_sigma'), 4)}"
               if f.get("amr") is not None else ""))
    for t in redacted:
        notes.append(t)
    if other_links:
        notes.append("catalog link column (non-pseudo-MPEC links): "
                     + "; ".join(other_links))
    if g["mkey"] == "Spektr-RG":
        notes.append(spektr_summary(g))
    hfit = best.get("H") if best else None
    notes.append(f"H (NEOCP/find_orb, H-G G=0.15): catalog {'/'.join(hm) or '-'}"
                 + (f"; selected fit H={hfit}" if hfit else ""))
    if meta.get("note"):
        notes.append(meta["note"])
    # first catalog note (identification text), trimmed
    idnote = next((r["Note"] for r in rows if r["Note"].strip()), "")
    if idnote and g["mkey"] != "Spektr-RG":
        notes.append("catalog note: " + idnote[:400])
    bl = [b for b in g["bill"]]
    if bl:
        notes.append("Bill cache: " + "; ".join(
            f"L{b['line']} {b['text'][:110]}" for b in bl[:6])
            + (f"; +{len(bl) - 6} more" if len(bl) > 6 else ""))
    src_ref = (f"catalog.csv:Indentification={g['label']}" if g["key"][0] == "N"
               else f"catalog.csv:Temp Desig={g['members'][0]}")
    return {
        "src": "neocpsat", "src_ref": src_ref, "name": name,
        "aliases": ";".join(aliases), "norad": meta.get("norad", ""),
        "cospar": meta.get("cospar", ""), "horizons_id": "",
        "obj_type": meta.get("obj_type", "unknown"), "regime": regime,
        "status": status, "status_date": status_date, **el,
        "ephem_sources": ";".join(es), "covers_now": cov,
        "notes": " | ".join(notes),
    }


def eph_near_arc(rec, pad_days=2.0):
    """True if the page's first geocentric-ephemeris line lies within the
    obs arc +/- pad (some pages start their ephemeris at the page creation
    date or at 2026-01-01, which says nothing about the observed range)."""
    try:
        t = datetime.strptime(rec["eph_start"], "%Y %m %d %H:%M").replace(
            tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return False
    a0, a1 = rec.get("ast_first"), rec.get("ast_last")
    if not a0 or not a1:
        return False
    return a0 - timedelta(days=pad_days) <= t <= a1 + timedelta(days=pad_days)


def compress(nums):
    """[3,4,5,9] -> '3-5,9'."""
    out, s = [], None
    for i, n in enumerate(nums):
        if s is None:
            s = p = n
        elif n == p + 1:
            p = n
        else:
            out.append(f"{s}-{p}" if p > s else f"{s}")
            s = p = n
    if s is not None:
        out.append(f"{s}-{p}" if p > s else f"{s}")
    return ",".join(out)


def spektr_summary(g):
    parts = []
    for f in sorted([f for f in g["fits"] if f["combined"]],
                    key=lambda f: (f.get("epoch_tt"), f.get("ast_first"))):
        parts.append(
            f"{f['page'].replace('.html', '')}: obs {iso(f['ast_first'])}.."
            f"{iso(f['ast_last'])} {f.get('n_used')}/{f.get('n_total')} "
            f"rms {fmt(f.get('resid'), 2)}\" ep {iso(f.get('epoch_tt'))} "
            f"a={fmt(f['a_km'] / P.AU_KM, 6)} AU e={fmt(f.get('e'), 5)} "
            f"i={fmt(f.get('incl'), 3)} q={fmt(f['q_km'] / P.AU_KM, 5)} AU "
            f"P={fmt(f.get('P_d'), 2)} d "
            + (f"AMR={fmt(f['amr'], 4)}+/-{fmt(f.get('amr_sigma'), 4)}"
               if f.get("amr") is not None else "AMR not fitted"))
    return ("orbit history (heliocentric combined batches, J2000 ecliptic; "
            "full table in catalog/sources/neocpsat_spektr_rg_history.tsv): "
            + " || ".join(parts)
            + " || no obs 2026-03-09..06-05 except A11Cswo May 15-16 (data "
            "only); batches split by station-keeping dVs: after Sep 7 and "
            "between Sep 21 and Oct 4 (catalog notes).")


# ------------------------------------------------------ Spektr-RG history
SRG_COLS = ["row_type", "page_or_file", "combine_as", "desigs_used_total",
            "obs_first_utc", "obs_last_utc", "n_obs_used", "n_obs_total",
            "fit_epoch_tt", "fit_epoch_utc", "frame", "center", "a_au", "a_km", "e",
            "incl_deg", "node_deg", "argp_deg", "M_deg", "q_au", "q_km",
            "Q_au", "Q_km", "P_d", "H", "U", "amr_m2kg", "amr_sigma",
            "resid_arcsec", "earth_moid_au", "geo_delta_km", "geo_delta_at_utc",
            "page_created_utc", "catalog_line", "pluto_mpec", "bill_cache",
            "notes"]

SRG_BATCH_NOTES = {
    "Spektr-RG_combined_2026_01_09.html":
        "OLD batch of build_spektr_batches.py (9 desigs); 5 obs rejected; "
        "catalog cell 'i =0.21, Om = 338.4, P = 396.6 day' AMR 0.026",
    "Spektr-RG_combined_2026_06_05.html":
        "JUNE batch of build_spektr_batches.py extended to Jul 23; catalog "
        "cell 'i =0.12, Om = 178.3, P = 382.2 day' AMR 0.016",
    "Spektr-RG_combined_2026_08_18.html":
        "Jun 12-Aug 19 refit incl. A11FMwA (A11D8yh Jun 5 obs not used here); "
        "catalog cell 'i =0.19, Om = 159.2, P = 389.3 day' AMR 0.018",
    "Spektr-RG_combined_2026_09_01.html":
        "Jun 5-Sep 7 batch; catalog: 'a small station-keeping dV after Sep 7 "
        "puts later apparitions on a separate fit'; cell 'i =0.20, Om = 158.3, "
        "P = 395.5 day' AMR 0.018",
    "Spektr-RG_combined_2026_09_08.html":
        "post-dV batch Sep 8-21 (8 desigs relabelled A11GrOs); 'refit "
        "separately after a small station-keeping dV on Sep 7 (AMR not "
        "fitted); another dV between Sep 21 and Oct 4'; cell 'i =0.15, "
        "Om = 153.3, P = 397.7 day'",
    "Spektr-RG_combined_2026_10_04.html":
        "post-dV batch Oct 4-13 (C46MLP1 = P12r91e = X91047 + A11HUyG, "
        "relabelled C46MLP1); AMR not fitted; latest fit; cell 'i =0.09, "
        "Om = 86.7, P = 393.2 day'",
}


def write_spektr_history(g, cat, D, M, USE, IDX, BILL):
    catrow = {r["Temp Desig"]: r for r in cat}
    rows = []
    desig_batches = {}

    def elem_cols(rec):
        c = rec.get("center")
        au = P.AU_KM
        a, q, Q = rec.get("a_km"), rec.get("q_km"), rec.get("Q_km")
        return {
            "fit_epoch_tt": rec["epoch_tt"].strftime("%Y-%m-%dT%H:%M:%S TT")
            if rec.get("epoch_tt") else "",
            "fit_epoch_utc": P.tt_to_utc_iso(rec["epoch_tt"]) if rec.get(
                "epoch_tt") else "",
            "frame": rec.get("frame", ""), "center": c or "",
            "a_au": fmt(a / au, 8) if a else "", "a_km": fmt(a, 1),
            "e": fmt(rec.get("e"), 7), "incl_deg": fmt(rec.get("incl"), 5),
            "node_deg": fmt(rec.get("node"), 5),
            "argp_deg": fmt(rec.get("argp"), 5), "M_deg": fmt(rec.get("M"), 5),
            "q_au": fmt(q / au, 8) if q else "", "q_km": fmt(q, 1),
            "Q_au": fmt(Q / au, 8) if Q else "", "Q_km": fmt(Q, 1),
            "P_d": fmt(rec.get("P_d") or rec.get("P_d_from_a"), 4),
            "H": fmt(rec.get("H")), "U": fmt(rec.get("U")),
            "amr_m2kg": fmt(rec.get("amr")) or ("not fitted" if rec.get(
                "amr_not_fitted") or rec.get("has_elements") else ""),
            "amr_sigma": fmt(rec.get("amr_sigma")),
            "resid_arcsec": fmt(rec.get("resid")),
            "earth_moid_au": fmt(rec.get("earth_moid_au")),
            "geo_delta_km": fmt(rec.get("eph_delta_km"), 0),
            "geo_delta_at_utc": rec.get("eph_start", ""),
            "page_created_utc": rec.get("created", ""),
        }

    fits = sorted(g["fits"], key=lambda f: (not f["combined"],
                                            f.get("epoch_tt") or NOW,
                                            f.get("ast_first")))
    for f in fits:
        per = f["per"]
        for d, (u, t) in per.items():
            desig_batches.setdefault(d, []).append(
                f"{f['page']} {u}/{t}") if f["combined"] else None
        r = {"row_type": "combined_helio_batch" if f["combined"] else
             ("apparition_geo_fit" if f.get("center") == "earth" else
              "apparition_fit"),
             "page_or_file": f"{REL}/mpecs/{f['page']}",
             "combine_as": f.get("combine_as", ""),
             "desigs_used_total": ";".join(f"{d}:{u}/{t}" for d, (u, t) in
                                           sorted(per.items(), key=lambda kv:
                                                  D[kv[0]]["first"] if kv[0] in D
                                                  else NOW)),
             "obs_first_utc": f["ast_first"].strftime("%Y-%m-%dT%H:%M:%SZ"),
             "obs_last_utc": f["ast_last"].strftime("%Y-%m-%dT%H:%M:%SZ"),
             "n_obs_used": f.get("n_used", ""), "n_obs_total": f.get("n_total",
                                                                     ""),
             **elem_cols(f)}
        notes = SRG_BATCH_NOTES.get(f["page"], "")
        if not f["combined"]:
            d = next(iter(per), "")
            cr = catrow.get(d)
            r["catalog_line"] = cr["_line"] if cr else ""
            r["pluto_mpec"] = cr["link to projectpluto site"] if cr else ""
            notes = ("single-apparition geocentric fit; geocentric osculating "
                     "elements of a heliocentric L2 object (P/e not physical)"
                     if f.get("center") == "earth" else "")
            if f.get("e") and f["e"] >= 1:
                notes = ("single-apparition geocentric fit, formally "
                         "hyperbolic (short arc)")
        r["notes"] = notes
        rows.append(r)
    # every apparition (catalog row / data file / Bill-only), even w/o page
    seen_pages = {next(iter(f["per"]), "") for f in g["fits"]
                  if not f["combined"]}
    bill = {}
    for b in g["bill"]:
        bill.setdefault(b["desig"], []).append(b)
    for d in sorted(g["gset"], key=lambda d: (D[d]["first"] if d in D else
                                              (bill[d][0]["date"] if d in bill
                                               and bill[d][0]["date"] else NOW))):
        if d in ("SRG",):
            continue
        if d in seen_pages:
            # annotate the existing apparition row with batch membership
            for r in rows:
                if r["row_type"] != "combined_helio_batch" and \
                        r["desigs_used_total"].startswith(d + ":"):
                    r["bill_cache"] = "; ".join(
                        f"L{b['line']} {b['text']}" for b in bill.get(d, []))
                    r["notes"] = (r["notes"] + "; batches: " + ", ".join(
                        desig_batches.get(d, []) or ["none"])).strip("; ")
            continue
        v = D.get(d)
        cr = catrow.get(d)
        if not v and not cr and d not in bill:
            continue
        r = {c: "" for c in SRG_COLS}
        r.update({
            "row_type": "apparition_obs_only" if v else
            ("apparition_catalog_only" if cr else "apparition_bill_only"),
            "page_or_file": f"{REL}/data/{d}.txt" if v else "",
            "combine_as": "", "desigs_used_total": d,
            "obs_first_utc": v["first"].strftime("%Y-%m-%dT%H:%M:%SZ") if v and
            v["first"] else "",
            "obs_last_utc": v["last"].strftime("%Y-%m-%dT%H:%M:%SZ") if v and
            v["last"] else "",
            "n_obs_total": v["n"] if v else "",
            "catalog_line": cr["_line"] if cr else "",
            "pluto_mpec": cr["link to projectpluto site"] if cr else "",
            "bill_cache": "; ".join(f"L{b['line']} {b['text']}"
                                    for b in bill.get(d, [])),
        })
        n = []
        if d in desig_batches:
            n.append("batches: " + ", ".join(desig_batches[d]))
        elif v and set(v["trksubs"]) - {d}:
            n.append("trksubs " + ",".join(sorted(v["trksubs"])))
        if cr:
            n.append(f"catalog orbit cell '{cr['Orbit']}', H {cr['H mag']}")
        if d == "A11Cswo":
            n.append("not in catalog.csv; Bill: 'not confirmed (2026 May "
                     "20.84 UT) = 2019-040A = Spektr-RG'; in no batch fit "
                     "(the only obs in the Mar 9-Jun 5 gap)")
            rf = refit("A11Cswo")
            if rf:
                r.update({k: v for k, v in elem_cols(rf).items() if v})
                r["n_obs_used"] = rf.get("n_used", "")
                r["row_type"] = "apparition_refit"
                n.append("elements: fo -h 3 refit, this extract (work/cache/"
                         "neocpsat_fo/A11Cswo/elements.txt), formally "
                         "hyperbolic geocentric 22-h arc")
        if d in ("P12r91e", "X91047"):
            n.append("trksub inside data/C46MLP1.txt")
        r["notes"] = "; ".join(n)
        rows.append(r)
    with open(OUT_SRG, "w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(SRG_COLS) + "\n")
        for r in rows:
            fh.write("\t".join(clean(r.get(c, "")) for c in SRG_COLS) + "\n")
    return len(rows)


if __name__ == "__main__":
    main()
