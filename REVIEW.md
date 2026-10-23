# cislunar-eye — review: efficiency, data workflow, visuals

Date 2026-10-22 · build pinned to `CLE_NOW=2026-10-21T13:00:00Z` · reviewed as-is, no project files changed.

Method: three lens reviews (efficiency, data workflow, visuals); every finding was then re-measured by a
different reviewer. Effort and gain below are the **corrected** figures from that second pass; where the first
figure was wrong it is said. Measurement scripts, profiles and screenshots are under
`work/review/{efficiency,data-workflow,visuals,verify-0..7,report}/`. Refuted items are in the appendix.

---

## 1. Verdict

The one-shot offline build is solid and the page is fast: every output is written atomically with sha256
provenance, a Horizons target rebuilds from cache bit-exactly (jwst: max |Δr| = 0), the viewer holds 60 fps at
≤ 1 ms of JS per frame on a flat 36 MB heap, and the physics checks (terminator on the Sun line, Cassini
orientation, Lagrange roots, 0.1 km lunar Hermite tolerance) pass. The weak points are the *recurring* paths and
the *opening view*. `make daily` has never run unattended and would fail in specific, reproducible ways: its nine
steps share no window, so a start late in the hour loses all 38 Horizons tracks; a Space-Track login failure
aborts the chain; a Horizons outage runs 3.6–18.7 h; the hourly NEOCP path carries a `NOW` frozen at 2026-10-21
that stops selecting new identifications after 2026-10-31; the catalog's frozen distance epochs drop 18 tier-1
objects on 2026-12-06. Build time is 99 % `states`, and 85 of its 215 s are 30 *sequential* one-day find_orb
refits per drifting target that run 6× faster in parallel with bitwise-identical output; the fo cache holds
0.99 GB of runs no build references. Visually, the default L1–L2 framing shows Earth as a 1.5-px dot inside a
106-px blob of 40 markers, 31 same-hue HEO trails form a tangle in every Earth–Moon view, 5 of 10 regime hues
fail the chroma floor, and a hollow marker means three different things. About 4.5 h of workflow fixes make the
first unattended daily safe; 6 h on fo parallelism cut `make all` from 3.5 to ≈ 2.2 min; ≈ 10 h of viewer work
fix the opening view and the encodings. Total verified backlog ≈ 90 h (quick wins 10.5 h, half-day items
48.5 h, larger 30–36 h).

---

## 2. Ranked recommendations

Ranked by gain/effort, correctness and failure-mode items first. Effort is the corrected estimate.

| # | ID | Area | Change | Verified gain | h | Risk |
|---|----|------|--------|---------------|---|------|
| 1 | DW-01 | Makefile window | `CLE_NOW ?= $(shell date -u +%Y-%m-%dT%H:00:00Z)` so one make shares one window; reorder `daily`; `-git` for tles-status | Removes a deterministic daily failure: 38/91 Horizons tracks + moon_check lost whenever the hour changes between update-horizons and `states --offline` | 1 | none |
| 2 | DW-04 a+b | Upstream outages | try/except around `gp.fetch()`; Horizons circuit breaker after 2 ConnectionErrors | Space-Track login failure no longer aborts the daily; Horizons outage bounded to minutes instead of 3.6–18.7 h | 1 | none |
| 3 | DW-03 | Hourly NEOCP | `NOW` in `scripts/neocp_live.py` from CLE_NOW / wall clock instead of the 2026-10-21 constant | Sticky `satid_current` selection keeps working after 2026-10-31; covers_now / σ-at-NOW notes stay meaningful | 1.5 | low |
| 4 | DW-02 | Stale states | Missing report entry ⇒ "not drawn"; window in StateTable meta; test | Closes the only path by which a once-successful, now-failed target could be drawn from stale states (latent today) | 1 | none |
| 5 | DW-13 | Ops | `ops/run.sh` (lock + CLE_NOW pinned + log), launchd plists, `make status` | The documented hourly/daily cadence actually runs; no states_report reset races; data ages in one command | 3 | low |
| 6 | DW-06 | NEOCP lifecycle | `listed_now` from set membership vs previous parsed.json; "left the NEOCP" reason | `removed` list correct (5 of 6 entries false today); objects that left get a "left <date>" reason within one hourly run | 3.5 | low |
| 7 | DW-08 | Catalog freshness | `make extract` + weekly `make sweep --now`; window-relative distance columns; dates derived from data | Tiering stays correct past 2026-12-05 (18 tier-1 objects lost at --now 2026-12-06 today); new ~/tles commits, batches, Horizons IDs enter via `daily` | 6 | medium |
| 8 | DW-07 | GP staleness rule | One graded rule in build_master; gp_age_d in validation report; page reads the catalog flag | TARGETS.md, validation_report.md and the page agree on the 9 GP tracks older than 14 d | 1.5 | policy (AM3) |
| 9 | EFF-1 | fo daily()/verify() | ThreadPoolExecutor(8) for the 30 one-day chunks and 3 legs under one fo.lock hold; short labels (fo 93-char -O limit) | `states` 215 → ≈ 130 s; `make all` 3.5 → ≈ 2.2 min (≈ 1.8 with EFF-2/3) | 6 | low (ephem bitwise identical) |
| 10 | EFF-2 | extrap fit_state | Cache (sol.x, info) by sha256(file)+t+span+step+nbody constants | −26 s per build (extrap 52 → ≈ 26 s) | 2 | none |
| 11 | EFF-9 + DW-05 | Cache retention | `make prune`: keep fo stamps referenced by build/states (+ previous); gzip/delete unchosen ephem.txt; same rule for horizons_eph | −0.99 GB now; fo steady state 60–120 MB; horizons_eph ≈ 55 MB | 2.5 | low (guard hourly run) |
| 12 | EFF-3 | fo candidates | 1-row fit + short table for acceptance; window table only for the chosen fit; keep E16 for usable candidates | −12…−17 s per build; −75…80 MB ephem.txt per stamp | 3 | low (tests) |
| 13 | DW-09 | fo verdict memo | `work/cache/fo/verdicts.json`; skip known-rejected candidates; report line for Chang'e-6 | Chang'e-6 16.6 s → < 1 s; fo 109 → ≈ 90 s; report says "known-unfittable since 2026-10-03" instead of a traceback | 4 | low |
| 14 | EFF-4 + DW-05 | Horizons fetch | Fetch each piece once at h/2 (free midpoint check); fixed-boundary tiles + daily header probe/TTL | Cold fetch ≈ 36 → 15–18 min; daily refresh ≈ 5–6 → 2.5–3 min; growth ≈ 18 → 4–5 MB/day | 7–12 | medium (freshness, AM2 lattice, tests) |
| 15 | DW-12 | Tests | catalog idempotence + judge thresholds, neocp_live replay with NOW injected, not_drawn branch, Chrome-free data-contract check | Coverage exactly where the daily/hourly paths break (would flag the frozen NOW immediately) | 5.5 | none |
| 16 | V2 | HEO trails | Idle 0.25·P / alpha 0.45 when nothing selected; hover emphasis; derelict dim | Idle HEO ink roughly halved (7 Horizons trails), −35…45 % (20 GP trails); hovered orbit legible without a click | 3.5 | low (shader z-mode) |
| 17 | V5 | EM Lagrange glyphs | Hide EM-L1…L5 unless frame=em, Moon ≥ 150 px from Earth, or Moon/EM focus | 4 labels + 5 glyphs (+ 5 dot boxes) leave the ~90-px core of the default view | 1 | none |
| 18 | V4 | Marker glyphs | 6.5 → 8.5 px, wider SDF shapes, hollow = stale/extrap only, derelict = dim | Hollow markers 33 → 19 with one meaning; stale alpha 0.4 → 0.6; shapes differ by ~1.5 px instead of < 1 | 2 | spec (§6.4 ring = derelict) |
| 19 | V1 | Opening view | Open on the `em` preset; SE-L1/L2 as edge chevrons; short always-on L labels in the wide view | Earth 1.5 → 5.7 px, markers within 60 px 30 → 5; but on-screen targets 56 → 42 and the HEO rosette grows 106 → 450 px unless V2 ships with it | 4 | medium (README says L1–L2 default) |
| 20 | V3 | Palette | 7 colour families (L7c) set in `src/cle/catalog.py` REGIMES + export; non-yellow `--warn`, non-red NEOCP or recolour `--live` | Adjacent ΔE 7.9 → ≥ 16.6 (normal), 4.2 → ≥ 13 (CVD); chroma ≥ 0.1; helio markers no longer look like stars | 3.5 | medium (second colour pass needed) |
| 21 | V8 | Far trails in EM frame | Cap SE_*/helio trails in the EM frame at ≤ 0.25 d / 0.1 d, or a "far objects" toggle | Six ~880-px arcs across every EM view go to ≈ 100 px (2 d/1 d would still leave 325–449 px) | 1 | none |
| 22 | V10 | Tracklet legend | Legend row for beads/hairline/sight line; station chips with the code as text; drop #ffe082 from the station cycle | 355 beads on 9 targets become self-explanatory | 1 | none |
| 23 | V14 | Camera transitions | Slerp the camera direction in `currentFocus()` | The 5 direction-changing presets become 1-s eased moves instead of 3–38° jumps | 1.5 | low |
| 24 | V11 | Readout redundancy | One GP-epoch line instead of three; staleness banner out of the scene into the NEOCP section; type minimums | Tooltip 3 → 1 epoch mentions; the 414×28 px scene keep-out box disappears when the snapshot is old | 2 | low (phone legend) |
| 25 | V7 | Focus on HEO targets | d1 = max(framing, 1.3·\|r\|/tan(fov/2)) + frame-aware direction tween, HEO/CISLUNAR_HEO only | Earth stays on screen while following Vela 5A (26 vs 2 HEO markers in view, apogee radius 328 px) | 3 | medium (scope: not EM_*) |
| 26 | V13 | Phone | Open on `em` preset; legend chip; one-line banner; label cap 16 | Earth 0.65 → 2.56 px/R⊕, markers within 30 px 38 → 5; keep-out −60…80 px of 153 | 2 | low |
| 27 | V12 | Scale cues | Scale bar in the scene foot; GEO / 1-LD rings layer; Earth/Moon edge chevrons | Absolute scale in every view; Earth direction+range in SE-L1/L2 and Moon views | 3.5 | low |
| 28 | EFF-6 | Sidecar overhead | Drop chunk tracks overridden by neocp.js, per-target provenance, ISO t0; intern src | −0.54 MB of 4.5 MB sidecars (−12 %) | 2.5 | contract (§11) |
| 29 | EFF-8 | Text payloads | Compact `sources` strings, drop `src_rows`, uint16/int16 star catalog | −290 KB; page 7.44 → 7.2 MB; no measurable load change | 2 | low |
| 30 | EFF-10 | Frame switch | Typed `IDENTITY` (one line); R once per native sample with the ω·δ term; edge markers on the ui() cadence | ECI switch 102 → ≈ 40 ms; SE/EM ≈ 110 → 70–75 ms; 0 change at the default view | 2 | physics (AM1) if ω·δ omitted |
| 31 | DW-04 c | Horizons fallback | Use the newest overlapping cached response when offline/down, with a "snapshot of <date>" caveat | Yesterday's Horizons data instead of 0 tracks during an outage | 6 | medium (6 h lattice re-grid) |

---

## 3. Efficiency

`make all` ≈ 3.5 min is 99 % `states` (export 0.85 s, validate 0.75 s, site 0.20 s, sanity 0.76 s, Python +
Skyfield + DE440s start 0.11 s). Of the 215 s of `states`: fo 109 s (6 targets) + neocp 34 s (6) = 67 %,
extrap 52 s (14), gp 9 s (27), bgtle 8.7 s (1), horizons 2 s (38, from cache).

**Status 2026-10-22 — EFF-1/2/3/4/6/8/9 and C2 applied and integrated** (one owner per item, then one integration pass:
`build/states_before` = the 12:00 daily, every rebuild pinned to `CLE_NOW=2026-10-22T12:00:00Z`; scripts and logs under
the integration scratchpad `int/`). EFF-10 is left for the visual pass.

