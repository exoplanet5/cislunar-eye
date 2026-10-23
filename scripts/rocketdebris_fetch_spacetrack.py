#!/usr/bin/env python3
"""Fetch Space-Track SATCAT (by launch) and current GP for the objects in the
../rocketdebris beyond-GEO wiki, for catalog/sources/rocketdebris.tsv.

Uses the shared space-track skill module (~/.claude/skills/space-track/st.py:
stored login, rate limiting, disk cache). Read-only. Idempotent: results are
written to work/cache/rocketdebris/spacetrack/{satcat,gp}.json and reused
unless --fresh is given.

Usage:
  ~/.venvs/astro313/bin/python scripts/rocketdebris_fetch_spacetrack.py [--fresh]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".claude/skills/space-track"))
import st  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "work/cache/rocketdebris/spacetrack"

# Every launch (YYYY-NNN) named in ../rocketdebris/beyond_geo_upper_stages.md
# or carried by status_verified.tsv.
LAUNCHES = """
1963-039 1964-040 1964-054 1964-077 1965-058 1966-084 1967-040 1968-118
1969-018 1969-043 1969-046 1969-059 1969-099 1970-029 1971-008 1971-063
1972-012 1972-031 1972-073 1972-096 1973-019 1973-078 1976-023 1976-081
1977-076 1977-084 1977-093 1977-102 1978-079 1983-020 1989-084 1992-044
1994-071 1995-065 1996-013 1997-045 1997-061 1999-003 1999-066 2000-045
2001-027 2001-034 2002-048 2003-019 2004-006 2005-001 2006-047 2007-004
2007-051 2008-051 2009-026 2009-068 2010-020 2010-050 2011-037 2011-070
2012-003 2013-026 2013-041 2013-063 2013-070 2013-074 2014-065 2014-076
2015-005 2015-007 2015-019 2016-017 2016-031 2016-038 2016-055 2018-017
2018-038 2018-042 2018-045 2018-065 2018-080 2018-103 2019-009 2019-021
2019-040 2019-042 2020-010 2020-047 2020-049 2020-052 2020-087 2021-093
2021-110 2021-130 2022-070 2022-087 2022-094 2022-156 2022-157 2022-168
2023-017 2023-053 2023-075 2023-092 2023-098 2023-118 2023-132 2023-137
2023-154 2023-157 2024-006 2024-030 2024-048 2024-051 2024-083 2024-179
2024-180 2024-182 2024-214 2025-010 2025-038 2025-260 2026-069
2000-041 1970-027
""".split()


def main() -> None:
    fresh = "--fresh" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    sc_path = OUT / "satcat.json"
    satcat: dict[str, list] = {}
    if sc_path.exists() and not fresh:
        satcat = json.loads(sc_path.read_text())
    for L in LAUNCHES:
        if L in satcat and not fresh:
            continue
        rows = st.query(["class", "satcat", "INTLDES", f"^{L}",
                         "orderby", "INTLDES asc"], ttl_h=24.0)
        satcat[L] = rows
        sc_path.write_text(json.dumps(satcat, indent=1))
    # current GP for every non-decayed catalogued piece (chunks of 80)
    norads = sorted({int(r["NORAD_CAT_ID"]) for rows in satcat.values()
                     for r in rows if not r.get("DECAY")})
    gp_path = OUT / "gp.json"
    gp: dict[str, dict] = {}
    if gp_path.exists() and not fresh:
        gp = json.loads(gp_path.read_text())
    todo = [n for n in norads if str(n) not in gp]
    for i in range(0, len(todo), 80):
        chunk = todo[i:i + 80]
        rows = st.query(["class", "gp", "NORAD_CAT_ID",
                         ",".join(map(str, chunk))], ttl_h=1.0)
        got = {str(r["NORAD_CAT_ID"]): r for r in rows}
        for n in chunk:
            gp[str(n)] = got.get(str(n), {})
        gp_path.write_text(json.dumps(gp, indent=1))
    print(f"satcat launches={len(satcat)} objects="
          f"{sum(len(v) for v in satcat.values())}; gp rows="
          f"{sum(1 for v in gp.values() if v)}/{len(gp)}", file=sys.stderr)


if __name__ == "__main__":
    main()
