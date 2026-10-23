#!/usr/bin/env python3
"""build_news_tsv.py -- write the `news` source extract of cislunar-eye.

Current-events research (web searches, Oct 2026) on artificial objects beyond
GEO, distilled into one row per physical object following
catalog/sources/SCHEMA.md.  The curated facts live in ROWS below (each with
its citation URL as `src_ref`); orbital elements and ephemeris coverage are
filled from caches written earlier by the same task, never from the network:

  work/cache/news/horizons_summary*.tsv   (scripts/news_horizons_check.py,
                                           elements at 2026-10-21 00:00 TDB)
  work/cache/news/horizons/<key>_elm.json (raw Horizons element tables)
  work/cache/news/horizons_objdata/obj_<id>.txt (Horizons headers -> spans)
  work/cache/news/spacetrack/gp_news_objects.json, satcat_proba.json
  work/cache/news/research_log.tsv         (claim -> URL log, all searches)
  (OSIRIS-APEX flyby scan: news_horizons_check.py horizons_targets4.tsv --scan
   2027-03-14 2027-03-20 10m, printed only; result copied into the row notes)

Outputs (overwritten, idempotent):
  catalog/sources/news.tsv
  work/notes/news_sources.md   (bullet list claim -> URL, from research_log)

Usage:
  ~/.venvs/astro313/bin/python -I scripts/build_news_tsv.py
  ~/.venvs/astro313/bin/python -I scripts/validate_source_tsv.py catalog/sources/news.tsv

Conventions (also written to the notes file):
  * Horizons elements: osculating about Earth (500@399, Earth body equator of
    date) or Moon (500@301, selenocentric period) at 2026-10-21 00:00 TDB
    = 2026-10-20T23:58:51Z.  Not given for heliocentric objects.
  * Space-Track GP elements: a from SEMIMAJOR_AXIS (or mean motion),
    perigee/apogee = a(1-+e) measured from Earth centre.
  * covers_now: Y if a Horizons/bgtle span contains 2026-10-21, or a
    Space-Track GP epoch is within 10 d, or a find_orb (pluto/neocpsat) fit
    arc ends on/after 2026-10-11; '?' if a GP epoch is within 30 d or a fit
    arc ends on/after 2026-09-11; otherwise N.
"""

import csv
import datetime as dt
import glob
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "work" / "cache" / "news"
OUT_TSV = ROOT / "catalog" / "sources" / "news.tsv"
OUT_MD = ROOT / "work" / "notes" / "news_sources.md"
NOW = dt.date(2026, 10, 21)
HZ_EPOCH = "2026-10-20T23:58:51Z"      # 2026-10-21 00:00 TDB
MU = 398600.4418
RE = 6378.137
AU = 149597870.7

COLUMNS = ["src", "src_ref", "name", "aliases", "norad", "cospar", "horizons_id",
           "obj_type", "regime", "status", "status_date", "period_d", "a_km", "e",
           "incl_deg", "perigee_km", "apogee_km", "elem_epoch", "frame_center",
           "ephem_sources", "covers_now", "notes"]

HZAPI = "https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND='{}'&OBJ_DATA=YES&MAKE_EPHEM=NO"
PLUTO = "https://www.projectpluto.com/neocp2/mpecs/{}.htm"
GCAT_DEEP = "https://planet4589.org/space/gcat/data/cat/deepcat.html"
GCAT_HCO = "https://planet4589.org/space/gcat/data/cat/hcocat.html"

# Horizons spans where the header table cannot be parsed or says "current"
HZ_SPAN_OVERRIDE = {
    "-557": ("2011-06-18", "2026-10-21"),      # TLE-based, 'Current'
    "-198": ("2002-10-17", "2026-10-21"),      # TLE-based, 'PRESENT'
    "-140482": ("2015-03-13", "2026-10-21"),   # TLE-based (MMS)
    "-140483": ("2015-03-13", "2026-10-21"),
    "-140484": ("2015-03-13", "2026-10-21"),
    "-140485": ("2015-03-13", "2026-10-21"),
    "-162719": ("2026-02-01", "2026-08-05"),   # per lunarmission.tsv / Horizons
}

