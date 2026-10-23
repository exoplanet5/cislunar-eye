# cislunar-eye: architecture proposal

**Status:** proposal for review. Nothing is built yet. After you approve or change it, phase 1 starts.
**Date:** 2026-10-21 UTC. The reference epoch "now" is 2026-10-21T00:00:00Z, and the default window is 2026-09-21 → 2026-11-20.
**Inputs read:**
- `catalog/sources/SCHEMA.md`
- `work/notes/mmx_reuse.md` (MMX at HEAD `8c60106`)
- the sibling projects, all read-only
- the Horizons probes in `work/cache/arch_probe/`
- the find_orb / sat_code sources under `~/Desktop/billgray/`

**Tags:**
- *(memory)* marks a statement that rests on my background knowledge rather than a file or an API response.
- *(computed)* marks a number computed for this document.
- `file:line` references are to the tree named. MMX references are to `/Users/mickey/solarsystem/MMX`.

---

## Amendments of 2026-10-21 (your decisions; these override the body)

- **AM1. Default frame: Sun–Earth rotating, Earth-centred, in phase 1.**
  - Origin: Earth centre. x̂ = unit(r⊕ − r☉), anti-sunward, so SE-L2 lies on +x and SE-L1 on −x. ẑ = unit((r⊕ − r☉) × (v⊕ − v☉)), the ecliptic-like normal. ŷ = ẑ × x̂, roughly Earth's heliocentric motion.
  - Every trail sample is rotated with its own R(tᵢ); this is what makes halo and L2 loiter orbits readable. Then the MMX mapping (x, z, −y) is applied, so screen-up is +ẑ.
  - The Earth globe (Skyfield quaternion) and the star sphere are rotated by R(t_now). The stars therefore drift about 1° per day in this frame. The Sun direction and the terminator are fixed.
  - Selectable alternatives:
    - **ECI**, exactly MMX (D2), with no rotation;
    - **Earth–Moon rotating**: x̂ = Moon direction, ẑ = Moon orbit normal (r☾ × v☾), Earth-centred, for DRO, NRHO and the EM L-points.
  - D13 is superseded: the rotating frames are phase 1, and their transforms run in the browser from the Sun and Moon context tracks.
- **AM2. Window: now − 15 d → now + 15 d.** "Now" is the build time floored to the hour (UTC). Grids align to the window start. The page opens LIVE at the wall clock when that is inside the window; otherwise it opens at the build's now. D5 and §6.1 (±30 d) are superseded.
- **AM3. Default visible: tier 1 only.**
  - Tier 2 objects are toggles in the list, with a "natural" filter for minimoons.
  - Rows with `draw=N` appear in the list with a "listed, not drawn" badge: Queqiao-1, Queqiao-2, Tiandu-2, and the ended objects kept for replay.
  - Each object's `caveat` (from `catalog/master_list.json`) is shown in its readout and in the list tooltip.
  - Staleness visuals (2026-10-23, review V4/V11): a **hollow** marker means the position is not from a current
    ephemeris (stale or extrapolated) and is drawn at 60 % alpha; derelicts are a dimmer fill (70 %), not a ring; stale
    rows carry ⚠ in the list, the Situation panel and the tooltip's one epoch line. An old `neocp.js` snapshot is
    announced in the Situation panel's NEOCP section; the scene banner keeps only the data-window and missing-chunk
    messages.
- **AM4. Lagrange markers**, drawn as small hollow diamonds with haloed labels and a toggle:
  - **SE-L1 and SE-L2**: on the Sun–Earth line at the CR3BP collinear distances. μ = (M⊕ + M☾)/(M☉ + M⊕ + M☾), scaled to the instantaneous Sun–Earth distance, about 1.49–1.51 × 10⁶ km. They are labelled as nearly stationary in the Sun–Earth frame.
  - **EM-L1 to EM-L5**: from the instantaneous geocentric Moon state. μ = 0.0121506, with the collinear roots scaled to the instantaneous Earth–Moon distance and measured from the barycentre. L4 and L5 sit ±60° from the Moon in the instantaneous orbit plane. They co-move with the Moon in every frame; EM-L5 is included as the symmetric partner of L4. Drawn (glyph, label, picking) only in the EM frame or zoomed to the Moon: the Moon, an EM point or an EM_* / lunar target focused (review V5, decision 4 of the visual pass).
- **AM5. Latest-orbit extrapolation.** IBEX (Bill Gray fit ending 2026-01-01), CAPSTONE (neocpsat fit of 2026-09-12) and every tier-2 "stale" object are propagated from their latest state with an Earth + Moon + Sun point-mass integrator (DE440s, DOP853, rtol 1e-11). This uses `quality=extrap` and the caveat text.
- **AM6. CR3BP objects with GP only.** For DRO-A and DRO-B the GP is used only for its state at epoch (SDP4 → TEME → GCRS). That state is then integrated as in AM5, with `quality=gp`.
- **AM7. Textures.**
  - **Moon:** `../lunarmission/moon_wac_z3.jpg` (LROC WAC, 4096 × 2048), copied to `web/assets/moon_4096.jpg`.
  - **Earth:** a 4096 × 2048 downsample of the NASA Blue Marble NG basemap. Use `~/sda/basemaps/world.topo.bathy.200409.3x21600x10800.jpg`, with `~/sda/satobserver/app/assets/earth_day.jpg` as the fallback.
- **AM8. Catalog files** are `catalog/master_list.tsv` / `.json` and `catalog/TARGETS.md`, built by `scripts/build_master.py --now <date>`. The builder takes every tier-1 and tier-2 row with `draw=Y` and dispatches on `primary_ephem` (`horizons`, `bgtle`, `spacetrack_gp`, `neocpsat_fit`, `pluto_mpec`, or a `STALE …` source).

## 0. Decisions at a glance

| # | Decision | Reason |
|---|---|---|
| D1 | The page is a **local, self-contained HTML page**. `dist/index.html` holds the esbuild IIFE bundle plus inlined static assets (Earth texture, star catalogs). Changing data lives in classic-script sidecars `dist/data/*.js`. Open it with `open dist/index.html`. No server, no network at runtime, no hosted Artifact, no git push. | This is your stated preference. On `file://`, `fetch` and ES modules are blocked in Chrome *(memory)*, but classic `<script src>` works. Sidecars let the NEOCP layer refresh hourly without rebundling a page of about 30 MB. |
| D2 | The frame is the **MMX ECI frame, exactly**: ICRF axes, geometric states (`VEC_CORR='NONE'`), UTC labels, km and km/s. Scene vector = `(x, z, −y) / 6378.137 km`. | Reuses MMX's tested mapping. It appears in 4 places (`ephemeris.py:28`, `observer.js:12-14`, `sky.js:4-7`, `app.js:37-40`). |
| D3 | **All ephemerides are computed offline in Python** into one uniform state table. The browser only interpolates: cubic Hermite, a separate grid per target, no extrapolation. | Validation happens in one place. No SGP4 or hex-TLE code in JS. |
| D4 | **Source priority per time interval** (as built in `scripts/build_master.py`): Horizons > Bill Gray TLE (inside `# Range` only) > Space-Track GP ≤ 30 d old > neocpsat find_orb fit > live NEOCP fit > Space-Track GP 30–120 d old > elements only. Per-object overrides are allowed. **Earth–Moon CR3BP objects that only have a GP (DRO-A/B) are not propagated with SDP4:** the GP state at its epoch is integrated with Earth + Moon + Sun. | §5.2. A fresh GP beats a single-night neocpsat fit for the HEO derelicts (GP and Bill Gray periods agree to about 1 %). A DRO's GP period swings between 26 and 47 d, so SDP4 is wrong there. |
| D5 | **Rolling window from now −30 d to now +30 d**, with a step set by regime and refined automatically near periapsis. Optional 1-day **history chunks** cover longer spans, e.g. Spektr-RG from 2019-07-14 to now. | §6. |
| D6 | **Lunar orbiters are stored and interpolated Moon-relative.** Their trails are drawn anchored to the Moon. | §5.3, §9.4. Avoids the geocentric "helix smear". |
| D7 | **Floating origin at the focus body**, plus a **logarithmic depth buffer** and float32 vertices relative to an Earth or Moon anchor. | Covers 10³–10⁷ km (§9.1–9.2). |
| D8 | **Trails** are batched, instanced segments carrying a per-vertex time. The past is solid and fades; the future is thin and dashed. The time window is applied on the GPU through uniforms. | No per-frame buffer uploads (§9.4). |
| D9 | **Stars are real but subdued.** All 9,827 Hipparcos stars (V < 6.6) are kept with retuned constants (§9.8). Grid, constellations and sky names are **off** by default. | You asked for real stars that do not stand out. |
| D10 | **Terminator colour `#ffd54f`**, matching your map convention `ift14_style.SUN`. An optional Sun −12° ring in `#4fc3f7` (`ift14_style.ACCENT`) is off by default. | One convention across PNG maps and the 3D page. MMX used `#b09025`. |
| D11 | **Master list** = a merge of the per-source TSVs into `catalog/master_list.tsv` / `.json`, plus the review file `catalog/TARGETS.md` (`scripts/build_master.py`; checks in `scripts/verify_master.py`). Hand edits go only in `catalog/overrides.tsv`. | You review the list before build (§4). |
| D12 | **NEOCP tracklet dots** = station GCRS position + fitted topocentric range along the observed RA/Dec. Sight lines are a toggle. | §7. |
| D13 | **Earth–Moon rotating and Sun–Earth rotating views** are **phase 2**. They are computed in the browser from the same data. | DRO, NRHO and halo orbits are unreadable in ECI (§8). |
| D14 | **Cadence:** `make update-neocp` hourly; `make update-horizons` and `make update-gp` daily; `make update-fits` when `../neocpsat` changes. `make build` is always offline and runs from cached snapshots. | §13. |

---

## 1. Goals and non-goals

**Goals**

- **G1** A situation display. It shows where each tracked artificial object in the Earth–Moon system and the Sun–Earth L1/L2 neighbourhood is **now** and over ±30 d. The scene is Earth-centred and inertial, in the NASA Eyes style.
- **G2** Cover every regime in the SCHEMA vocabulary: `LUNAR_ORBIT`, `EM_L1`, `EM_L2`, `EM_DRO`, `EM_OTHER`, `TRANSLUNAR`, `CISLUNAR_HEO`, `HEO` (P > 2.4 d), `SE_L1`, `SE_L2`, `EARTH_NEAR_HELIO`, `NEOCP_GEO`, `OTHER`.
  - This includes abandoned upper stages and derelicts.
  - It also includes natural temporary companions that sources list (e.g. 2024 PT5), flagged `obj_type=natural`.
- **G3** Each object has:
  - a marker;
  - part of its trajectory (trail);
  - a decluttered label;
  - readouts;
  - its **data source, data age and quality**, always visible.
- **G4** Live NEOCP geocentric candidates refresh hourly and show their observation tracklets.
- **G5** Builds are reproducible offline: each raw snapshot carries a manifest and sha256 hashes, following the MMX `data/raw/<stamp>/manifest.json` pattern.
- **G6** The page works fully offline from `file://` in Chrome and Safari. It keeps MMX's look: dark palette, Menlo, halo labels, Blue Marble.

**Non-goals (phase 1)**

- No orbit determination inside the page. `fo` and Horizons run offline.
- No conjunction screening, collision probability or manoeuvre planning.
- No LEO, MEO or GEO population. Bound objects with P < 2.4 d and apogee < 100,000 km are out.
- No 3D spacecraft models; markers only.
- No ground-observer views. MMX `observer.js` and `skychart.js` are deferred to phase 3.
- No hosted deployment. No writes to sibling projects. Never run `../neocpsat main.py` (scan) or `generate_page.deploy`.
- Not an operational or safety product. Every position carries its quality class.

---

## 2. What you see (NASA Eyes-like behaviour)

NASA Eyes behaviours referenced here come from my memory of the product *(memory)*: Earth-centred, focus-and-fly, fading orbit trails, a LIVE clock, a search list.

**Default view**

- **Upper left:** the title "Cislunar Eye" with the UTC clock.
- **Centre:** the Blue Marble Earth, lit by the Sun, with its terminator. The Moon is grey and has a faint trail over one synodic month (7 d in the EM frame).
- **Camera:** framed on the Moon's orbit (≈ 60 % of the scene width, Earth ≥ 5 px), about 30° above the Earth–Moon plane, Sun on the left (the `em` preset; review V1, 2026-10-23). SE-L1 and SE-L2 are off screen and shown as edge chevrons with their range; the SE-L1–SE-L2 framing is the "L1–L2" preset.
- **Markers:** one per live target, coloured by regime family. Labels are on for the 40 highest-priority targets (16 on a phone); the frame's own Lagrange points carry short always-on labels.

**Panels**

- **Left panel:** the target list, grouped by regime, with search, filters and per-row visibility toggles.
- **Right panel:**
  - when a target is selected: its readouts;
  - when nothing is selected: a "Situation" summary with counts per regime, the latest NEOCP items and stale-data warnings.
- **Bottom time bar:** **LIVE**, play/pause/reverse, rate, a slider over the window, and ±1 h / ±1 d steps.

**Interaction**

- **Click** a marker or a list row to select it. **Double-click** or **F** to focus: the camera flies to the target, which becomes the orbit pivot. **E** and **M** focus Earth and the Moon.
- **Trails:** a regime-specific past span is drawn solid and fades. A shorter future span is dashed.
- **Selected target:** drawn with its whole window trajectory.
- **Moving the time slider:** everything moves together. Earth rotates using baked ITRS quaternions.
- **NEOCP objects:** the observation dots appear along the trail at the epochs they were observed, with optional sight lines from the stations.

---

## 3. System overview

### 3.1 Component diagram

