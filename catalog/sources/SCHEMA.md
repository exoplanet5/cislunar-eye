# Per-source candidate extract schema

Every extractor writes one tab-separated file `catalog/sources/<src>.tsv`
(UTF-8, header row, no quoting, empty string for unknown, `;` inside list
fields). One row per physical object per source. Historical NEOCP apparitions
that were never identified are one row each.

Reference epoch for "now": **2026-10-21T00:00:00Z** for the committed one-off
extracts; `neocp.tsv` is rebuilt hourly against the run's reference day
(`CLE_NOW` floored to 00:00 UTC, else the wall clock; `scripts/_now.py`).
Default display window under discussion: 2026-09-21 → 2026-11-20 UTC.

| column | meaning |
|---|---|
| `src` | source key: `neocpsat`, `billgray`, `rocketdebris`, `lunarmission`, `horizons`, `neocp`, `news` |
| `src_ref` | row key inside that source (file name, temp desig, TSV label, URL) |
| `name` | common name, e.g. `JWST`, `Chang'e-3 CZ-3B R/B` |
| `aliases` | other names / temp desigs / Horizons names, `;`-separated |
| `norad` | NORAD number (Bill Gray 79xxx/89xxx surrogate allowed; say so in notes) |
| `cospar` | `YYYY-NNNX` |
| `horizons_id` | exact Horizons `COMMAND` that works (e.g. `-170`, `2020 SO`) — only if verified or listed by the source |
| `obj_type` | `payload`, `rocket_body`, `debris`, `unknown`, `natural` |
| `regime` | controlled vocabulary below |
| `status` | `active`, `derelict` (in orbit, not operating), `decayed`, `lunar_impact`, `landed`, `escaped` (heliocentric, not returning soon), `returning` (heliocentric with Earth encounter forecast), `lost` (contact lost, location uncertain), `unknown` |
| `status_date` | date of the status event or last confirmation, `YYYY-MM-DD` |
| `period_d` | geocentric (or selenocentric for lunar orbit, say so in notes) period, days |
| `a_km`,`e`,`incl_deg`,`perigee_km`,`apogee_km` | latest known osculating elements; distances from body centre |
| `elem_epoch` | epoch of those elements, ISO UTC |
| `frame_center` | `earth`, `moon`, `sun` for the elements above |
| `ephem_sources` | every ephemeris source known for this object with its coverage, `;`-separated, form `kind:ref[start..end]`, kinds: `horizons`, `bgtle` (Bill Gray hex TLE file), `neocpsat_fit` (find_orb fit/combined MPEC in ../neocpsat), `neocp_obs` (80-col obs file), `pluto_mpec` (projectpluto pseudo-MPEC URL), `spacetrack_gp`, `tle_other` |
| `covers_now` | `Y` if at least one listed ephemeris source covers the reference day, `N` otherwise, `?` if unknown |
| `notes` | short free text; cite the line/section of the source |

`neocp.tsv` only (appended after `notes`; other sources end at `notes`, readers
take columns by name):

| column | meaning |
|---|---|
| `listed_now` | `Y` if the object is in the current NEOCP listings (Project Pluto summary or MPC `neocp.txt`) at the run, `N` if it is carried by a sticky selection or `--keep-days` only |
| `last_listed` | ISO UTC regeneration stamp of the last listing in which `neocp_live.py` saw the object; empty if it was never seen listed (selected from Bill Gray's sat_id2 log only). An object with `listed_now` `N` and a `last_listed` has left the NEOCP on or after that stamp; the merge reports it as "left the NEOCP <date>" |

Regime vocabulary:

- `LUNAR_ORBIT` — bound to the Moon (incl. elliptical frozen, ELFO)
- `EM_L1`, `EM_L2` — Earth–Moon L1/L2 halo, Lyapunov, NRHO
- `EM_DRO` — distant retrograde orbit (and DRO-family resonant)
- `EM_OTHER` — other Earth–Moon CR3BP orbit (DPO, P/2 resonance like TESS, Lissajous, L4/L5)
- `CISLUNAR_HEO` — bound geocentric, apogee ≥ 100 000 km (Spektr-R, IBEX, Chang'e-3 R/B)
- `HEO` — bound geocentric, period > 2.4 d but apogee < 100 000 km (Vela, Chandra, INTEGRAL)
- `TRANSLUNAR` — transfer / free-return / flyby trajectory
- `SE_L1`, `SE_L2` — Sun–Earth L1/L2 libration orbits
- `EARTH_NEAR_HELIO` — heliocentric but within ~0.05 AU of Earth at some time 2024–2030 (returners, temporary captures, quasi-satellites, Earth flybys)
- `NEOCP_GEO` — NEOCP object with geocentric/S flag, not yet identified
- `OTHER` — say why in notes

Selection rule for extractors (be inclusive; the merge step tiers objects):
geocentric bound orbit with period > 2.4 d (a ≳ 75 700 km) at any recorded
epoch, or any Earth–Moon / Sun–Earth libration, lunar-orbit or translunar
object, or heliocentric object passing within ~0.05 AU of Earth 2024–2030, or
NEOCP geocentric/S-flag object. Record decayed, impacted and escaped objects
too — with the right `status` — so the merge can show what was excluded and
why.