# --------------------------------------------------------------------------
# Curated rows.  elems: ("hz", key) | ("hzelm", key) | ("gp", norad) |
# ("satcat", norad) | ("fixed", {...}) | None.
# eph: list of (kind, ref, start, end); kind 'horizons' with ref=id and empty
# span is filled from the Horizons header.
# --------------------------------------------------------------------------
ROWS = [
    # ===== (a) launched Jan-Oct 2026 beyond GEO =====
    dict(ref="https://www.britannica.com/topic/Artemis-II",
         name="Artemis II Orion (Integrity)",
         aliases="Orion EM-2;Integrity;ARTEMIS 2 (INTEGRITY);Artemis II (spacecraft)",
         norad="68538", cospar="2026-069A", hz="-1024", type="payload",
         regime="TRANSLUNAR", status="decayed", sdate="2026-04-11", elems=None,
         eph=[("horizons", "-1024", "", ""), ("bgtle", "26069a.tle", "2026-04-03", "2026-04-10"),
              ("spacetrack_gp", "68538", "2026-04-10", "2026-04-10")],
         notes="Crewed lunar free-return flyby. Launch SLS 2026-04-01 22:35:12 UTC LC-39B; TLI "
               "2026-04-02 23:49 UTC; far-side closest approach 6545 km altitude 2026-04-06 (NASA SVS "
               "5633); CM/ESM separation 2026-04-10 23:33 UTC (ESA); splashdown off San Diego "
               "2026-04-10 8:07 pm EDT = 2026-04-11 00:07 UTC, crew recovered (status decayed = "
               "re-entered and recovered). Horizons -1024 header (rev 2026-04-20) lists the event "
               "timeline."),
    dict(ref="https://www.esa.int/Science_Exploration/Human_and_Robotic_Exploration/Artemis_II_splashdown",
         name="Artemis II European Service Module (ESM-2)",
         aliases="ESM-2 Bremen;Orion ESM-2;Crew Module Adapter (attached)",
         norad="", cospar="", hz="", type="payload", regime="TRANSLUNAR", status="decayed",
         sdate="2026-04-10", elems=None, eph=[],
         notes="Separated from the crew module 11 Apr 00:33 BST (2026-04-10 23:33 UTC) and burned "
               "up in the atmosphere (ESA). Component of launch 2026-069 without its own NORAD "
               "number (GCAT deepcat D01206 'ESM-2 Bremen', D01205 Crew Module Adapter)."),
    dict(ref="https://www.nasa.gov/blogs/missions/2026/04/01/artemis-ii-flight-update-proximity-operations-complete-perigee-raise-burn-up-next",
         name="Artemis II ICPS-2 (SLS upper stage)",
         aliases="ICPS 02;Interim Cryogenic Propulsion Stage 2",
         norad="68655", cospar="2026-069E", hz="", type="rocket_body", regime="OTHER",
         status="decayed", sdate="2026-04-02", elems=None, eph=[],
         notes="Raised Orion to a ~185x70377 km, 23.5-h HEO (Horizons -1024 header); target of the "
               "crew proximity-operations demo; disposal burn ~2026-04-02 03:35 UTC for re-entry "
               "over the remote Pacific (NASA blog); GCAT satcat: deorbited 2026-04-02 ~23:44 UTC "
               "near 132W 24N. Regime OTHER: HEO with P<2.4 d, never translunar. No Horizons ID found."),
    dict(ref="https://www.nasa.gov/humans-in-space/artemis-ii-science",
         name="TACHELES (DLR cubesat, Artemis II)", aliases="TACHELES",
         norad="68539", cospar="2026-069B", hz="", type="payload", regime="OTHER",
         status="decayed", sdate="2026-04-02", elems=None,
         eph=[("spacetrack_gp", "68539", "2026-04-02", "2026-04-02")],
         notes="Artemis II secondary payload deployed into the ~70,000 km HEO ~2026-04-02 03:39 UTC "
               "(NASA). Failed to raise perigee and re-entered at first perigee (McDowell, cited in "
               "Wikipedia Artemis II); Space-Track DECAY_DATE 2026-04-02 (GCAT 2026-04-03 00:01? "
               "UTC near 116W 23N). OTHER: HEO P~0.98 d."),
    dict(ref="https://en.wikipedia.org/wiki/Artemis_II",
         name="K-RadCube (KASA cubesat, Artemis II)", aliases="K-RadCube;K-RADCUBE",
         norad="68541", cospar="2026-069D", hz="", type="payload", regime="OTHER",
         status="decayed", sdate="2026-04-02", elems=None,
         eph=[("spacetrack_gp", "68541", "2026-04-02", "2026-04-02")],
         notes="Korea AeroSpace Administration radiation cubesat; deployed in HEO with the other "
               "Artemis II cubesats; did not achieve perigee raise and re-entered at first perigee "
               "(McDowell 2026-04-10 via Wikipedia); Space-Track decay 2026-04-02, GCAT 2026-04-02 "
               "~23:52 UTC near 120W 28N."),
    dict(ref=HZAPI.format("-168540"),
         name="SWC-1 (SHAMS, Saudi Space Weather CubeSat-1)",
         aliases="SHAMS;SHMS;Space Weather CubeSat-1;SWC-1 (spacecraft)",
         norad="68540", cospar="2026-069C", hz="-168540", type="payload", regime="OTHER",
         status="unknown", sdate="2026-04-17", elems=("gp", "68540"),
         eph=[("horizons", "-168540", "", ""), ("spacetrack_gp", "68540", "2026-04-17", "2026-04-17")],
         notes="Only Artemis II cubesat that raised its perigee and stayed in orbit (McDowell via "
               "Wikipedia Artemis II); GCAT status O (in orbit) 492x70228 km; Space-Track last GP "
               "2026-04-17, no decay. Horizons ID -168540 verified by one request: ephemeris ends "
               "2026-04-07 14:30 UT. Operational status after April not found. OTHER: P=0.98 d."),
    dict(ref="https://www.lavoz.com.ar/ciudadanos/ciencia/mision-atenea-satelite-argentino-batio-record-distancia-supero-potencias-mundiales_0_eWzSHMSHnr.html",
         name="ATENEA (CONAE cubesat, Artemis II)", aliases="Atenea",
         norad="", cospar="", hz="", type="payload", regime="OTHER", status="decayed",
         sdate="2026-04-02", elems=None, eph=[],
         notes="Argentine microsatellite deployed from the Orion stage adapter on Artemis II; "
               "communicated from ~70,000 km (national record) and completed its planned 20-h "
               "mission on 2026-04-02 (CONAE). Carried no propulsion (McDowell: only the other three "
               "cubesats were to raise perigee) so re-entry at first perigee ~1 d after deployment is "
               "INFERRED; not catalogued in GCAT (2026-10-17) or the cached Space-Track data."),
    dict(ref="https://phys.org/news/2026-06-spacecraft-science-orbit.html",
         name="SMILE", aliases="Solar wind Magnetosphere Ionosphere Link Explorer;SMILE (spacecraft)",
         norad="69123", cospar="2026-109A", hz="-463", type="payload", regime="CISLUNAR_HEO",
         status="active", sdate="2026-10-18", elems=("gp", "69123"),
         eph=[("horizons", "-463", "", ""), ("bgtle", "26109a.tle", "2026-06-22", "2027-01-01"),
              ("spacetrack_gp", "69123", "2026-10-18", "2026-10-18")],
         notes="ESA-CAS; Vega C VV29 2026-05-19 03:52 UTC into ~700 km LEO (Avio); reached science "
               "orbit 2026-06-20 after 12 main-engine burns, commissioning to end Aug, science from "
               "Sep 2026 (ESA/phys.org). ~5000x121000 km i~70 (Horizons header; ESA trajectory fit "
               "through 2026-08-05). Apogee >100,000 km from centre -> CISLUNAR_HEO though P=2.10 d. "
               "status_date = latest GP epoch."),
    dict(ref="https://www.stsci.edu/contents/news/roman/2026/roman-science-operations-center-newsletter-september",
         name="Nancy Grace Roman Space Telescope",
         aliases="Roman;NGRST;RST;WFIRST;Roman Space Telescope (spacecraft)",
         norad="100532", cospar="2026-199A", hz="-211", type="payload", regime="SE_L2",
         status="active", sdate="2026-10-18", elems=("hz", "roman"),
         eph=[("horizons", "-211", "", ""), ("bgtle", "26199a.tle", "2026-08-31", "2026-09-27"),
              ("spacetrack_gp", "100532", "2026-08-30", "2026-08-30")],
         notes="Launched Falcon Heavy LC-39A 2026-08-30 11:26:04 UTC (Horizons header); ~90-day "
               "transfer, commissioning in cruise; arrays/HGA/aperture cover deployed, SE-L2 "
               "quasi-halo arrival early Dec 2026 (orbitalradar.com/roman-tracker). Horizons "
               "tracking through 2026-10-18. Geocentric osculating elements are not meaningful on "
               "the L2 transfer."),
    dict(ref=PLUTO.format("A11Gf3x"),
         name="Falcon Heavy-013 stage 2 (Roman launch)",
         aliases="FALCON HEAVY R/B;FalconH;A11Gf3x;A11GdpM;A11GkXR;C46K3U1;CEWWX82;A11Ix7l",
         norad="100533", cospar="2026-199B", hz="", type="rocket_body",
         regime="EARTH_NEAR_HELIO", status="escaped", sdate="2026-10-17",
         elems=("fixed", dict(a_km="152660670.2", e="0.0136669", incl_deg="0.10948",
                              perigee_km="150574270.6", apogee_km="154747068.3",
                              elem_epoch="2026-10-16T23:58:50Z", frame_center="sun")),
         eph=[("neocpsat_fit", "../neocpsat/mpecs/Falcon Heavy S2_combined.html", "2026-08-30", "2026-10-17"),
              ("pluto_mpec", PLUTO.format("A11Gf3x"), "2026-09-02", "2026-09-03"),
              ("pluto_mpec", PLUTO.format("A11Ix7l"), "2026-10-16", "2026-10-17"),
              ("spacetrack_gp", "100533", "2026-08-30", "2026-08-30")],
         notes="Upper stage of the Roman launch, escaped to heliocentric orbit a=1.0205 AU e=0.0137 "
               "i=0.11 deg (ecliptic), q=1.0065 Q=1.0344 AU, Earth MOID 0.0101 AU, 313 obs "
               "2026-08-30..10-17 (Bill Gray find_orb, elements as in neocpsat combined fit); "
               "geocentric distance 0.0166 AU on 2026-10-17; slow drift (synodic ~33 yr) -> escaped. "
               "GCAT deepcat D01207: heliocentric from ~2026-09-25. Period column left empty "
               "(heliocentric P ~376 d)."),
    dict(ref="https://global.jaxa.jp/press/2026/10/20261020-1_e.html",
         name="MMX (Martian Moons eXploration)", aliases="MMX;MMX (spacecraft)",
         norad="101109", cospar="2026-236A", hz="-239", type="payload",
         regime="EARTH_NEAR_HELIO", status="escaped", sdate="2026-10-20", elems=("hzelm", "mmx"),
         eph=[("horizons", "-239", "", "")],
         notes="H3 F10 (H3-24) from Tanegashima 2026-10-20 04:41:03 JST = 2026-10-19 19:41:03 UTC; "
               "separated ~71 min after liftoff onto its Earth-escape trajectory (JAXA). Mars "
               "arrival Aug 2027, Earth return 2031. Horizons -239 is JAXA's pre-launch planning "
               "trajectory V0.2 (rev 2026-10-07) - expect offsets; it gives r=8704 km at 2026-10-19 "
               "21:00 UTC and 388,136 km at 2026-10-21 00:00. Hyperbolic geocentric elements "
               "(a<0). Space-Track SATCAT 101109, no GP yet."),
    dict(ref="https://www.space-track.org/basicspacedata/query/class/satcat/LAUNCH/2026-10-16--2026-10-21",
         name="H3 F10 second stage (MMX launch)", aliases="H3 R/B",
         norad="101110", cospar="2026-236B", hz="", type="rocket_body",
         regime="EARTH_NEAR_HELIO", status="escaped", sdate="2026-10-19", elems=None, eph=[],
         notes="Catalogued by Space-Track with MMX (launch 2026-10-19 TNSTA, no GP sets yet). "
               "Presumed on an Earth-escape trajectory like MMX (direct injection by H3) - not "
               "confirmed by any source; no ephemeris."),

    # ===== (b) status of listed missions =====
    dict(ref="https://science.nasa.gov/mission/soho", name="SOHO",
         aliases="Solar and Heliospheric Observatory;SOHO (spacecraft)",
         norad="23726", cospar="1995-065A", hz="-21", type="payload", regime="SE_L1",
         status="active", sdate="2026-09-01", elems=("hz", "soho"), eph=[("horizons", "-21", "", "")],
         notes="ESA/NASA, halo orbit about SE-L1; mission repeatedly extended and continuing (NASA "
               "SOHO page); Horizons -21 revised 2026-09-01 with GSFC FDF trajectory to 2026-11-02."),
    dict(ref="https://www.cmetracker.ai/observatories", name="ACE",
         aliases="Advanced Composition Explorer;ACE (spacecraft)",
         norad="24912", cospar="1997-045A", hz="-92", type="payload", regime="SE_L1",
         status="active", sdate="2026-09-30", elems=("hz", "ace"), eph=[("horizons", "-92", "", "")],
         notes="Still flying ~29 yr after launch and streaming real-time solar wind as backup to "
               "SOLAR-1 (CME Tracker observatories page, 2026); Horizons -92 revised 2026-09-30."),
    dict(ref="https://link.springer.com/article/10.1007/s11214-025-01257-4", name="Wind",
         aliases="WIND;GGS Wind;Wind (spacecraft)",
         norad="23333", cospar="1994-071A", hz="-8", type="payload", regime="SE_L1",
         status="active", sdate="2026-09-21", elems=("hz", "wind"), eph=[("horizons", "-8", "", "")],
         notes="Part of the existing L1 fleet with ACE (Space Sci Rev IMAP paper, 2026); Horizons -8 "
               "based on tracking through 2026-09-21 (rev 2026-09-30)."),
    dict(ref="https://www.octodaydispatch.com/news/national/rare-moment-3-tropical-cyclones-in-pacific-captured-from-space/article_7747a915-bf64-59df-a00e-ce7ee4928610.html",
         name="DSCOVR", aliases="Deep Space Climate Observatory;Triana;DSCOVR (spacecraft)",
         norad="40390", cospar="2015-007A", hz="-78", type="payload", regime="SE_L1",
         status="active", sdate="2026-09-02", elems=("hz", "dscovr"), eph=[("horizons", "-78", "", "")],
         notes="EPIC imaged three Pacific tropical cyclones 2026-09-02; operational real-time "
               "solar-wind role passed to SOLAR-1 in spring 2026 (cmetracker.ai). Horizons -78 rev "
               "2026-10-19."),
    dict(ref="https://adityayaan.com/overview.html", name="Aditya-L1",
         aliases="Aditya L1;Aditya-L1 (spacecraft)",
         norad="57754", cospar="2023-132A", hz="-156", type="payload", regime="SE_L1",
         status="active", sdate="2026-10-15", elems=("hz", "aditya"), eph=[("horizons", "-156", "", "")],
         notes="Healthy, routine science in third year at SE-L1; station-keeping burns 2026-02-22 and "
               "2026-06-07 (adityayaan.com); ISRO 2026-10-15 joint Aditya-L1/Proba-3 coronagraph "
               "results. Horizons -156 rev 2026-09-30."),
    dict(ref="https://svs.gsfc.nasa.gov/14957", name="IMAP",
         aliases="Interstellar Mapping and Acceleration Probe;IMAP (spacecraft)",
         norad="65725", cospar="2025-215A", hz="-43", type="payload", regime="SE_L1",
         status="active", sdate="2026-10-20", elems=("hz", "imap"), eph=[("horizons", "-43", "", "")],
         notes="Entered final SE-L1 Lissajous orbit 2026-01-10, science from 2026-02-01 (NASA SVS, "
               "eoPortal). status_date = Horizons -43 revision 2026-10-20."),
    dict(ref="https://www.instagram.com/p/Ddrb-n5lrG5", name="SOLAR-1 (SWFO-L1)",
         aliases="SWFO-L1;Space Weather Follow On-Lagrange 1;SOLAR-1",
         norad="65726", cospar="2025-215B", hz="-231", type="payload", regime="SE_L1",
         status="active", sdate="2026-10-13", elems=("hz", "swfo"), eph=[("horizons", "-231", "", "")],
         notes="NOAA: renamed SOLAR-1, reached L1 (GCAT 2026-01-23) and became the operational "
               "real-time solar-wind monitor in spring 2026 (NOAA Satellites, cmetracker.ai). "
               "Horizons tracking through 2026-10-13."),
    dict(ref="https://science.nasa.gov/blogs/carruthers-geocorona-observatory/2026/03/02/nasas-carruthers-geocorona-observatory-begins-primary-science-mission",
         name="Carruthers Geocorona Observatory",
         aliases="Carruthers;CGO;GLIDE;Carruthers Geocorona Observatory (spacecraft)",
         norad="65727", cospar="2025-215C", hz="-171", type="payload", regime="SE_L1",
         status="active", sdate="2026-10-19", elems=("hz", "carruthers"), eph=[("horizons", "-171", "", "")],
         notes="24-month primary science mission began 2026-03-01 at SE-L1 (NASA blog 2026-03-02); "
               "Horizons -171 last tracking 2026-10-19."),
    dict(ref=GCAT_DEEP + "#D01196", name="Falcon 9-518 stage 2 (IMAP launch)",
         aliases="FALCON 9 R/B;F9-518 St2",
         norad="65728", cospar="2025-215D", hz="", type="rocket_body",
         regime="EARTH_NEAR_HELIO", status="escaped", sdate="", elems=None,
         eph=[("spacetrack_gp", "65728", "2025-09-24", "2025-09-24")],
         notes="Launched with IMAP/SWFO-L1/Carruthers to a 217x2.27e6 km trajectory; GCAT deepcat "
               "D01196 moves it to heliocentric orbit ~Jan 2026 (date uncertain); Space-Track marks "
               "DECAY_DATE 2025-09-26 (escape bookkeeping). No ephemeris found."),
    dict(ref="https://orbitalradar.com/jwst-tracker", name="JWST",
         aliases="James Webb Space Telescope;Webb;JWST (spacecraft)",
         norad="50463", cospar="2021-130A", hz="-170", type="payload", regime="SE_L2",
         status="active", sdate="2026-10-19", elems=("hz", "jwst"), eph=[("horizons", "-170", "", "")],
         notes="Fully operational in fifth science year, propellant for 20+ yr (orbitalradar, "
               "keeptrack 2026); Horizons -170 rev 2026-10-19."),
    dict(ref="https://www.cosmos.esa.int/web/gaia/spacecraft-passivation", name="Gaia",
         aliases="Gaia (spacecraft)", norad="39479", cospar="2013-074A", hz="-139479",
         type="payload", regime="EARTH_NEAR_HELIO", status="escaped", sdate="2025-03-27",
         elems=None, eph=[("horizons", "-139479", "", "")],
         notes="Science ended 2025-01-15; left SE-L2 via burns 2025-03-07 and 2025-03-27 into a "
               "heliocentric retirement orbit avoiding Earth, passivated 2025-03-27 (ESA). Horizons "
               "scan: 7.89e7 km from Earth on 2026-09-01 (far now); EARTH_NEAR_HELIO only because "
               "it was <0.05 AU in 2025."),
    dict(ref="https://euclid.caltech.edu/news/esa-s-euclid-captures-the-milky-way-s-crowded-heart",
         name="Euclid", aliases="Euclid (spacecraft)", norad="57209", cospar="2023-092A",
         hz="-680", type="payload", regime="SE_L2", status="active", sdate="2026-08-31",
         elems=("hz", "euclid"), eph=[("horizons", "-680", "", "")],
         notes="Galactic-bulge survey data release 2026-06-23, quasar discoveries 2026-07 (Euclid "
               "Consortium); Horizons -680 tracking-data based through 2026-08-31."),
    dict(ref=PLUTO.format("C46MLP1"), name="Spektr-RG",
         aliases="SRG;Spectrum-Roentgen-Gamma;Spektr-RG (eROSITA/ART-XC)",
         norad="44432", cospar="2019-040A", hz="", type="payload", regime="SE_L2",
         status="active", sdate="2026-10-13",
         elems=("fixed", dict(a_km="157131544.7", e="0.0447928", incl_deg="0.0867",
                              perigee_km="150093172.8", apogee_km="164169916.5",
                              elem_epoch="2026-10-12T23:58:50Z", frame_center="sun")),
         eph=[("pluto_mpec", PLUTO.format("C46MLP1"), "2026-10-04", "2026-10-06"),
              ("pluto_mpec", PLUTO.format("A11HUyG"), "2026-10-11", "2026-10-13")],
         notes="Tracked at SE-L2 Jun-Oct 2026; station-keeping dV 2026-09-07 and between 2026-09-21 "
               "and 10-04 seen in find_orb fits (pluto A11GdpR/C46MLP1) -> actively controlled; "
               "primary 6.5-yr mission complete, RAS Space Council (June 2026) extended operations "
               "to 2031 (de.wikipedia citing IKI/RAS). Heliocentric ecliptic elements from the "
               "find_orb fit (as in neocpsat.tsv). Horizons has no 'Spektr-RG'/'SRG' target."),
    dict(ref=PLUTO.format("A11FfIf"), name="Chang'e-6 orbiter",
         aliases="Chang'e 6 orbiter;Chang'e-6 Guidao Qi;CE-6 orbiter",
         norad="59627", cospar="2024-083A", hz="", type="payload", regime="CISLUNAR_HEO",
         status="unknown", sdate="2026-10-01",
         elems=("fixed", dict(period_d="33.6979", a_km="440699.5", e="0.1646873",
                              incl_deg="74.52206", perigee_km="368121.9", apogee_km="513277.1",
                              elem_epoch="2026-08-01T23:58:50Z", frame_center="earth")),
         eph=[("pluto_mpec", PLUTO.format("A11FfIf"), "2026-08-01", "2026-08-01"),
              ("pluto_mpec", PLUTO.format("A11Htul"), "2026-10-01", "2026-10-01")],
         notes="Extended mission after the 2024 sample return (SE-L2 in 2024, then back to "
               "cislunar space); Aug 2026 geocentric orbit q=368k Q=513k km i=74.5 P=33.7 d "
               "(pluto A11FfIf); still observed 2026-10-01 (A11Htul). No 2026 CNSA operational "
               "statement found -> unknown. Horizons: no Chang'e 6 orbiter target."),
    dict(ref="https://www.facebook.com/advancedspace/posts/at-smallsat-2026-dr-jeffrey-parker-co-founder-and-chief-technology-officer-gave-/1672538154391207",
         name="ESCAPADE Blue", aliases="EscaPADE-Blue;ESCAPADE-Blue (spacecraft)",
         norad="66451", cospar="2025-260A", hz="-9", type="payload", regime="SE_L2",
         status="active", sdate="2026-10-12", elems=None, eph=[("horizons", "-9", "", "")],
         notes="New Glenn NG-2 2025-11-13 20:45 UTC to an SE-L2 loiter orbit (Horizons header, "
               "Wikipedia). Powered Earth flyby with ~9-min trans-Mars injection burn Nov 7 2026 "
               "(Advanced Space, SmallSat 2026); Horizons predicts perigee r=8036 km (alt ~1660 km) "
               "2026-11-07 ~10:00 UTC; ~11-month cruise to Mars. Horizons tracking through "
               "2026-10-12. Geocentric osculating elements omitted (e~0.999, meaningless at 1.4e6 km)."),
    dict(ref="https://en.wikipedia.org/wiki/ESCAPADE", name="ESCAPADE Gold",
         aliases="EscaPADE-Gold;ESCAPADE-Gold (spacecraft)",
         norad="66452", cospar="2025-260B", hz="-10", type="payload", regime="SE_L2",
         status="active", sdate="2026-10-12", elems=None, eph=[("horizons", "-10", "", "")],
         notes="Twin of ESCAPADE Blue; Earth flyby + TMI burn Nov 9 2026 (Advanced Space); Horizons "
               "perigee r=7388 km (alt ~1010 km) 2026-11-09 10:59 UTC; Wikipedia: late-2026 Earth "
               "gravity assist then Mars. Horizons tracking through 2026-10-12."),
    dict(ref="https://www.blueorigin.com/missions/ng-2", name="New Glenn NG-2 second stage (GS2)",
         aliases="EscaPADE STAGE;New Glenn GS2;Viasat CPS Demo (hosted)",
         norad="66453", cospar="2025-260C", hz="-9901495", type="rocket_body",
         regime="EARTH_NEAR_HELIO", status="escaped", sdate="2025-11-28", elems=None,
         eph=[("horizons", "-9901495", "", ""), ("bgtle", "25260c.tle", "2025-11-14", "2026-09-04")],
         notes="Safed after ESCAPADE deployment 2025-11-13 (Blue Origin) and nudged to slightly "
               "positive C3 (Horizons header: ballistic extrapolation of last telemetry, large "
               "errors expected); GCAT heliocentric from 2025-11-28. Horizons distance 3.0e7 km on "
               "2027-01-05 (no near-term return)."),
    dict(ref="https://science.nasa.gov/solar-system/moon/nasas-moon-orbiter-spots-new-once-in-century-moon-crater",
         name="LRO", aliases="Lunar Reconnaissance Orbiter;LRO (spacecraft)",
         norad="35315", cospar="2009-031A", hz="-85", type="payload", regime="LUNAR_ORBIT",
         status="active", sdate="2026-10-07", elems=("hz", "lro"), eph=[("horizons", "-85", "", "")],
         notes="Operating (new-crater discovery, NASA 2026-09-16; FY2027 request funds LRO "
               "2027-31). Horizons -85 rev 2026-10-07. Elements selenocentric (Moon body equator), "
               "period selenocentric."),
    dict(ref="https://x.com/SpacesFuture/status/2085623925820854478", name="Danuri (KPLO)",
         aliases="KPLO;Korea Pathfinder Lunar Orbiter;Danuri (spacecraft)",
         norad="53365", cospar="2022-094A", hz="-155", type="payload", regime="LUNAR_ORBIT",
         status="active", sdate="2026-10-18", elems=("hz", "kplo"), eph=[("horizons", "-155", "", "")],
         notes="Mission extended to Dec 2027 (KARI); imaged the Falcon 9 stage impact site before/after "
               "Aug 2026 (Chosun 2026-08-06). Horizons tag-up data through 2026-10-18. Elements "
               "selenocentric."),
    dict(ref="https://www.isro.gov.in/Chandrayaan2_Dual_Frequency_Synthetic_Aperture_Radar.html",
         name="Chandrayaan-2 orbiter", aliases="Chandrayaan-2 (ORBITER);CH-2 orbiter",
         norad="44441", cospar="2019-042A", hz="-152", type="payload", regime="LUNAR_ORBIT",
         status="active", sdate="2026-10-18", elems=("hz", "ch2"), eph=[("horizons", "-152", "", "")],
         notes="Orbiter healthy, all payloads operational (DFSAR results ISRO 2026-05-27, The Hindu "
               "2026-05-28); Horizons data through 2026-10-18. Elements selenocentric."),
    dict(ref=PLUTO.format("ZTF10FG"), name="Chandrayaan-3 Propulsion Module",
         aliases="CH-3 PM;Chandrayaan-3P (ORBITER);CH 3 PROPULSION MODULE",
         norad="57770", cospar="2023-098C", hz="-169", type="payload", regime="CISLUNAR_HEO",
         status="unknown", sdate="2026-08-11",
         elems=("fixed", dict(period_d="25.5863", a_km="366785.6", e="0.6268694",
                              incl_deg="28.61089", perigee_km="136858.9", apogee_km="596712.3",
                              elem_epoch="2026-06-24T23:58:50Z", frame_center="earth")),
         eph=[("horizons", "-169", "", ""),
              ("pluto_mpec", PLUTO.format("ZTF10FG"), "2026-08-11", "2026-08-11"),
              ("spacetrack_gp", "57770", "2025-06-27", "2025-06-27")],
         notes="June 2026 geocentric orbit a=366,786 km e=0.627 i=28.6 P=25.6 d; predicted lunar "
               "passage ~28,000 km on 2026-08-19 sending it to ~1e6 km by Aug 30, near escape - "
               "probably its last apparition (pluto ZTF10FG / neocpsat). No ISRO status since 2023; "
               "Horizons -169 ends 2023-11-10."),
    dict(ref=PLUTO.format("SK000bY"), name="CAPSTONE",
         aliases="Cislunar Autonomous Positioning System Technology Operations and Navigation Experiment;CAPSTONE (spacecraft)",
         norad="52914", cospar="2022-070A", hz="-1176", type="payload", regime="CISLUNAR_HEO",
         status="derelict", sdate="2026-09-12",
         elems=("fixed", dict(period_d="14.8696", a_km="255430.5", e="0.2851572",
                              incl_deg="36.6117", perigee_km="182592.6", apogee_km="328268.3",
                              elem_epoch="2026-09-11T23:58:50Z", frame_center="earth")),
         eph=[("horizons", "-1176", "", ""),
              ("pluto_mpec", PLUTO.format("SK000bY"), "2026-09-12", "2026-09-12")],
         notes="Completed NASA mission: Horizons -1176 note 2026-07-28 'no further updates', nav "
               "data through 2026-07-15, prediction to 2026-08-14. Left the NRHO/Moon end of Aug "
               "2026 into HEO a=255,431 km e=0.285 i=36.6 P=14.87 d (pluto SK000bY, 10 obs "
               "2026-09-12). derelict = no operations reported (actual spacecraft state unknown)."),
    dict(ref=HZAPI.format("-192"), name="ARTEMIS P1 (THEMIS-B)",
         aliases="THEMIS-B;THEMIS P1;ARTEMIS-P1", norad="30581", cospar="2007-004B", hz="-192",
         type="payload", regime="LUNAR_ORBIT", status="active", sdate="2026-10-20",
         elems=("hz", "artemis_p1"), eph=[("horizons", "-192", "", "")],
         notes="Horizons -192 revised 2026-10-20 with trajectory to 2026-11-15 (routine navigation "
               "updates imply continuing operations); no 2026 news item found. Elements "
               "selenocentric (retrograde, i~177 deg)."),
    dict(ref=HZAPI.format("-193"), name="ARTEMIS P2 (THEMIS-C)",
         aliases="THEMIS-C;THEMIS P2;ARTEMIS-P2", norad="30582", cospar="2007-004C", hz="-193",
         type="payload", regime="LUNAR_ORBIT", status="active", sdate="2026-10-20",
         elems=("hz", "artemis_p2"), eph=[("horizons", "-193", "", "")],
         notes="Horizons -193 revised 2026-10-20, trajectory to 2026-11-15; no 2026 news item "
               "found. Elements selenocentric."),
    dict(ref="https://selenianboondocks.com/2026/04/queqiao-eml2", name="Queqiao-1",
         aliases="Queqiao;Chang'e-4 relay;QUEQIAO", norad="43470", cospar="2018-045A", hz="",
         type="payload", regime="EM_L2", status="active", sdate="2026-04-19", elems=None,
         eph=[("spacetrack_gp", "43470", "2018-05-20", "2018-05-20")],
         notes="Still operational in its EM-L2 halo orbit relaying Chang'e-4/Yutu-2 (Selenian "
               "Boondocks 2026-04-19). No public ephemeris (Space-Track GP frozen at 2018-05-20; "
               "Horizons 'Queqiao' not found)."),
    dict(ref="https://beyondleo.org/mission/queqiao-2-chinas-bridge-to-the-far-side", name="Queqiao-2",
         aliases="Queqiao 2;QUEQIAO-2", norad="59276", cospar="2024-051A", hz="", type="payload",
         regime="LUNAR_ORBIT", status="active", sdate="2026-10-01", elems=None,
         eph=[("bgtle", "24051a.tle", "2024-03-20", "2024-03-24")],
         notes="Operating stably >2 yr in a 24-h elliptical frozen lunar orbit (~300x16,900 km alt, "
               "GCAT) to relay for Chang'e-7 (BeyondLEO, updated 2026-10-01); Chang'e-7 now 2027. "
               "Horizons 'Queqiao-2' not found."),
    dict(ref="https://kevinjamesng.com/2026/03/26/cnsa-chinanationalspaceadministration-%E5%9B%BD%E5%AE%B6%E8%88%AA%E5%A4%A9%E5%B1%80-march2026-chinamannedlunarmission-chinas-dro-distant-retrograde-orbit-experimental-satellites-achieve-bre",
         name="DRO-A", aliases="DRO A", norad="59228", cospar="2024-048A", hz="", type="payload",
         regime="EM_DRO", status="active", sdate="2026-03-25", elems=("gp", "59228"),
         eph=[("spacetrack_gp", "59228", "2026-10-12", "2026-10-12"),
              ("bgtle", "24048a26.tle", "2026-01-01", "2026-07-01")],
         notes="2026-03-25 Zhongguancun Forum: DRO-A/DRO-B/DRO-L still experimenting after 2 yr, "
               "K-band ISL to 1.17e6 km. DRO-A in the lunar DRO (~28-d period; damaged solar arrays "
               "after launch anomaly, Aerospace Sci. Tech. 2026). Elements = Space-Track GP "
               "2026-10-12 (SGP4 fit, geocentric, rough for a DRO)."),
    dict(ref="https://space.skyrocket.de/doc_sdat/dro-a.htm", name="DRO-B", aliases="DRO B",
         norad="60058", cospar="2024-048D", hz="", type="payload", regime="EM_OTHER",
         status="active", sdate="2026-03-25", elems=("gp", "60058"),
         eph=[("spacetrack_gp", "60058", "2026-09-21", "2026-09-21"),
              ("bgtle", "24048d26.tle", "2026-01-01", "2026-07-01")],
         notes="Moved from the DRO to a 3:2 Earth-Moon resonant orbit in March 2025 (Gunter's Space "
               "Page upd. 2026-09-03; Jatan Moon Monday #291: passes L3/L4/L5 regions) -> EM_OTHER; "
               "active per 2026-03-25 CAS statement. Elements = Space-Track GP 2026-09-21."),
    dict(ref=PLUTO.format("ZTF10GU"), name="Tiandu-1", aliases="Tiandu 1;TD-1",
         norad="59278", cospar="2024-051C", hz="", type="payload", regime="CISLUNAR_HEO",
         status="active", sdate="2026-10-15",
         elems=("fixed", dict(period_d="11.7571", a_km="218410.5", e="0.3152227",
                              incl_deg="34.19149", perigee_km="149562.5", apogee_km="287258.4",
                              elem_epoch="2026-10-14T23:58:50Z", frame_center="earth")),
         eph=[("pluto_mpec", PLUTO.format("ZTF10GU"), "2026-10-01", "2026-10-02"),
              ("pluto_mpec", PLUTO.format("C46PCR1"), "2026-10-15", "2026-10-15"),
              ("bgtle", "24051c26.tle", "2026-01-01", "2026-07-01")],
         notes="Left lunar orbit 2025 for a geocentric HEO (GCAT); P=11.76 d q=150k Q=287k km "
               "i=34.2; post-manoeuvre batch 2026-10-01..15 -> manoeuvring/active (pluto ZTF10GU, "
               "elements as in neocpsat combined fit). Daytime lunar-distance laser ranging "
               "2025-04-26/27 (DSEL)."),
    dict(ref="https://space.skyrocket.de/doc_sdat/tiandu-1.htm", name="Tiandu-2",
         aliases="Tiandu 2;TD-2", norad="59279", cospar="2024-051D", hz="", type="payload",
         regime="LUNAR_ORBIT", status="unknown", sdate="", elems=None, eph=[],
         notes="15-kg companion of Tiandu-1, separated in lunar orbit 2024-04-03; GCAT lprcat still "
               "lists it in a ~289x10,000 km i=105 lunar orbit (status O); no 2026 report found."),
    dict(ref="https://www.jpl.nasa.gov/missions/lunar-trailblazer", name="Lunar Trailblazer",
         aliases="LTB;Lunar Trailblazer (spacecraft)", norad="63101", cospar="2025-038C",
         hz="-242", type="payload", regime="EARTH_NEAR_HELIO", status="escaped",
         sdate="2025-07-31", elems=None, eph=[("horizons", "-242", "", "")],
         notes="Contact lost the day after the 2025-02-26 launch (solar arrays mis-pointed, batteries "
               "depleted); mission ended 2025-07-31 (NASA JPL, Horizons note). Heliocentric; "
               "Horizons distance 8.8e7 km on 2026-09-01."),
    dict(ref="https://orbitalradar.com/tess-tracker", name="TESS",
         aliases="Transiting Exoplanet Survey Satellite;TESS (spacecraft)",
         norad="43435", cospar="2018-038A", hz="-95", type="payload", regime="EM_OTHER",
         status="active", sdate="2026-10-18", elems=("hz", "tess"), eph=[("horizons", "-95", "", "")],
         notes="P/2 lunar-resonant orbit; operating in third extended mission (to 2028); safe mode "
               "2026-01-15..18 recovered (TASOC DRN sector 1751). Horizons tracking through "
               "2026-10-18."),
    dict(ref="https://svs.gsfc.nasa.gov/gallery/interstellar-boundary-explorer-ibex", name="IBEX",
         aliases="Interstellar Boundary Explorer;SMEX-10", norad="33401", cospar="2008-051A", hz="",
         type="payload", regime="CISLUNAR_HEO", status="active", sdate="2026-03-02",
         elems=("gp", "33401"),
         eph=[("spacetrack_gp", "33401", "2025-07-25", "2025-07-25"),
              ("bgtle", "08051a25.tle", "2025-01-01", "2026-01-01")],
         notes="NASA SVS gallery (2026-03-02) says IBEX continues to collect heliospheric-boundary "
               "ENA data; caution: Space-Track GP stale since 2025-07-25 and Bill Gray TLE ends "
               "2026-01-01, no 2026 orbit found. Horizons 'IBEX' not found."),
    dict(ref="https://science.nasa.gov/missions/chandra/nasas-chandra-breaks-down-treble-clefs-galaxy-cluster-collision",
         name="Chandra X-ray Observatory", aliases="Chandra;AXAF;Chandra Observatory (spacecraft)",
         norad="25867", cospar="1999-040B", hz="-151", type="payload", regime="CISLUNAR_HEO",
         status="active", sdate="2026-10-14", elems=("hz", "chandra"), eph=[("horizons", "-151", "", "")],
         notes="New science release 2026-10-14 (NASA); still operational (Ars Technica Oct 2026), "
               "FY2026 appropriation $63M. Apogee >100,000 km -> CISLUNAR_HEO by the numeric rule "
               "(SCHEMA lists Chandra as an HEO example; P=2.65 d)."),
    dict(ref="https://www.esa.int/Enabling_Support/Operations/Integral_operations", name="INTEGRAL",
         aliases="International Gamma-Ray Astrophysics Laboratory;INTEGRAL (spacecraft)",
         norad="27540", cospar="2002-048A", hz="-198", type="payload", regime="CISLUNAR_HEO",
         status="derelict", sdate="2025-02-28", elems=("hz", "integral"),
         eph=[("horizons", "-198", "", "")],
         notes="Science operations ended 2025-02-28; ESOC keeps monitoring it until atmospheric "
               "re-entry ~Feb 2029 (ESA, Wikipedia). Apogee ~152,000 km -> CISLUNAR_HEO by the "
               "numeric rule (SCHEMA example lists INTEGRAL under HEO)."),
] + [
    dict(ref="https://nasawatch.com/2026/01/16/aas-summary-of-nasa-fy-2026-budget" + ("" if i == 1 else f"#MMS-{i}"),
         name=f"MMS-{i}", aliases=f"MMS {i};Magnetospheric Multiscale {i};MMS-{i} (spacecraft)",
         norad=str(40481 + i), cospar=f"2015-011{'ABCD'[i - 1]}", hz=str(-140481 - i),
         type="payload", regime="CISLUNAR_HEO", status="active", sdate="2026-10-21",
         elems=("gp", str(40481 + i)),
         eph=[("horizons", str(-140481 - i), "", ""),
              ("spacetrack_gp", str(40481 + i), "2026-10-21", "2026-10-21")],
         notes="NASA FY2026 appropriation keeps >=$23M for MMS (AAS summary 2026-01-16); fresh "
               "Space-Track GP at 2026-10-21 (maneuvering formation). Apogee ~178,000 km from "
               "centre -> CISLUNAR_HEO (P=3.51 d).")
    for i in (1, 2, 3, 4)
] + [
    dict(ref="https://rdw.com/newsroom/redwire-supports-historic-recovery-of-proba-3-coronagraph-satellite",
         name="Proba-3 Coronagraph (CSC)", aliases="PROBA-3 CSC;Proba-3 Coronagraph Spacecraft",
         norad="62256", cospar="2024-233A", hz="", type="payload", regime="OTHER", status="active",
         sdate="2026-10-15", elems=("satcat", "62256"), eph=[],
         notes="CSC anomaly 2026-02-14/15 (attitude/contact loss), fully recovered; formation flying "
               "resumed early June 2026 (Redwire, Gunter); joint Aditya-L1/Proba-3 results "
               "(ISRO 2026-10-15). OTHER: HEO 1035x60170 km alt, P=0.82 d (below 2.4 d, apogee "
               "<100,000 km). Elements from Space-Track SATCAT (no epoch). Horizons 'Proba-3' not found."),
    dict(ref="https://space.skyrocket.de/doc_sdat/proba-3.htm", name="Proba-3 Occulter (OSC)",
         aliases="PROBA-3 OSC;Proba-3 Occulter Spacecraft", norad="62258", cospar="2024-233C",
         hz="", type="payload", regime="OTHER", status="active", sdate="2026-10-15",
         elems=("satcat", "62258"), eph=[],
         notes="Formation-flying partner of the CSC; formation flying resumed early June 2026 after "
               "the CSC recovery (Redwire). OTHER: P=0.82 d, apogee <100,000 km. Elements from "
               "Space-Track SATCAT (no epoch)."),
    dict(ref="https://en.wikipedia.org/wiki/Spektr-R", name="Spektr-R (RadioAstron)",
         aliases="RadioAstron;SPEKTR R;Spektr-R Observatory (spacecraft)",
         norad="37755", cospar="2011-037A", hz="-557", type="payload", regime="CISLUNAR_HEO",
         status="derelict", sdate="2019-05-30", elems=("hz", "spektrr"),
         eph=[("horizons", "-557", "", ""), ("spacetrack_gp", "37755", "2026-10-05", "2026-10-05")],
         notes="Communication problems from 2019-01-10, last contact 2019-01-11; recovery attempts "
               "until mid-May; mission declared finished 2019-05-30 (Wikipedia Spektr-R, Interfax "
               "Feb 2019). Still tracked: Space-Track GP 2026-10-05, Horizons -557 (TLE-based, rev "
               "2026-10-02)."),

    # ===== (c) Earth flybys / returns Sep-Dec 2026 and related =====
    dict(ref="https://www.esa.int/ESA_Multimedia/Videos/2026/09/Juice_flies_past_Earth_in_September_2026_2D_view",
         name="JUICE", aliases="JUpiter ICy moons Explorer;Juice;JUICE (spacecraft)",
         norad="56176", cospar="2023-053A", hz="-28", type="payload", regime="EARTH_NEAR_HELIO",
         status="active", sdate="2026-09-28", elems=None, eph=[("horizons", "-28", "", "")],
         notes="Second Earth gravity assist 2026-09-28 11:45:11.5 UTC at 15,018 km from Earth centre "
               "(alt ~8640 km, DLR) over the Indian Ocean, +3.5 km/s (ESA, Horizons header); next "
               "Earth flyby Jan 2029."),
    dict(ref="https://en.wikipedia.org/wiki/Europa_Clipper", name="Europa Clipper",
         aliases="Clipper;Europa Clipper (spacecraft)", norad="61507", cospar="2024-182A", hz="-159",
         type="payload", regime="EARTH_NEAR_HELIO", status="active", sdate="2026-09-30",
         elems=None, eph=[("horizons", "-159", "", "")],
         notes="Earth gravity assist 2026-12-03: Horizons -159 minimum geocentric distance 9607 km "
               "(alt ~3230 km) at 20:14 UTC (1-min scan); Wikipedia lists '4:15 PM EST' (21:15 UTC) "
               "- 1 h discrepancy, Horizons used. Jupiter arrival 2030-04-11. status_date = Horizons "
               "revision 2026-09-30."),
    dict(ref="https://www.projectpluto.com/pluto/mpecs/pseudo.htm", name="Cert-2 Centaur V upper stage",
         aliases="Centaur V-002;VULCAN CENTAUR R/B;Cert-2 Centaur RB Booster;C43KHJ1;A11cvui;A11cvun",
         norad="61448", cospar="2024-179A", hz="-161448", type="rocket_body",
         regime="EARTH_NEAR_HELIO", status="returning", sdate="2025-11-16", elems=None,
         eph=[("horizons", "-161448", "", ""), ("bgtle", "24179a.tle", "2024-10-04", "2025-01-01"),
              ("bgtle", "24179a25.tle", "2025-09-03", "2025-12-25")],
         notes="Heliocentric since 2024-10 with inert CERT-2 payload. Bill Gray fit to 21 obs "
               "2025-10-19..11-16 (24179a25.tle): Earth perigee 2029-02-27.42 TT q=8867+/-4574 km; "
               "'no updates until it returns in late 2026', tle_list Warn 2026-Nov-10 -> recovery "
               "window late 2026 (no 2026 recovery reported in a web search 2026-10-21). Horizons "
               "-161448 is the old 2024 solution: min distance 8.13e6 km on 2026-12-21; daily "
               "samples put a close approach (3.4e4-1.2e5 km) on 2029-02-27, BG: 'good chance of "
               "re-entering our atmosphere on 2029 Feb 27'. Not physically close in 2026."),
    dict(ref="https://iopscience.iop.org/article/10.3847/PSJ/acf75e", name="OSIRIS-APEX",
         aliases="OSIRIS-REx;OSIRIS-APX;ORX;APEX;OSIRIS-REx (spacecraft)", norad="41757",
         cospar="2016-055A", hz="-64", type="payload", regime="EARTH_NEAR_HELIO", status="active",
         sdate="2026-10-21", elems=None, eph=[("horizons", "-64", "", "")],
         notes="Extended mission to (99942) Apophis. Earth gravity assist EGA2 planned 2027-03-17 at "
               "4146 km altitude, EGA3 2029-04-13 (1153 km, ~1 h after Apophis), arrival 2029-06-05 "
               "(PSJ 2023 mission design); Univ. Arizona (2026): team writing the command sequence "
               "for the 'next March' (2027) flyby. Horizons -64 (rev 2025-09-18) 10-min scan: min "
               "r=10,561 km (alt ~4180 km) 2027-03-16 03:30 UTC. Bill Gray tle_list Warn 2027-Feb-01 "
               "'OSIRIS-APEX flyby in March'. status_date = date of this check (operating per UA "
               "news). No Earth flyby Sep-Dec 2026."),
    dict(ref=PLUTO.format("P22qGC1"), name="P22qGC1 (unidentified Earth-flyby object)",
         aliases="Sar2923", norad="", cospar="", hz="", type="unknown", regime="EARTH_NEAR_HELIO",
         status="unknown", sdate="2026-10-05",
         elems=("fixed", dict(period_d="68.4925", a_km="707135.8", e="0.9579183",
                              incl_deg="45.35168", perigee_km="29757.4", apogee_km="1384513.3",
                              elem_epoch="2026-10-04T23:58:50Z", frame_center="earth")),
         eph=[("pluto_mpec", PLUTO.format("P22qGC1"), "2026-09-21", "2026-10-05")],
         notes="NEOCP object with Earth flyby: perigee 2026-09-22 17:08 TT, q=29,757 km in the fit "
               "(~54,000 km per note); pre-flyby heliocentric a=1.0171 AU e=0.0088 i=0.33 deg - "
               "artificial-object candidate, not identified (pluto P22qGC1; geocentric elements of "
               "the fit as in neocpsat)."),
    dict(ref="https://skyandtelescope.org/astronomy-news/hayabusa-2-completes-flyby-past-asteroid-torifune",
         name="Hayabusa2", aliases="Hayabusa 2;Hayabusa2#;Hayabusa 2 (spacecraft)",
         norad="40319", cospar="2014-076A", hz="-37", type="payload", regime="EARTH_NEAR_HELIO",
         status="active", sdate="2026-09-29", elems=None, eph=[("horizons", "-37", "", "")],
         notes="Flew by (98943) Torifune 2026-07-05 (S&T; JST: ~400 m); next Earth flybys Dec 2027 "
               "and Jun 2028, 1998 KY26 arrival 2031 (S&T, Horizons -37 header rev 2026-09-30, "
               "tracking through 2026-09-29)."),
    dict(ref="https://www.scientificamerican.com/article/chinas-tianwen-2-spacecraft-arrives-at-one-of-earths-mysterious-quasi-moons",
         name="Tianwen-2", aliases="Tianwen 2;ZhengHe", norad="64197", cospar="2025-114A", hz="",
         type="payload", regime="EARTH_NEAR_HELIO", status="active", sdate="2026-06-11",
         elems=None, eph=[],
         notes="At Earth quasi-satellite 469219 Kamo'oalewa from ~July 2026, departs Apr 2027, Earth "
               "flyby with sample-capsule re-entry Nov 2027 (Scientific American, Planetary Society). "
               "Horizons 'Tianwen-2' not found."),
    dict(ref=GCAT_HCO + "#D01190", name="Tianwen-2 CZ-3B Y110 stage 3",
         aliases="CZ-3B R/B", norad="64198", cospar="2025-114B", hz="", type="rocket_body",
         regime="EARTH_NEAR_HELIO", status="escaped", sdate="", elems=None, eph=[],
         notes="Escaped with Tianwen-2 (launched 2025-05-28); GCAT hcocat D01190 heliocentric, no "
               "elements or ephemeris published. Included as a post-2024 Earth-departure object."),
    dict(ref="https://www.chosun.com/english/industry-en/2026/08/06/VGWNSU3ZWZDRJPVP3G27VWIMFI",
         name="Falcon 9-427 stage 2 (Blue Ghost M1 launch)",
         aliases="Falcon 9 RB Booster;F9-427 St2;FALCON 9 R/B", norad="62719", cospar="2025-010D",
         hz="-162719", type="rocket_body", regime="CISLUNAR_HEO", status="lunar_impact",
         sdate="2026-08-05", elems=None, eph=[("horizons", "-162719", "", "")],
         notes="Long-lived lunar-perturbed cislunar orbit after the 2025-01-15 Blue Ghost M1/HAKUTO-R "
               "M2 launch; struck the Moon 2026-08-05 (~06:35 UTC, 19.6N 93.4W per GCAT/lunarmission; "
               "Horizons ephemeris ends at the surface); Danuri LUTI imaged the site before and "
               "after (Chosun 2026-08-06)."),
]