```
 SOURCES (read-only)                 OFFLINE PIPELINE  (src/cle, ~/.venvs/astro313, parsing with -I)
 ───────────────────                 ───────────────────────────────────────────────────────────────
 ../neocpsat  catalog.csv,  ───┐
   data/*.txt, mpecs/*.html    │     scripts/extract_*.py  (exist)        catalog/overrides.tsv (hand)
 ../highartsat  build.py,      │            │                                    │
   satid/data/artsats.csv      ├──────────► catalog/sources/<src>.tsv ──► cle.catalog (merge, tier, sort)
 ~/tles  tle_list.txt, *.tle   │            (SCHEMA.md)                          │
 ../rocketdebris  *.tsv, *.md  │                       catalog/master.tsv, MASTER.md, merge_review.tsv
 ../lunarmission  *.tsv, *.md  │                                                 │
 JPL Horizons API ─────────────┤                                                 ▼
 projectpluto neocp2, MPC ─────┤                                      cle.plan  → build/plan.json
 Space-Track (skill CLI) ──────┘                         (per target: [source, ref, start, stop, step, centre])
 ~/JPLDE/de440s.bsp                                                              │
 ~/Desktop/billgray/{fo,get_vect}     ┌──────────────┬──────────────┬────────────┼─────────────┬──────────────┐
                                      ▼              ▼              ▼            ▼             ▼              ▼
                                 adapters.      adapters.fo    adapters.    adapters.     adapters.gp    cle.context
                                 horizons       (neocpsat      bgtle        neocp (live   (Space-Track   (DE440s Moon,
                                 (API, cache,   fits → fo      (get_vect,   fetch → fo,   GP history,    Sun; Earth
                                 clip, splice)  vectors)       in Range)    tracklets)    sgp4→GCRS)     quats 1 h)
                                      └──────────────┴───────┬──────┴────────────┴─────────────┘              │
                                                             ▼                                                │
                                         StateTable  (UTC, ICRF, geometric, km, km/s, centre earth|moon,      │
                                                      segments, quality, σ)  → build/states/<id>.npz          │
                                                             │  cle.validate: midpoints, splices,             │
                                                             │  coverage, Moon check, regime check            │
                                                             ▼                                                │
                                         cle.export  ──►  dist/data/core.js ◄────────────────────────────────┘
                                                          dist/data/catalog.js, targets-<chunk>.js,
                                                          neocp.js (hourly), history-<id>.js
 web/ (app.js, states.js, sky.js, sky-style.js, scene/*, ui/*,
       vendor/three r170, assets/)  ── scripts/build_site.mjs (esbuild IIFE) ──►  dist/index.html
```

### 3.2 Runtime (browser) modules

```
 index.html ─ inline: bundle (IIFE), stars JSON, Earth texture <img data:>
   │  <script src="data/core.js">  <script src="data/catalog.js">  (static, in order)
   │  data/targets-*.js, data/neocp.js, data/history-*.js  (injected on demand, re-injected for refresh)
   ▼
 loader.js   CLE.register(name, payload) → decode base64 → Float32/64Array views (zero copy)
 time.js     TimeModel {t_ms, rate, live, playing}; clamps to window; emits 'tick'
 states.js   Hermite per target/segment into preallocated buffers; Moon-relative recombination
 frames.js   ECI (default); EM-rotating and SE-rotating transforms (phase 2)
 focus.js    floating origin = focus position (float64 CPU); fly-to tween; view presets
 scene/      earth.js (globe, terminator, light), moon.js, refpoints.js (EM L1/L2/L4/L5, SE L1/L2),
             markers.js (one THREE.Points), trails.js (batched LineSegments2 + trail material),
             tracklets.js (NEOCP dots and sight lines), sky.js / sky-style.js (copied from MMX, retuned)
 labels.js   priority, cap 40, grid-hash collisions, Earth and Moon occlusion, cached sizes
 ui/         list.js (grouped list, search, filters), readout.js, situation.js, timebar.js, url.js
 render      renderer.clear → sky.render → clearDepth → scene render (MMX order); render on demand
```

---

## 4. Catalog: from source extracts to the master list

### 4.1 Inputs

There is one `catalog/sources/<src>.tsv` per source, in the exact SCHEMA columns: `src`, `src_ref`, `name`, `aliases`, `norad`, `cospar`, `horizons_id`, `obj_type`, `regime`, `status`, `status_date`, `period_d`, `a_km`, `e`, `incl_deg`, `perigee_km`, `apogee_km`, `elem_epoch`, `frame_center`, `ephem_sources`, `covers_now`, `notes`.

| src | extractor (exists) | key facts relevant to the design |
|---|---|---|
| `neocpsat` | `scripts/neocpsat_parse.py` (+ extract) | `../neocpsat/catalog.csv` is the system of record: 269 lines, 264 obs files and 178 MPEC pages *(computed)*. Many objects have no Horizons ephemeris, e.g. Spektr-RG, Chang'e-6 orbiter, Chandrayaan-3 PM, Falcon Heavy S2 and Blok SO-L. |
| `billgray` | `scripts/extract_billgray.py` | 838 `# Include`s, of which 645 are live. 305 objects are selected. **Only 2 local Ranges cover 2026-10-21:** `c45gf81.tle` (to 2027-01-01) and `26109a.tle` = SMILE (2026-06-22 → 2027-01-01). Most yearly files end 2026-01-01 *(computed from `~/tles/tle_list.txt`)*. Local `~/tles` HEAD is `3a32146` (2026-07-19); upstream is `4b14176` (2026-09-14) (`work/cache/billgray/upstream/commit_messages.txt`). |
| `rocketdebris` | `scripts/rocketdebris_fetch_spacetrack.py` (+ extract) | `status_verified.tsv`: 127 objects, with GP epoch and Bill Gray file per object. |
| `lunarmission` | `scripts/extract_lunarmission.py` | GCAT lunar objects; 43 rows in Horizons status; impact and landing statuses. |
| `horizons` | `scripts/horizons_sweep.py` | Empirical sweep of the Horizons major-body list. |
| `neocp` | `scripts/neocp_live.py` | Live NEOCP: Bill Gray summary `(geo)` + MPC `S` flag. Today it has 12 rows: 5 hyperbolic geocentric fits, 1 `CISLUNAR_HEO` (C46PP91, P = 4.75 d) and 6 `EARTH_NEAR_HELIO`. |
| `news` | `scripts/news_horizons_check.py` | Current-events targets with verified Horizons IDs. Examples:<br>- **SE-L1:** SOHO −21, ACE −92, Wind −8, DSCOVR −78, Aditya-L1 −156, IMAP −43, SWFO-L1 −231, Carruthers −171.<br>- **SE-L2:** JWST −170, Euclid −680, Roman −211.<br>- **Lunar:** LRO −85, Danuri −155, Chandrayaan-2 −152, ARTEMIS P1/P2 −192/−193.<br>- **Others:** TESS −95, Chandra −151, INTEGRAL −198, SMILE −463, Spektr-R −557, MMS-1…4 −140482…−140485.<br>Source: `work/cache/news/horizons_summary.tsv`. |

When this document was written, only `neocp.tsv` existed. **As built (2026-10-21):** there are nine extracts, adding `rocketdebris.tsv` (Space-Track GP of 2026-10-14) and `spacetrack.tsv` (latest GP for objects without a fresh source; placeholder GPs with P < 1 d are dropped). The merge gives 341 objects: 62 in tier 1, 34 in tier 2 and 245 in tier 3. See `catalog/TARGETS.md`.

### 4.2 Merge (built as `scripts/build_master.py`; it moves to `src/cle/catalog.py` behind `make catalog` in phase 1)

**As built, these differ from the plan below:**

- **Three tiers, not four.** T2 and T3 below are folded into tier 2 (available, off by default); T4 is tier 3.
- **Weak keys do auto-join.** These are normalised names and NEOCP temp designations. A join is refused when the two sides hold different real NORAD numbers or different sweep-verified Horizons IDs, and name joins inside one source are never made. Every weak join is listed in `TARGETS.md` for review. On 2026-10-21 there were 10, all correct.

The original plan follows.

1. **Validate each extract.**
   - The header must equal the SCHEMA columns exactly.
   - `regime`, `status` and `obj_type` must be in the vocabulary.
   - Dates must be ISO.
   - `ephem_sources` items must match `kind:ref[start..end]`.
   - `horizons_id` must be marked as verified.

   A row that fails is reported, never silently fixed.
2. **Identity, by union-find on strong keys:**
   - COSPAR `YYYY-NNNX`;
   - real NORAD numbers (Bill Gray surrogates 79xxx/89xxx are *weak*);
   - numeric Horizons IDs;
   - neocpsat equivalence lists (the `Note` field "A11xbcg = A11xjKv = …" and the `Indentification` column);
   - Bill Gray's identification text in pseudo-MPECs.
3. **Weak keys never auto-merge.** These are normalised names, aliases and surrogate NORADs. Matches go to `catalog/merge_review.tsv`, and you accept or reject them in `catalog/overrides.tsv`.
   - This matters because Horizons name lookup is unsafe. `COMMAND='2020 SO'` silently resolves to the spacecraft record "Surveyor-2 Centaur RB", whose ephemeris ends 2022-01-01. `DES=2020 SO;` finds no small body. `Chang'e 6` and `Chandrayaan-3` return "multiple".
   - Sources: `work/cache/arch_probe/name_2020SO.json`, `smallbody_2020SO.json` and `news/horizons_summary.tsv`.
4. **Field precedence.** A non-empty value from the first source in each order wins; `overrides.tsv` beats everything.
   - **`name`:** `lunarmission` (lunar objects) > `rocketdebris` > `news` > `horizons` (cleaned target name) > `billgray` > `neocpsat` > `neocp`.
   - **`status`:** the latest `status_date` wins. Ties go `news` > `horizons` > `lunarmission` > `rocketdebris` > `neocpsat` > `billgray` > `neocp`.
   - **Elements:** the latest `elem_epoch`.
   - **`ephem_sources`:** the union of all sources, de-duplicated.
5. **Regime cross-check.** Once states exist, a classifier at "now" gives `regime_now`. Disagreements with the source regime are listed in `MASTER.md`; the source regime stays authoritative for libration classes.
   - **Lunar:** selenocentric energy < 0 and r☾ < 61,300 km (Moon Hill radius 384,400·(μ/3)^⅓, μ = 0.01215 *(computed)*).
   - **Geocentric bound:** P > 2.4 d, then apogee ≥ 100,000 km → `CISLUNAR_HEO`, else `HEO`.
   - **Heliocentric:** otherwise.
6. **Tiers**

| tier | rule | in page | default visible |
|---|---|---|---|
| **T1 live** | some ephemeris source covers now, and status ∈ {active, derelict, returning, lost, unknown} | yes | **yes** |
| **T2 window** | an ephemeris overlaps the window but not now (ended or starts mid-window) | yes | no; shown greyed in the list, drawn when enabled |
| **T3 catalogued** | in scope but no ephemeris in the window (elements only, or an ended SPK such as CAPSTONE after 2026-08-14 11:57:50 UT) | list only | no; phase 2 can draw an indicative conic |
| **T4 excluded** | decayed, lunar_impact, landed, escaped, out of scope | `MASTER.md` only | — |

### 4.3 Sort order

This order is used both in `MASTER.md` and in the page list.

1. **Regime group, from the Moon outward:** `LUNAR_ORBIT`, `EM_L1`, `EM_L2`, `EM_DRO`, `EM_OTHER`, `TRANSLUNAR`, `CISLUNAR_HEO`, `HEO`, `SE_L1`, `SE_L2`, `EARTH_NEAR_HELIO`, `NEOCP_GEO`, `OTHER`.
2. Tier (T1 first).
3. `obj_type`: payload, rocket_body, debris, unknown, natural.
4. `status`: active, returning, derelict, lost, unknown.
5. Launch date, taken from COSPAR year and number.
6. Name.

### 4.4 Outputs

- **`catalog/master.tsv`** has one row per physical object. Columns:
  - `cle_id`: a stable slug, e.g. `jwst`, `spektr-rg`, `ce3-cz3b-rb`;
  - `name`, `label` (≤ 14 characters, for 3D), `aliases`, `norad`, `cospar`, `horizons_id`;
  - `group` (mission), `parent_id`;
  - `obj_type`, `regime`, `regime_now`, `status`, `status_date`;
  - `tier`, `default_visible`;
  - `primary_source`, `ephem_plan` (ordered `kind:ref[start..end]@step`), `covers_now`, `last_data_utc`;
  - `elements` (epoch, centre, a, e, i, peri, apo, P);
  - `src_rows` (provenance `src:src_ref;…`), `notes`.
- **`catalog/MASTER.md`** (generated) is the review document. It groups and counts objects in the sort order above and has sections for the T4 exclusions with their reason, conflicts, and merge proposals.

---

## 5. Ephemeris pipeline

### 5.1 Uniform state table

Every adapter returns the same object, saved as `build/states/<cle_id>.npz` plus a JSON sidecar:

```
StateTable
  cle_id, centre ∈ {earth, moon}        # interpolation centre; lunar orbiters use moon
  segments: list of
     t0_utc (ISO), step_s (float) | epochs_utc_s (float64[n], irregular only if unavoidable)
     r_km float64[n,3], v_kms float64[n,3]          # ICRF axes, geometric, UTC-labelled
     source = "<kind>:<ref>"  (horizons:-170, fo:Spektr-RG@2026-10-04, bgtle:19040a25.tle, gp:44432, ...)
     quality ∈ {ref, pred, fit, extrap, gp, conic}   (§6.4)
     join_before ∈ {start, continuous, splice, gap, source}
     sigma_km float32[n] | null        # 1σ position uncertainty where the source provides it
  provenance: raw file paths + sha256, retrieved_utc, exact request/command, software versions
  validation: max midpoint error (km), splice list, Moon check, regime_now
```

**Rules**