| item | status | evidence |
|---|---|---|
| EFF-1 fo pool | done | `Fit.daily()`/`verify()`: legs + 30 one-day chunks in a `ThreadPoolExecutor(8)` under one re-entrant `fo.lock` hold; labels ≤ 93 chars (`fo.short`; 125/125 runs rc 0, the 12:00 stamp had 28 × rc 255). Sequential: falcon-heavy 46.1 → 19.5 s, p22qgc1 25.8 → 6.5 s, fo + neocp 132 → 58 s. Real defect found on the way: fo's automatic fit is wall-clock limited (`elem_out.cpp`, 4 steps or 0.25 s), so the 313-obs Falcon fit landed in one of three minima ~300 km apart depending on load (138/233/282 samples across runs); every run now passes `-y 10`: Falcon pool vs sequential 0.02 km, NEOCP-class fits bitwise |
| EFF-2 fit memo | done | `work/cache/extrap/fits.json` (sha256(file)+t+state+span+step+nbody constants+FIT_VERSION): cdt9az2 23.5 → 1.3 s, ibex 3.4 → 0.9, chimera 1.3 → 0.4, xl8d89e 1.3 → 0.2; a hit returns the identical x (test) |
| EFF-3 candidates | done | 1-row probes `_f`/`_fy`, short `_s` table for `later_oc`, `-E 16` only for usable candidates, window table only for the chosen fit: `ephem.txt` per update-fits stamp (6 targets, 125 runs) 97.7 → 9.1 MB — a full `states` stamp (343 runs) still writes 34.5 MB of it, the window tables of 22 targets and their one-day chunks; tiandu-1 10.6 → 9.1 s, change-6 memo 0.0–0.4 s |
| EFF-4 Horizons | done | one step/2 lattice per piece (midpoint check free on every build), 10-day tiles on the 00:00 UTC lattice served onto the hour-floored window, daily 2-row `OBJ_DATA` probe → solution id: cold 752 → 463 requests / 16.7 min; +1 d refresh 127–265 → 52 requests / 1.7 min / 0.56 MB; offline rebuild from tiles 38/38 bit-identical, 0 fetched; real `ops/run.sh daily` at 17:11Z (window moved +5 h): 15 fetched / 770 from cache (the day's probes were already cached), the whole daily 243 s, `make status` → ok — yesterday's two dailies: 265 and 127 fetched, 1002 and 560 s |
| EFF-6 sidecars | done | live NEOCP tracks only in `neocp.js` (`"chunk": "neocp"`), no per-target provenance, no ISO `t0`, `src` indexes `track.srcs`, `sigma_km`/`jump_km` only when present: `dist/data` 4,863,636 → 4,111,301 B (−15 %, with EFF-8); `sanity_check.py` resolves the index (integration fix) |
| EFF-8 text payloads | done | compact `kind:ref[start..end]` sources, no `src_rows`, packed uint16/int16 stars (312 → 79 KB inlined): `catalog.js` 274 → 218 KB, `dist/index.html` 7,444,214 → 7,211,634 B (6.88 MiB); page ready 740 ms (unchanged) |
| EFF-9 + DW-05 prune | done | `make prune` (dry) / `prune-apply`, and `daily` ends with it (non-fatal): fo stamps referenced by `build/` + the previous per run label kept, their other run directories trimmed to `obs.txt`, `elements.txt`, `command.json`, `fo_stdout.txt`, `ephem.txt.gz` (fo's side files are ~80 % of a stamp), the rest deleted; `horizons_eph` referenced + newest 2; min age 1 h; refuses under `make.lock`/`fo.lock`. First apply freed 1855 MB (fo 2202 → 332 MB), the next `make states --offline` served 971/971 Horizons requests from cache. Integration: `make prune-apply` before the daily freed 226 MB (fo 804 → 605 MB, horizons_eph 105 → 55 MB; eight stamps < 1 h old left for the next run), the daily's own prune 19 MB; a full-states fo stamp was written at 104 MB (343 runs: `ephem.txt` 34.5 MB, fo side files 67 MB) and trimmed to 29 MB by prune, of which the 21 referenced run dirs are ≈ 21 MB — since the scratch-run trim (regressions row below) it is written at 54 MB |
| EFF-10 frame switch | done (2026-10-23, visual pass) | ECI 127.7 → 63.7 ms (typed `IDENTITY`), EM 109.1 → 82.9 ms and SE 106.9 → 81.5 ms (`frames.matrixNear`, 468,780 → 98,044 matrix evaluations per EM rebuild); AM1 ≤ 0.1 km SE, ≤ 7.4e-5·r EM |
| C2 states pool | done | `cle states --jobs N` (`CLE_JOBS`, default 8): gp/bgtle/extrap in a spawn pool, every fo/neocp target in order in one more worker, Horizons in the parent; `states` 208.6 s (12:00 daily, sequential, old code) → 115.4 s sequential (`CLE_JOBS=1`) → 80.0 s with 8 jobs; `make states export validate site` 83 s; pool vs sequential arrays 92/93 identical (Falcon 0.02 km). The remaining wall is the `fo.lock` chain (fo 44 + neocp 14 + 9 extrapolations from a find_orb epoch ≈ 29 s) |
| verifier regressions | done | (1) a pool run's per-target `seconds` included the `fo.lock` waits (8 jobs: adapter sum 267.9 s for 79 s of wall, the nine extrap-via-fo targets 164.3 s — 157.5 s in the verifier's final build — for 28.8 s of work): `fo.lock_wait()` sums the blocked time per process and `cli.build_one` records it apart as `lock_wait_s` — 8 jobs now 110.5 s of `seconds` (sequential 109.1) + 147.2 s of waits, extrap-via-fo 29.9 s (+126.0 s waits; multijunk 23.0 → 4.2 s + 18.8 s wait), so the README/REVIEW per-target numbers hold at any `CLE_JOBS` and the "longest first" ordering sees work, not queueing; (2) the EFF-3 "97.7 → 9.1 MB" was an update-fits stamp, a full `states` stamp wrote 104 MB (`ephem.txt` 34.5, fo side files 67.1 MB in 343 runs) until prune: probes, legs, one-day chunks and short tables are now scratch runs (`fo.run(scratch=True)`, side files deleted at once, `KEEP` = what prune keeps + `ephem.txt`) — full `states` stamp 103.7 → 53.6 MB (side files 17.0 MB, only in the 56 window / `-E 16` tables), prune still leaves 28.9 MB, outputs unchanged (`compare_states.py`: 92/93 bitwise vs the pre-change 8-job build, Falcon 0.02 km); (3) the latent `<page>_epoch` label race between pool workers sharing one `fo.STAMP`: `fo.epoch_state(cid=…)` labels the run `<cid>_epoch` (9 targets → 9 distinct run dirs; `short()` still caps the path at 93 chars). Tests: `test_fo_parallel` LockTest/ScratchTest/EpochLabelTest/PoolTest, `test_pipeline_eff` StatesLanesTest (`seconds` vs `lock_wait_s`). Re-verified 2026-10-23 with the verifier's probes on a fresh `CLE_NOW=2026-10-22T12:00:00Z make states export validate site test test-browser sanity` (128 s, green): 8 jobs 81.1 s wall, `seconds` 112.9 s + `lock_wait_s` 147.6 s, the nine 30.2 s + 126.1 s; stamp `20261023T011959Z` 343 runs / 53.6 MB (`ephem.txt` 34.5, side files 15.4, rc 0 throughout), the six find_orb targets' runs 17.7 MB (= an `update-fits` stamp); nine distinct `<cid>_epoch` dirs, no page-named one; tables 92/93 identical to the pre-fix 8-job build (Falcon 0.04 km) |

Equality (`compare.py`, `np.array_equal` per npz array, `build/states_before` vs the rebuilt 12:00 window): 84/94 targets
identical (gp 27, neocp 8, horizons 32, extrap 13, fo 3, bgtle 1). The ten that differ, with cause: integral,
xmm-newton, mms-1..4 — Horizons re-delivered their solutions on 2026-10-22 (`Revised: Oct 22`), and the old build had
spliced old-solution refine/repair pieces under the new grid (|Δr| up to 1473 km inside those spans; the base-grid
samples agree); falcon-heavy-013-stage-2 (7 m, σ 65 m) and tiandu-1 (0.2 m) — the `-y 10` fixed steps (the old
Falcon output was not reproducible at all); c45zjd1 — its find_orb epoch fit now converges to 46/52 obs, rms 0.43″
instead of 44/52, 0.46″ under the wall-clock-limited default, which moves the 215-day extrapolation by ≤ 89 km
(σ_now 1,069 km). None is a regression of the table path; the new outputs are the reference.

### Quick wins (< 2 h)

**EFF-10 (part): typed `IDENTITY` in `web/scene/frames.js`.**
In ECI, `frames.matrix()` is `out.set(IDENTITY)` with a frozen plain Array (frames.js:14, :57): 59 ms per
360k calls vs 5 ms with a Float64Array (`work/review/verify-1/identity_set.txt`). One line; ECI frame switch
102 → ≈ 40 ms. The rest of EFF-10 is under "medium".

### Medium (half day)

**EFF-2 · extrap adapter: `fit_state()` recomputed every build.** — 2 h
- Problem: `extrap.fit_state()` least-squares-fits the n-body start state to the Bill Gray file's last 20 d on
  every build; its inputs (the TLE file, which ends 2026-01-01; t = Range end; span; step) are independent of
  the window (`src/cle/adapters/extrap.py:62-81` takes no `win`). 36 finite-difference Jacobians × 7 DOP853
  propagations in Python.
- Evidence: `work/review/efficiency/profile_cdt9az2.txt`: fit_state 31.5 s under cProfile — 258
  nbody.propagate/solve_ivp calls, 49,091 RK steps, 907,497 `Bodies.accel` calls at 27 µs (13.3 s self +
  11.5 s in two CubicHermiteSpline lookups). Without the profiler (`work/review/verify-0/fit_state_verify.txt`):
  cdt9az2 21.6 s, ibex 2.5 s, chimera-geo-1 1.0 s, xl8d89e 1.1 s = 26.2 s; identical `sol.x` on a second run;
  fit span 2025-12-11..2025-12-31; the rest of `build()` for the four targets is 2.8 s. cdt9az2's result is
  flagged "indicative only" anyway (single-state rms 38,803 km → fit rms 15,725 km > FIT_POOR_KM).
- Change: cache `(sol.x, info)` in `work/cache/bgtle/derived/fit_state_<sha256(file)>_<t>_<span>_<step>.json`
  (the file sha256 is already computed for provenance). The key must also cover the nbody constants (J2,
  RTOL/ATOL, kernel path) or an integrator change leaves stale fits. Secondary and unverified (4–6 h more):
  integrate nominal + 6 perturbed states as one 42-dimensional ODE with shared Moon/Sun lookups, or evaluate
  Moon/Sun on a precomputed uniform grid with an inline cubic (6 µs → ≈ 1 µs); plausibly 2–3× on cold builds.
- Gain: −26 s per daily build (extrap 52 → ≈ 26 s), wall time because `cli.states` runs targets sequentially.
- Status 2026-10-22: **done.** `extrap.fit_state()` memoised in `work/cache/extrap/fits.json` (flock + atomic write,
  `CLE_REFRESH=1` refits, `FIT_VERSION` in the key); the four bgtle/GP fits 29.2 → 3.6 s (cdt9az2 23.2 → 1.7 s);
  `tests/test_pipeline_eff.py` FitMemoTest asserts the identical x on a hit.

**EFF-3 · fo candidate fits get full window tables before acceptance.** — 3 h
- Problem: `fo.choose()` (`src/cle/adapters/fo.py:364-404`) gives every candidate page a full `Fit`
  (whole-window -E 0 table at 2-min/30-min steps plus an -E 16 sigma run) before the acceptance test;
  change-6-orbiter fits 12 pages (24 runs) and then fails.
- Evidence: `work/cache/fo/20261021T183231Z` (obs.txt → fo_stdout.txt mtimes): change-6-orbiter 24 runs
  16.1 s, 47.5 MB, failed; tiandu-1 11 runs 8.2 s, 20.6 MB; falcon-heavy 12 runs 6.9 s; multijunk 12 runs
  5.8 s. Table cost, same fit with a 1-row vs the 21601-row 2-min table, 3 reps
  (`work/review/verify-0/fo_table_cost.txt`): tiandu-1 0.92 → 1.45 s, multijunk 0.14 → 0.80 s, change-6
  A11BT9Q 0.04 → 0.26 s, falcon (1441 rows) 1.21 → 1.40 s; E16 runs 0.10–1.39 s. 18 of 29 candidates in this
  build are usable (tiandu-1 5/5, falcon 5/6, multijunk 5/7, spektr-rg 3/3) and `choose()` ranks them by fo
  sigma at now (fo.py:388-393), so the E16 run must stay for usable candidates; only change-6 (0/12 usable)
  drops all of them.
- Change: fit candidates with a 1-row ephemeris, apply `check()`/`usable()`/`later_oc` on a short table over
  the newest batch's arc, generate the window table only for the chosen fit, keep E16 for usable candidates,
  run candidate fits in the EFF-1 pool, keep per-candidate `elements.txt` for provenance. Restructuring
  `Fit.__init__` (deferred table, -y 10 retry) touches `tests/test_adapters.py` FoTest expectations.
- Gain: −12…−17 s per build (change-6 16 → ≈ 7 s, tiandu-1 8 → ≈ 5.5 s, multijunk −4 s, falcon −3 s,
  spektr-rg −0.3 s) and −75…80 MB of ephem.txt per stamp. The original −20 s / tiandu-1 → 3 s was overstated.
- Status 2026-10-22: **done.** `Fit(table=False)` probes with a 1-row table (`_f`, retry `_fy` with `-y 30`),
  `later_oc` on a short `_s` table, `-E 16` only for usable candidates, the window table only for the chosen fit
  (`fo.py choose()`); `ephem.txt` per update-fits stamp 97.7 → 9.1 MB (6 targets, 125 runs; a full `states` stamp of
  343 runs writes 34.5 MB, mostly the 22 window tables and their one-day chunks); the candidate fits themselves are still sequential inside
  `choose()` (≈ 9 s of Falcon's 20 s, ≈ 7 s of tiandu-1's 9 s) — pooling them needs a 3-stage restructure (open).
  `tests/test_fo_memo.py` asserts no table/E16 for rejected candidates.

**EFF-9 (+ DW-05 prune half) · cache retention.** — 2.5 h
- Problem: `work/cache/fo` is 1.11 GB in 45 stamps; `build/states` references exactly one
  (`20261021T183231Z`, 124.0 MB, 19 run dirs); three superseded 125-run stamps hold 369.7 MB and
  diagnostic/partial runs 615 MB — 985–989 MB unreferenced. Inside the referenced stamp `ephem.txt` is 97.7 of
  124 MB, 81 MB of it in dirs of candidate fits that were not chosen. `fo.py:55` creates a STAMP per process;
  `daily` runs fo twice (update-fits and `states --offline`) ≈ 250 MB/day; hourly stamps 13–26 MB. `run()`
  rmtree's an existing `<label>_E<opt>` dir (fo.py:171-174) and `daily()` passes the same `..._day` label for
  every chunk (fo.py:281), so only the last one-day dir survives. `horizons_eph` holds 27.4 MB (grid 10.9, mid
  13.9, refine 1.8, repair 0.8 MB) and grows ≈ 18 MB per daily window shift (see EFF-4). No pruning code exists
  (grep prune/rmtree/flock: only `extract_neocpsat.py:488`).
- Evidence: `work/review/efficiency/stars_and_focache.txt`, `work/review/verify-1/fo_cache.txt`.
- Change: `make prune` (also called at the end of `cle states` / `daily`): keep fo stamps referenced by
  `build/states/*.json` provenance plus the previous one, delete the rest; gzip `ephem.txt` (2.8×: 3.69 →
  1.30 MB, not ~4×) or delete it from unchosen candidate dirs; give chunk runs distinct labels only if their
  retention is wanted. For `horizons_eph` apply the same rule (requests referenced by the latest build plus the
  previous stamp): the originally proposed "STOP_TIME before window start" rule keeps each file ≈ 30 d, i.e.
  0.4–0.8 GB, not 40 MB. Guard against a concurrent hourly `cle states --adapter neocp` (fo.lock or stamp
  mtime); add a test.
- Gain: −0.99 GB now; fo steady state ≈ 60–120 MB; horizons_eph ≈ 55 MB.
- Status 2026-10-22: **done.** `scripts/prune.py` (`make prune` dry run, `make prune-apply`; `daily` ends with the apply,
  non-fatal): fo stamps referenced by `build/*.json` + `build/states/*.json` plus the previous stamp per referenced run
  label are kept, and in those stamps every other run directory is trimmed to `obs.txt`, `elements.txt`,
  `command.json`, `fo_stdout.txt` and `ephem.txt.gz` (after EFF-3 `ephem.txt` is only 9 of 62 MB; `total.json`,
  `elements.json`, `fo_env.dat`, `covar.txt` are the bulk, nothing reads them back); other stamps are deleted;
  `horizons_eph` keeps the referenced stamps + the two newest; stamps < 1 h old are left alone; refuses (rc 2) under a
  live foreign `make.lock`, a held `fo.lock`, or when `build/` references nothing. First apply: 68 stamps 2152 MB → kept
  6, freed 1855 MB in 5 s, next offline build 971/971 Horizons requests from cache. Integration pass: the side-file
  trim added (3,782 files / 66 MB in the then-kept stamps on the dry run), `make prune-apply` before the real daily
  freed 226 MB in 1.3 s (fo 804 → 605 MB, horizons_eph 105 → 55 MB), the daily's own prune 19 MB. Tests:
  `test_pipeline_eff.py` PruneTest (plan/apply/idempotent, side files, locks, Makefile wiring).

**EFF-6 · lossless sidecar overhead.** — 2.5 h
- Problem: (a) `targets-neocp.js` and `targets-helio.js` carry tracks that `neocp.js` also carries and
  overwrites on load (`web/app.js:74` attaches every chunk track, then app.js:76/59 `applyNeocp` →
  `W.attach`, `web/model.js:80`): 90,515 B bitwise-identical tracks for 8NIQ001, A11IIEI, TKO0003, ZTF10Go,
  ZTF10Gu in targets-neocp.js plus cew2vg2's 101,785 B in targets-helio.js (neocp.js tracks total 192,300 B).
  (b) every target embeds its full provenance list: 273.0 KB over all chunks (860 entries, 814 distinct raw
  files, so de-duplication saves nothing); the page reads it only at `web/ui/readout.js:16` as a
  `retrieved_utc` fallback that `export.py:215` already folds into `catalog data.retrieved_utc`. (c) 1,462
  segments (1,263 in targets-cislunar.js) repeat an ISO `t0` (40.9 KB) beside `t0_ms` and a `src` string
  (46.9 KB).
- Evidence: targets-cislunar.js 2.025 MB = rv base64 1.498 + sigma 0.058 + provenance 0.206 + other JSON
  0.262 MB (vela-4-titan-3c-transtage-10: 57 segments, 34 kB provenance for 94 Horizons requests;
  1969-046-fragment: 105 segments, 21 kB metadata); `work/review/verify-1/decompose.txt`.
- Change: in `export.run` skip the track for cids present in the neocp.json override (`export.py:175` covers
  all chunks; keep the catalog entry); drop per-target provenance from the sidecars — a contract change,
  ARCHITECTURE.md:845 (§11) specifies it and `build/validation.json` carries no provenance today, so add it to
  `validate.py` or point to `build/states/<cid>.json`; drop ISO `t0` (`web/loader.js:34` uses t0_ms,
  `scripts/sanity_check.py:81` too) and intern `src` (`sanity_check.py:83` and
  `tests/fixtures/make_fixture.py:124-127` read/write it). Update §11.
- Gain: ≈ −0.54 MB of 4.5 MB (−12 %): −192 KB duplicate tracks, −273 KB provenance, −75 KB segment metadata
  (not −150 KB); positions unchanged. Risk: live NEOCP objects then depend on neocp.js loading — acceptable
  since applyNeocp overwrites anyway.
- Status 2026-10-22: **done.** `export.track()` writes `t0_ms` only, `src` as an index into a per-Track `srcs` list,
  `sigma_km`/`jump_km` only when present; a live NEOCP object's Track travels in `neocp.js` only (catalog
  `"chunk": "neocp"`); provenance stays in `build/states/<cid>.json`. Readers: `web/loader.js decodeTrack`,
  `scripts/sanity_check.py decode` (fixed in the integration pass: it still read the raw index), §11 updated,
  `tests/data_contract.test.mjs` enforces the shape. Sidecars 4,863,636 → 4,201,520 B (owner) / 4,111,301 B after the
  Horizons re-delivery (−15 %).

**EFF-8 · catalog.js and the inlined star catalog.** — 2 h
- Problem: `catalog.js` (263 KB): `sources` are 904 verbose objects (103,294 B) of which the page reads only
  kind/ref/primary/end (`readout.js:13`) and kind/ref (`:99`); `src_rows` 11,615 B has no reader in `web/`.
  The inlined Hipparcos array is 312 KB (file 372 KB; 9,827 stars, e.g. `[0.05331696, 38.30408636, 6.53]`)
  for 1–3 px sprites. Notes are already capped by `build_master.py:461` (longest 389 chars), so the proposed
  400-char cap saves 0 B — the "multijunk note 15.6 KB" is not in catalog.js.
- Evidence: `work/review/verify-1/catalog_bytes.txt`, `work/review/efficiency/stars_and_focache.txt`.
- Change: export `sources` as compact `kind:ref[start..end]` strings (57,971 B; `readout.js` needs a parser),
  drop `src_rows` (keep in master_list.json); encode stars as base64 uint16 RA/Dec + int16 mag (78.6 KB;
  20-arcsec quantisation is 0.13 px at the sky camera's 38° over 900 px; `tests/browser.mjs:71` asserts only
  the 9,827 count); update `make_fixture.py`, §11 catalog shape and the `sky.js` decode path.