# Things researched that are NOT physical objects in space as of 2026-10-21
NOT_LAUNCHED = [
    ("Chang'e-7 (lander/orbiter/rover/hopper) postponed from Aug 2026 to early 2027",
     "https://arstechnica.com/space/2026/09/nasa-chief-concerned-china-may-deny-access-to-parts-of-south-pole"),
    ("Astrobotic/Voyager Griffin-1 (Falcon Heavy) NET December 2026",
     "https://spacelaunchnow.app/launch/falcon-heavy-griffin-mission-one"),
    ("Blue Moon MK1 'Endurance' and Intuitive Machines IM-3 slipped to early 2027",
     "https://aerospaceamerica.aiaa.org/nasa-expects-one-lunar-launch-this-year-moon-base-manager-says"),
    ("Firefly Blue Ghost M2 with ESA Lunar Pathfinder, Elytra Dark, Rashid 2: later in 2027",
     "https://spacenexus.us/moon/missions/blue-ghost-2"),
    ("ispace: no 2026 launch (ULTRA lander; Japanese M3 2028 on H3, US M3 2030)",
     "https://www.ispace-inc.com/2026/03/27/ispace-announces-new-ultra-lunar-lander-integrating-japanese-and-u-s-lander-models"),
    ("CZ-10A debut with Kinastra stage + Space-Time Reference Satellite to LTO: not flown",
     "https://spacelaunchnow.app/launch/long-march-10a-demo-flight"),
    ("China Cislunar CubeSat Constellation (30 sats) announced 2026-09-04, deployment by 2030",
     "https://www.techtimes.com/articles/326732/20260904/china-launches-30-satellite-lunar-safety-net-guard-moon-crews-solar-storms.htm"),
    ("ESA PLATO (Ariane 6 to SE-L2): launch planned beginning of 2027 (ESA, March 2026)",
     "https://www.esa.int/ESA_Multimedia/Videos/2026/03/Plato_s_launch_on_Ariane_6"),
    ("JAXA DESTINY+: launch 2028 or later",
     "https://newspaceeconomy.ca/2026/05/27/which-global-space-exploration-missions-are-planned-for-2026-and-2027"),
]