- **Time** is UTC in skyfield (`load.timescale(builtin=True)`, as MMX does). Inputs in TT (from `fo` and `get_vect`) are converted on read.
- **Leap seconds:** none falls inside the window. Leap seconds are only inserted at the end of June or December *(memory)*, so a window that crosses 2026-12-31 must be checked against IERS Bulletin C.
- **Frames:**
  - Horizons: ICRF.
  - find_orb "J2000 equator": treated as ICRF. The frame bias is ≈ 23 mas *(memory)*, which is 0.11 km at 10⁶ km *(computed)*. This is documented, not corrected.
  - TEME (GP): rotated to GCRS by skyfield.
  - Bill Gray TLEs: `get_vect` precesses its SDP4 output back to J2000 with **precession only**, the exact inverse of how `eph2tle` fitted them. Skyfield TEME→GCRS is **not** applied to them (§5.5).

### 5.2 Plan and source resolver (`src/cle/plan.py`)

- For each T1/T2 target, the resolver builds `build/plan.json`, a list of non-overlapping intervals covering the window.
- Each interval takes the highest-priority source that covers it:

| priority | source | used for | coverage rule |
|---|---|---|---|
| 1 | **Horizons** vectors | spacecraft with agency SPKs, Horizons small bodies (`DES=`), Bill Gray objects hosted on Horizons | the coverage Horizons reports; the request is clipped to it (§5.3) |
| 2 | **neocpsat `fo` fit** | objects with optical astrometry and no Horizons coverage (Spektr-RG 2026, CE-6 orbiter, Chandrayaan-3 PM, ETBOs, …) | [first obs − pad, last obs + pad] as `fit`. Beyond that it is `extrap`, up to a staleness limit (§6.4). |
| 3 | **Bill Gray TLE** (`bgtle`) | Bill Gray objects inside their `# Range` (history, plus SMILE / C45GF81 now) | only inside `# Range` / `# Ephem range`; never outside |
| 4 | **NEOCP live `fo`** | unidentified NEOCP `(geo)` / `S` objects | arc ± pad as `fit`, then `extrap` until σ > 1° (§5.6) |
| 5 | **Space-Track GP** | catalogued objects with none of the above covering an interval | the TLE epoch closest before t; age flagged. **As built, a GP ≤ 30 d old ranks above the neocpsat fits (rank 2½), and one 30–120 d old ranks below them.** |
| 6 | **elements only** | the latest osculating elements in the extracts | not drawn in phase 1 (T3); phase 2 draws an indicative conic |

**Overrides and joins**

- `overrides.tsv` can force or forbid a source per object, e.g. preferring Bill Gray over a stale Horizons record.
- A change of source between intervals always starts a new segment (`join_before=source`). The trail is broken there, and the size of the position jump is recorded and shown.

### 5.3 Horizons adapter (`src/cle/adapters/horizons.py`, adapted from MMX `ephemeris.py`)

**Request**, exactly as MMX (`ephemeris.py:46-52`):

```
format=json, EPHEM_TYPE='VECTORS', CENTER='500@399' (or '500@301' for LUNAR_ORBIT),
REF_SYSTEM='ICRF', REF_PLANE='FRAME', TIME_TYPE='UT', VEC_CORR='NONE',
OUT_UNITS='KM-S', VEC_TABLE='2', CSV_FORMAT='YES', CAL_FORMAT='CAL', OBJ_DATA='NO'
COMMAND = verified id only:  '-170'  |  'DES=2024 PT5;'      (never bare names)
```

The endpoint is `https://ssd.jpl.nasa.gov/api/horizons.api`, called sequentially with ≥ 0.3 s between requests.

**Checks.** Keep MMX's `parse_response` (`:55-86`), with these changes:

- **Header check, generalised.**
  - **Lines that must be present:**
    - `Center body name: Earth (399)` (or `Moon (301)`);
    - `Reference frame : ICRF`;
    - `Output units    : KM-S`;
    - `GEOMETRIC cartesian states`;
    - `Calendar Date (UT`.
  - **The target line:** parse `Target body name: … (<id>)` and require it to equal the requested numeric ID. For `DES=` requests, require `(<designation>)`. The probe shows `Target body name: (2024 PT5)                      {source: JPL#70}` (`work/cache/arch_probe/smallbody_2024PT5.json`).
  - **Manifest:** store the `{source: …}` tag, e.g. `JWST_merged`, in the manifest. A change in the tag triggers re-validation.
- **Coverage clip instead of failure.**
  - A window past the SPK end returns `error: No ephemeris for target "CAPSTONE (spacecraft)" after A.D. 2026-AUG-14 11:57:50.8169 UT` (`arch_probe/capstone_past_end.json`).
  - Parse `(after|prior to) A.D. <date> UT` with a regex. The "prior to" form is *(memory)*.
  - Clip `start`/`stop` to the grid inside the coverage, re-request, and record the coverage in the plan. Coverage under 2 steps means no Horizons interval.