- Gain: ≈ −290 KB: catalog.js 263 → ≈ 205 KB (not 150), stars 312 → 79 KB; page 7.44 → 7.2 MB (−3 %); no
  measurable load-time change (load time is set by the textures: 722 vs 375 ms with 2048-px textures).
- Status 2026-10-22: **done.** `catalog.compact()` sources as `kind:ref[start..end]` (`*` = primary), `src_rows`
  dropped, `scripts/pack_stars.py` (ra uint16, dec int16 at 19.8″, mag millimag; run by `import_mmx.py`, decoded by
  `web/sky.js unpackStars`): `catalog.js` 273,947 → 217,704 B, `web/assets/hipparcos.json` 78,704 B inlined (was
  311,944), `dist/index.html` 7,444,214 → 7,211,634 B; browser test page ready 740 ms (725–800 before).

**EFF-10 · frame switch and edge markers.** — 2 h
- Problem: runtime is not a bottleneck — 1800 frames/30 s at 1 d/s, draw 0.48 ms/frame (updateScene 0.18,
  render 0.15, positionLabels 0.14, labels.place 0.08), GC 51 ms/30 s, heap 36 → 36 MB; tier 2 + Natural chip
  (89 visible): 0.93 ms/frame, edgeMarkers 0.42 (8 off-screen chevrons rebuilt every frame with rangeText/fmt
  and a 40-step collision loop), fmt 0.18. The measurable cost is the frame-switch rebuild.
- Evidence: `work/review/efficiency/profile_page.txt`, `trails_measure.txt` (setFrame 99–123 ms for 360k
  instances: earth 127,795 + moon 231,915, 13.8 MB instanced buffers); `work/review/verify-1/rebuild_share.txt`:
  `trails.rebuild` alone 84.6 / 82.5 / 102.5 ms (se/em/eci); stubbing `frames.matrix()` gives 40.8 / 39.2 /
  38.0 ms (matrix() is 52–63 % of the rebuild); sharing R within 300-s buckets 64 / 67 / 72 ms. Edge markers:
  5 chevrons visible (cap 8), probe medians 2.7 → 2.8 → 2.5 ms (off/on/off) ≈ 0.1–0.3 ms/frame.
- Change: typed `IDENTITY` (above); in `trails.push()` compute R once per native sample and reuse it for the
  sub-samples **with the first-order ω·δ rotation about ẑ** — without it the displacement ω·δ·r reaches
  2.66e-6 × 1,800 s × 4e5 km ≈ 1,900 km for 1-h Earth-anchored samples (k = 2, EM frame), a visible sawtooth
  in DRO/NRHO trails and a breach of AM1; with it the residual (ωδ)²/2·r is ≈ 5 km (EM, 1 h) and ≈ 600 km for
  6-h SE targets seen in the EM frame, < 0.2 px; add a unit test (`tests/browser.mjs` trailSpread tolerances
  6e5 / 1.3e5 km and the JWST focus precision pass). Update edge-marker text/boxes on the 80 ms `ui()` cadence.
- Gain: frame switch ≈ 110 → 70–75 ms in SE/EM (rebuild 85 → ≈ 50 ms; the original "→ 50 ms" was the upper
  bound), ECI 102 → ≈ 40 ms; edge markers ≤ 0.3 ms/frame; 0 change at the default view.
- Status 2026-10-23: **done.** Typed `IDENTITY` (ECI 127.7 → 63.7 ms) and `frames.matrixNear` (exact R per native sample advanced by ω·δ; `trails.vertex` passes the sample epoch for the Hermite sub-samples): EM 109.1 → 82.9 ms, SE 106.9 → 81.5 ms, ECI 63.9 → 65.5 ms on this machine (floor 62–65 ms, review machine 38–40), matrix evaluations per EM rebuild 468,780 → 98,044; AM1 residual ≤ 0.1 km in SE, ≤ 7.4e-5·r in EM (< 0.1 px); markers-on-trails and JWST focus checks unchanged. Edge chevrons cost 0.02–0.06 ms per draw (style writes only on change), so no `ui()` cadence was needed (`work/review/visual-pass/overlays/eff10.mjs`).

### Larger

**EFF-1 · fo `daily()`/`verify()`: 30 sequential one-day refits per drifting target.** — 6 h
- Problem: `Fit.verify()` rebuilds the window from 30 one-day fo runs whenever fo's multi-step table drifts
  > 1 km from its one-leg propagation. Each one-day run is a full differential correction (0.7–1.2 s, the same
  as a 1-row run; the 26k-row table is negligible), run under `work/fo.lock` one at a time. ≈ 85 s of the 215 s
  state build; 7 targets are in mode `fo_daily` this build.
- Evidence: `work/review/efficiency/profile_*.txt`: falcon-heavy-013-stage-2 50.2 s wall = 44 fo runs 49.97 s,
  of which `daily()` 41.4 s (30 chunks) + `single()` legs 5.2 s + `choose()` 5 s; p22qgc1 26.1 s: daily 22.3 s
  of 25.9 s fo; cew2vg2 19.9 s: daily 16.1 s of 19.7 s. Coarser tables do not avoid the rebuild
  (`fo_step_experiment.txt`: 1800/7200/21600/43200-s steps give 817.7/815.4/806.3/792.6 km end discrepancy for
  p22qgc1; cew2vg2 320.9/320.9/320.5/316.5 km). Fitted elements/epoch are identical across the E0, leg and day
  runs. Verified parallel rerun under a 68-char path (`work/review/verify-0/fo_parallel_verify.txt`): p22qgc1
  30 chunks 21.0 s sequential+locked → 3.4 s with 8 workers (6.1×), 5.9 s with 4; cew2vg2 22 chunks 15.1 →
  2.5 s; rc 0 everywhere; `ephem.txt` bitwise identical; `elements.txt` differs only in "# Elements written";
  the only `~/.find_orb` files touched are `sof.txt` and `sofv.txt`, which the pipeline never reads. The cost
  is invisible in the stamp because only the last one-day dir survives
  (`work/review/verify-3/fo_timing.txt`: falcon-heavy report 44.6 s vs 6.9 s of surviving runs; p22qgc1 25.4
  vs 2.9 s).