# --------------------------------------------------------------------------
def fmt(x, nd):
    return "" if x is None else f"{x:.{nd}f}"


def load_hz_summary():
    out = {}
    for p in sorted(CACHE.glob("horizons_summary*.tsv")):
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                out[r["key"]] = r
    return out


def hz_spans():
    mon = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
    spans = {}
    for p in glob.glob(str(CACHE / "horizons_objdata" / "obj_*.txt")):
        hid = Path(p).stem[4:]
        t = Path(p).read_text(errors="replace")
        idx = [m.start() for m in re.finditer(r"(?im)^.*(trajectory|name)\s.*(start|stop|end)", t)]
        seg = t[idx[-1]:] if idx else ""
        ds = [dt.date(int(y), mon[m], int(d))
              for y, m, d in re.findall(r"(\d{4})-([A-Z][a-z]{2})-(\d{1,2})", seg) if m in mon]
        if ds:
            spans[hid] = (min(ds).isoformat(), max(ds).isoformat())
    spans.update(HZ_SPAN_OVERRIDE)
    return spans


def gp_elements():
    out = {}
    data = json.loads((CACHE / "spacetrack" / "gp_news_objects.json").read_text())
    for r in data:
        e = float(r["ECCENTRICITY"])
        n = float(r["MEAN_MOTION"])                     # rev/day
        a = float(r["SEMIMAJOR_AXIS"]) if r.get("SEMIMAJOR_AXIS") else \
            (MU * (86400.0 / (2 * math.pi * n)) ** 2) ** (1 / 3)
        out[str(int(r["NORAD_CAT_ID"]))] = dict(
            period_d=fmt(1.0 / n, 4), a_km=fmt(a, 1), e=f"{e:.7f}",
            incl_deg=f"{float(r['INCLINATION']):.4f}", perigee_km=fmt(a * (1 - e), 1),
            apogee_km=fmt(a * (1 + e), 1), elem_epoch=r["EPOCH"][:19] + "Z", frame_center="earth")
    return out