- **Request size.** Keep each request below 50,000 rows by chunking. Horizons caps output at ≈ 90k lines *(memory)*.
- **Validation:**
  - Every piece is fetched once at **step/2** (even samples = the grid, odd samples = MMX's midpoint check), so the Hermite check costs nothing and runs on every build, not only "on first fetch and weekly" (review EFF-4, 2026-10-22).
  - Require the Hermite midpoint error to be below tolerance: 1 km Earth-centred, 0.1 km Moon-centred.
- **Refine-or-break.** If a midpoint fails, refetch that interval at h/4.
  - If the error falls roughly as h⁴, the step was too coarse: keep the finer step for the interval.
  - If it does not, there is a discontinuity: a manoeuvre, an SPK arc join or an impact. Break the segment there (`join_before=splice`).
  - This generalises the expected-epoch splice test in MMX `transfer.py:113-118`: jump − 30 s·(v₀+v₁) > 1,000 km.
- **Moon-centred targets.** Geocentric = Moon-relative + DE440s Moon. A daily cross-check of Horizons 301 against DE440s must agree to < 1 km; MMX archived < 0.003 km (README:91).
- **Snapshots.**
  - Raw files go to `work/cache/horizons_eph/<UTC stamp>/` with `manifest.json`: `retrieved_utc`, the exact parameters, the URL, sha256 and per-request time.
  - **Tiles (EFF-4).** The grid is fetched as 10-day tiles on the 1970-aligned 00:00 UTC lattice at gcd(step/2, 3600) (3600 s for the 21600-s SE/helio targets, 1800/900/150 s for the others), clipped to the Horizons coverage; a request is served by stitching any cached responses of the same command/centre whose lattice contains its epochs (`Snapshots.serve`), which includes every AM2 hour-floored window lattice, so a moved window fetches only the tiles it lacks plus the refine/repair pieces at the new window edge. A 2-row `OBJ_DATA` **probe** per target per UTC day (TTL 20 h) hashes the object block into a solution id; tiles, refine and repair responses are tagged with it and responses of another solution are not used, so a re-delivered trajectory (`Revised:` date) refetches that target only. `validation.solution` (`id`, `note`, `checked_utc`) and `fetched` are recorded per target.
  - Rebuilds read the cache offline: tiles first; offline or down, a lattice served only in part is clipped to its longest cached run, an older solution's tiles stand in with the "Horizons snapshot of <date>" caveat, and a lattice the cache cannot serve at all is Hermite re-gridded from the newest overlapping pre-tile response (DW-04c). Only `make update-horizons` uses the network. `make prune` keeps the stamps `build/` references plus the two newest.
- **Quality.** Samples before `retrieved_utc` are `ref`; later samples are `pred`. Horizons does not mark the reconstruction/predict boundary in a machine-readable way *(memory)*, so retrieval time is the proxy.

### 5.4 `fo` adapter for neocpsat fits (`src/cle/adapters/fo.py`)

Spektr-RG is the motivating case. It has **no Horizons record** (`news/horizons_summary.tsv`: `not_found`).

- **Its history:**
  - Bill Gray TLEs `19040a*.tle` cover 2019-07-14 → 2026-01-01, as daily TLEs. The 2025 file reports "Worst residual 0.76 km".
  - neocpsat combined fits cover 2026-01-09 → 2026-10-13: `mpecs/Spektr-RG_combined_2026_{01_09,06_05,08_18,09_01,09_08,10_04}.html`. These are heliocentric J2000-ecliptic elements plus a heliocentric equatorial state vector. The latest is 60 obs from Oct 4–13, mean residual 0.31″.
- **Its next segment** must come from `fo`.

**Inputs**

- Obs per group: the union of `../neocpsat/data/<desig>.txt` over the group's designations, copied to `work/cache/neocpsat_fo/<canonical>/obs.txt` with sha256. The extractor has already done this for 9 groups.
- The sibling project is never written.

**Command.** It reuses neocpsat's flags (`correlate.py:386-399`) and adds a state-vector ephemeris. The option meanings were read from the source:

```
FO=/Users/mickey/Desktop/billgray/local/bin/fo
$FO obs.txt -c <canonical> -h 3 -O <fresh_dir> -i [-g 17 if arc ≥ 7 d] \
    -C 500 -E 0 -e <fresh_dir>/vec.txt -D <fresh_dir>/fo_env.dat
```

- **`-E 0`.** `parse_bit_string("0")` sets bit 0, so options = 1 = `OPTION_STATE_VECTOR_OUTPUT` (`cssfield.cpp:104`; `mpc_obs.h:116-118`; `ephem0.cpp:2249`). Note that `-E 1` would give *positions only*. neocpsat's `-E 5` is bit 5, motion output.
- **`-C 500`:** geocentric. **`-e`:** ephemeris file (`fo.cpp:592-598, 613-616`).
- **`fo_env.dat`:** a copy of `~/.find_orb/environ.dat` with overrides. This is neocpsat's `_build_ephem_env_file` pattern (`correlate.py:303-331`), because `-D` replaces environ.dat rather than merging it. The overrides:
  - `EPHEM_START=<JD>`;
  - `EPHEM_STEPS=<n> <step>`, e.g. `1441 1h`, read by `sscanf("%d %79s")` at `fo.cpp:997`;
  - `VECTOR_OPTS=0,149597870.7,86400`, giving equatorial J2000 in km and km/s (`environ.def:212-228`). The user's current environ.dat already has this value, but it is set explicitly so it does not depend on that.
- **Output format:**
  - Header `jd_start step n VECTOR_OPTS note` (`ephem0.cpp:2381-2386`).
  - Rows `JD x y z vx vy vz`, with the **JD in TT** (`timescale="y"`, forced TT).
  - **Geometric:** light time is applied only to the observables type (`ephem0.cpp` ≈ 2634-2641).
- **Isolation:**
  - Always use a fresh `-O` directory plus `-i` (ignore previous solutions). Without them, `fo` reuses `~/.find_orb` solutions (neocpsat CLAUDE.md gotcha). Today, `~/.find_orb/elements.json` shows a run from 01:48 that did not use `-O`.
  - `fo` does not call `store_defaults`, so it does not rewrite environ.dat. Only `findorb.cpp:6838` and `orbitdlg.cpp:1664` do.
  - Runs are **serialised between processes by a lock file** (`work/fo.lock`), and never overlap a neocpsat scan. Inside one process the lock is re-entrant (`fo.locked()`): the legs and one-day chunks of one fit run through a `ThreadPoolExecutor(8)` under a single hold (review EFF-1), so the hourly and the daily still take turns while one fit's 30 one-day runs finish in ~3 s instead of ~21 s. `cli.states` therefore builds every fo/neocp target in one worker process, in order.
  - Every run passes `-y 10`: fo's automatic fit stops after four full-improvement steps *or* a quarter second of wall time (`elem_out.cpp`), so without fixed steps a long fit (Falcon Heavy, 313 obs) lands in one of several minima ~300 km apart depending on machine load. With them a rerun repeats to ~0.05 km; NEOCP-class fits, whose automatic steps finish in time, stay bitwise identical. Run labels are shortened to cid + 8-char sha where the `-O` path would exceed fo's 93-character limit (`fo.short`). Probes, legs, one-day chunks and short tables are scratch runs: fo's side files (`total.json`, `elements.json`, `covar.txt`, … ≈ 160 KB a run, nothing reads them) are deleted right after the run, so a full `states` stamp is ≈ 54 MB instead of ≈ 104 MB; the window and `-E 16` tables keep them. An extrapolation's epoch fit is labelled `<cid>_epoch`, never by the page alone: `cli.states`' pool workers share one stamp and `run()` replaces an existing run directory.
- **Uncertainty.** A second run with `-E 16` (observables with sigmas, `-C 500`) gives σ in arcseconds per step. σ_km = σ″ × Δ / 206265 is stored as `sigma_km`.
- **Acceptance**, reusing neocpsat's criteria:
  - ≥ 70 % of obs used;
  - mean residual < 5″;
  - a, e and i reproduce the neocpsat page within 1σ (regression test).
- **History.**
  - Each historical fit page becomes its own `fit` segment over [first obs − 2 d, last obs + 2 d].
  - The latest fit continues as `extrap` to now + 30 d. Staleness is flagged (§6.4) because Spektr-RG station-keeps.
  - Gaps between fits stay gaps. They are never bridged by interpolation.

### 5.5 Bill Gray TLE adapter (`src/cle/adapters/bgtle.py`)

- **Which TLEs are valid.** The directives follow sat_id semantics, as already implemented in `scripts/extract_billgray.py` (§1 of its docstring):
  - `# Range:` applies to the next `# Include` only;
  - `# ID:` persists;
  - indented lines are comments.
- **Which TLE to use at time t.** Use the TLE whose `# MJD m` header satisfies `m ≤ t < m + step`, where `step` comes from `# Ephem range: start end step` (`sat_id.cpp:1301-1339`).
  - The MJD header is in **TT**. Example: `# MJD 61133.000000 (2026 Apr 3 0:00)` goes with TLE epoch `26092.99919926` UTC, which is 69.18 s earlier *(computed)*.
  - Only times inside `# Range` are used.
- **Evaluation.**
  - Bill Gray's `get_vect` (`~/Desktop/billgray/sat_code/get_vect`) handles classical and hex/SDP4-high TLEs. Its output is precessed to J2000.
  - `-t` must be **TT** (bill-gray skill pitfall #1). `get_vect` compares it with the TLE epoch converted to TDT (`get_vect.cpp:94-101`).
- **Frame: SDP4 "TEME" → ICRF.** The inverse must match how Bill Gray fitted the TLEs, not the textbook TEME→GCRS.
  - `eph2tle` precesses its J2000 input ephemeris to the mean equator of date with **precession only, no nutation** before fitting SDP4 (`find_orb/eph2tle.cpp:542-546, 846-852`). Its own comment says nutation might belong there.
  - `get_vect` undoes exactly that: `setup_precession` + `precess_vector` from date to 2000 (`sat_code/get_vect.cpp:114-117`; `-d` would print of-date instead).
  - So `get_vect` output is taken as J2000 ≈ ICRF (the 23 mas bias of §5.1). The UTC vs TT precession epoch (`eph2tle` uses UTC, `get_vect` TT) differs by ≈ 1e-4″ *(computed)*.
  - **Skyfield TEME→GCRS is never applied to Bill Gray TLEs.** It would add the nutation rotation √(Δε² + (Δψ·sin ε)²), which is **8.67″ on 2026-10-21** (IAU 2000A *(computed)*). That is 16 km at lunar distance and 63 km at 1.5e6 km: a spurious frame error.
  - Space-Track GP TLEs, which are true TEME, do get skyfield TEME→GCRS (§5.7). §14 test 3 checks both conventions.
  - Parse the `# Ctr 3 km sec` lines, as `extract_billgray.py:407-421` does.
  - Cache keyed by TLE lines plus JD (`work/cache/billgray/derived/get_vect_cache.json` already exists).
- **Cost and joins.** One process per state: 1,441 calls per object-month at 8 threads, a few seconds *(memory: process-start cost)*. Phase 2 can add a batch helper linked against `libsatell.a` if profiling needs it. The jump between adjacent TLEs at a boundary is ≤ the file's "Worst residual" and is recorded. A jump > 50 km becomes a splice.
- **Python sgp4 is not used** for Bill Gray files. It cannot read the hex format, and Bill Gray fits his TLEs with his own SDP4 variant.

### 5.6 NEOCP live adapter (`src/cle/adapters/neocp.py`, wraps `scripts/neocp_live.py`)

- **Selection:**
  - Bill Gray's summary `https://www.projectpluto.com/neocp2/summary.htm`, where `(geo)` in the MOID column means find_orb preferred a geocentric fit;
  - the MPC `neocp.txt` `S` flag, parsed with `../neocpsat/scraper.parse_neocp_line`, imported read-only.
- **Fetch:**
  - the pseudo-MPEC `neocp2/mpecs/<desig>.htm` (needs a browser-like UA, per the neocpsat gotcha);
  - 80-column obs from the Pluto page and the MPC NEOCP obs API, merged into `work/cache/neocp/obs/<desig>.txt`.
- **What the pseudo-MPEC supplies.** `parse_mpec` (`scripts/neocp_live.py:472-690`) already writes these into `work/cache/neocp/parsed.json`:
  - the elements block: epoch (TT); M, n, a, e, i, Ω and ω with 1σ; q, Q and P; "From N observations …; mean residual";
  - the find_orb version (`2026 Mar 01`) and the perturbers;
  - `State vector relative to central body` (AU, mAU/d), and the heliocentric state vector;
  - TLE comment lines. Today only the bound geocentric page carries them: C46PP91, `2 00000 035.4147 012.1194 6066904 …`;
  - the 48-row hourly "Ephemerides (geocentric)" table: RA, Dec, Δ, σ″ and PA, plus derived `xyz_km`.
- **How it is used:**
  1. **Readout:** the elements with 1σ, the GEO score and the find_orb version go into `fit` (§11).
  2. **Cross-check** of our `fo` run.
     - At the page epoch, our elements must agree with the page's within its quoted 1σ. Our central-body state vector is compared with the page's and the difference is recorded.
     - Our positions must match the 48-h table within the table's σ.
     - On failure, the object is flagged `fit_disagrees` and the page solution is used.
  3. **Fallback**, when our `fo` run fails acceptance or the astrometry is withheld or redacted (the `astrometry_withheld` and `astrometry_redacted` flags):
     - first, the page TLE through `get_vect` (§5.5): `fit` over the arc, `extrap` after it;
     - otherwise, the table itself as positions (`xyz_km`) with spline velocities: `quality=extrap`, σ from the table.
     - The table is astrometric (light-time) rather than geometric. The difference, v·Δ/c, is ≈ 1 km at Δ = 0.002 AU *(computed)*, far below its σ.
  - The σ rule below still applies. C46PP91 (8 obs over 56.4 min) already has σ = 208,800″ (58°) in the first table row, so it is drawn only near its arc.
- **Fit.** Run `fo` as in §5.4, with no `-c` and `-g 17` only if arc ≥ 7 d.
  - Step: 10 min over [first obs − 1 d, last obs + 1 d], 1 h beyond that.
  - Span: [first obs − 2 d, now + 7 d], and only while σ < 1°. Past that the object is drawn up to the σ limit, then the trail stops with a "σ > 1°" tag.
- **Identification.** If Bill Gray's identification text or a neocpsat correlation ties a designation to a master object (e.g. a new Spektr-RG apparition), the tracklet attaches to **that** object and no new target is created.
- **Lifecycle.** An object that leaves the NEOCP stays 7 d in a "recently removed" sublist with its last fit (`work/cache/neocp/history.json`). `neocp_live.py` writes `listed_now` (Y/N: in the current summary or neocp.txt) and `last_listed` (regeneration stamp of its last listing) as the trailing columns of `neocp.tsv` (`catalog/sources/SCHEMA.md`); `build_master.py` turns `listed_now=N` into `tier_reason` "left the NEOCP <date>" and `left_neocp` (ISO date), which the list shows as a "left NEOCP <date>" chip. `parsed.json` keeps the `removed` set (§11 `neocp.js`).
- **Hyperbolic geocentric fits.** Five of today's 12 rows have e > 1 (e.g. 8NIQ001, e = 7.68, perigee 7,487 km on 2026-10-15). These are probably natural flybys.
  - They are kept in a `NEOCP_GEO · flyby` subgroup, **default hidden**.
  - Bound fits (e < 1) are default visible. See open question Q3.

### 5.7 Space-Track GP adapter (`src/cle/adapters/gp.py`)

- **Fetch** through the space-track skill CLI (`~/.claude/skills/space-track/st.py`: stored login, rate limits, disk cache). Never a new login script.
  - Query `gp_history` for each NORAD over [window start − 30 d, now].
  - Plus the current `gp`.
  - Cache in `work/cache/spacetrack_gp/<stamp>/`.
- **Propagation:** skyfield `EarthSatellite`, which runs sgp4/SDP4 and converts TEME to GCRS. The TLE used at t is the latest epoch ≤ t; before the first epoch, use the first.
- **Exception for CR3BP regimes** (`EM_DRO`, `EM_L1`, `EM_L2`, `EM_OTHER`): SDP4 gives only the state at the GP epoch. That state is converted to GCRS and integrated with Earth + Moon + Sun point masses (DE440 Moon/Sun via the JPLDE kernels), with RK8 or DOP853 and a 1-min output re-sampled to the regime step. This applies to DRO-A and DRO-B.
- **Placeholder sets.** Space-Track carries fresh-epoch, LEO-period GP sets for deep-space objects it no longer tracks (EQUULEUS, ArgoMoon, the Artemis I cubesats). Any GP with P < 1 d for an object outside LEO is rejected.
- **Quality:**
  - The segment breaks at each TLE switch, and the switch jump is recorded. The readout shows it as "GP consistency".
  - `quality=gp`, with `age = |t − epoch|`.
  - Lunar-perturbed HEO through SDP4 can be off by tens to hundreds of km per day from epoch *(memory)*, hence the lowest analytic priority.
- **Licence.** Space-Track terms restrict redistribution *(memory)*. GP-derived tracks are for this local page only. If the page is ever shared, GP segments must be dropped (risk R9).

### 5.8 Elements-only targets

- **Phase 1:** a list entry only (T3), showing "last known elements (epoch, source)" and the reason there is no track.
- **Phase 2:** a dotted osculating conic. Restore `conicPoints` from MMX `78bd4e7:web/states.js` line 92, together with the in-place Line2 rewrite at `78bd4e7:web/app.js:325-336`. It is labelled "indicative" and has no moving marker.

### 5.9 Context bodies (`src/cle/context.py`, from MMX `ephemeris.py:115-128`)

- **Moon and Sun:** `(de440s[body] − de440s['earth']).at(t)` from `~/JPLDE/de440s.bsp`. The kernel is never copied (JPLDE convention). Both are on the core **1 h grid** of 1,441 samples.
  - Moon Hermite error at 1 h ≈ 1e-5 km *(computed)*.
- **Earth orientation:** Skyfield `itrs.rotation_at(t)`, transposed, conjugated by `MAPPING`, converted to xyzw and sign-aligned. It goes on an **independent 1 h track**: 1,441 quaternions for 60 d.
  - Slerp error is 0.0002″ at 1 h and 0.0062″ at 6 h (`scripts/check_earth_rotation_sampling.py`).
  - The step stays < 12 h because slerp takes the short arc.
  - Polar motion is omitted, as in MMX.

---

## 6. Time model, sampling and quality

### 6.1 Window

- **The data window** is [now − 30 d, now + 30 d], aligned to 00:00 UTC. The daily build moves it forward one day.
- **Grid alignment.** All grids are aligned to the window start t₀ = `2026-09-21T00:00:00Z` (`t0_ms = 1789948800000` *(computed)*). This allows direct indexing.
- **Page clock:**
  - **LIVE**, the default, follows the wall clock while it is inside the window.
  - Within 1 d of the window end, a banner says "data window ends — run `make daily`".
  - Outside the window, time clamps (no extrapolation, as MMX `states.js:20`).

### 6.2 Step per regime, plus automatic refinement

The base steps are below. **Refinement rule:** on a coarse pass, compute the local angular rate ω = |r × v| / r² about the interpolation centre. Wherever h·ω > 0.3, re-sample that interval with h = max(60 s, 0.3/ω).

- The Hermite bound r·(ωh)⁴/384 is then ≈ 2.1e-5·r. At a 6,600 km perigee this is ≈ 0.14 km *(computed)*.
- Refined spans are separate uniform sub-segments, following MMX `transfer.py:26-31`.

| regime | centre | base step | samples per 60 d | dtype | notes |
|---|---|---:|---:|---|---|
| `LUNAR_ORBIT` | Moon | 5 min | 17,281 | f32 | LLO with P = 2 h: bound ≈ 22 m *(computed)*. f32 ulp ≤ 4 m below 65,536 km. |
| `EM_L1`, `EM_L2` (incl. NRHO) | Earth | 30 min | 2,881 | f32 | NRHO perilune is refined by the ω rule. |
| `EM_DRO`, `EM_OTHER`, `TRANSLUNAR`, `CISLUNAR_HEO`, `HEO` | Earth | 1 h | 1,441 | f32 | low perigees are refined by the ω rule |
| `SE_L1`, `SE_L2` | Earth | 6 h | 241 | **f64** | halo bound ≈ 2e-5 km *(computed)*. f64 because r > 10⁶ km. |
| `EARTH_NEAR_HELIO` | Earth | 6 h | 241 | **f64** | Earth flybys are refined |
| `NEOCP_GEO` | Earth | 10 min around obs, 1 h otherwise | ≈ 1,000 | f32 | span limited by σ (§5.6) |
| context: Moon, Sun, Earth quaternions | Earth | 1 h | 1,441 | f64 | |

**dtype rule:** f64 if max |r| > 1.0e6 km, otherwise f32. The f32 ulp is 0.03 km at 4e5 km and 0.125 km at 1.5e6 km *(computed)*.

### 6.3 History chunks (optional, phase 1b)

- Selected targets get `history-<id>.js` files at a 1-day step, with the same segment and quality format.
  - **Spektr-RG:** Bill Gray 2019-07-14 → 2026-01-01, then the neocpsat fits 2026-01-09 → now. The gap is kept.
  - **Candidates:** Spektr-R, the Chang'e-6 orbiter, CE-3 R/B and the TESS P/2 history (Q11).
- The time slider gets a "History" mode that loads these chunks.
- Outside the 1 h quaternion window, Earth rotation uses ERA + IAU-76 precession computed in JS. This is cosmetic, < 0.01° *(memory)*.

### 6.4 Quality and staleness

| class | meaning | trail style | marker |
|---|---|---|---|
| `ref` | Horizons, before retrieval time | solid | filled |
| `pred` | Horizons predict, after retrieval | dashed future | filled |
| `fit` | `fo` / Bill Gray TLE inside the data arc or Range | solid | filled |
| `extrap` | `fo` beyond last obs + 2 d | dotted | hollow, 60 % alpha |
| `gp` | Space-Track GP | solid at 68 % alpha; dashed future | filled, 82 % alpha |
| `conic` | elements only (phase 2) | dotted | none |

**Stale** forces a hollow marker at 60 % alpha (review V4; 40 % before) and a ⚠ in the list. It applies in any of these cases:

- the catalog's own flag, `staleness.flag` in `catalog.js` (one wording for TARGETS.md, `validation_report.md` and
  the page; `build_master.judge()` decides): `gp-stale` for a Space-Track GP primary 30–120 d old (≤ 30 d is
  `covers_window` Y, 30–120 d `stale-gp` with the caveat "stale: Space-Track GP N d old", > 120 d unusable → latest-orbit
  extrapolation), `extrap` for a `STALE …` primary (AM5); `staleness.age_d` counts from the primary's end date;
- `fo` extrapolation > 30 d or > 3 × arc;
- NEOCP σ > 1°;
- a Horizons snapshot older than 3 d;
- a `neocp.js` whose `neocp_snapshot_utc` is older than 3 h. This means the hourly job has stopped. Every NEOCP object is marked stale and the Situation panel's NEOCP section opens with a ⚠ line (not the scene banner, review V11).

The readout always shows: source, quality, age (now − last obs / TLE epoch / Horizons retrieval) and σ when known.

---

## 7. NEOCP tracklets in 3D (`src/cle/tracklets.py` → `neocp.js`)

For each observation (80-column line):

1. **Station position.**
   - Read the parallax constants (λ, ρcosφ′, ρsinφ′) from `~/.find_orb/ObsCodes.htm`, the same file `fo` uses (dated 2026-10-04).
   - Compute the ITRS position = 6378.137 km × constants, then convert to GCRS with skyfield at the obs UTC (cols 16-32). This uses the globe's Earth-orientation model.
   - **Satellite observers:** the `S`/`s` line pair gives the geocentric vector directly.
   - **Roving observers:** the `V`/`v` pair gives longitude, latitude and altitude.
2. **Line of sight:** û(α, δ) from the J2000 astrometric RA/Dec.
3. **Range:**
   - Iterate ρ = |r(t − ρ/c) − R_stn(t)| twice, where r comes from the object's fitted StateTable.
   - Dot position = R_stn + ρ·û. It is tagged with the epoch t − ρ/c.
   - The perpendicular offset from the trail is ρ × residual: 1″ at 10⁶ km ≈ 4.8 km *(computed)*.
4. **Residuals.** Store O − C in arcseconds per obs. A test checks that the RMS reproduces `fo`'s reported mean residual.
5. **Rendering:**
   - 3 px dots coloured by station;
   - one hairline per tracklet (station + night);
   - **sight lines** (toggle, off by default): faint segments from the station to the dot.
   - A tracklet (dots, hairline, sight lines) is drawn only inside its target's drawn trail window [t − past, t + future]
     (the texel `trails.restyle()` wrote: idle dim, EM-frame far cap, hover, selection), and in the EM frame not at all
     for SE_L1 / SE_L2 / EARTH_NEAR_HELIO targets unless selected, hovered or focused, so no bead floats where no trail is
     drawn (`TrackletLayer.update(…, t, windowOf)`; per target the tracklets are sorted by epoch and drawn as one range).
   - The same layer is optional for the history of catalogued objects, e.g. the Spektr-RG astrometry in `../neocpsat/data/`.

---

## 8. Frames

- **Default ECI, identical to MMX:**
  - ICRF axes, origin at the geocentre (or the focus body, §9.2);
  - scene = `(x, z, −y)/R⊕`, so ICRF +Z (celestial north) is scene +Y;
  - geometric states;
  - RA/Dec = `atan2(y,x)`, `atan2(z, hypot(x,y))` (`states.js:13-17`).
- **Phase 2, Earth–Moon rotating (synodic) view:**
  - Basis: x̂ = r☾/|r☾|, ẑ = (r☾ × v☾)/|…|, ŷ = ẑ × x̂.
  - Optional pulsating normalisation by |r☾(t)|, giving CR3BP units.
  - Each trail vertex is transformed with the basis **at its own time**. This is done on the CPU when you switch modes: 400 × 1,441 vertices takes ≈ ms *(memory: typical JS throughput)*.
  - Markers transform per frame.
  - Lagrange markers sit at the CR3BP collinear points, γ_L1 = 0.15093 and γ_L2 = 0.16783 from the Moon for μ = 0.0121506, which is L1 ≈ 326,400 km and L2 ≈ 448,900 km from Earth at 384,400 km *(computed)*. L4 and L5 are ±60°.
- **Phase 2, Sun–Earth rotating view:**
  - Basis: x̂ = −r☉/|r☉| (anti-Sun, so L2 is on +x), ẑ = normal of Earth's orbit, taken from the Sun's geocentric state.
  - L1 and L2 sit at 1.4976e6 km and 1.5077e6 km from Earth (μ = 3.0404e-6 *(computed)*).
- The **reference points layer** shows EM L1/L2/L4/L5 and SE L1/L2 as small crosses. It is computed in JS from the Moon and Sun tracks, is on by default at low opacity, and is visible in ECI too.

---

## 9. Rendering

### 9.1 Scale, depth and camera

- **Scene unit:** 1 R⊕ = 6378.137 km, kept from MMX. The renderer uses `WebGLRenderer({ logarithmicDepthBuffer: true })`.
  - The vendored `LineMaterial` already includes the `logdepthbuf` chunks (`addons/lines/LineMaterial.js:34,236,280,392`).
  - Every custom ShaderMaterial in the main scene must include them too.
  - The star scene is unaffected: it has its own camera, and depth is cleared after it.
- **Camera:**
  - `PerspectiveCamera(38°, near 1e-4 R⊕ (0.64 km), far 1e5 R⊕)`;
  - `minDistance` = 1.02 × the focus radius (Earth 1, Moon 0.2724, target 2e-4);
  - `maxDistance` = 6,000 R⊕ (3.8e7 km), which frames the 0.05 AU returners.
- **Why log depth.** With a linear 24-bit depth buffer, the depth step is 690 km at the Moon and 10,500 km at SE-L2 (`mmx_reuse.md` §7.1). Lunar tracks would z-fight the Moon without it.
- **Fallback:** a dynamic near plane = 0.1 × the distance to the nearest visible surface. Re-check the terminator lift ×1.0002 under log depth.

### 9.2 Precision: floating origin

- **On the CPU**, all positions are float64.
- **Each frame:** origin = the focus position f(t). Earth, the Moon, markers, labels and Lagrange points get `position = p − f`.
- **Trail batches** keep float32 vertices relative to their anchor (Earth, or the Moon for lunar trails). `group.position = anchor − f` is set on the CPU in float64.
- **The focused target's own trail** gets a dedicated anchor at its current position, so it stays jitter-free when zoomed to about 1 km.

### 9.3 Markers

- **One `THREE.Points`** for all targets (`web/scene/markers.js`).
  - Per-vertex attributes: colour (regime family), size, shape id and a hollow flag, alpha.
  - A shared SDF sprite shader with the logdepth chunks and the 1.6 px `#04060c` halo.
  - Screen-space size (review V4): 8.5 px idle, 10 px hovered, 12 px selected; selection and hover rings 21 / 18 px.
    Proportions in r = size/2: square half-side and diamond bound 0.96 r, triangle inradius 0.78 r (the circle's area),
    so the outlines differ by ≥ 1.5 px; the sprite is 1.66·size + 3.4 px so corners and halo fit.
- **Shapes** encode `obj_type`: payload ● circle, rocket body ■ square, debris and unknown ◆ diamond, natural ▲ triangle.
- **Fill** encodes the data (§6.4, AM3): hollow = not from a current ephemeris (extrapolated, or stale at 60 % alpha);
  GP fills at 82 %; derelicts at 70 % of that (a GP derelict 57 %); hovered and selected markers are opaque.
- **Hover and pick:** the nearest projected anchor within 12 px, the same screen-space test as MMX's place picking;
  then the nearest drawn trail segment within 6 px (≈ 1 ms over 360,656 segments). Hidden glyphs are not pickable.
- **Earth and the Moon** are drawn as point sprites when they are under 2 px across (MMX `addPlanet`). Earth from 2 to 8 px
  gets an ink ring of 2·r + 6 px at 80 % (3 px outside the limb), so the opening view's 5 px Earth (2.4 px on a phone) is
  not smaller than the 8.5 px markers around it; the Earth chevron is ink too (ΔE 36 from the lunar blue).
- **Regime palette** (review V3, validated on `#04060c` by `tests/palette.test.mjs`: chroma ≥ 0.1, list-adjacent
  ΔE ≥ 15 normal and ≥ 13 under protan/deutan simulation, ≥ 8 from the Sun, star and Lagrange colours). The colours
  live in `src/cle/catalog.py` `FAMILY` and reach the page through `catalog.js` (`web/model.js` keeps a fallback copy):

| family | regimes | colour |
|---|---|---|
| lunar | LUNAR_ORBIT | `#4f81ff` |
| Earth–Moon | EM_L1, EM_L2, EM_DRO, EM_OTHER, TRANSLUNAR | `#d1a9ff` |
| HEO | CISLUNAR_HEO, HEO | `#4db99a` |
| SE-L1 | SE_L1 | `#e4bb1b` |
| SE-L2 | SE_L2 | `#e66c04` |
| heliocentric | EARTH_NEAR_HELIO | `#d13ec7` |
| NEOCP | NEOCP_GEO | `#d15a62` |
| other | OTHER | `#90a4ae` |

- **Status and reference colours** are `web/theme.css` tokens (the template keeps no colours; JS reads them through
  `web/ui/theme.js token()`): `--warn #fcc598` (apricot, not a yellow), `--live #a1d4fc` (red is no status),
  `--lagrange #ffe9a6`, star core `#d6d6d6` (pure grey), `--sun #ffd54f` and `--cyan #4fc3f7` (fixed convention),
  `--hairline #e8eaed` (ink: 16.6 ΔE from `--sun`, so the legend no longer shows two yellows) and a six-colour station
  bead cycle without it.

### 9.4 Trails

- **Storage:**
  - All targets share **one batched `LineSegments2`** per anchor (Earth, Moon).
  - Its instanced attributes are `instanceStart`, `instanceEnd` (f32×3), `tStart`, `tEnd` (f32 s since t₀; ulp 0.5 s), and `targetIndex`.
  - A forked `LineMaterial` (`web/scene/trailMaterial.js`, derived from the vendored r170 file, logdepth intact) samples a per-target `DataTexture` (RGBA32F; row 0 colour + visible, row 1 pastLen, futureLen, highlight, alpha gain).
  - The material then computes: hidden outside [uNow − pastLen, uNow + futureLen]; alpha fading with age in the past; thin and dashed in the future.
  - **Dashes are screen-space** (review C1): each segment works out its own pixels per scene unit (depth and viewing angle), rounded to a half-octave step, and the dash follows the arc length along the 3D trail: a 10 px cycle (`DASH_PX`; 8.4–11.9 px measured in every preset) instead of a period in time that was sub-pixel in the wide view. Extrapolated spans are dots on a [0.5, 0.25] pattern (≈ 5 px).
  - **Frame switch** (EFF-10): sub-samples use `frames.matrixNear` (one exact R per native sample advanced by ω·δ), so an EM or SE rebuild makes 98,044 instead of 468,780 matrix evaluations (≈ 110 → 82 ms); AM1 holds to 0.1 km in SE and 7.4e-5 of r in EM.
  - **Toggling visibility or changing trail length updates one texel. No geometry is rebuilt.**
- **Head segment:** one small dynamic segment set (N segments, 9.6 KB per frame at N = 400) runs from the last native sample to the interpolated current position.
- **Breaks:** segments are never connected across `splice`, `gap` or `source` joins.
- **Default lengths:**

| regime | past | future |
|---|---|---|
| LUNAR (Moon-anchored) | 1 orbital period (≤ 24 h) | 0.25 period |
| EM_* | 14 d | 7 d |
| TRANSLUNAR | full past window | 3 d |
| CISLUNAR_HEO / HEO | 0.5 period, ≤ 7 d (idle 0.25 period) | 0.25 period, ≤ 1.5 d |
| SE_L1/L2, EARTH_NEAR_HELIO | 30 d | 30 d |
| NEOCP | first obs → now | to the σ limit |
| Moon | 14.75 d (7 d in the EM frame) | 14.75 d (off in the EM frame): one synodic month, closed in the SE frame |
| selected target | full window | full window |

- **Emphasis** (review V2, V8): while nothing is selected the CISLUNAR_HEO / HEO trails idle at 0.25 period and 45 %
  alpha and derelict trails at 70 %; pointing at a marker or a trail (`trails.setHover`) gives that orbit its full
  length, peak alpha and the wider highlight line. A selection draws the whole window and dims every other trail to
  30 %. In the EM frame the SE_L1 / SE_L2 / EARTH_NEAR_HELIO trails are capped at 0.25 d back and 0.1 d ahead
  (`trails.setFarCap`), unless hovered or selected; the SE-frame halo and return arcs keep their 30 d.

- **Lunar-orbiter trails** are anchored to the **current** Moon. They show the orbit about the Moon, as NASA Eyes does *(memory)*, not a geocentric helix.

### 9.5 Labels

- **Style:** DOM labels in Menlo 11 px with a double text-shadow halo, as MMX.
- **Priority order:** focus > selected > hovered > pinned > tier/obj_type rank > nearer screen depth.
- **Cap:** 40 visible labels (16 on a phone); Lagrange keys are outside the cap.
- **Markers stay visible:** every on-screen marker reserves an 8 px soft box. An ordinary label that finds no clear
  offset is dropped; an always-on label (Earth, the Moon, the frame's own L points, focus, selection) tries a second ring
  of offsets and is then forced to the one that covers the fewest boxes, never over the hard boxes (title, legend, chips,
  scale bar, Sun indicator, edge chevrons).
- **Collisions:** a screen grid hash replaces the O(N²) `occupied.some` loop (`app.js:378`). Sizes are cached once; this also fixes the `sky.js:96-97` layout thrash.
- **Occlusion:** labels are hidden behind Earth **and the Moon**. MMX checks Earth only (`app.js:367-370`).
- **Sky labels** are placed after body labels and always yield to them.

### 9.6 Earth, Sun and terminator

- **Earth:**
  - `SphereGeometry(1,128,64)` + `MeshPhongMaterial({shininess 5, specular 0x151b22})`;
  - ambient light 0.22 and a directional "Sun" light of 2.3 at `vector(sun)`, as MMX `app.js:257,272,337`.
  - Night lights (review C8, 2026-10-23): NASA Earth Observatory city lights, 2400×1200 (0.23 MB, via SatObserver-MX), as an `emissiveMap` faded out across the terminator (Sun 3°–12° below the horizon) by a shader patch; with it the ambient drops to 0.10.
- **Texture:**
  - **Source:** your default `~/sda/basemaps/world.topo.bathy.200409.3x21600x10800.jpg`, falling back to the satobserver 5400×2700. Downsampled with Pillow LANCZOS to **4096×2048**, JPEG q 85.
  - **Size:** ≈ 1.2 MB *(computed from the 5400 source)*, inlined as a `data:` URI on an `<img>` and wrapped with `THREE.Texture`.
  - **Why inline:** a `file://` image is cross-origin for WebGL *(memory)*, so it cannot be a sidecar file.
  - The source path and sha256 go in the provenance.
- **Terminator:** the finite-distance geometric horizon (MMX `states.js:43-50`), a Line2 in `#ffd54f`, 1.2 px, opacity 0.55, lifted ×1.0002.
  - Optional Sun −12° ring: a small circle 102° from the subsolar point, `#4fc3f7`, off by default.
- **Sun:** no mesh. A small Sun glyph sits in the **sky scene** at the Sun's direction, so it never competes with depth. A screen-edge arrow appears when the Sun is off-screen.

### 9.7 Moon

- A `SphereGeometry(1737.4 km)` with grey Lambert `#c1c7ce` (MMX), lit by the same light, so the phase is correct.
- Orientation: `moon_pa_de421_1900-2050.bpc` with `moon_080317.tf` from `~/JPLDE`, needed only once a texture is added.
- No global lunar albedo map exists locally; `~/JPLDE/lola` has only the south-pole DEM (Q5).

### 9.8 Stars: real but subdued (exact settings)

The sky scene is copied from MMX: it has its own scene and camera, follows the camera rotation only, and is drawn first with a depth clear (`sky.js:79-80`, `app.js:401`). All 9,827 Hipparcos stars with V < 6.6 are kept (J1991.25, no proper motion).

| item | MMX | **cislunar-eye** |
|---|---|---|
| point area (`sky-style.js:8`) | 0.55·max(0, 6.9−V)^2.3 pt² | **0.30·max(0, 6.9−V)^2.0 pt²** |
| min diameter (`:9`) | 0.65 px | **0.6 px** |
| opacity (`:12`) | clamp(0.25 + 0.115(6.6−V), 0.25, 1) | **clamp(0.12 + 0.075(6.6−V), 0.12, 0.60)** |
| bright-star rings (`:13`) | V < 1.6 → ≥ 14 px | **off** |
| core colour (`sky.js:48`) | vec3(1.0) | **vec3(0.80, 0.84, 0.90)**, plus a `uStarGain` uniform (default 1.0; UI slider 0–1.5; 0 hides stars) |
| constellation lines (`sky.js:61`) | opacity 0.6, on | **opacity 0.20, off by default** |
| RA/Dec grid (`sky.js:70`) | opacity 0.8, 15°/15°, 264 labels, on | **opacity 0.32, 30°/30° (60 labels), off by default** |
| star names | 11 px `#b9c4d8`, tied to the stars toggle | **9 px `#818e9e`, own toggle, off** |
| constellation names | 13 px `#5a7bb8` | **10 px, opacity 0.6, off** |
| background | `#04060c` | `#04060c` (unchanged) |

Resulting star sizes and opacities *(computed, `mmx_reuse.md` §2.5)*. Magnitude order is preserved.

| V | size | opacity |
|---:|---:|---:|
| −1.44 | 6.1 px | 0.60 |
| 0 | 5.0 px | 0.60 |
| 3 | 2.9 px | 0.39 |
| 5 | 1.4 px | 0.24 |
| 6 | 0.66 px | 0.17 |

These constants need new tests, because MMX's tests pin the old ones.

---

## 10. User interface

- **Layout:** the MMX workspace grid `--left-w | 1fr | --right-w`, with the left panel at **300 px** and the right at **264 px**.
  - The 44 px title bar holds "Cislunar Eye", the UTC clock, the left and right panel toggles, and a layers popover.
  - At ≤ 980 px the panels become overlays; at ≤ 700 px the right panel does too. This is MMX's breakpoint logic.
- **Target list (left):**
  - a search box matching name, alias, NORAD, COSPAR, Horizons ID and temp designation;
  - filter chips for regime, status, obj_type, source and tier;
  - group headers with counts (visible/total) and a group eye toggle;
  - one row per target: colour swatch, label, status glyph, range now (km below 100,000 km, LD below 1 Mkm, Mkm beyond), source badge (H, F, T, N, G), age and a ⚠ if stale.
  - Click selects; double-click or ⏎ focuses. `[` and `]` step through the visible list.
- **Readouts (right) for the selected target:**
  - **Identity:** name, aliases, NORAD, COSPAR, Horizons ID, group, obj_type, status (with date), regime / regime_now.
  - **State:** UTC; geocentric range (km, R⊕, LD); speed; geocentric RA/Dec (ICRF, geometric); Moon distance; Sun–Earth–object angle; selenocentric range and speed when the target is within 66,000 km of the Moon.
  - **Elements:** osculating a, e, i, perigee/apogee (Earth- or Moon-centred, following the regime), P and C3, from `states.js osculating()`. Phase 2 adds the EM Jacobi constant.
  - **Data:** source per segment, with a mini coverage bar that marks now, segment joins and jumps; quality; age; σ; links to Horizons, the Pluto MPEC and the neocpsat page; notes.
- **Situation panel** (nothing selected):
  - counts per regime (live / window / catalogued);
  - the NEOCP list with first-seen times;
  - targets whose source ended within ±7 d;
  - stale-data warnings.
- **Time bar:**
  - **LIVE** (wall clock, 1×), play/pause, reverse;
  - rates 1×, 1 min/s, 10 min/s, 1 h/s, 6 h/s, 1 d/s;
  - a slider over the window with ticks at now, segment joins and NEOCP observation epochs;
  - ±1 h and ±1 d steps.
  - Playback dt is capped at 0.1 s per frame and the page pauses when hidden (MMX).
- **Focus and views:**
  - **Fly-to:** a 1.0 s ease-in-out tween of the focus point that keeps the camera direction, with a log-distance tween to the target's framing (`web/camera.js` `focusDistance`). Cislunar HEO / HEO targets are framed so Earth stays in view and seen from the frame's plane view (review V7). Instant with `prefers-reduced-motion`.
  - **Presets** (the camera direction slerps along a great circle with the same ease, review V14):
    - **Earth–Moon** (opening view): the Moon's orbit over ≈ 60 % of the width, 30° above the lunar orbit plane;
    - **Earth** (12 R⊕) and **Moon** (2 R⊕);
    - **L1–L2**: SE-L1 to SE-L2 across the view, 30° above the ecliptic, Sun on the left;
    - **SE-L1** and **SE-L2**: focus on the point, 55° above the ecliptic.
  - **Edge chevrons** for what is off screen: the selected target, Earth, the Moon, SE-L1/L2 (with the count of shown targets of that regime off screen, "SE-L1 · 8 · 1.49 Mkm") and the natural objects, each with its range from the focus point (the list and tooltip stay geocentric) on a dark scrim, clear of the title, legend, Sun indicator, each other and every on-screen marker, Lagrange glyph and body; a click flies there. The Sun indicator leaves its in-view point for the edge when that point is within 24 px of the SE-L1 glyph, and an edge-placed Sun slides clear of SE-L1 and the markers.
  - **Scale:** a scale bar (1–2–5 steps from 1 m, or 1 LD = 384,400 km) in the scene foot; a "Scale rings" layer when Earth ≥ 2 px: GEO (42,164 km, equatorial) and a ring at the Moon's instantaneous distance in the lunar orbit plane (356,000–407,000 km over a month; it passes through the Moon, so it is labelled "Moon distance", not "1 LD").
- **Legend** (scene foot, `web/ui/legend.js`): two rows — glyphs (payload, rocket body, debris, natural with its chip, extrap / stale, derelict as a full and a dimmed dot, Lagrange) and lines (past, future, extrap, GP, terminator, Sun −12° with its layer, GEO · Moon distance as a small ring, tracklets as beads on a hairline with the tracklets layer, sight line with its layer). `?` opens the keys, the colour key (the seven families from `catalog.js`) and the glyph glossary; on a phone it lists the touch gestures. The scene foot carries "drag · scroll · dbl-click · ? keys" from 981 to 1600 px, the long hint above.
- **Readout notes** (review V11): a GP target states its epoch once ("GP epoch 2026-08-05 · 78 d · stale"); a target with NEOCP tracklets lists station chips with the code as text and the bead colour as a grouping hint.
  - **Phone** (≤ 700 px): the same opening view, the legend behind a chip, a one-line banner, 16 labels.
- **Keys:**

| key | action |
|---|---|
| Space | play / pause |
| ← / → | ±1 h (Shift: ±1 d) |
| L | LIVE |
| F | focus selected |
| E | focus Earth |
| M | focus Moon |
| / | search |
| Esc | deselect (closes the help first) |
| Enter | focus selected |
| [ / ] | previous / next visible target |
| ? | help overlay: keys and glyph legend |

- **URL state:** `?t=<ISO>&focus=<id>&sel=<id>&layers=<csv>&frame=eci&view=<preset>|cam=…` (a preset is stored as `view`, a dragged camera as `cam`), updated with `history.replaceState` throttled to 500 ms. MMX only read the URL.
- **Test hook:** `window.__cle` (mirrors `window.__mmx`).
- **Credit:** "Illustration: Mickey · base map NASA Blue Marble", bottom-right, small type.
  - Data credits go in About: JPL Horizons, MPC, Project Pluto / Bill Gray, Space-Track, ESA Hipparcos, d3-celestial.

---

## 11. Data files and JSON schema

All sidecars are classic scripts: `CLE.register("<name>", {...});`. The bundle defines `CLE.register` before any sidecar runs.

- **Binary arrays** are base64 of **little-endian** Float32 or Float64 (uint16 / int16 for the packed star catalogue). They are decoded with `Uint8Array.fromBase64` when available, otherwise `atob`, and wrapped as zero-copy typed-array views (`web/loader.js decode`).
- **Writes are atomic** (temp file + rename), so an hourly NEOCP write never leaves a half file.
- **Lossless overhead rules (review EFF-6/EFF-8, 2026-10-22):** epochs are `t0_ms` only (no ISO twin); a segment's `src` is an index into the Track's `srcs`; `sigma_km` and `jump_km` are present only when there is one; per-target provenance is **not** in the sidecars (it stays in `build/states/<id>.json`; `catalog.js data.retrieved_utc` carries the newest retrieval); a live NEOCP object's Track travels only in `neocp.js` (its catalog entry says `"chunk": "neocp"`); catalog `sources` are compact strings; the star catalogue is packed. `tests/data_contract.test.mjs` enforces this shape on `dist/data`.

```jsonc
// Track: shared by Moon, Sun, targets, history
{
  "center": "earth" | "moon",
  "dtype": "f32" | "f64",
  "srcs": ["horizons:-170", "fo:X@2026-10-19"],           // distinct sources in first-use order
  "segments": [{
    "t0_ms": 1789948800000,
    "step_s": 3600, "n": 1441,
    "rv": "<base64 n×6: x,y,z km, vx,vy,vz km/s, ICRF, geometric>",
    "sigma_km": "<base64 f32 n>",                           // only when known
    "src": 0,                                               // index into srcs (loader.decodeTrack resolves it)
    "quality": "ref|pred|fit|extrap|gp|conic",
    "join_before": "start|continuous|splice|gap|source",
    "jump_km": 12.3                                         // only when non-zero
  }]
}

// data/core.js
CLE.register("core", {
  "schema": "cle/core@1",
  "meta": {
    "built_utc": "...", "now_utc": "2026-10-21T00:00:00Z",
    "window": {"start_utc": "2026-09-21T00:00:00Z", "stop_utc": "2026-11-20T00:00:00Z"},
    "frame": "ICRF", "state_type": "geometric", "time": "UTC (no leap second in window)",
    "units": {"pos": "km", "vel": "km/s"},
    "scene": {"mapping": "(X,Y,Z) -> (X,Z,-Y) / 6378.137 km; right handed", "unit_km": 6378.137},
    "earth_orientation": "Skyfield ITRS, builtin timescale, no polar motion",
    "kernel": {"path": "~/JPLDE/de440s.bsp", "sha256": "..."},
    "software": {"python": "...", "skyfield": "...", "fo": "2026 Mar 01", "three": "r170"}
  },
  "earth": {"radius_km": 6378.137,
            "quat": {"t0_ms": 1789948800000, "step_s": 3600, "n": 1441, "xyzw": "<base64 f64 n×4>"}},
  "moon":  {"radius_km": 1737.4, "track": Track},
  "sun":   {"track": Track}
});

// data/catalog.js: every T1–T3 object (T4 only in MASTER.md)
CLE.register("catalog", {
  "schema": "cle/catalog@1",
  "regimes": [{"key": "LUNAR_ORBIT", "label": "Lunar orbit", "order": 0, "color": "#4f81ff"}, ...],
  "targets": [{
    "id": "jwst", "name": "James Webb Space Telescope", "label": "JWST", "aliases": ["-170"],
    "norad": 50463, "cospar": "2021-130A", "horizons_id": "-170", "group": "JWST",
    "obj_type": "payload", "regime": "SE_L2", "regime_now": "SE_L2",
    "status": "active", "status_date": "2026-10-20", "tier": 1, "default_visible": true,
    "chunk": "targets-se",                       // which targets-*.js holds its Track; "neocp" for a live NEOCP object
                                                 // (Track in neocp.js only); null when not drawn
    "sources": ["*horizons:-170[2026-01-01..2026-11-20]", "bgtle:jwst.tle"],   // kind:ref[start..end], '*' = primary
                                                 // (cle.catalog.compact; web/loader.js parseSource gives the page
                                                 //  {kind, ref, start, end, primary} on cat.sources)
    "data": {"start_utc": "...", "end_utc": "...", "quality": ["pred", "ref"], "src": ["horizons:-170"],
             "retrieved_utc": "..."},            // drawn targets only: span and sources of the exported Track
    "elements": {"epoch": "...", "center": "earth", "a_km": 0, "e": 0, "i_deg": 0,
                 "peri_km": 0, "apo_km": 0, "period_d": 0},
    "trail": {"past_s": 2592000, "future_s": 2592000},
    "notes": "...", "links": [{"label": "Horizons", "url": "..."}]
  }]
});

// data/targets-<chunk>.js  (chunks: lunar, em, cislunar, heo, se, helio)
CLE.register("targets-se", {
  "schema": "cle/targets@1", "built_utc": "...",
  "targets": {
    "jwst": {"track": Track,
             "events": [{"t_utc": "...", "kind": "ephem_end|splice|maneuver|impact", "note": "..."}],
             "validation": {"midpoint_max_km": 0.0, "midpoint_method": "...", "tol_km": 1.0, "moon_check_km": null,
                            "regime_check": "...", "regime_pass": true},
             "caveat": "...", "quality_note": "..."}
             // no provenance here: build/states/jwst.json keeps {src, retrieved_utc, raw, sha256, command}
  }
});

// data/neocp.js: rewritten hourly. Every 10 min the page re-injects <script src="data/neocp.js?v=<ms>">
// (cache-buster) and swaps in the new data only if built_utc changed.
CLE.register("neocp", {
  "schema": "cle/neocp@1", "built_utc": "...", "neocp_snapshot_utc": "...",
  "objects": {
    "C46PP91": {
      "catalog": { /* same shape as a catalog target, tier 1, regime NEOCP_GEO|CISLUNAR_HEO|... */ },
      "track": Track,
      "fit": {"n_obs": 0, "n_used": 0, "arc_h": 0, "rms_arcsec": 0, "epoch": "...",
              "elements": {...}, "geo_score": null, "srp": false, "pluto_url": "...",
              "page_check": {"fit_disagrees": false, "dr_page_sv_km": 0.0, "table_max_sigma_ratio": 0.0},
              "fallback": null},                   // null | "page_tle" | "page_table" (§5.6)
      "tracklets": [{
        "stn": "T05", "n": 5,
        "t_ms": [ ... ],                                  // light-time-corrected epochs
        "ra_deg": [ ... ], "dec_deg": [ ... ], "mag": [ ... ],
        "stn_km": "<base64 f64 n×3, geocentric ICRF>",
        "dot_km": "<base64 f64 n×3, geocentric ICRF>",
        "resid_arcsec": [[0.1, -0.2], ...]
      }]
    }
  },
  // live in the previous hourly run's parsed.json and absent from summary.htm and neocp.txt now (set membership,
  // kept --keep-days after last_listed, dropped when listed again); objects still in "objects" are excluded.
  // removed_utc = the listing stamp (max of summary regeneration and neocp.txt mtime) at which the object was
  // first missing; last_listed = the regeneration stamp of its last listing.
  "removed": [{"desig": "...", "removed_utc": "...", "last_listed": "...", "reason": "..."}]
});

// data/history-<id>.js: same Track format, step 86400 s, quality per segment
```

**Star catalogue** (`web/assets/hipparcos.json`, inlined into the page by `scripts/build_site.mjs`, read by `web/sky.js unpackStars`):
`scripts/pack_stars.py` (run by `scripts/import_mmx.py`) turns the MMX `[ra_deg, dec_deg, Vmag]` triples into
`"stars": {"n": 9827, "ra": "<base64 uint16>", "dec": "<base64 int16>", "mag": "<base64 int16>", "units": {"deg": 360/65536, "mag": 0.001}}`
(ra = `ra[i]·units.deg`, dec likewise, V = `mag[i]·units.mag`; 19.8 arcsec LSB = 0.13 px at the 38° sky camera over 900 px);
`meta` (plus a `packed` note) and `hip_ids` are kept. 312 KB → 79 KB in the page.

**Python intermediates:**

- `build/plan.json`;
- `build/states/<id>.npz` (`t_utc_s` f64, `r_km` f64 n×3, `v_kms` f64 n×3, `seg` i2, `sigma_km` f4) plus `<id>.json` (meta, provenance, validation);
- `build/validation_report.md`, regenerated on every build.

---

## 12. Directory layout

```
cislunar-eye/
  ARCHITECTURE.md  README.md  Makefile  package.json  package-lock.json  requirements.txt
  catalog/
    sources/SCHEMA.md, <src>.tsv          per-source extracts (exist / in progress)
    overrides.tsv                         hand edits: merge/split, regime, status, source priority, hide
    master.tsv  MASTER.md  merge_review.tsv   generated
  scripts/                                extractors + checks (exist); build_site.mjs; import_mmx.py
  src/cle/
    __init__.py  timeutil.py  catalog.py  plan.py  statetable.py  validate.py  context.py
    adapters/ horizons.py  fo.py  bgtle.py  neocp.py  gp.py  elements.py
    tracklets.py  export.py  cli.py       (python -m cle <command>)
  web/
    index.template.html  app.js  loader.js  time.js  states.js  frames.js  focus.js  labels.js
    sky.js  sky-style.js                  copied from MMX, retuned (§9.8)
    scene/ earth.js  moon.js  markers.js  trails.js  trailMaterial.js  tracklets.js  refpoints.js
    ui/ list.js  readout.js  situation.js  timebar.js  url.js
    vendor/three/                          three r170 + addons + LICENSE (copied verbatim from MMX)
    assets/ hipparcos.json  stars.json     copied verbatim; earth_4096.jpg generated
  tests/  test_*.py (unittest)  *.test.mjs (node:test)  browser.mjs (Playwright)  fixtures/
  build/                                   intermediates (regenerable, not hand-edited)
  dist/   index.html  data/*.js            THE PAGE: open dist/index.html
  ops/launchd/                             plist templates (not installed automatically)
  work/   cache/<source>/<stamp>/  notes/  fo.lock
```

The MMX files are imported by `scripts/import_mmx.py`. It copies them, writes `web/vendor/three/IMPORT.json` (MMX commit plus sha256 per file), and never edits the MMX tree.

---

## 13. Build and update commands

Each target is idempotent. Raw downloads go to fresh `work/cache/<source>/<stamp>/` directories, and every parse runs `~/.venvs/astro313/bin/python -I`.

| make target | network | cadence | does |
|---|---|---|---|
| `setup` | npm | once | `npm ci` (esbuild 0.25.0, playwright 1.58.2, as MMX `package.json`); `import_mmx.py`; builds the Earth texture |
| `extract` | Space-Track only for `extract_spacetrack_gp.py` (skipped without credentials or with `EXTRACT_FLAGS=--offline`) | daily | `scripts/extract_all.py`: billgray, neocpsat (local `fo`, `work/fo.lock`), lunarmission `--offline`, rocketdebris, spacetrack → `catalog/sources/*.tsv`, one `CLE_NOW` per run, logs in `work/logs/extract/` |
| `sweep` | Horizons | daily, acts about weekly | `horizons_sweep.py --now $CLE_NOW --in-scope-only --max-age 7`: re-probes the in-scope IDs at now − 15 d / now / now + 15 d (`d_m15_km`, `d_0_km`, `d_p15_km`, `sweep_now`) once the audit table's oldest in-scope `sweep_now` is > `SWEEP_MAX_AGE_D` (7) d from now, else exits at once; `FORCE=1` re-probes now; non-fatal, and a request that fails after every retry aborts the run with nothing written |
| `catalog` | no | daily, after extract or overrides | `validate_source_tsv.py` over the merged sources (fail fast, C4), `build_master.py --now <date>` → `master_list.*`, `TARGETS.md`, then `git diff --stat -- catalog/` |
| `neocp-live` | MPC, Project Pluto | daily, non-fatal | `neocp_live.py` → `catalog/sources/neocp.tsv` before `catalog` (new designations enter the master list) |
| `update-horizons` | **yes** | **daily** | Horizons for all Horizons-planned targets plus the 301 check: one 2-row solution probe per target per UTC day, then only the 10-day tiles and window-edge refine/repair pieces the cache lacks (§5.3; a normal daily ≈ 50 requests / ≈ 2 min, a cold cache or a re-delivered solution ≈ 460 requests / ≈ 17 min). Two consecutive failed attempts trip a breaker; then (or with `CLE_OFFLINE=1`) every target is built from the cache (tiles, else an older solution, else a re-gridded pre-tile response), caveat "Horizons snapshot of <date> (offline fallback)", provenance `fallback` |
| `update-gp` | **yes** | **daily** | Space-Track `gp`/`gp_history` through the skill CLI (rate limits respected); a failed pull is recorded as `gp_fetch` in `build/states_report.json` and the cached sets are used |
| `update-fits` | no | when `scripts/fits_changed.py` finds `../neocpsat/{catalog.csv,data,mpecs}` changed (or `FORCE=1`) | `fo` re-runs for the neocpsat groups (one process, `work/fo.lock`; one fit's legs and one-day chunks pooled under one hold, §5.4); candidate verdicts memoised in `work/cache/fo/verdicts.json`; `fits_changed.py --mark` records the inputs after a successful run |
| `update-neocp` | **yes** | **hourly** | `neocp_live.py` → `fo` → tracklets → writes `dist/data/neocp.js` and its `manifest.js` entry only (atomic); `ops/run.sh` pins it to the daily's window |
| `tles-status` | git fetch (read) | daily, non-fatal | reports `~/tles` HEAD against upstream; **never pulls** (that is your call) |
| `states` | no | after any update | snapshots → `build/states/*.npz` + `.json` stamped with the window (`now_utc`, `t0_utc`, `t1_utc`); every `states_report.json` entry carries its `window`. `cle states --jobs N` (`CLE_JOBS`, default 8; review C2): gp / bgtle / extrap targets in N spawned worker processes (one `Context` each, longest previous build first), every fo / neocp target in order in one more worker, Horizons targets in the parent (one `Snapshots`: politeness, breaker, counters); each table is saved by the process that built it, the report only by the parent, which records `states_run {jobs, targets, wall_s}`; an entry's `seconds` is the target's own work and the time it queued on `work/fo.lock` behind another process's run is `lock_wait_s`, so the per-target numbers compare across `--jobs`; a worker that dies costs its target a rebuild in the parent; `--jobs 1` is the sequential catalog-order loop. The extrap start-state fit (AM5) is memoised in `work/cache/extrap/fits.json` under sha256(file) + epoch + state + span + step + nbody constants + `FIT_VERSION` (`CLE_REFRESH=1` refits; bump `extrap.FIT_VERSION` when the method changes) |
| `build` | no | after states | `cle.export` → `dist/data/*.js` (draws only states of the current window); `node scripts/build_site.mjs` → `dist/index.html` |
| `test` / `test-browser` / `sanity` | no | each build | §14; `tests/data_contract.test.mjs` checks the `cle/*@1` fields the page reads |
| `status` | git (read) | any time | `scripts/status.py`: ages of the NEOCP snapshot, Horizons/GP/fo stamps, master list, `~/tles`, last logs; exit 1 when the NEOCP snapshot > 3 h or the last daily > 36 h old or failed |
| `open` | no | — | `open dist/index.html` |
| `install-launchd` / `uninstall-launchd` | no | opt-in | copy `ops/launchd/*.plist` to `~/Library/LaunchAgents` and `launchctl bootstrap` them / bootout and remove |
| `prune` / `prune-apply` | no | end of `daily` (apply), or by hand | `scripts/prune.py` (review EFF-9, DW-05): `work/cache/fo` keeps the stamps `build/*.json` + `build/states/*.json` reference plus the previous stamp per referenced run label; in kept stamps the other run directories (chunks, legs, probes, rejected candidates) are trimmed to `obs.txt`, `elements.txt`, `command.json`, `fo_stdout.txt`, `ephem.txt.gz`; every other stamp is deleted. `work/cache/horizons_eph` keeps the referenced stamps plus the two newest. Stamps younger than 1 h are left alone; refuses (rc 2) under a live foreign `work/make.lock`, a held `work/fo.lock`, or when `build/` references nothing. `prune` is the dry run |
| `daily` | yes | daily | `neocp-live extract sweep catalog update-horizons update-gp tles-status update-fits states export validate site test sanity test-browser`, then `prune.py --apply` (non-fatal) |

**One window per make.** `CLE_NOW` is fixed once at the top of the Makefile (the environment's value, else the wall
clock floored to the hour) and exported to every step; each target echoes it. The export draws a target only from a
`states_report.json` entry and a states file of the current window; anything else is "not drawn (adapter not run in
this window)".

**Scheduling.** `ops/run.sh <target>` is the unattended entry point: `work/make.lock` (mkdir; a lock whose pid is dead
or older than 6 h is removed, exit 75 while held), `CLE_NOW` pinned for `update-neocp` to the daily's `now_utc`
while the wall clock is inside that window, `make` output in `work/logs/<target>-<UTC date>.log` (14-day rotation,
last line `ok` or `FAILED rc=N at step <target>`). `ops/launchd/` holds the agents: `com.cislunar-eye.neocp`
(hourly at :45, after Bill Gray's :35 regeneration) and `com.cislunar-eye.daily` (01:30 local).

- They are **not loaded automatically** (Q8): `make install-launchd` loads them, `make uninstall-launchd` removes them.
- Nothing in the pipeline ever pushes, deploys or writes outside this project, `work/cache` and `~/.find_orb`'s transient files.

---

## 14. Tests and fixtures

**Python (`unittest`, `-I`)**

1. **Horizons parsing.** Fixtures are copied with sha256 from `work/cache/arch_probe/` and new captures:
   - JWST −170;
   - LRO −85 at `500@301`;
   - `DES=2024 PT5;` (header variant);
   - CAPSTONE past-end error (parsed into a coverage clip);
   - `2020 SO` name ambiguity (must be rejected because the target ID does not match).

   Checks: calendar vs JD ≤ 2 ms, sample count and spacing, midpoints < tolerance, refine-or-break on a synthetic splice.
2. **Moon:** DE440s against Horizons 301 < 1 km. **Earth orientation:** MMX fixtures (Greenwich, 90°E, pole at 3 epochs).
3. **Bill Gray TLE against Horizons:**
   - **Artemis II:** `26069a.tle` (Range 2026-04-03 → 04-10.9) against Horizons −1024 over the same span. Expect agreement within the file's "Worst residual" plus a few km.
   - **SMILE:** `26109a.tle` against Horizons −463 over the window.
   - TT handling: a 69 s offset must show up as ≈ 52 km in a deliberately wrong-scale control case (bill-gray skill).
   - Frame control: the same TLEs passed through skyfield TEME→GCRS instead of `get_vect` must disagree with Horizons by the predicted nutation rotation (≈ 8.7″ in October 2026). This proves the precession-only convention of §5.5.
   - Space-Track GP, through skyfield TEME→GCRS, is checked against Horizons in test 6.
4. **`fo` against Horizons:**
   - `../neocpsat/mpecs/CAPSTONE_combined.html` obs against Horizons −1176 (before 2026-08-14);
   - `SMILE_combined.html` obs against −463.

   Acceptance: inside the obs arc |Δr| ≤ 3·ρ·rms″/206265 + 5 km; beyond the arc, the growth is recorded rather than asserted. Plus a **`-E 0` format test**: header, TT column and km units.
5. **neocpsat regression:** re-running `fo` on the Spektr-RG 2026-10-04 group must reproduce a = 1.05035950 AU, e = 0.0447928 and i = 0.08670° (ecliptic) within 1σ.
6. **GP:** TESS (Horizons −95) GP history against Horizons. This produces an error-against-age table for the quality notes; no assertion.
7. **Tracklets:** dot → RA/Dec round trip < 0.01″; RMS of O − C against `fo`'s mean residual within 10 %.
8. **Catalog:** schema validation of every extract; merge determinism (same inputs give a byte-identical `master.tsv`); no weak-key auto-merges.

**JS (`node:test`)**

- Hermite reproduces native samples to < 1e-8 km.
- JS against the Python spline < 1e-6 km.
- Clamping holds, with no interpolation across a `join_before ≠ continuous`.
- Moon-relative recombination is correct.
- Base64 decoding is little-endian for both f32 and f64.
- The `sceneVector` mapping preserves handedness.
- `starAppearance` matches the new constants.
- The CR3BP L-point solver is correct.

**Browser (Playwright, system Chrome, as MMX)**

- `file://` load with every http(s) request aborted, and an assertion that none were attempted.
- Lazy chunk injection works, and **re-injecting `neocp.js` replaces the data** (proves the hourly refresh works on `file://`; also checked in Safari by hand).
- Label cap and occlusion by Earth and the Moon.
- Focus and fly-to; URL round trip.
- Phone layout at 390×844.
- A frame-time probe with all T1 targets: median < 16 ms on your Mac.
- `--screenshots` → `test-results/`.

---

## 15. Performance budget

**Design point** (to be re-checked once the catalog is merged): about 400 drawable targets. That is ≈ 350 at 1 h, 15 lunar at 5 min, 25 SE/helio at 6 h and 20 NEOCP. Sizes *(computed)*:

| item | per target | count | binary |
|---|---:|---:|---:|
| 1 h, f32 (1,441 × 24 B) | 34.6 KB | 350 | 12.1 MB |
| lunar 5 min, f32 (17,281 × 24 B) | 415 KB | 15 | 6.2 MB |
| SE/helio 6 h, f64 (241 × 48 B) | 11.6 KB | 25 | 0.3 MB |
| NEOCP (≈ 1,000 × 24 B) + tracklets | ≈ 30 KB | 20 | 0.6 MB |
| periapsis refinement (allowance) | — | — | +1.9 MB |
| core: quaternions 46 KB, Moon and Sun 69 KB each | — | — | 0.2 MB |
| **total sidecar binary** | | | **≈ 21 MB → ≈ 28 MB base64** |
| `index.html`: three r170 0.69 MB, stars 0.41 MB, Earth texture ≈ 1.6 MB (base64), app | | | ≈ 3 MB |

**Runtime targets**

- Cold load from local disk < 1.5 s.
- Base64 decode ≈ 0.1–0.2 s *(memory: atob throughput)*.
- Per frame:
  - interpolation of the visible targets ≤ 1 ms;
  - label pass ≤ 2 ms;
  - ≤ 30 draw calls: two trail batches, the head segments, one Points, Earth, the Moon, the terminator, reference points, the focus trail, tracklets and three sky passes.
- GPU trail memory ≈ 400 × 1,441 × 36 B ≈ 21 MB, plus lunar 15 × 17,281 × 36 B ≈ 9 MB.
- **Render on demand:** redraw only when playing, when the camera moves or when the UI changes. In LIVE mode at 1×, redraw at 10 Hz.

**Pipeline**

- Daily Horizons: ≈ 150 targets × 1–2 requests, sequential, ≈ 10–20 min.
- Hourly NEOCP: ≤ 30 objects × 2 `fo` runs, about 1–3 min.

---

## 16. Phasing

- **Phase 0 (now):**
  - finish the per-source extracts;
  - `make catalog` → `MASTER.md`, which you review;
  - you approve this document and answer §19.
- **Phase 1 (MVP):**
  - MMX import;
  - ECI viewer;
  - Horizons, `fo` (neocpsat), Bill Gray TLE, NEOCP live with tracklets, GP;
  - trails, markers, labels, list, focus, time bar, readouts, Situation panel;
  - subdued stars;
  - all §14 tests;
  - daily and hourly make targets.
- **Phase 1b:** history chunks (Spektr-RG first).
- **Phase 2:**
  - Earth–Moon and Sun–Earth rotating frames;
  - elements-only conics;
  - σ halos;
  - Moon texture;
  - optional night lights (`~/sda/satobserver/app/assets/earth_night.jpg`);
  - a batch helper for Bill Gray TLEs.
- **Phase 3:** ground-observer view and sky chart (MMX `observer.js`, `skychart.js`); visibility from your sites; alerts.

---

## 17. Alternatives considered (rejected or deferred)

- **One fully inlined HTML file, as MMX does.** Rejected for dynamic data: the hourly NEOCP refresh would rewrite about 30 MB. It is kept for static assets.
- **A local HTTP server, allowing `fetch` and ES modules.** Rejected as the default, because classic sidecars already work on `file://`. `python -m http.server` is used only inside the Playwright tests.
- **CesiumJS.** Rejected. Its float64 RTC is attractive, but the bundle is heavy, its star skybox is not catalog-based, styling control over the MMX look is weaker, and `file://` needs extra work *(memory)*.
- **Client-side SGP4 (satellite.js).** Rejected. It cannot read Bill Gray hex TLEs, and it would mean two propagation paths to validate.
- **Shader-side interpolation of every target.** Premature: CPU Hermite at about 400 targets fits easily in the budget.
- **NAIF SPICE kernels per spacecraft instead of Horizons.** Horizons already serves them, with simpler provenance. Reconsider only for missions that publish kernels Horizons lacks.
- **An in-house n-body integrator in place of `fo`.** `fo` is what neocpsat already uses and validates. An integrator (scipy DOP853 + DE440s + SRP) is deferred to phase 2, and would only extrapolate passive objects whose ephemeris ended.

---

## 18. Risks

- **R1 Coverage of "now".**
  - The local Bill Gray archive covers 2026-10-21 for only 2 objects.
  - highartsat's `orbits.json` (generated 2026-08-04) has **no** entry whose Range reaches 2026-10-21.
  - So for derelict HEO objects, "now" mostly comes from GP (lower accuracy) or `fo`.
  - Mitigation: quality flags; a `tles-status` report; you can pull upstream `~/tles`.
- **R2 Horizons ID ambiguity.** Names resolve silently to other records ('2020 SO' → Surveyor-2 Centaur RB) or to "multiple". Mitigation: verified numeric or `DES=` commands only, plus a target-ID header assertion.
- **R3 Shared find_orb state.** `~/.find_orb` is shared with your interactive use and with neocpsat. Mitigation:
  - fresh `-O` + `-i`;
  - `-D` env copy with its hash recorded;
  - a lock file;
  - never run concurrently with a neocpsat scan.
  - Changes to your environ.dat (e.g. `SETTINGS` filtering) change our fits, so its sha256 goes in the provenance.
- **R4 Manoeuvring spacecraft without agency ephemerides.** Spektr-RG station-keeping and the Chang'e-6 orbiter reconfiguration invalidate extrapolations. Mitigation: `extrap` and stale classes, and gaps are never bridged.
- **R5 Volume and `file://` constraints.** The page is about 30 MB, and textures must be inlined. Script re-injection for the NEOCP refresh must be proven in both Chrome and Safari (browser test plus a manual check).
- **R6 Precision.** f32 trails jitter at extreme zoom on far targets. Mitigation: a precise trail for the focused target, and f64 for r > 10⁶ km.
- **R7 Duplicates across sources.** NEOCP temp designations against known objects; Bill Gray surrogate NORADs. Mitigation: strong-key merge plus the `merge_review.tsv` workflow.
- **R8 Clutter.** HEO and cislunar objects bunch inside about 70 R⊕. Mitigation: label cap and priority, group toggles, regime presets.
- **R9 Space-Track terms.** GP-derived tracks stay local and are never published *(memory: redistribution limits)*.
- **R10 Leap seconds and window roll-over.** Check Bulletin C whenever the window spans a 30 June or 31 December.

---

## 19. Open questions for you

The questions about the target list itself (threshold, stale objects, natural bodies, replays, heliocentric radius, source order) are at the end of `catalog/TARGETS.md`. The ones below are about the viewer.

1. **Default visible set.** Is it T1 only (my proposal), or T1 + T2?
2. **Window.** Is ±30 d right? Should the page open in LIVE (wall clock, my proposal) or at the build's "now"?
3. **NEOCP hyperbolic `(geo)` fits** (e.g. 8NIQ001, e = 7.7, Earth flyby). Show them by default, or keep them hidden in a "flyby" subgroup (my proposal)?
4. **Terminator colour.** `#ffd54f` (my proposal, consistent with your maps) or MMX's `#b09025`? Do you want the −12° ring available?
5. **Moon texture.** Download one (e.g. LROC WAC global mosaic or NASA SVS CGI Moon Kit *(memory)*) or stay grey in phase 1?
6. **Rotating frames.** Is phase 2 acceptable? Earth–Moon first, or Sun–Earth first?
7. **Size budget.** Is about 30 MB of local sidecars acceptable? If not, I would lazy-load lunar 5-min data in 10-day pages.
8. **Automation.** Should I install the launchd agents (hourly NEOCP, daily refresh), or will you run `make` by hand?
9. **Space-Track.** Is daily `gp_history` use for the GP-planned targets OK with your account?
10. **`~/tles`.** Will you pull upstream (`4b14176`, which updates SMILE)? The pipeline never pulls.
11. **History mode.** Which objects beyond Spektr-RG (2019-07-14 →) should have history? How far back?
12. **Naming.** Should labels use mission-style short names ("CE-3 R/B") or SATCAT-style names ("CZ-3B R/B")?

---

## Appendix A: where the facts come from

- **MMX:** reuse spec `work/notes/mmx_reuse.md`, with all `file:line` references at MMX `8c60106`.
- **Horizons behaviour:** `work/cache/arch_probe/{capstone_past_end,name_2020SO,smallbody_2020SO,smallbody_2024PT5}.json` (from `scripts/arch_horizons_probe.py`) and `work/cache/news/horizons_summary.tsv`.
- **`fo` options and output:**
  - `~/Desktop/billgray/find_orb/fo.cpp:575-831, 975-1010`;
  - `ephem0.cpp:2249, 2381-2386, 2687-2730`;
  - `mpc_obs.h:116-167`;
  - `cssfield.cpp:95-130`;
  - `environ.def:212-228`;
  - `~/.find_orb/environ.dat:28,227`;
  - neocpsat `correlate.py:303-331, 380-399` and neocpsat `CLAUDE.md`.
- **Bill Gray TLE frame:** `sat_code/get_vect.cpp:94-101, 114-117` (TT time argument; precession-only back to J2000) and `find_orb/eph2tle.cpp:542-546, 846-852` (precession-only J2000 → of date). The 8.67″ nutation size is computed with skyfield IAU 2000A for 2026-10-21.
- **NEOCP pseudo-MPEC content:** `scripts/neocp_live.py:472-690` and `work/cache/neocp/parsed.json` (C46PP91: TLE comment lines, 48-row geocentric table, σ 208,800″ in the first row).
- **Bill Gray TLE semantics:** `sat_code/sat_id.cpp:833-837, 1301-1339`; `~/tles/26069a.tle:54,217-226`; `~/tles/tle_list.txt` (838 Includes; 3 Ranges reach 2026-10-21, one of them the missing `all_tle.txt`); `scripts/extract_billgray.py:24-40, 396-440`.
- **Spektr-RG:**
  - `../neocpsat/catalog.csv` rows 24-262 (Spektr-RG apparitions);
  - `../neocpsat/mpecs/Spektr-RG_combined_2026_10_04.html` (60 obs, Oct 4–13, 0.31″);
  - `~/tles/tle_list.txt:1267-1289` (`19040a*.tle`, 2019-07-14 → 2026-01-01; Warn line 307 for the Spektr-RG booster, 2038).
- **Assets:** MMX `web/vendor/three/three.module.min.js` (691,648 B), `hipparcos.json` (372,315 B), `stars.json` (36,668 B). Earth texture 4096×2048 q85 ≈ 1,206,176 B (computed from the 5400 source).
- **Statements from memory** are tagged *(memory)* inline. They are:
  - the `file://` fetch, ES module and WebGL cross-origin rules;
  - the Horizons output-line cap and "prior to" error wording;
  - the ICRF–J2000 frame bias;
  - SDP4 error growth in lunar-perturbed HEO;
  - Space-Track redistribution terms;
  - NASA Eyes behaviours;
  - Moon texture sources;
  - leap-second insertion months.