- Change: (1) run the one-day chunks and the three `single()` legs through a `ThreadPoolExecutor(8)` in
  `Fit.daily()`/`Fit.verify()`; hold `work/fo.lock` once around the pool rather than dropping it
  (`../neocpsat/*.py` has no lock file and never reads sof.txt, so the lock only serialises cislunar-eye's own
  hourly-vs-daily runs). (2) Shorten run labels to cid + 8-char sha: fo truncates -O paths longer than 93 chars
  ("strlcpy overflow: dsize = 94", rc 255 — `CAPSTONE_combined_epoch_E0` at 92 chars returns 0, `_E16` at 93
  returns 255); the long-label runs in `work/cache/fo/20261021T183231Z/*/command.json` already record rc 255
  (outputs were still written there; in a longer scratch path `ephem.txt` was not written at all). (3) Optional
  per-chunk cache keyed by sha256(obs lines + args + chunk start + n + step) in `work/cache/fo/chunks/`: not
  additive with the pool — at most −12 s more on daily builds, and only for targets whose obs lines do not
  change (live NEOCP objects gain obs; `neocp.build`'s b = now + 7 d moves daily).
- Gain: cold `states` 215 → ≈ 130 s (`daily()` 85 → ≈ 15 s, verify legs in parallel −≈ 15 s; fo + neocp 143
  → ≈ 58 s); `make all` 3.5 → ≈ 2.2 min alone, ≈ 1.8 min with EFF-2/3 (`states` ≈ 100–110 s). The original
  "states → 60 s, make all 1.2 min" was overstated.
- Status 2026-10-22: **done.** (1) `Fit.daily()`/`verify()` run the legs and one-day chunks through a
  `ThreadPoolExecutor(WORKERS=8)` under one hold of `fo.lock`, which is now re-entrant per process (`fo.locked()`); the
  hourly and the daily still serialise, and `cli.states` keeps every fo/neocp target in one worker process. (2)
  `fo.short()` keeps run paths ≤ 93 chars (cid head + 8-char sha). (3) not done (not additive). Measured, sequential
  `CLE_JOBS=1`: falcon-heavy 46.1 → 19.5 s, p22qgc1 25.8 → 6.5 s, cew2vg2 19.6 → 5.7 s, fo + neocp 132 → 58 s (the
  estimate); PoolTest: 15 chunks 3.4 → 0.7 s, outputs bitwise equal. A defect surfaced under the pool: fo's automatic
  differential correction runs four steps *or* 0.25 s of wall time (`elem_out.cpp` `QUARTER_SECOND` loop), so the
  313-obs Falcon fit converged to one of three minima ~300 km apart depending on machine load — also in the old
  sequential builds (138/233/282 samples across the day's runs). Every run now adds `-y 10` fixed steps
  (+0.27 s on that fit, nothing on NEOCP-class fits): 18 concurrent/sequential Falcon chunk runs agree to 0.05 km,
  pool vs sequential full build 0.02 km, NEOCP-class fits bitwise identical. Bitwise reproducibility of the Falcon fit
  is impossible with this fo build (the budget is not configurable); a "second run equal" check needs a tolerance for
  it. `tests/test_fo_parallel.py` (lock, labels, `-y` rule, pool bitwise vs sequential).

**EFF-4 (+ DW-05 Horizons half) · Horizons fetch and cache.** — 7 h ((1)+(2)); 8–12 h with the
refine/checked/repair rewrite and tests
- Problem: per-window stamps do **not** force a full daily refetch: `Snapshots.load_index()` indexes every
  stamp's manifest (`horizons.py:128-132`, newest wins) and interior refine/repair/mid pieces keep their absolute
  epochs, so they hit across daily builds. What re-fetches on a one-day window shift is each target's
  whole-window grid (41 requests, 10.9 MB) and whole-window step/2 check (38 window-aligned mid requests,
  7.5 MB) plus the window-edge pieces: ≈ 89–110 of 752 requests (12–15 %), ≈ 229–260 s of fetch + PAUSE_S
  ≈ 4–6 min, ≈ 18 MB/day of cache growth. The cold fetch is 752 requests, 1220 s server time, 2.1–3.5 s wall
  per request ≈ 36 min; the step/2 "mid" requests are 448 (60 % of requests, 13.9 MB = 51 % of bytes). The
  offline rebuild reads 38 targets in 1.6 s. *Reconciliation:* DW-01/DW-05 and EFF-9's verifier state that a
  moved window re-fetches "all 752" (33 min); the cache filenames show one window-aligned grid and one
  window-aligned mid per target, the other 410 mids belonging to interior pieces
  (`work/review/report/horizons_daily_refetch.txt`), so 33 min is the cold or solution-changed case and
  ≈ 5 min the normal daily one.
- Evidence: `work/review/efficiency/horizons_reuse.txt` (752 requests, +1 d shift: 110 re-fetched = 41 grid +
  59 mid + 3 refine + 5 repair + probes, 260 s of 1220 s), `work/review/verify-0/horizons_manifest_reclass.txt`
  (window-edge rule: 89 = 38 grid + 49 mid + 2; stamps 141258Z 293 req in 1034 s, 143037Z 398 req in 1030 s),
  `fo_daily_and_hz_bytes.txt`.
- Change: (1) fetch each piece once at h/2 and keep every other sample: samples at t0 + k·h/2 are exactly the
  grid (even k) plus the midpoints (odd k); `skip_one()` already implements the check; LUNAR 300 s gives
  17,281 rows per 30 d < MAX_ROWS = 50,000; ARCHITECTURE §5.3 asks for the step/2 grid "on first fetch … and
  weekly", so a free per-build check keeps the contract; adapt validation `midpoint_method` and
  `test_core.test_refine_and_splice` (FakeSampler). (2) tile grid requests on fixed absolute boundaries,
  fetching whole tiles beyond the window edges (partial edge tiles would still change daily: ≈ 82 requests ≈
  4 min, no gain), keyed to the window lattice — day-aligned 00:00 pieces put the 21600-s SE_L1/L2/HELIO/OTHER
  targets off the AM2 hour-floored grid (13:00 + 6h·k); the page indexes per segment (`web/states.js:32`) so
  §11 survives, but AM2 must be amended or pieces keyed to the lattice — plus a freshness policy: a 1-row
  OBJ_DATA probe per target per day (41 × 2.6 s ≈ 1.8 min) or a TTL, because 8/38 targets have a tracking-data
  boundary inside the window (pred_after_utc 2026-10-13..10-20) and all 38 are *_merged / JPL# solutions that
  are re-delivered. (3) Keep PAUSE_S; Horizons politeness, not the cache, is the floor.
- Gain: (1) alone: cold 752 → ≈ 300 requests, ≈ 36 → 15–18 min, midpoint validation free. (1)+(2): daily
  refresh ≈ 5–6 → 2.5–3 min (DW-05's "33 → 1–2 min" assumed reusing 30-day-old chunks, i.e. stale
  ephemerides), cache growth → ≈ 4–5 MB/day.
- Status 2026-10-22: **done** ((1) + (2) + probe). `horizons.py`: one lattice per piece at step/2 (even = grid, odd =
  the Hermite check, so `midpoint_method` stays "Horizons step/2 samples" and the check runs on every build); the grid
  as `TILE_D = 10`-day tiles on the 1970-aligned 00:00 UTC lattice at gcd(step/2, 3600), clipped to the coverage, served
  by stitching cached responses whose lattice contains the requested epochs (`Snapshots.serve`) — AM2's hour-floored
  window needs no amendment; a 2-row `OBJ_DATA` probe per target per UTC day (`PROBE_TTL_S` 20 h) hashes the object
  block into `validation.solution`, tiles/refine/repair carry it, and another solution's responses are not used.
  Offline/down order: tiles → longest cached run → older solution with the caveat → Hermite re-grid of a pre-tile
  response. Measured: cold 752 → 463 requests (38 probes, 153 tiles, 153 refine, 119 repair) / 16.7 min / 35.5 MB; +1 d
  refresh 127–265 → 52 requests / 104 s / 0.56 MB (the floor is the 38 probes ≈ 85 s); offline rebuild from tiles 38/38
  bit-identical in 3 s; six targets (integral, xmm-newton, mms-1..4) changed on purpose — Horizons re-delivered them on
  10-22 and the old code had spliced old-solution refine pieces under the new grid. Real daily (`ops/run.sh daily`
  2026-10-22T17:11Z, window moved +5 h from the 12:00 build): 15 fetched / 770 from cache, 243 s end to end and
  `make status` → ok, against 265 / 127 fetched and 1002 / 560 s for yesterday's two dailies.
  `validate.py` surfaces `solution` and `fetched` per Horizons target; `tests/test_workflow_upstream.py` TileTest (6).

---

## 4. Data workflow

**Status 2026-10-22 — all 16 items applied and re-verified** (adversarial re-run of the original probes under
`work/review/workflow-verify/`; then three real `ops/run.sh daily` runs on the wall clock and one `update-neocp`):

| item | status | evidence |
|---|---|---|
| DW-01 window pin | done | `make -n daily` echoes one `CLE_NOW` 14×; daily log 2026-10-22: every step `CLE_NOW=2026-10-22T12:00:00Z` |
| DW-02 stale states | done | `not_drawn_hole.py` → "not drawn (adapter not run in this window)"; states carry `now_utc/t0/t1`; export refuses other windows |
| DW-03 frozen NOW | done | `neocp_live.py:133 NOW = _now.now('day')`; replay at 2026-11-15 keeps `satid_current` for ABC0069 / A11Ix7l |
| DW-04a Space-Track | done | `GPFetchError` → `report['gp_fetch']`, run continues on cached sets |
| DW-04b breaker | done | simulated outage: 4 Horizons targets in 7 s instead of 8 retries × 330 s each |
| DW-04c fallback | done | +1 h offline window: JWST re-gridded from the cached snapshot; rule = whole-window snapshot first, newest among them, else longest overlap (fixed 2026-10-22 after the real daily exposed newest-first dropping a day) |
| DW-06 lifecycle | done | `removed` = set membership (today: P22rrti only; TKO0003 kept by `satid_current`); `listed_now`/`last_listed` columns; "left NEOCP" chip |
| DW-07 GP staleness | done | one graded rule in `judge()` (≤30 d window, 30–120 d `stale-gp`, >120 d unusable); 7 stale-gp rows; page reads `staleness.flag` |
| DW-08 catalog freshness | closed | `make extract` + `make sweep --max-age 7` in `daily`; window-relative distances; `--now 2026-12-06` tier 1 = 50 (the rest is genuine ageing, see the DW-08 status bullet) |
| DW-09 fo memo | done | Chang'e-6 16.6 s → 0.4 s in the real daily, message "known-unfittable since 2026-10-22, waiting for new observations"; `update-fits` skipped when neocpsat inputs unchanged |
| DW-12 tests | done | 34 + 16 → 98 Python + 21 node, all green inside `make daily` |
| DW-13 ops | done | `ops/run.sh` (lock, pinned window, logs), launchd templates (not installed), `make status` → "ok" after a green daily, exit 1 on stale/failed |
| C3 extractor NOW | done | five extractors use `_now.now('day')`; outputs byte-identical under the pinned epoch |
| C4 validator | done | wired into `make catalog`; 52 bad cells fixed; 0 errors over the 8 sources |
| C5 git | done | repo with `.gitignore`; baseline staged, nothing committed |
| C7 daily tests | done | `daily` ends with `test sanity test-browser` (+15 s) |

Real-run numbers (2026-10-22): daily on a moved window 16.7 min cold-ish (265 Horizons requests fetched, 532 cached,
not 748), 9.3 min on the next hour (127 fetched), 4.8 min fully cached; hourly `update-neocp` 42 s pinned to the
daily's window. Two real-run defects were found and fixed the same day: a `fallback` key clash in
`validate.py` (NEOCP `"page_table"` string vs the Horizons dict) and the newest-first fallback choice above.

### Quick wins (< 2 h)

**DW-01 · `make daily` pins no window.** — 1 h
- Problem: `export CLE_NOW` with CLE_NOW unset puts `CLE_NOW=` (empty) in the child environment
  (`Makefile:8-9`); `timeutil.window()` treats "" as falsy and floors the wall clock to the hour; `make -n daily`
  is 9 separate cle/python invocations, each computing its own window. Horizons cache keys are sha1 of the
  request params including START_TIME/STOP_TIME = window ± 15 d (`horizons.py:132-146`, manifests show
  START_TIME "2026-10-06 13:00:00"); under CLE_OFFLINE=1 a miss raises `LookupError: offline and not cached`
  (`horizons.py:145`), caught at `cli.py:58` → status failed, not drawn, moon_check skipped.
- Evidence: `work/review/verify-2/measurements.txt` — grid requests of 7 Horizons targets hit 8/8 at
  now = 2026-10-21T13:00Z, 0/8 at 14:00Z, 1/7 at 2026-10-22T13:00Z. Before `states` the daily runs
  update-horizons (≈ 4–6 min on a normal day, ≈ 33 min cold — see EFF-4) + update-gp / tles-status /
  update-fits ≈ 2 min.
- Change: `CLE_NOW ?= $(shell date -u +%Y-%m-%dT%H:00:00Z)` at the top of the Makefile; print it in every
  target; reorder `daily` to run `neocp_live.py` and extract before `catalog`, and `catalog` before
  update-horizons/update-gp/update-fits (they choose targets from the master list); make `tles-status`
  non-fatal (`-git …`) so an offline run does not abort mid-chain. ("Refuse export when the states window
  differs" is DW-02.)
- Gain: removes a deterministic daily failure (38/91 Horizons tracks + moon_check lost for the day) for any
  fixed launchd start minute whose update-* phase crosses the hour: starts after ≈ hh:50 on a normal day,
  after ≈ hh:20 on cold / solution-change days (the verifier's "≈ 60–65 % of random starts" used the 33-min
  figure). No speed gain by itself.

**DW-04 (a)+(b) · upstream outages abort or stall the daily.** — 1 h ((c) below is +6 h)
- Problem: `gp.fetch()` runs the Space-Track skill CLI with `check=True` (`gp.py:52`) before and outside the
  per-target try/except (`cli.py:36-38`); `st.py` raw queries go through `query()` → `opener()`, which
  `sys.exit()`s on a failed login (`st.py:83`) with no CelesTrak fallback → CalledProcessError stops
  `make daily` at update-gp, so no catalog/states/export/site that day although every GP set is cached
  (`gp.records()` reads all stamps). `horizons.fetch` (`horizons.py:165-185`) makes 8 attempts with sleeps
  10+20+30+40+50+60+60+60 = 330 s (≈ 5.6 min on an instant URLError) or + 8 × 180 s timeouts ≈ 29.6 min; the
  first failing request raises ConnectionError out of `build()`, one cycle per target: 38 targets ≈ 3.6 h /
  18.7 h, and after a window move (DW-01) there is no cached response to fall back to.
- Change: (a) wrap `gp.fetch` in try/except in `cli.states()`: log, record
  `report['gp_fetch']={'status':'failed',…}`, continue with cached sets. (b) circuit breaker: after 2
  consecutive ConnectionErrors set `SNAPSHOTS.down=True` and fail remaining fetches immediately.
- Gain: a Space-Track login failure or a Horizons outage no longer aborts the daily; worst-case runtime
  bounded to minutes instead of 3.6–18.7 h.

**DW-02 · stale states can be drawn when the report entry is missing.** — 1 h (severity medium, latent)
- Problem: the "failed target is not drawn even with old states" guard works only while
  `build/states_report.json` has an entry. `cli.states()` resets the report to `{"targets": {}}` whenever the
  window differs (`cli.py:41`, `:44-46`), which an unpinned hourly `make update-neocp` (`Makefile:30-33`) does
  at any other wall-clock hour; afterwards the report holds only the 6 NEOCP targets and a standalone
  `make export` / `make build` calls `export.not_drawn(row, {})`, which returns '' when
  `build/states/<cid>.json` exists (`export.py:155-163`) and draws 86 targets from whatever states are on
  disk. Latent today: the only failed target (change-6-orbiter) has no states file (91 files = 91 drawn) and
  the current report (neocp run_utc 2026-10-21T18:34 under window 13:00) shows the hourly was run pinned.
- Evidence: `work/review/data-workflow/not_drawn_hole.txt`
  (`not_drawn(existing states, report entry missing) -> ''`); `tests/test_integration.py:17-24` covers only
  status == 'failed' and the no-states case.
- Change: treat "no report entry" as "not drawn (adapter not run in this window)"; store the window in
  StateTable meta and have export refuse states whose window ≠ CLE_NOW; add the missing-entry branch to
  `tests/test_integration.py`. Do **not** delete states on failure: `validate.py:176` deliberately reports
  "<status> (previous states kept)".
- Gain: closes the only path by which a previously-successful, now-failed target could reach dist/data from
  stale states; makes hourly and daily runs safe to interleave. No change to today's dist/data.

**DW-03 · `NOW` frozen at 2026-10-21 in the hourly NEOCP path.** — 1.5 h
- Problem: `scripts/neocp_live.py` assigns `NOW = 2026-10-21T00:00Z` once (line 121) and never rebinds it (no
  CLE_NOW, no wall clock, no `global NOW`). It drives the sat_id2 '??.??' recency test (line 750:
  `m0 <= NOW and (NOW-m0).days <= 60`, so an identification Bill logs under "Nov. ??.??" can never be
  `satid_current` — the path that selected ABC0069 = Tiandu-1 and, with earthlike_helio/lowvenc_helio,
  A11Ix7l = Falcon Heavy S2), `_recent()` (853), the "row nearest NOW" sigma (1675-1676) and `covers_now`
  (1749-1768). The lifecycle (--keep-days, --max-age) uses `utcnow()` (1193), so only selection/coverage is
  frozen. `covers_now` is consumed by `build_master.py` only for src == 'horizons' rows (281, 359), so the
  '?'/'N' drift for live rows from 2026-10-23 is cosmetic (TARGETS.md); the real loss is the sticky
  `satid_current` selection going dead for identifications dated November onward.
- Evidence: grep shows a single assignment; `history.json` reasons_last: ABC0069 ['satid_current'], A11Ix7l
  ['satid_current','earthlike_helio','lowvenc_helio'].
- Change: one helper (`scripts/_now.py` or `cle.timeutil`) giving NOW from CLE_NOW (floored to the day) else
  the wall clock; use it in `neocp_live.py`; make `covers_now` relative to that day; add a test that runs
  `neocp_live.py --offline` (flag exists, line 1180) on the cached snapshots with NOW = snapshot date + 7 d and
  asserts the sticky/satid selections. The five one-off extractors (`extract_spacetrack_gp.py:25`,
  `verify_master.py:34/36`, `extract_rocketdebris.py:31`, `build_news_tsv.py:52`, `horizons_sweep.py:77`)
  are not in `daily`, their TSVs are committed with a documented reference epoch, and `build_news_tsv.py` is a
  curated snapshot where a moving NOW is wrong — out of scope (0.5 h cosmetic at most).
- Gain: the hourly path keeps selecting newly identified artsats after 2026-10-31; covers_now and
  sigma-at-NOW notes stay meaningful.

**DW-07 · GP staleness is defined differently in catalog and page.** — 1.5 h
- Problem: `build_master.judge()` accepts a Space-Track GP up to 120 d old as 'window' (GP_OK_DAYS,
  `build_master.py:40, 230-236`): of 38 GP primaries, imp-7 is 118 d old, vela-5a 77, vela-1a 70,
  vela-5-spin-interstage 67 (tier 2), vela-5b 57, vela-1b 48, solrad-11a-b-pkm 44, dro-b 30 — all tier 1
  "ephemeris covers window", covers_window Y, verify_report.tsv ok, caveat "age N d" without the word stale.
  The page applies its own rule (`web/ui/readout.js:23-31`: GP > 14 d → hollow marker at alpha 0.4,
  `app.js:244-246`), so 9 GP rows carry ⚠ on screen (8 default-visible + vela-5-spin hidden;
  `work/review/verify-3/stale_count.out`) while TARGETS.md says they are covered. At > 120 d the primary
  silently becomes "STALE spacetrack_gp" → extrap adapter → tier 2: a scratch merge keeps IMP-7 tier 1 at
  --now 2026-10-23 and drops it on 2026-10-24 (`judge()` uses <= GP_OK_DAYS; `work/review/verify-3/bm`).
- Nuance: the mismatch is partly by design (§6.4 defines GP > 14 d as stale for the page; TARGETS.md documents
  GP 30–120 d as last resort), and `gp.py:152` already records `gp_age_d_now` in `build/states/<cid>.json`,
  just not in validation_report.md. Keeping tier 1 beyond 120 d is a policy change under AM3, not a bug fix —
  the extrap adapter (n-body from the GP state) is at least as defensible as SDP4, and IMP-7 cannot be checked
  against Horizons (sanity_check.md: no ephemeris after 2021).
- Change: one graded rule in build_master: ≤ 30 d 'window'; 30–120 d keep the GP adapter, covers_window
  'stale-gp', caveat "stale: Space-Track GP N d old"; > 120 d as now; write gp_age_d into the validation
  report; the page reads the catalog flag instead of its own 14-d constant. No tests cover `judge()`.
- Gain: TARGETS.md, validation_report.md and the page use one wording for the 9 GP tracks older than 14 d;
  no silent tier flip without a report line.

### Medium (half day)

**DW-13 · scheduling, locking, status.** — 3 h
- Problem: README.md:97 and ARCHITECTURE.md:918, 946 refer to `ops/launchd` templates; `ops/` does not exist.
  The hourly job has never run on cadence (`fetch_log.jsonl`: four ad-hoc runs 2026-10-20T16, T21, 10-21T02,
  T07Z, nothing after 07:13:41Z; neocp.txt snapshot 07:02:26Z, 19.2–19.6 h old → the page's "older than 3 h"
  banner persists, `app.js:430, :462`). Only fo runs are serialised (`work/fo.lock`). Every build/dist write is
  atomic, so an overlapping hourly and daily cannot corrupt files — the damage is last-writer-wins plus
  `cli.py:44-46` resetting `build/states_report.json` whenever the window differs (DW-02); `export
  --neocp-only` also rewrites `manifest.js` (`export.py:170-172`), contrary to ARCHITECTURE.md:938 "only
  neocp.js". No log and no single command reports data ages.
- Evidence: `work/review/data-workflow/status.txt` (prototype: neocp snapshot 19.3 h, horizons stamp 11.5 h,
  gp stamp 11.4 h, master generated_for 2026-10-21, ~/tles HEAD 2026-07-19 3a32146).
- Change: `ops/run.sh <target>` (mkdir-style lock on `work/make.lock`, CLE_NOW pinned to the daily
  `build/states_report.json now_utc` for update-neocp, log to `work/logs/<target>-<date>.log`, 14-day
  rotation); `ops/launchd/com.cislunar-eye.neocp.plist` (hourly at :45, after Bill Gray's :35 regeneration,
  `neocp_live.py:57`) and `com.cislunar-eye.daily.plist` (01:30 local); `make install-launchd` /
  `uninstall-launchd` kept opt-in (ARCHITECTURE.md:1111 Q8); `make status` from
  `work/review/data-workflow/status.py` with a non-zero exit when the NEOCP snapshot > 3 h or the daily > 36 h.
- Gain: the documented cadence actually runs; serialised runs plus pinned CLE_NOW remove the states_report
  reset and last-writer races; data ages visible in one command.

**DW-06 · NEOCP object lifecycle.** — 3.5 h
- Problem: (a) the `removed` list in `build/neocp.json` is wrong: 5 of its 6 entries (6K72D21, A11IDud,
  A11IIEo, C46PP91, P22rmMn) are present in the cached `neocp.txt` (1 hit each) and `summary.htm` (4 hits);
  only P22rrti is absent. Cause: `neocp.py:270` compares history `last_listed` (= Bill's "Last regenerated"
  stamp 2026-10-21T05:35:01Z, `neocp_live.py:1205, 1360-1363`) with `neocp_snapshot_utc` (= neocp.txt mtime
  07:02:26Z) using a 1 h threshold, so a 1 h 27 min regen/fetch gap mislabels every listed object. The page
  ignores the list (`app.js:53-62` derives removal from presence in `objects`; `p.removed` is never read), so
  no visible harm yet. (b) An object that leaves the NEOCP keeps tier_reason "on NEOCP now"
  (`build_master.py:429-432` keys on live_obs within 30 d): TKO0003 is absent from neocp.txt and summary.htm
  (last listed 2026-10-20T21:03:33Z) yet tier 1 "on NEOCP now" and drawn ("stale · since arc end 11.5 d");
  neocp_live already writes "not in current NEOCP listings (last listed …)" into notes (~line 1595, visible via
  `readout.js:72`) but nothing downstream reads it; TKO0003 is kept by the live `satid_current` reason
  (`neocp_live.py:1343-1346`), not only by --keep-days. (c) The 3-h banner is designed behaviour
  (ARCHITECTURE §6.4) — not a defect.
- Change: neocp_live writes `listed_now` (Y/N) and `last_listed` columns (update `catalog/sources/SCHEMA.md`
  and `validate_source_tsv.py:43` exact-header check); compute `removed` from set membership (listed in the
  previous parsed.json, absent now) and store it in parsed.json; build_master uses listed_now for the
  tier_reason ("left the NEOCP <date>") and a 'left' subgroup; the page shows that badge and the date.
  `test_integration.py:30` touches `removed` (empty case) only.
- Gain: correct removed list (5 false entries today); "on NEOCP now" true at most one hourly run after
  removal instead of up to 8 d.

**DW-09 · Chang'e-6 and repeated candidate refits.** — 4 h
- Problem: change-6-orbiter fails every build after 16.6 s trying 13 candidate pages whose verdicts cannot
  change until new observations arrive (2 unbound e = 1.19e3 with 4/4 obs; 11 miss the 2026-10-01 astrometry
  by 31–143°; A11Htul.html is byte-identical to Chang'e 6_combined_2026_10_01.html, so Fit dedup merged
  them): 16.1 s in 12 E0 + 12 E16 runs. `choose()` memoises only within one run (`fo.py:373-381`); Makefile
  `update-fits` (lines 27-28) always reruns although ARCHITECTURE §13 says it should run only when the hash of
  `../neocpsat/{catalog.csv,data,mpecs}` changes. Note `make daily` runs update-fits and then states, so fo
  runs twice per daily.
- Evidence: `build/validation_report.md:151-164`; `build/states/*.json` validation.candidates;
  `work/review/verify-3/fo_timing.txt`.
- Change: `work/cache/fo/verdicts.json` keyed by sha256(canonical obs lines + fo args + newest-batch obs
  keys) storing {accepted, e, later_oc_arcsec, epoch state, elements}; `choose()` skips known-rejected
  candidates and reuses accepted epoch states, re-running only the -E ephemeris for the new window; any new
  obs line changes the key; validation_report gets a "known-unfittable since 2026-10-03, waiting for new obs"
  line for Chang'e-6 instead of a traceback.
- Gain: Chang'e-6 16.6 s → < 1 s; fo 109 → ≈ 90 s; `make all` 3.5 → ≈ 3.2 min. The original "fo → 30 s,
  make all → 2 min" counted the ≈ 60 s of `daily()` one-day runs for falcon-heavy/p22qgc1, which belong to the
  accepted fit over the new window and are EFF-1's job; usable candidates are re-ranked by sigma at now, which
  changes with the window. Overlaps EFF-3 on the change-6 table cost.

### Larger

**DW-08 · catalog inputs are frozen snapshots; no extract/sweep targets.** — 6 h
- Problem: the merge is idempotent (two runs byte-identical and equal to the committed master_list.tsv /
  TARGETS.md; --now +1 d changes only 38 caveat strings) but `horizons.tsv` / `horizons_all_spacecraft.tsv`
  (350 rows) carry distances only at d_20260921 / 20261021 / 20261120; `load_distances()`
  (`build_master.py:318-331`) takes the epochs inside W0..W1 else the nearest one. At --now 2026-12-06 (W0 =
  11-21) every distance is the 11-20 one: 43 tier/reason changes, tier 1 66 → 48, falcon-heavy-013-stage-2
  and p22qgc1 drop to tier 3 "heliocentric, far in window", mmx "min 0.39 → 8.29 Mkm", artemis-p1/p2, dscovr,
  soho fall to tier 2 because horizons.tsv's frozen span_end values (11-15, 11-09, 11-02) are never re-probed;
  covers_now is a static Y/N column (53/57). New Horizons spacecraft never appear; billgray.tsv tracks ~/tles
  (HEAD 3a32146, 2026-07-19) only via a hand run of `extract_billgray.py`; neocpsat.tsv via
  `extract_neocpsat.py`; none is in the Makefile although ARCHITECTURE §13 lists an `extract` target.
  TARGETS.md hard-codes "fresh Space-Track GP (2026-10-14)", "349 spacecraft IDs" and "Decisions (2026-10-21)"
  (`build_master.py:527-529, 612, 619`).
- Evidence: `work/review/data-workflow/idem` (run1 == run2 == project outputs), `work/review/verify-3/bm`.
- Change: `make extract` (extract_billgray, extract_neocpsat, extract_lunarmission, extract_rocketdebris;
  offline, idempotent) and a weekly `make sweep` = `horizons_sweep.py --now` re-probing only the 3-epoch
  vectors of the ≈ 110 in-scope IDs plus new MB-list IDs (`horizons_sweep.py` hard-codes EPOCHS at line 77 with
  no --now; its cache is keyed by request params, so a new epoch triple is a fresh request per ID;
  `extract_neocpsat.py` runs local fo for uncached groups); make the distance columns window-relative
  (d_m15/d_0/d_p15 — the d_YYYYMMDD_km names are parsed by regex in build_master, so both scripts change);
  derive the TARGETS.md dates from the data; run `extract` before `catalog` in daily (DW-01).
- Gain: tiering stays correct past 2026-12-05 (18 tier-1 objects would otherwise be lost); new ~/tles
  commits, new batches (e.g. a new Spektr-RG page, see DW-10 in the appendix) and new Horizons IDs enter
  through `make daily` instead of a hand sequence.
- Status 2026-10-22: **closed.** `CLE_NOW=2026-10-21T13:00:00Z make sweep` run online (112 jobs, 231 requests + 485
  cached, 0 retries, ≈ 2.5 min): `horizons_all_spacecraft.tsv` carries `sweep_now` = 2026-10-21 and
  `d_m15/d_0/d_p15_km` on all 349 rows (111 in scope re-probed, span_end included: INTEGRAL / MMS-1..4 / XMM extended
  2–4 d, MMX cut to 2026-12-01); the merge at 2026-10-21 changes 48 `dist_window_km`, 16 `tier_reason`, 7
  `primary_ephem` and one tier — JUICE 3 → 1 (7.13 Mkm at the window start 10-06, which the ±30 d epochs missed):
  tier 1 66 → 67. Probe `--now 2026-12-06` with an online sweep at that date in a scratch copy (253 requests):
  tier 1 = 50 (frozen 48, offline sweep 47), no TARGETS.md Warning, "not re-probed" caveats 62 → 9 (all tier-3
  out-of-scope rows, carried by design); Europa Clipper 3 → 1 (2.17 Mkm, December flyby), CERT-2 Centaur 2 → 1.
  The other 17 tier-1 objects of 10-21 that are not tier 1 at 12-06 are real ageing, not frozen data: the SOHO /
  DSCOVR / ARTEMIS P1+P2 Horizons spans end 11-02 / 11-09 / 11-15 in today's JPL files (the sweep re-probes
  span_end, so they return once JPL extends them), 11 fit / GP / NEOCP arcs end in October (extract / NEOCP path,
  not Horizons), and JUICE / Falcon Heavy-013 S2 / p22qgc1 / cew2vg2 are genuinely far by then — the "18 tier-1
  objects lost" above overstated the frozen-data share. Unattended cadence: `horizons_sweep.py --max-age DAYS`
  exits 0 at once while every in-scope row's `sweep_now` is within DAYS of now; `make sweep` passes
  `--max-age $(SWEEP_MAX_AGE_D)` (7; `FORCE=1` drops it), is non-fatal and runs in `daily` between `extract` and
  `catalog`, so the re-probe happens about weekly with no hand run and no third launchd agent. A request that fails
  after every retry raises `HorizonsDown` (nothing cached, nothing written; the previous tables stay) instead of
  caching an empty response. Tests: `tests/test_sweep.py` (audit_age, gate paths, outage, Makefile wiring);
  `make test` 97 Python + 21 node OK; offline `make catalog states export site test-browser sanity` at the pin
  green (JUICE, new in tier 1, is "not drawn" offline until the daily's `update-horizons` fetches its window);
  `make validate` fails today in the DW-04c owner's in-flight `validate.py:255` (it assumes the `fallback` field is
  the Horizons dict, while `neocp.py:131` writes the string `"page_table"` for tko0003) — not a sweep issue,
  request filed with that owner.

**DW-12 · tests.** — 5.5 h
- Problem: 34 Python + 16 node tests pass in 10.4 s (`work/review/verify-4/make_test.log`) and cover
  adapters, frames, decode and the not_drawn failed branch, but not: neocp_live.py (1,892 lines, 0 tests:
  parse_summary / parse_satid / covers_now / removed), catalog idempotence and `judge()` thresholds
  (build_master runs 3× identically, nothing asserts it), the verify_master integration (verify_report.tsv is
  merged with no cid/freshness check, `build_master.py:593-606`; cids currently match 100/100), the not_drawn
  missing-entry branch (DW-02), and the daily/hourly window interplay. The export → page contract is already
  exercised end to end by `make test-browser` (`browser.mjs:132-134`: missing == [], tracks > 0, flyby rule,
  through decodeTrack) and `test_core` round-trip tests; what is missing is a Chrome-free field-level check
  (`loader.js:31` checks only rv.length === n·6).
- Change: `tests/test_catalog.py` (build_master twice → identical; `judge()` at 29/31/119/121 d; overrides —
  must set the module globals W0/W1/NOW that main() assigns), `tests/test_neocp_live.py` replaying
  `work/cache/neocp/snapshots` with NOW injected (imports ../neocpsat scraper/pluto/config and requests; NOW
  at `neocp_live.py:121` and `verify_master.py:36` patchable), a node test asserting the `cle/*@1` fields the
  page reads from `dist/data/*.js`, a verify_report cid/freshness guard in build_master, and the DW-02 test.
- Gain: regression coverage exactly where the daily and hourly paths break (DW-01/02/03/06); the replay test
  flags the frozen NOW immediately.

**DW-04 (c) · Horizons offline fallback.** — +6 h
- Change: when offline or `down`, pick the newest cached response for the same COMMAND/STEP_SIZE whose span
  overlaps the window and splice the overlapping part (existing Piece/splice logic); mark the track with
  caveat "Horizons snapshot of <retrieved date>" and an ephem_end event; do the same for moon_check. Any
  hour-floored window works for 300/1800/3600-s steps, but the 21600-s SE_L1/L2/HELIO/OTHER targets only when
  the hour phase matches mod 6 h, else a Hermite re-grid.
- Gain: yesterday's Horizons data with a caveat instead of 0 tracks during an outage.

---

## 5. Visuals

**Status 2026-10-23 — V1–V5, V7, V8, V10–V14, C1, C6, C8, C9 and EFF-10 applied and integrated** (owners trails,
space, app, ui, tests, then one integration pass; every build pinned to the 12:00 window, `CLE_NOW=2026-10-22T12:00:00Z`).
Before: `build/screens_before/` (staged baseline); after: `build/screens/dist-*.png` (browser test, baselines in
`build/screens_baseline/`) and the 1440 / 1920 / phone set in `build/screens/integration/` with `metrics.json`
(`work/review/visual-pass/integration/shots.mjs`). Per-item lines below. Integration fixes on top of the owners' work:
always-on labels (Earth in the L1–L2 blob and on the phone) no longer cover markers (5 → 0), chevron × Sun-label overlap
1 → 0 (Sun keep-out box sized to its line box), chevron and Sun text on a dark scrim (trails running toward the
off-screen body no longer hide it), the camera matrix updated before `updateScene()`, staleness set before the readouts
render, selection/hover rings 21/18 px, the remaining `var(--x, #fallback)` colours and 9 px rules folded into the
template / `web/theme.css`, night lights inlined, `trails.js` EFF-10 patch. Frame time (`probe(60)`, 1440 opening view):
median 2.7 → 2.8 ms, p90 3.5 → 5.2 ms (pre- vs post-integration; integration set: median 4 ms, p90 6–7 ms at 1440 and
1920 under load). Open: legend wraps to 3 rows at 1440 with the rings and sight-line layers on; V7's leader line;
lavender vs the −12° cyan under CVD; tracklet beads are not in the label occupancy (a label may sit on a bead).

Screenshots at 1440×900, 1920×1080 and 390×844 are in `work/review/visuals/`; re-measurements in
`work/review/verify-5/`, `verify-6/`, `verify-7/`.

### Quick wins (< 2 h)

**V5 · EM Lagrange glyphs and labels in every frame and zoom.** — 1 h
- Problem: `overlays.js:21-30` draws all 7 Lagrange points at alpha .85 whenever the layer is on; `app.js:308`
  adds an 8-px dot box for each and `app.js:310-313` labels them in every frame. In the default SE view the
  Moon is 66 px from Earth and EM-L1…L5 sit 56/78/59/92/67 px from Earth at 1440 (four labelled: EM-L2/L5/L4/L3)
  and 87/123/90/143/106 px at 1920 (five labelled); their dot boxes block target labels in the busiest 100 px,
  while the frame's own SE-L1/L2 are the labels that get dropped (V1).
- Evidence: `work/review/visuals/1440-01-default.png`, `1440-01b-default-earth-crop.png`;
  `work/review/verify-5/metrics.json`.
- Change: draw EM-L1…L5 only when `state.frame === 'em'`, or the Moon's screen distance (already computed from
  `bodies.moon.position`) exceeds ≈ 150 px (moonDistPx is 66/104 in the wide views and 303–546 in the
  Earth–Moon presets), or the focus is the Moon / an EM target; alpha 0 in `LagrangeLayer.update`
  (`overlays.js:27`), skip them in `positionLabels` (`app.js:308-313`), in the dot-box loop (`app.js:308`) and
  in `pick()` (`app.js:380-383`) so hidden glyphs are not hoverable. Tests only assert Lagrange geometry and
  `lagrange.layer.style[1]`.
- Gain: 4 labels, 5 glyphs and 5 dot boxes leave the ≈ 90-px core of the default 1440 view (5 labels / 4
  glyphs at 1920); EM points remain in the EM frame, Earth–Moon presets and Moon focus.
- Status 2026-10-23: **done.** `lagrange.js lagrangeVisibility(frame, moonPx, focus)` drives the glyphs, labels, dot boxes and picking; `app.js` updates the camera matrix before `updateScene()` so the Moon distance is this draw's. L1–L2 view: EM glyphs 5 → 0, no EM label, EM-L4 not pickable; Earth–Moon view (Moon 268 px out): all 7 shown (`tests/browser.mjs`). **Amended by the follow-up below (decision 4 taken literally): the 150 px rule is gone, EM points only in the EM frame or with a Moon-bound focus.**

**V8 · 30-day trails of SE-L1/L2 and heliocentric objects in the EM frame.** — 1 h
- Problem: SE_L1, SE_L2 and EARTH_NEAR_HELIO carry `cat.trail {past_s 2592000, future_s 2592000}` from
  `src/cle/catalog.py:75-91` (§9.4 specifies 30 d/30 d by design, consumed at `model.js:24`; the
  `model.js:30` fallback is never reached, so "add entries to defaultTrail()" is a no-op). In the EM preset
  (camDist 216.6 R⊕, scene 876×856) 11 of the 18 visible SE_*/helio targets draw on-screen polylines of
  826–899 px (ESCAPADE Blue 1,380 px) across the top of the scene, meaningless for those objects' motion
  (SE-L2 objects move 11.8°/d in the EM frame).
- Evidence: `work/review/visuals/1440-03-em-rotating.png`, `1920-03-em-rotating.png`;
  `work/review/verify-6/v8-em-30-30.png`, `v8-em-2-1.png`, `v8-em-025-01.png`.
- Change: frame-aware cap in `trailParams()` (`app.js:203-214`) for regimes not centred on the Earth–Moon
  system when `frame === 'em'`: ≤ 0.25 d / 0.1 d (≈ 100 px arcs; ≈ 20 px at 0.1/0.05 d), or a "far objects"
  layer toggle that clears the view. Do not trim the SE-frame halo/return arcs: AM1's per-sample rotation is
  what makes the halo and L2 loiter orbits readable, `tests/browser.mjs` asserts the SE-L2 halo spread, and
  ESCAPADE's Earth-return arc is informative.
- Gain: the six ≈ 880-px arcs in every EM view shrink to ≈ 100 px (the originally proposed 2 d/1 d would
  leave 325–449 px: JWST 410, Spektr-RG 375, Euclid 325, ESCAPADE 332–367, MMX 449, CEW2VG2 419).
- Status 2026-10-23: **done.** `trails.setFarCap` (default on): in the EM frame SE_L1/SE_L2/EARTH_NEAR_HELIO trails ≤ 0.25 d / 0.1 d unless hovered or selected. EM-frame trail ink in the 450-px disc 0.048 → 0.014 coverage (−71 %), intensity 10,981 → 2,531 (`work/review/visual-pass/integration/trails/metrics.json`). No layer toggle (not needed with the cap).

**V10 · NEOCP tracklets have no legend.** — 1 h
- Problem: tracklet dots (4 px, six-colour station cycle `overlays.js:12`, size `:68`), the yellow hairline
  (#ffe082) and the blue sight line (#a1d4fc, `:75`) appear nowhere in `#legend` (`index.template.html:76-77`);
  355 dots on 9 targets (FH-013 S2: 144). The dots do have the marker halo (same PointLayer shader, 1.6-px
  #04060c). "Colour = station" is not decodable: 57 distinct stations map onto 6 colours (`overlays.js:46`,
  `stations.size % 6`), and STATION_COLORS[0] equals the hairline colour #ffe082, so the first station's beads
  are indistinguishable from the hairline.
- Evidence: `work/review/visuals/1440-10-neocp-tracklets.png`, `1440-10b-neocp-sightlines.png`.
- Change: legend row shown when `layers.tracklets` ("• tracklet obs · — hairline · — sight line"; `legendUI()`
  already keys rows on layers/chips); when a target with tracklets is selected, station chips in the readout
  Data block carrying the station **code as text** with colour only as a grouping hint; drop #ffe082 from the
  station cycle; keep the hairline yellow (re-tinting it to the target hue would hide it in the target's own
  NEOCP_GEO red trail).
- Gain: beads, hairline and sight line become self-explanatory; station identity readable from the chips.
- Status 2026-10-23: **done.** Legend row "tracklet obs · hairline" with the tracklets layer and "sight line" with its layer; the legend bead now shows a station colour (`--bead`), not the sight-line blue; `#ffe082` out of the station cycle; station chips (code as text) in the readout: FH-013 S2 36 stations, CEW2VG2 7, 8NIQ001 4, fixture G96/T05 (`tests/browser.mjs` "station chips").

**V14 · presets snap the camera direction.** — 1.5 h
- Problem: `preset()` (`app.js:149-158`, :153) copies `planeView()` into `camera.position` in one frame, then
  `focusOn(id,false)` tweens only the focus point and log-distance (`app.js:108-117`). From the wide view the
  snap is em 3.4°, l1/l2 25.0°, earth/moon 0°; after a 40° user drag every preset except Moon snaps (wide/earth
  34.5°, em 37.2°, l1/l2 37.8°). `focusOn()` never changes direction — documented §10 behaviour ("keeps the
  camera offset"), so fly-tos are already continuous.
- Evidence: `work/review/verify-7/verify.json` v14; `work/review/visuals/metrics2.json` presetSnap.
- Change: store from/to unit directions in `tween` inside `preset()` and slerp them in `currentFocus()` with
  the same ease before the existing `setLength()` (OrbitControls re-derives its spherical from
  `camera.position` each frame); add a `saveURL()` at tween end; guard the great-circle path through the
  (0,1,0) pole; reduced-motion keeps dur 0 (`tests/browser.mjs` preset screenshots use `reduce`).
- Gain: the 5 direction-changing presets (wide, em, earth, l1, l2) become 1-s eased moves instead of 3–38°
  jumps.
- Status 2026-10-23: **done.** `web/camera.js CameraTween` (slerp + log distance, pole guard) drives presets and fly-tos: Earth–Moon → SE-L2 27.7° in all, 10.9° at 0.45 s (`tests/browser.mjs`); moves of 3.4–35° now 1 s eased at ≤ 0.11–1.7° per frame (`work/review/visual-pass/overlays/tween2.mjs`).

### Medium (half day)

**V4 · marker glyph semantics.** — 2 h
- Problem: at the default 6.5 px the SDF makes a circle of r 3.25 px, a square of half-side 2.67 px, a
  diamond of half-diagonal 3.68 px and a triangle of inradius 1.6 px (`markers.js:34-37`: 0.82 r, 0.80 r,
  0.5 r) — shape differences under 1 px at 1× DPR. "Hollow" carries three meanings (stale GP > 14 d, extrap,
  derelict; `app.js:245-246`), so 33 of the 60 visible markers are hollow (alpha histogram {1: 32, 0.82: 14,
  0.4: 14}; extrap 11, derelict 22, stale ∪ extrap = 19), and the legend says only "○ extrap · derelict"
  (`index.template.html:76`). Stale markers at alpha 0.4 nearly vanish against the trails (Vela 5A in the
  crops).
- Evidence: `work/review/visuals/1440x2-default-earth-crop.png`, `1440-01b-default-earth-crop.png`;
  `metrics2.json` markerGeom / hollow.
- Change: markers 6.5 → 8.5 px with hover/selected raised to ≈ 10/12 (today hover 8, selected 10, so an
  8.5-px idle would shrink on hover); widen the SDF shapes (square 0.82 r → 0.95 r, diamond 0.80 r → 0.95 r,
  triangle inradius 0.5 r → 0.72 r) and grow the sprite margin from +4 to about +6 px (`markers.js:20, :31`;
  a 0.95 r corner at 1.34 r plus the 1.6-px halo is 7.3 px against a 6.25-px half-sprite); reserve hollow for
  "position is not from a current ephemeris" (stale or extrap), encode derelict by fill alpha 0.7; stale alpha
  0.4 → 0.6; ring stroke 0.85 → 1.2 px; legend "○ extrapolated / stale · dim = derelict". The ring = derelict
  encoding is documented (ARCHITECTURE.md:645, §6.4 table) — amend the spec, not only the legend.
- Gain: hollow markers 33 → 19 with one meaning; stale alpha 0.4 → 0.6; shape differences ≈ 1.5 px (circle
  r 4.25 vs corner 5.71) instead of < 1 px; legend matches the encoding.
- Status 2026-10-23: **done.** 8.5 / 10 / 12 px markers, square/diamond 0.96 r, triangle 0.78 r; hollow = extrapolated or stale (60 %), derelict fill 70 %, GP 82 %; selection/hover rings 17/14 → 21/18 px. Hollow markers 32/60 → 17/60, smallest outline difference 0.58 → 1.52 px (`tests/trails.test.mjs`, browser "marker glyphs"); §6.4/§9.3 amended.

**V11 · readout density, redundancy, the scene banner.** — 2 h
- Problem: 513 of 962 visible text nodes are ≤ 10 px (53.3 %) — but 470 of them are the 100 target-list rows'
  secondary cells (.range 91, .src 89, .q 89, .st 93, .tier 39, .warn 29); scene overlays hold 16 nodes at
  10 px, right-hand readouts are mostly 10.5 px (77). The Vela 5A tooltip states the GP epoch 2026-08-05 three
  times (source string, caveat, build note; `app.js:393-395`, `model.js:81`). NEOCP staleness appears in five
  places at once (scene banner 414×28 px at (444,14), NEOCP section note, 6 ⚠ Situation rows, 4 ⚠ Stale rows,
  6 ⚠ list rows); the banner is "permanent" only because this pinned build's snapshot is 19.6 h old — with the
  hourly job running it is the intended alarm. Legend dash vs dot swatches are distinguishable at 1×
  (`work/review/verify-7/legend-4x.png`); only dim-GP vs solid past (both grey, opacity .55 vs 1) is subtle.
  Sky labels (9 px) are off by default.
- Evidence: `work/review/visuals/1440-01c-hover-vela5a.png`, `1440-12-situation-panel.png`,
  `1440-13-left-panel.png`; `work/review/verify-7/verify.json` v11.
- Change: in `tooltip()` drop buildNote when it shares the caveat's epoch and show "GP epoch 2026-08-05 · 77 d"
  once; move the 3-h staleness banner out of the scene into the NEOCP section (ARCHITECTURE §6.4 says "the
  Situation panel shows a banner", so this matches the spec; `tests/browser.mjs` reads #banner but asserts
  nothing); keep the ⚠ per row; raise `.scene-foot`/`.scene-credit` (8.5–9 px) and the list's secondary cells
  modestly — 28-px swatches at 11 px push the 362-px phone legend to 4 rows (conflicts with V13), and the
  proposal leaves .tier/.eyebrow/.live-badge at 9 px and `sub` at 8.75 px.
- Gain: tooltip 3 → 1 epoch mentions; staleness in 4 places instead of 5 and the 414×28 px keep-out box gone
  when the snapshot is old; legend gain marginal (dim-GP swatch only).
- Status 2026-10-23: **done.** `readout.js notes()/targetTip()`: Vela 5A tooltip epoch mentions 3 → 1; the NEOCP-snapshot alarm moved from the scene banner to the Situation panel's NEOCP section (banner 414×28 px → none in that case); text under 10 px other than tier tags and the credit 10 → 0 (folded into the template).

**V13 · phone layout.** — 2 h
- Problem: at 390×844 the default wide view is unusable: Earth 0.65 px, 38 of 56 on-screen markers within 30 px
  of it, 15 labels placed (≈ 8 on the blob: Earth, Moon, EM-L3, EM-L5, TESS, MMX, IMP-7, CE-3 R/B).
  In-scene overlays take title 60 + banner 44 + legend 49 (+ credit 12) = 153–165 px of the 675-px scene (the
  125-px time panel is below the scene, not inside it); the legend computes to 10 px and wraps to 3 line boxes.
  Overlays are pointer-events: none, so the canvas stays 675 px — the gain is label keep-out, not scene height.
- Evidence: `work/review/visuals/phone-01-default.png`, `phone-02-left-open.png`, `phone-03-right-open.png`;
  `work/review/verify-7/phone-default.png`, `phone-em.png`.
- Change: under max-width 700 px open on the `em` preset (V1) with fitDistance(60); collapse the legend to a
  "Legend" chip that expands on tap; one-line banner with an icon; hide the frame-note line of the title;
  label cap 40 → 16 (`new Labels(layer, narrow ? 16 : 40)`; not binding in the default view, trims 6 in em).
  `tests/browser.mjs` phone assertions (scrollWidth ≤ 390, left panel hidden, canvas ≥ 380) unaffected.
- Gain: opening phone view 0.65 → 2.56 px/R⊕ with the Moon 121 px out and 5 markers within 30 px (at the cost
  of the SE-L1 population leaving the screen); in-scene keep-out −60…80 px of 153.
- Status 2026-10-23: **done.** Phone opens on the Earth–Moon view: Earth 0.65 → 2.42 px, markers within 30/60 px 32/40 → 12/23, legend behind a chip, one-line banner, label cap 16; labels over other markers 5 → 0 and the right-edge chevrons displayed (both checked in `tests/browser.mjs`).

**V7 · focus camera for Earth-orbiting targets.** — 3 h
- Problem: focusing an HEO target (list ◎, double-click, F) flies to 1.1·apo/R⊕ (`app.js:94-106` framing())
  and keeps the previous direction (`:119-131` focusOn()): Vela 5A (207,451 km from Earth, 32.7 R⊕) gives
  camDist 35.8 R⊕, Earth projects outside the frame, 2 markers on screen, 1 label — six disconnected teal arcs
  with no body in sight.
- Evidence: `work/review/visuals/1440-07c-vela5a-focus.png`, `1920-07c-vela5a-focus.png`; metrics velaFocus.
- Change: in `focusOn()` for HEO / CISLUNAR_HEO targets with apogee-dominated Earth ellipses (key on
  `T.track.center` and |r| vs apo): `tween.d1 = max(framing(id), 1.3·|r|/(tan(fov/2)·min(1,aspect)))` — for
  Vela 5A d1 = 123.4 R⊕ puts Earth at (545,107) px with Vela centred at (438,428), 26 HEO markers in view,
  apogee radius 328 px; a frame-aware direction tween inside `currentFocus()` (`planeView('se','sun')` is
  meaningless in the EM frame; not on the zoom=false path, `app.js:560, :154`); a 1-px leader line
  target → Earth with the range. **Exclude EM_* and lunar-bound targets**: the same rule sends DRO-A to 211 R⊕,
  TESS to 167 R⊕, CAPSTONE to 146 R⊕ (framing today 25), turning "follow DRO-A" into a whole-system view of a
  40-px orbit. LUNAR_ORBIT keeps the Moon as primary.
- Gain: Earth stays on screen while following an HEO orbiter; the orbit reads as an ellipse around its primary.
- Status 2026-10-23: **done.** `camera.focusDistance` HEO rule with max(|r|, apogee) + the frame's plane view: Vela 5A camera 35.8 → 123.4 R⊕, 2 → 25 markers on screen, Earth on screen at all 17 epochs over 48 h of following (|ndc| ≤ 0.75, `tests/browser.mjs` "follow Vela 5A"). Not done: the 1-px leader line to Earth.

**V2 · the HEO tangle.** — 3.5 h
- Problem: 31 visible CISLUNAR_HEO targets share one hue (#4db6ac, `model.js:13-14`), each draws 0.5·P of past
  trail (`model.js:29`; measured 1.2–7.0 d) plus 0.7–1.5 d dashed future at 1.5 px (`trailMaterial.js:128`);
  peak alpha 0.95 for the 7 Horizons trails, 0.95×0.68 = 0.65 for the 20 GP trails (`trailMaterial.js:113`),
  0.57 for 4 extrap. In the Earth–Moon preset they overlap into a 450-px rosette; in the EM frame the rosette
  is smeared by the 13°/d frame rotation. Only selection raises a trail (hovering vela-5a leaves the highlight
  texel at 0 and only grows the marker 6.5 → 8 px, `app.js:209, :246, :577`); 22 of the 31 HEO targets are
  derelict and styled like active missions. Target labels are 32–38, below the 40 cap: missing labels are
  collision drops, not cap drops.
- Evidence: `work/review/visuals/1440-03b-earthmoon-preset-se.png`, `1440-03-em-rotating.png`,
  `1920-03-em-rotating.png`, `1440-07-vela5a-selected-default.png`, `1440x2-vela5a-earth-crop.png`.
- Change: (a) idle style for crowded Earth-orbit regimes when nothing is selected: HEO/CISLUNAR_HEO past trail
  0.25·P and base alpha 0.45 via a per-regime "dim" texel — a new `vParam.z` mode must rework all four
  `vParam.z > 0.5` tests (`trailMaterial.js:68, 103, 105, 120`) or z = 2 reads as highlight; (b) hover
  emphasis: pass `sel || state.hovered === T.id` as highlight in `app.js:209` and set `paramsDirty = true` in the
  pointermove handler (`app.js:577` sets only `dirty`); (c) derelict/debris trails at 0.6× alpha
  (`cat.status` available at `app.js:206`). (d) faint full-orbit conic rings (STYLE.conic, `osculating()`
  exists) are a separate 2–3 h item (31 ring geometries).
- Gain: idle HEO ink roughly halved for the 7 Horizons trails, −35…45 % for the 20 GP trails already at 0.65
  peak; hovered orbit legible without a click; active/derelict separation for 22 of the 60 visible targets.
- Status 2026-10-23: **done.** Idle HEO trails 0.25·P at 45 %, derelict trails 70 %, hover (marker or trail, ≈ 1 ms pick) raises the orbit. Earth–Moon view, 106-px core: trail coverage 0.243 → 0.118, intensity 2,577 → 977; 450-px disc 0.0356 → 0.0242; wide 106-px core 0.149 → 0.108 (`work/review/visual-pass/integration/trails/metrics.json`, same as the owner's after).

**V3 · regime palette.** — 3.5 h
- Problem: the 10 regime hues fail the dataviz validator on #04060c: 5 of 10 are below the chroma floor and
  read as grey (LUNAR_ORBIT #9fb4c8 C 0.037, EM #b39ddb 0.091, CISLUNAR_HEO #4db6ac 0.098, EARTH_NEAR_HELIO
  #cfd8dc 0.011, OTHER #90a4ae 0.027); adjacent EM↔LUNAR normal-vision ΔE 7.9 (floor 15); all-pairs worst
  OTHER↔HEO deutan ΔE 0.6; Lagrange grey #c9d1dc ↔ helio #cfd8dc ΔE 2.1. Status colours double as series:
  SE_L1 #ffb74d is byte-identical to `--warn` (`index.template.html:11`), NEOCP #ff5252 ≈ `--live` #ff6e6e.
  Grey-white heliocentric markers (ESCAPADE, MMX, P22qGC1, CEW2VG2 at #cfd8dc) are the same 4–5 px grey discs
  as 0–2 mag stars (star core #ccd6e6, ΔE 1.7): MMX at (407,360) and Sirius at (673,656) look alike
  (`work/review/verify-5/crops-x8.png`).
- Evidence: `work/review/visuals/palette_check.txt`, `work/review/verify-5/palette_recheck.txt`,
  `metrics2.json` starsVsMarkers.
- Change: 7 colour families in fixed legend order, candidate "L7c": LUNAR #5aa0f2, HEO + CISLUNAR_HEO #35c69a,
  EM_* + TRANSLUNAR #a99bff, SE_L1 #f2c230, SE_L2 #f07a3a, EARTH_NEAR_HELIO #e86fd4, NEOCP #ff5c5c (chroma
  ≥ 0.1, adjacent CVD ΔE ≥ 13.0, normal ≥ 16.6, contrast ≥ 3:1; the lightness-band check fails on purpose for
  1.5-px lines on near-black); OTHER stays #90a4ae; stars pure grey. **The live colours come from
  `src/cle/catalog.py:16-29` REGIMES, exported into `catalog.js regimes[].color` and applied at
  `model.js:46`** — editing `web/model.js` alone changes nothing in dist; the fix is catalog.py + `make
  export` + the ARCHITECTURE §9.3 colour table. The listed hues do **not** yet separate status from series:
  proposed `--warn` #ffd166 vs `--sun` #ffd54f ΔE 2.4, SE_L1 #f2c230 vs #ffd54f ΔE 5.3 (today 7.4), NEOCP
  #ff5c5c vs `--live` #ff6e6e ΔE 3.3, lunar #5aa0f2 vs accent #3987e5 ΔE 7.7; all-pairs L7c has NEOCP↔SE_L2
  ΔE 8.0 and EM↔lunar protan 1.7 — pick a non-yellow warn and a non-red NEOCP or recolour `--live`. Add the
  validator run to `npm test` (the script is the dataviz skill's `validate_palette.js`, not in the project).
- Gain: adjacent-pair ΔE 7.9 → ≥ 16.6 normal and 4.2 → ≥ 13 CVD; chroma ≥ 0.1 for all series; helio markers
  separated from stars; status/series separation needs the second colour pass.
- Status 2026-10-23: **done** (second colour pass included). Seven families in `src/cle/catalog.py FAMILY` (lunar `#4f81ff`, EM `#d1a9ff`, HEO `#4db99a`, SE-L1 `#e4bb1b`, SE-L2 `#e66c04`, helio `#d13ec7`, NEOCP `#d15a62`); `--warn` apricot, `--live` UI blue, `--lagrange` pale gold, star grey. Families < 0.1 chroma 4/7 → 0/7; adjacent ΔE 7.9 → 17.3 normal, 4.8 → 13.5 CVD; helio vs star 1.6 → 27.0. `web/theme.css` holds every colour token, the template none (`tests/palette.test.mjs`, 10 tests). Open: lavender vs the −12° cyan is 3.4 ΔE under CVD (separated by place).

**V12 · scale cues.** — 3.5 h
- Problem: no scale bar, no ring and no numeric scale anywhere in the scene (only terminator/twilight rings in
  `bodies.js`); Earth is 1.5 px (wide), 5.7 px (em), 2.4 px (l2); in the SE-L2 preset Earth projects to x =
  −210 and the Moon to (−160,554), both off screen, `edgeMarkers` is empty (`app.js:346-369` handles only
  natural/selected targets) and no overlay text names Earth beyond "focus SE-L2". Relative cues do exist in the
  default view (Moon 66 px out, all 7 Lagrange points, 7-d Moon trail).
- Evidence: `work/review/visuals/1440-05-focus-se-l2.png`, `work/review/verify-7/l2-1440.png`.
- Change: scale bar in the scene foot from `pixelRadius()` math (`app.js:226-229`), round value
  (10,000 km / 100,000 km / 1 LD / 1 Mkm) updated in `positionLabels` and added to keepOutBoxes; a "Scale rings"
  layer (GEO 42,164 km in the equatorial plane from the Earth quaternion, `bodies.js:54`; 1 LD at the
  instantaneous Moon distance, 391,893 km now, 356–407k range, or it misses the Moon by ≈ 7,500 km in the EM
  frame) at 0.25 alpha when Earth ≥ 2 px — i.e. never in the default wide view; Earth and Moon edge chevrons
  (requires refactoring `edgeMarkers` away from `W.targets`); legend entry, URL layer state, screenshot tests.
- Gain: an absolute scale in every view; Earth/Moon direction + range in the SE-L1/L2 and Moon views; rings
  only where Earth ≥ 2 px (em, earth, moon).
- Status 2026-10-23: **done.** Scale bar in the scene foot (200,000 km Earth–Moon view, 500,000 km L1–L2, 1 LD SE-L2, 1,000 km Moon, 10,000 km Earth); GEO / 1 LD rings layer; edge chevrons for Earth, the Moon, SE-L1/L2 with the range from the focus, on a dark scrim, clear of the Sun indicator, title and legend (SE-L2 view: 0 → 3 cues; 0 overlaps in every checked view).

**V1 · opening view.** — 4 h (ship with V2; confidence medium)
- Problem: the page opens on the L1–L2 wide view (`app.js:558`: `planeView('se','sun')·fitDistance(275)`):
  Earth at 1.50 / 2.33 / 0.65 px (1440 / 1920 / phone; `app.js:249` draws a 7-px Earth marker when
  earthPx < 2, so the visible Earth is a 7-px dot); 40 of 56 on-screen targets within a 106-px radius (12
  within 30 px, 30 within 60 px; the 17 SE-L1/L2/helio targets sit ≈ 300 px away — the original "56 of 60" was
  wrong); neither SE-L1 nor SE-L2 is labelled at 1440 (the 143-px "SE-L2 · nearly stationary" label hits a
  marker dot box at every offset; Lagrange labels are 'free' but `always` only when focused,
  `app.js:310-313`; the 29-px short key "SE-L2" is placeable, "SE-L1" is blocked by the Sun glyph keep-out).
  ARCHITECTURE.md:103 specifies the opening camera "framed on the Moon's orbit, about 30° above the Earth–Moon
  plane", README.md:51 documents "L1–L2 (default)"; no amendment covers the camera.
- Evidence: `work/review/visuals/1440-01-default.png`, `1920-01-default.png`, `phone-01-default.png`,
  `1440-01b-default-earth-crop.png`; `work/review/verify-5/metrics.json`.
- Change: open on the existing `em` preset (`app.js:150`, fitDistance(72): Earth 5.7 px, Moon orbit 352 px
  across) when no `?cam` is present; add SE-L1/SE-L2 to the edge-chevron pool ("SE-L2 · 1.50 Mkm", click flies
  there; `edgeMarkers` iterates `W.targets`, needs a Lagrange branch); keep the "L1–L2" button; in the wide
  view make own-system Lagrange labels `always:true` with the short key only, moving "· nearly stationary" to
  the tooltip/frame note (`tests/browser.mjs:75` asserts the long label); update README/ARCHITECTURE.
- Gain: Earth 1.5 → 5.7 px, within60 30 → 5, within30 12 → 2 at 1440, SE-L1/L2 always indicated — but
  on-screen targets 56 → 42 (14 SE-L/helio targets leave with no chevron), labels 28 → 39 and the HEO rosette
  grows from 106 to 450 px unless V2 ships with it.
- Status 2026-10-23: **done** (with V2). Opens on the Earth–Moon view; `?view=` / `?cam=` override. 1440: Earth 1.50 → 5.20 px, markers within 30/60 px of Earth 17/28 → 1/7, SE-L1/L2 chevrons 0 → 2, on-screen targets 56 → 42, labels 28 → 37; 1920: Earth 2.33 → 6.71 px, 12/24 → 1/5. The old framing is the L1–L2 preset (short SE-L1/SE-L2 keys, Earth label moved off the 106-px blob). README/ARCHITECTURE §2/§10 amended.

### Follow-up: review of the visual pass (2026-10-23)

A second visual review of the integrated pass (`work/review/vis-review/`, probes in `probes/`) found 16 issues; all were
re-checked and applied (owner: one fix pass; every build at `CLE_NOW=2026-10-22T12:00:00Z`; before screenshots of this pass in
`build/screens_before_fix/`, after in `build/screens/dist-*.png`, baselines regenerated). Numbers are 1440×900 at the build
epoch unless noted; pre = the integrated pass, post = this fix (`work/review/visual-pass/fix/measure.mjs`).

| Finding | Change | Pre → post |
|---|---|---|
| Beads float where no trail is drawn (high) | `TrackletLayer` sorts each target's tracklets by epoch and draws only those inside the target's drawn trail window (`app.js windowOf(id)` reads the texel `trails.restyle()` wrote); in the EM frame SE-L1/L2 / helio targets show none unless selected, hovered or focused | beads on screen, opening view 119 → 0 (57 Tiandu-1 + 51 Multijunk far from any trail, 11 8NIQ001 by the SE-L2 chevron); EM frame idle 236 → 0 (122 of off-screen targets, 101 FH-013 S2); Tiandu-1 selected: all 57 on its whole-window trail |
| Earth the hardest object to see | Earth 2–8 px: ink ring 2·r + 6 px at 80 %; Earth chevron and dot ink (ΔE 35.7 vs lunar blue, was 4.8) | opening view ring 16.4 px around the 5.2 px globe; phone 10.8 px around 2.42 px (before: nothing drawn) |
| Lunar orbit not legible | Moon trail ±14.75 d (one synodic month) outside the EM frame, 7 d back in it | Moon trail span about Earth 78° → 359° |
| LD / Mkm mixed | `rangeText`: LD only below 1 Mkm | "CEW2VG2 · 5.06 LD" → "1.95 Mkm"; SE-L2 view "Earth · 3.90 LD" → "1.50 Mkm" |
| EM glyphs in the opening view (V5 / decision 4) | `lagrangeVisibility` drops the Moon ≥ 150 px rule | opening view EM glyphs / labels 5 / 5 → 0 / 0 |
| No colour key in the page | Help: "colour = regime family" with the 8 swatches from `catalog.js`; swatches nowrap with their labels; phone help gives the touch gestures | 0 → 8 swatches |
| Look-alike legend swatches | `--hairline` #ffe082 → ink #e8eaed (ΔE vs `--sun` 4.4 → 16.6); one "tracklets" entry (beads on a hairline); GEO ring as a small circle; derelict = full + 55 % dot | legend with the rings layer at 1440: 3 → 2 rows (rings + sight lines still 3) |
| Chevrons on markers (phone) | Chevrons and the edge-placed Sun keep clear of every on-screen marker, Lagrange glyph and body | phone EM frame: SE-L2 over Euclid → none, no tap caught |
| Sun glyph on SE-L1 (phone L1–L2) | Sun goes to the edge when its in-view point is within 24 px of SE-L1 and slides clear there | Sun–SE-L1 glyph 4.2 → 65.7 px |
| No mouse hint at 1440 | "drag · scroll · dbl-click · ? keys" below the scale bar from 981 to 1600 px | hidden → shown |
| SE-L1/L2 population off screen unannounced | L chevrons carry the count of shown off-screen targets of that regime (title explains it) | "SE-L1 · 3.88 LD" → "SE-L1 · 8 · 1.49 Mkm" |
| Readout polish | "rocket body" not `rocket_body`; tooltip drops the quality when the source starts with it ("gp:3954", not "gp · gp:3954") | — (FH-013 S2 caveat/build-note dates: data owner) |
| Scale bar > scene at close focus | Steps down to 1 m with "m" labels; keep-out boxes re-measured when the bar moves > 40 px | JWST at 1.6 km: "1 km" 779.5 px → "200 m" 156 px |
| Frame switch during a tween | `setFrame` turns the camera tween's start and end (and the focus tween's start) by S_new·S_oldᵀ / R_new·R_oldᵀ | next frame after the switch: 37.4° back-jump (review probe, dist) → 1.5° (fixture check); end 29.1° → 0° off the rotated target |
| Interrupted fly-to snaps | `focusOn` eases from the current eased point when a tween is running | first-frame focus jump 292,677 km (review probe, dist) → 655 km (fixture check) |
| "1 LD" ring is the Moon distance | Named "GEO · Moon distance" (legend, layer, help, README, ARCHITECTURE); geometry unchanged (passes through the Moon) | — |

Checks added: `tests/overlays.test.mjs` (rangeText units, sub-km scale steps, `lagrangeVisibility` at 268 px),
`tests/palette.test.mjs` (hairline vs Sun / sight line / NEOCP, merged legend entry, tooltip source), `tests/browser.mjs`
(Earth ring, EM glyphs hidden in the opening view, chevron text with count and Mkm, Moon trail ≥ 300°, no bead of an
off-screen target on screen at the opening view and in the idle EM frame, selected target shows every bead, scale bar in
metres at 1.6 km, chevrons over markers at the opening, SE-L2 and phone views, phone EM-frame taps, Sun vs SE-L1 on the
phone, help colour key and touch line, short hint, setFrame mid-tween, interrupted fly-to). Frame time unchanged (mean of
3 × 150 frames, 4 alternating runs: Earth–Moon 2.43 → 2.49 ms, L1–L2 2.49 → 2.51 ms). Open: the legend still wraps to 3 rows
at 1440 with rings + sight lines on; long chevron labels reach into the core on a 390-px phone.

---

## 6. What already works well

Efficiency
- The Horizons raw cache is a rolling, sha256-verified cache across stamps: a +1 d shift reuses ≈ 640 of 752
  requests; the offline rebuild reads 38 targets in 1.6 s; everything outside `states` is negligible
  (export 0.85 s, validate 0.75 s, site 0.20 s, sanity 0.76 s, startup 0.11 s); gp (27 targets, 9 s), bgtle
  (8.7 s) and horizons adapters are cheap.
- Page runtime: 60.0 fps at 1 d/s with 0.5–0.9 ms of JS per frame, trail windowing/fading/dashing on the GPU
  via the `uNow` uniform, zero-copy base64 → typed-array views, flat 36 MB heap, 51 ms of GC per 30 s; label
  declutter costs 0.08 ms/frame at 60 targets and 0.03 ms at 89.
- Sidecar bytes are dominated by payload (74 % rv base64); the f32/f64 rule by radius is correct (f32 ulp ≤ 4 m
  below 65,536 km); every track carries a verified Hermite midpoint error and the 0.1 km lunar tolerance is met
  (0.031–0.070 km).
- fo runs are reproducible (identical elements/epoch across E0, one-leg and one-day runs; lock-free parallel
  runs bitwise identical), and the build is offline-reproducible with manifests, sha256 provenance and atomic
  writes for every output.

Data workflow
- `build_master.py` is idempotent and reproducible (two runs byte-identical and equal to the committed
  outputs; a one-day --now shift changes only the 38 GP-age caveats); hand corrections stay in
  `catalog/overrides.tsv`.
- Atomic writes everywhere that matters (StateTable.save, export.write_js, build/neocp.json,
  validate.write_atomic, the Horizons manifest, neocp_live.write_atomic); per-request sha256 manifest with an
  integrity check ("Cached Horizons response changed"); jwst rebuilds with max |Δr| = 0.
- find_orb runs are serialised by `work/fo.lock` in fresh -O directories with `command.json` recording the
  exact command, return code and grid; adapter failures are caught per target with the traceback in
  `build/validation_report.md` and a short "not drawn" reason on the page (unit-tested).
- `dist/data/manifest.js` carries per-file sha256; `core.js` meta records the kernel sha256 and the Python
  stack; the page swaps neocp.js only when `built_utc` changes; `neocp_live.py` keeps every distinct upstream
  version under `snapshots/`, a fetch log, per-designation history, sticky selection, and uses `../neocpsat`
  strictly read-only.

Visuals
- Earth: Blue Marble globe with the #ffd54f terminator fixed on the Sun line in the SE frame at both window
  ends (terminator normal · Sun = 1.000 at t0 and t1) and the #4fc3f7 Sun −12° ring per the project convention.
- Moon: LROC texture, Cassini tidal orientation (tests verify body +X toward Earth within 6°), lit limb
  consistent with the Sun indicator; lunar orbiters read clearly in the Moon focus view.
- Frames: SE / ECI / EM switching keeps the camera continuous; EM-L4/L5 at ±60° and EM-L1/L2 at the CR3BP
  roots are test-verified; DRO-A's trail closes around the Moon in the EM frame; EM-frame halo trails at SE-L2
  are compact.
- Selection emphasis (whole-window trail at 1.45× width, others at 30 %), the 17-px selection ring and the
  hover ring survive inside the HEO cluster; the Hipparcos star field is genuinely subdued and its 0.99°/day
  SE-frame drift is physically correct and explained; edge chevrons with range text are a good pattern.
- The readout is scientifically complete (range in km / R⊕ / LD, speed, RA/Dec, elongation, osculating
  elements, C3, SE-L offsets, source/quality/age/σ/coverage, caveats); dark palette, Menlo, halo labels,
  credit line, keyboard shortcuts and URL state round-trip; no console errors, no network from file://; the
  phone layout holds structurally.

---

## 7. Appendix

### Refuted findings (not recommended)

- **EFF-5 — 2048-px textures (page 7.44 → 2.41 MB, ready 600 → 370 ms).** Size and load numbers hold, but
  AM7 ("Amendments of 2026-10-21", which override the body) fixes the Moon (LROC WAC 4096×2048) and Earth
  (4096×2048 Blue Marble) textures, and "no visible change at any reachable zoom" is false: at 1.5 R_moon
  (≈ 870 km altitude, reachable toward minDistance 1.02 R) the 2048 Moon is visibly blurred — small craters
  smeared, PSNR 40.3 dB, 1.4 % of pixels differ by > 8 levels, mean gradient −4 % (`work/review/verify-0/
  shots/`, `crop-moon-2x.png`); at the Moon-fills-the-view distance 1.4 px/texel, PSNR 42.8 dB. The "9 px per
  texel at minDistance" figure is ≈ 100 px/texel; the preset Moon radius is ≈ 183 px, not ≈ 115. An
  AM7-compatible alternative: keep 4096 and re-encode the Moon (JPEG q75–85 = 2.1–3.1 MB, WebP q85 = 2.6 MB,
  `work/review/efficiency/textures.txt`), −1…−2 MB.
- **EFF-7 — 600-s lunar step to shrink targets-lunar.js (1.44 MB).** The lunar size is set by
  `BASE_STEP['LUNAR_ORBIT'] = 300` (`horizons.py:39`) with the refinement rule h·ω > OMEGA_H = 0.3
  (`horizons.py:42`, `refine()` :278-294), not by the 0.1 km tolerance. At 600 s chandrayaan-2 / danuri / lro
  have h·ω = 0.546 / 0.570 / 0.554 on 100 % of samples (`work/review/verify-1/lunar_omega.txt`), so `refine()`
  would re-request them at 300 s; only ARTEMIS P1/P2 would halve: 43,423 → 35,042 samples, −0.27 MB, trail
  instances −3 %, no frame-switch gain. Raising OMEGA_H to ≥ 0.6 is a physics change (the §6.2 Hermite bound
  grows 16×, ≈ 2 km at a 6,600-km perigee). The finding's own recommendation was to keep 300 s.
- **DW-10 — Spektr-RG batch path (glob newer `../neocpsat/mpecs/<Name>_combined_*.html` in `candidates()`).**
  The mechanism is real (`fo.py:339-345` reads only the master row's ephem_sources), but "none of which `make
  daily` runs" is false: `Makefile:68` daily = update-horizons update-gp tles-status update-fits catalog
  states …, so only `extract_neocpsat.py` is missing — one command, which DW-08's `make extract` removes. The
  glob would bypass TARGETS.md review and let the drawn source differ from the catalog primary. Survives as a
  README line on batch → extract → daily (0.5 h).
- **DW-11 — fo provenance not reproducible (obs.txt hash differs, sort obs lines, `make reproduce`).** The
  three tiandu-1 `obs.txt` files have the identical sha256 2ccf1448…; the quoted pair (f0e25c… / ae0678…) is
  `elements.txt`, which embeds "# Elements written: <wall clock>" (line 19) and can never hash stably.
  `fo.resolve()` emits the page's Astrometry order deterministically; sorting would split the 2-line s/v
  records it keeps adjacent (`fo.py:87-88, 139`). The −y 10 divergence is find_orb-side (identical obs.txt and
  fo_env.dat: 64/64 obs rms 0.44 in the build, 56/64 in the rerun; two direct reruns reproduce the build
  byte-for-byte except the timestamp; fo rewrites `~/.find_orb/sof.txt`/`sofv.txt` and another batch ended
  27 s earlier). Survives as cosmetic (1 h): read the fo version from `elements.txt:21` and three from
  `IMPORT.json` instead of the `export.py:126-127` constants; a `build_manifest.json` with CLE_NOW,
  master_list sha256 and stamps.
- **V6 — label priority terms and hysteresis.** An in-page re-implementation of `labels.place()` reproduces
  the live label set exactly (`work/review/verify-6/metrics.json`). "Effectively by depth" is wrong: the −z
  term spans 1.5e-8; all 7 active HEO payloads (IBEX, MMS-1..4, Tiandu-1, Chandra) sit at priority 329, above
  CE-3 R/B / YZ-1 Y1 R/B (299), and still fail because all four 9-px offsets collide with other markers' 8-px
  dot boxes. The proposed +60/−40/−30 terms yield an identical label set at 1440; hysteresis (+50, +200,
  sticky previous offset) gives 0.38 instead of 0.44 changes/frame (−14 %), not "near zero". Only the optional
  cluster suppression ("+N" label, ≈ 1.5 h) attacks the real cause (density).
- **V9 — σ rings for extrapolated tracks.** Extrapolated markers are not "crisp points": `app.js:245` draws
  all 11 default-visible extrap targets hollow, with the dotted extrap trail, ⚠ flags and the caveat in the
  hover tooltip (`app.js:394`). Only 4 of the 17 targets with max σ > 10,000 km are visible by default with
  data now (TKO0003, CAPSTONE, A11IIEI, IBEX). For bgtle/GP-sourced extrapolations σ is the display heuristic
  "s0 + 1e-3·r per day" (`extrap.py:34, 153`), not a covariance, while the 14 stale-GP markers (77-day-old
  TLEs) carry no σ at all — the cue would invert the trust ordering. Full implementation 4–5 h.
- **V15 — event timeline from exported events.** The page carries 93 events, not 104 (applyNeocp re-attaches
  the neocp.js payload, which has no events, dropping 6 of 8 ephem_end); tracks have up to 8,769 samples, not
  ≤ 1,441 (a naive Earth-range minimum scan yields 370 "events" for LRO); the Situation panel's "Data ending
  within ±7 d" (`readout.js:121, 127`) already lists ephemeris ends; Chrome renders no label or hover text
  for datalist options on a range input and tick colours cannot be styled (`work/review/verify-7/
  slider-label-hover.png`). What survives: a custom tick strip plus an "Upcoming" readout block with
  regime-aware minima (the shipped DRO-A track contains the 5,842 km Moon pass at 2026-10-25 18:20;
  `verify2.json`) and the applyNeocp events-merge fix, ≈ 6 h.

### Unverified

None — every retained finding was re-measured.

### Numbers reconciled in this report

- Horizons daily re-fetch: ≈ 89–110 requests / ≈ 4–6 min / ≈ 18 MB on a one-day window shift (EFF-4), not
  all 752 / 33 min (stated by DW-01, DW-05 and EFF-9's verifier); the latter is the cold or solution-changed
  case. Checked from the cache filenames, `work/review/report/horizons_daily_refetch.txt`. The DW-01 failure
  mode is unchanged (the grid requests always miss); its exposure window is narrower on a normal day.
- `make all` chain: 3.5 min today → ≈ 2.2 min with EFF-1 alone → ≈ 1.8 min with EFF-2/3 → fo ≈ 90 s with
  DW-09 on top (its change-6 gain overlaps EFF-3's).

---

## Addendum: completeness check

A second reader went through the project for what the three lenses did not examine. Each item below was checked once
by that reader only ("critic addendum (single-checked)"), not re-measured by a second verifier like §3–§5. Scripts
and outputs: `work/review/completeness/` (`dash_probe.mjs/.txt`, `extrap_parallel.py/.txt/.json`, `dpr_probe.mjs/.json`,
`test_browser.log`).

**C1 · Future-dash and extrap-dot periods are defined in time and are sub-pixel in the opening view.** — visuals, 1–1.5 h
— critic addendum (single-checked)
- Problem: the dash/dot patterns are functions of sample time, not screen length: `trailMaterial.js:99-111`
  (`mod(vT, dash)`, lit 58 % of a cycle; extrap `mod(vT, 0.5·dash) > 0.3·dash`) with `dash_s = max(120, future_s/10)`
  from `app.js:209`. The on-screen period is therefore screen speed × dash_s and shrinks with the view scale.
- Evidence (`dash_probe.txt`, speed at the marker over 60 s, 1440×900): default wide view — of 51 on-screen
  non-lunar targets, 33 have a future-dash cycle < 4 px and 20 < 2 px; all 31 CISLUNAR_HEO targets median 1.72 px
  (0.16–9.6 px); the extrap "dots" of multijunk 0.52 px, cew2vg2 0.79, CAPSTONE 1.67, Tiandu-1 1.71 (lit 0.3–1.0 px).
  Earth–Moon preset: HEO median 6.2 px, 5 of 37 under 4 px. SE-L2 and Moon presets are fine (16–29 px). A 0.93-px
  future line with a sub-pixel 42 % gap rasterises as a dimmer solid line, so "future = dashed" and "extrap = dotted"
  in the legend are not decodable where most targets sit, and future collapses into the GP "dim" encoding (cf. V11).
- Change: set `dash_s` per target on the `ui()` cadence from the current screen speed at the marker
  (`dash_s = clamp(10 px / v_px, 120 s, future_s/2)`): one texel write in `trailParams()`, no shader change; or a
  screen-space dash in the vertex shader (segment NDC length × resolution, phase from `instanceTime`).
- Gain: ≈ 10-px dash cycles at every marker in every view; the legend's two line styles become readable in the
  opening view; complements V2 (HEO ink) and V4 (hollow = extrap).
- Status 2026-10-23: **done.** Screen-space dash per segment (`trailMaterial.js`, DASH_PX 10): opening view 42 targets, cycle 8.4–11.7 px (median 10.1), before 0.04–203 px; wide view < 4 px 38/56 → 0; all 10 measured views 8.4–11.9 px, extrap dots ≈ 5 px (`work/review/visual-pass/integration/trails/dash_cycles.json`, browser check `dash cycle`).

**C2 · `cli.states` runs the 92 targets sequentially; a process pool overlaps the non-fo adapters with the fo chain.**
— efficiency, 2 h — critic addendum (single-checked)
- Problem: `cli.py:44-66` is one loop; only EFF-1 (parallelism *inside* fo) was proposed. The non-fo adapters
  (extrap 52.5 s, gp 9.2 s, bgtle 8.7 s, horizons 1.6 s = 72 s, `build/states_report.json`) have no reason to wait for
  the lock-serialised fo + neocp chain (108.7 + 34.5 = 143 s).
- Evidence (`extrap_parallel.txt`): the five bgtle/gp extrap targets built in-process without saving — sequential
  30.7 s (cdt9az2 23.6, ibex 3.7, chimera-geo-1 1.5, xl8d89e 1.4, imp-8 0.3) vs 24.8 s with 4 and 24.2 s with 8 spawn
  workers (per-target times unchanged, same sample counts), i.e. the pool cost is nil and the floor is the longest
  target. No shared state found: `Context` per process, fo `STAMP` per process (`fo.py:55`), atomic `StateTable.save`,
  the report is written only by the parent. The neocpsat/pluto-based extrap targets (9 of 14, ≈ 22 s) were not run
  here because their `fo.epoch_state` may invoke find_orb.
- Change: `ProcessPoolExecutor(4–6, spawn)` over `rows` in `cli.states()` returning `(cid, item)`; parent keeps
  `gp.fetch`, `moon_check`, `neocp.write_json` and the report; sum the per-process Horizons fetched/reused counters;
  prefix log lines with the cid; fo workers simply queue on `work/fo.lock`.
- Gain: `states` 215 → ≈ 145–150 s today (the 72 s of non-fo work disappears under the 143-s fo chain; `make all`
  3.5 → ≈ 2.4 min); on top of EFF-1 (fo + neocp ≈ 58 s) the floor becomes max(58, cdt9az2 24) ≈ 60–70 s instead of the
  ≈ 130 s stated in §3, and ≈ 60 s with EFF-2's cached fit. Not additive with EFF-1 for the fo part.
- Status 2026-10-22: **done.** `cli.states --jobs N` (`CLE_JOBS`, default 8): gp/bgtle/extrap targets in a spawn
  `ProcessPoolExecutor` (one `Context` per worker, `fo.STAMP` shared, longest previous build first), every fo/neocp
  target in order in one more worker (the re-entrant `fo.lock` is per process, so they must not share one), Horizons in
  the parent (one `Snapshots`); tables saved by the builder, report + `states_run {jobs, targets, wall_s}` by the
  parent; a lost worker's target is rebuilt in the parent; `--jobs 1` is the old loop. Measured on the 12:00 window:
  208.6 s (old sequential daily) → 115.4 s sequential → 80.0 s with 8 jobs; outputs 92/93 identical between the two
  (Falcon 0.02 km). The floor is now the `fo.lock` chain, not cdt9az2: fo 44 s + neocp 14 s + the nine extrapolations
  that start from a find_orb epoch state (≈ 29 s of lock holds; their report `seconds` are the work alone, the time
  queued on the lock is `lock_wait_s` — see the §3 regressions row);
  any further `states` gain needs shorter or fewer lock holds (candidate fits in the pool, per-chunk cache).
  `tests/test_pipeline_eff.py` StatesLanesTest/StatesPoolTest (real spawn workers, jobs 1 vs 2 identical).

**C3 · The three extractors that DW-08 puts into `make daily` carry the same frozen NOW as DW-03.** — data workflow,
0.5 h — critic addendum (single-checked)
- Evidence: `scripts/extract_billgray.py:85` `NOW = datetime(2026, 10, 21, tzinfo=UTC)`;
  `scripts/extract_lunarmission.py:67` `NOW = dt.datetime(2026, 10, 21)`; `scripts/extract_neocpsat.py:87`
  `NOW = datetime(2026, 10, 21, …)`, used for fit ordering (:735, :772), `build_row(g, D, M, NOW)` (:797), the
  report's `now_reference` (:813) and fallback epochs (:1335, :1350, :1378). DW-03 scoped out five *other* one-off
  scripts; DW-08's `make extract` would run these three daily with "now" fixed at 2026-10-21, so ended/active
  judgements, latest-fit selection and the TSVs' `covers_now` would age in place exactly as `neocp_live.py` does.
- Change: the DW-03 helper (`CLE_NOW` floored to the day, else wall clock) in all three; include them in DW-03's test.
- Gain: DW-08's daily extract stays date-correct; one definition of "now" across the pipeline.

**C4 · A TSV schema validator exists and exits 1 on errors, but nothing runs it — and two merged sources fail it
today.** — data workflow, 1 h — critic addendum (single-checked)
- Evidence: `scripts/validate_source_tsv.py` (exact header, vocabularies, formats, `return 1 if errs`) is referenced
  by neither the Makefile nor `build_master.py`. Run over `catalog/sources/*.tsv` now: `rocketdebris.tsv` 43 errors
  ("elements without frame_center", + 106 open-span warnings) and `spacetrack.tsv` 9 errors (`src 'spacetrack'` is not
  in the validator's SRC set; `obj_type`/`status` empty) — both are in `build_master.py:46` SOURCES; the other six
  sources pass (`horizons_all_spacecraft.tsv` 350 / `neocpsat_spektr_rg_history.tsv` 37 errors are not sources).
- Change: `make catalog` runs the validator over SOURCES before the merge (fail fast); add `spacetrack` to SRC; fill
  or waive the 43 `frame_center` cells; run it in DW-12's catalog test.
- Gain: a malformed extract (DW-08's daily `make extract`, a hand edit of `overrides.tsv`) stops before it reaches
  `master_list.*` / `TARGETS.md` instead of silently changing tiers.

**C5 · No version control for the curated inputs.** — data workflow, 0.5 h — critic addendum (single-checked)
- Evidence: the project is not a git repository and has no `.gitignore`; 1,407 lines of hand-curated or extracted
  TSV (`catalog/overrides.tsv` 25, `catalog/sources/*.tsv` 1,045, `master_list.tsv` 337) plus `TARGETS.md` carry
  the tiering decisions; the review's "two runs byte-identical" and `--now +1 d` checks (DW-08) were done by hand
  with scratch copies; `tles-status` already uses git for `~/tles`.
- Change: `git init`; ignore `work/`, `build/`, `dist/`, `node_modules/`; commit `catalog/`, `src/`, `scripts/`,
  `web/`, `tests/`, docs; end `make catalog` with `git diff --stat catalog/` so a daily run prints what changed in
  `master_list.*` and `TARGETS.md`.
- Gain: history and diff of every catalog decision and daily merge; DW-08's idempotence check becomes `git status`.

**C6 · The browser screenshots are bit-identical across runs, but nothing compares them.** — visuals / tests, 1.5 h
— critic addendum (single-checked)
- Evidence: all 17 `build/screens/*.png` from the 03:37 run vs a rerun now (`reducedMotion: 'reduce'`, pinned
  `?t=`): 0 differing pixels in every file; `tests/browser.mjs` only writes them (`shot()`, :32); `make test-browser`
  takes 14.4 s wall (`test_browser.log`, 47 checks).
- Change: keep `tests/baselines/*.png`; in `browser.mjs` after each `shot()` count pixels with |Δ| > 8 against the
  baseline and fail above 0.1 % (≈ 1,300 px at 1440×900), with `--update-baselines`; regenerate baselines as part of
  landing V1–V14.
- Gain: the terminator, Cassini, label and marker checks become pixel diffs; a three.js or Chrome upgrade that moves
  the rendering is caught in 15 s instead of by eye.
- Status 2026-10-23: **done.** `tests/browser.mjs` compares all 29 screenshots with `build/screens_baseline/` (|Δ| > 8 on any channel, fail > 0.1 %, diff → `build/screens_diff/`, keyed on the data manifests, `--update-baselines` only after a passing run, `--strict`). Baselines written 2026-10-23 after the visual pass; rerun Δ ≤ 2 px per shot.

**C7 · `make daily` runs `test` but neither `sanity` nor `test-browser`.** — data workflow, 0.25 h — critic addendum
(single-checked)
- Evidence: `Makefile:68` `daily: … site test`; `all:` carries the comment "then: make test test-browser sanity";
  `sanity` (0.76 s) exits 1 on a FAIL of the JS-vs-numpy, frame, Lagrange, GP-vs-Horizons and tracklet-dot checks,
  and `test-browser` (14.4 s) is the only end-to-end check of the data contract (missing chunks, flyby rule,
  markers-on-trails, no console errors).
- Change: `daily: … site test sanity test-browser`; DW-13's `run.sh` reports the failing step.
- Gain: a bad export is caught in the same unattended run (+15 s) instead of on the page the next morning.

**C8 · Earth's night side has no night lights.** — visuals, low priority, 1.5 h — critic addendum (single-checked)
- Evidence: `build/screens/fixture-earth.png` luminance inside the disc: day side 49, night side 10 (ratio 0.20;
  `AmbientLight 0.22`, `bodies.js:30`), i.e. the night hemisphere shows dim continents and nothing that says "night";
  NASA Eyes renders Black Marble city lights. `web/assets/earth_4096.jpg` is 1.15 MB.
- Change: Black Marble 4096×2048 as `emissiveMap` (≈ 1 MB JPEG inlined, AM7-compatible), ambient 0.22 → ≈ 0.10.
- Gain: the night hemisphere reads as night with cities; the terminator ring is legible from both sides.
- Status 2026-10-23: **done.** NASA Earth Observatory city lights (2400×1200, 0.23 MB, from SatObserver-MX's `earth_night.jpg`, darkened) as an emissive map faded across the terminator, ambient 0.22 → 0.10; inlined by `build_site.mjs` (page 6.90 → 7.20 MiB). Night/day disc brightness 0.22 → 0.37 (`work/review/visual-pass/overlays/night.mjs`).

**C9 · No in-page key reference.** — visuals, low priority, 0.75 h — critic addendum (single-checked)
- Evidence: `#scene-hint` lists only drag / scroll / double-click and is `display:none` at ≤ 1400 px
  (`index.template.html` CSS); the 11 shortcuts (Space, ←/→, Shift, L, F, E, M, /, Esc, Enter, [ ]) exist only in
  README.md and a few button titles.
- Change: `?` toggles a help overlay (keys + legend glossary); mention it in the hint.
- Gain: the NASA-Eyes-style controls help; the keyboard model becomes discoverable on laptop widths.
- Status 2026-10-23: **done.** `?` (or the ? chip) toggles a help panel with the 12 keys and the glyph glossary; Esc closes it without deselecting (`tests/browser.mjs` "help overlay").

**Checked, no gap found** (single-checked): Retina rendering — `probe(60)` at deviceScaleFactor 2 (canvas 1752×1712)
median 3.1–4.9 ms vs 2.8–3.4 ms at DPR 1, 4.0–4.5 vs 3.7–4.1 ms with tier 2 + Natural (`dpr_probe.json`), so the §3
frame times hold on the owner's display. Idle cost — paused: 0 draws/s, 17.5 ms of main-thread task per second
(rAF loop + `controls.update`); LIVE: 9.6 draws/s, 75 ms/s (`time.js:22` redraws at 10 Hz; at 1× the fastest
on-screen motion is LRO at ≈ 0.17 px/s in the Moon preset, so 1 Hz would do: 0.1 h, ≈ −60 ms/s on a page left open
all day). Horizons requests use `TIME_TYPE="UT"` (`horizons.py:65`), so there is no TDB/UTC offset. Trails are
depth-tested against the globes (`depthWrite:false` only). `neocp_live.py` fetches have timeouts and retries
(`http_get`, :242) and it does not run find_orb itself, so the hourly path fits each live object once
(`neocp.py:147`). NEOCP snapshots are kept only when the upstream version changes (9 files, 356 KB).