def satcat_elements():
    out = {}
    for r in json.loads((CACHE / "spacetrack" / "satcat_proba.json").read_text()):
        rp, ra = float(r["PERIGEE"]) + RE, float(r["APOGEE"]) + RE
        a = (rp + ra) / 2
        out[r["NORAD_CAT_ID"]] = dict(
            period_d=fmt(float(r["PERIOD"]) / 1440.0, 4), a_km=fmt(a, 1),
            e=f"{(ra - rp) / (ra + rp):.5f}", incl_deg=r["INCLINATION"], perigee_km=fmt(rp, 1),
            apogee_km=fmt(ra, 1), elem_epoch="", frame_center="earth")
    return out


def hz_elements(summary, key):
    r = summary[key]
    if r["status"] != "ok" or not r["e"]:
        return {}
    center = "moon" if r["center"].endswith("301") else "earth"
    d = dict(e=r["e"], incl_deg=r["incl_deg"], elem_epoch=HZ_EPOCH, frame_center=center)
    if r["a_km"]:
        d.update(a_km=r["a_km"], perigee_km=r["perigee_km"], apogee_km=r["apogee_km"],
                 period_d=r["period_d"])
    return d


def hzelm_elements(key):
    t = json.loads((CACHE / "horizons" / f"{key}_elm.json").read_text())["result"]
    blk = re.search(r"\$\$SOE(.*?)\$\$EOE", t, re.S).group(1)

    def g(tag):
        m = re.search(tag + r"\s*=\s*([-+]?\d[\d.]*E[-+]\d+)", blk)
        return float(m.group(1)) if m else None
    e, q, i, a = g("EC"), g("QR"), g("IN"), g(r"\bA")
    d = dict(e=f"{e:.5f}", incl_deg=f"{i:.2f}", perigee_km=fmt(q, 0), elem_epoch=HZ_EPOCH,
             frame_center="earth")
    if a is not None:
        d["a_km"] = fmt(a, 0)
    return d


def covers(eph):
    best = "N"
    for kind, _ref, s, e in eph:
        if kind in ("horizons", "bgtle") and e and e >= NOW.isoformat() and (not s or s <= NOW.isoformat()):
            return "Y"
        if kind == "spacetrack_gp" and s:
            age = abs((NOW - dt.date.fromisoformat(s)).days)
            if age <= 10:
                return "Y"
            if age <= 30:
                best = "?"
        if kind in ("pluto_mpec", "neocpsat_fit") and e:
            end = dt.date.fromisoformat(e)
            if end >= NOW - dt.timedelta(days=10):
                return "Y"
            if end >= NOW - dt.timedelta(days=40):
                best = "?"
    return best


def main():
    summary = load_hz_summary()
    spans = hz_spans()
    gp = gp_elements()
    sc = satcat_elements()
    lines = ["\t".join(COLUMNS)]
    refs = set()
    for r in ROWS:
        if r["ref"] in refs:
            sys.exit(f"duplicate src_ref {r['ref']}")
        refs.add(r["ref"])
        el = {}
        kind = r["elems"][0] if r["elems"] else None
        if kind == "hz":
            el = hz_elements(summary, r["elems"][1])
        elif kind == "hzelm":
            el = hzelm_elements(r["elems"][1])
        elif kind == "gp":
            el = gp[r["elems"][1]]
        elif kind == "satcat":
            el = sc[r["elems"][1]]
        elif kind == "fixed":
            el = r["elems"][1]
        eph = []
        for k, ref, s, e in r["eph"]:
            if k == "horizons" and not s and not e:
                s, e = spans.get(ref, ("", ""))
            eph.append((k, ref, s, e))
        row = dict(src="news", src_ref=r["ref"], name=r["name"], aliases=r["aliases"],
                   norad=r["norad"], cospar=r["cospar"], horizons_id=r["hz"], obj_type=r["type"],
                   regime=r["regime"], status=r["status"], status_date=r["sdate"],
                   period_d=el.get("period_d", ""), a_km=el.get("a_km", ""), e=el.get("e", ""),
                   incl_deg=el.get("incl_deg", ""), perigee_km=el.get("perigee_km", ""),
                   apogee_km=el.get("apogee_km", ""), elem_epoch=el.get("elem_epoch", ""),
                   frame_center=el.get("frame_center", ""),
                   ephem_sources=";".join(f"{k}:{ref}[{s}..{e}]" for k, ref, s, e in eph),
                   covers_now=covers(eph), notes=r["notes"])
        lines.append("\t".join(str(row[c]).replace("\t", " ").replace("\n", " ") for c in COLUMNS))
    OUT_TSV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_notes()
    print(f"wrote {OUT_TSV} ({len(lines) - 1} rows) and {OUT_MD}")


def write_notes():
    out = ["# news source: claims and citations",
           "",
           "Current-events research for cislunar-eye, reference epoch 2026-10-21T00:00Z. "
           "Built by `scripts/build_news_tsv.py` from `work/cache/news/research_log.tsv` "
           "(searches via Tavily, WebSearch fallback when rate-limited, Oct 2026) and the cached "
           "Horizons / Space-Track / GCAT / "
           "Bill Gray files. Each bullet is *claim -> URL*.",
           "",
           "## Conventions and caveats",
           "",
           "- `src_ref` is the primary citation URL of each row; sibling rows sharing one page get "
           "a `#fragment` (e.g. MMS-2..4) so `src_ref` stays unique.",
           "- Horizons IDs are given only when resolved by a Horizons API request this task "
           "(work/cache/news/horizons*/); spans come from the cached object-data headers.",
           "- Horizons elements: osculating at 2026-10-21 00:00 TDB (2026-10-20T23:58:51Z) about "
           "Earth or Moon (`frame_center`), Earth/Moon body equator of date; for lunar orbiters "
           "`period_d` is selenocentric. Omitted for heliocentric spacecraft and for ESCAPADE "
           "(e~0.999 at 1.4e6 km, not meaningful).",
           "- Elements of FH-013 S2, Spektr-RG, Chang'e-6, Ch-3 PM, CAPSTONE, Tiandu-1, P22qGC1 are "
           "Bill Gray find_orb fits (projectpluto pseudo-MPECs, values as parsed in neocpsat.tsv).",
           "- covers_now: Y if a Horizons/bgtle span contains 2026-10-21, a Space-Track GP epoch is "
           "within 10 d, or a find_orb fit arc ends on/after 2026-10-11; '?' for GP within 30 d or a "
           "fit arc ending on/after 2026-09-11; else N.",
           "- Regime by numeric rule: apogee >= 100,000 km from Earth centre -> CISLUNAR_HEO "
           "(Chandra, INTEGRAL, MMS, SMILE) although SCHEMA.md lists Chandra/INTEGRAL as HEO "
           "examples; Artemis II cubesats, ICPS-2 and Proba-3 are HEO with P < 2.4 d -> OTHER.",
           "- Status for Orion/ESM: `decayed` = re-entered (crew module recovered). ATENEA re-entry "
           "and the H3 F10 stage escape are inferences, flagged in the row notes.",
           "- Spektr-R end of mission (last contact 2019-01-11, declared finished 2019-05-30) "
           "re-verified by web search 2026-10-21 (Wikipedia). From memory only: eROSITA on "
           "Spektr-RG idle since Feb 2022 (ART-XC continues).",
           "- Tavily searches hit HTTP 429 rate limits in the resumed session; the last few checks "
           "used the built-in WebSearch fallback (logged with the URL it returned).",
           "- GCAT (2026-10-17 cache) deepcat/hcocat/lprcat/satcat were scanned for every 2025-2026 "
           "launch with apogee >= 40,000 km: the only 2026 objects beyond GEO are Artemis II "
           "(2026-069 A-E + ESM/CMA), SMILE (2026-109A, reached HEO by own propulsion) and Roman + "
           "FH-013 S2 (2026-199A/B); Space-Track SATCAT 2026-10-16..21 adds MMX + H3 R/B "
           "(2026-236A/B).",
           "",
           "## Not launched / not in space as of 2026-10-21 (no TSV row)",
           ""]
    for c, u in NOT_LAUNCHED:
        out.append(f"- {c} -> {u}")
    out += ["", "## Rows (object -> primary URL)", ""]
    for r in ROWS:
        out.append(f"- {r['name']} [{r['regime']}, {r['status']} {r['sdate']}] -> {r['ref']}")
    out += ["", "## Claim log (claim -> URL)", ""]
    with open(CACHE / "research_log.tsv", encoding="utf-8") as f:
        for ln in f:
            if ln.startswith("#") or not ln.strip():
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3:
                out.append(f"- **{p[0]}**: {p[1]} -> {p[2]}")
    OUT_MD.write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
