# cislunar-eye

A NASA Eyes-style viewer of artificial objects in the Earth–Moon system and near Sun–Earth L1/L2: lunar orbiters,
Earth–Moon DROs and resonant orbits, cislunar HEOs (apogee ≥ 100,000 km), SE-L1/L2 spacecraft, Earth-returning
heliocentric objects and live MPC NEOCP candidates, with natural minimoons as an optional layer.

It is one local page, `dist/index.html`, opened from `file://`. The page carries the code, Earth/Moon textures and
stars; the ephemerides sit beside it in classic-script sidecars `dist/data/*.js`. There is no server and no network at
runtime. Every position is computed offline (`src/cle`, Python) and only interpolated in the page (cubic Hermite,
never extrapolated). Design: `ARCHITECTURE.md` (the "Amendments of 2026-10-21" override the body).

## Open it

**Online:** <https://exoplanet5.github.io/cislunar-eye/> — the last published build (data window shown in the
page). It changes only when `make publish` is run after a refresh; the local `dist/index.html` is always the newest.

```sh
open dist/index.html                     # or: make open
open "dist/index.html?t=2026-10-21T13:00:00Z&frame=em&sel=dro-a"
```

It opens LIVE (wall clock) when the clock is inside the data window, otherwise at the build's "now".
URL keys: `t`, `frame` (`se` | `eci` | `em`), `view` (`em` | `earth` | `moon` | `wide` | `l1` | `l2`), `focus`, `sel`,
`layers`, `tiers`, `trail`, `cam`. A preset travels as `view`; once the camera is dragged, `cam` replaces it.

## What you see

- **Opening view:** the Earth–Moon system in the Sun–Earth rotating frame, seen 30° above the lunar orbit plane with the
  Sun on the left. The Moon's orbit spans about 60 % of the scene width and Earth is at least 5 px in radius (2.5 px on a
  phone). SE-L1 and SE-L2 lie off screen and get edge chevrons with their range (click one to fly there). The old
  SE-L1–SE-L2 framing is the **L1–L2** preset.
- **Frame:** Sun–Earth rotating by default. The Sun is fixed on −x, SE-L2 lies on +x, and screen-up is the ecliptic-like
  normal; each trail sample is rotated at its own epoch. ECI (ICRF) and Earth–Moon rotating (Moon on +x) are one click away.
- **Bodies:** a Blue Marble Earth with city lights on the night side (NASA Earth Observatory, faded out across the
  terminator), the Sun 0° terminator (`#ffd54f`) and an optional Sun −12° ring (`#4fc3f7`), plus the LROC Moon, tidally
  oriented, with a trail over one synodic month (±14.75 d, so the lunar orbit closes in the opening view; 7 d back in the
  EM frame). Below 8 px Earth gets a thin ink ring 3 px outside its limb (a 7 px dot below 2 px), so it is never the
  hardest thing to find. Stars are real (Hipparcos, V < 6.6) but subdued, pure grey.
- **Lagrange points:** hollow diamonds with a centre dot (◈) for SE-L1/L2 and EM-L1…L5, so they never read as a target's
  hollow diamond. CR3BP roots are scaled to the instantaneous distances. The frame's own points carry short always-on
  labels (`SE-L2`); the tooltip gives the range and "nearly stationary in the Sun–Earth frame" or "co-moves with the
  Moon". The EM points appear only in the EM frame or zoomed to the Moon (the Moon, an EM point or an EM / lunar target
  focused); the SE-frame Earth–Moon opening view shows SE-L1/L2 only.
- **Targets:** tier 1 is on by default; the Tier 2 and Natural chips add the rest (toggling a chip restores each row's
  default, so hidden NEOCP rows stay hidden). Colour shows the regime family; shape shows payload, rocket body, debris or
  natural (markers 8.5 px; 10 hovered, 12 selected). A hollow marker means the position is not from a current ephemeris
  (extrapolated, or stale at 60 % alpha, §6.4); derelicts are dimmed to 70 % and GP markers to 82 % (a GP derelict 57 %). Past trails are solid and fade, future trails are thin
  and dashed with a fixed on-screen dash, extrapolated spans dotted. Lunar orbiters are drawn about the current Moon.
- **Colours:** seven regime families — lunar blue `#4f81ff`, Earth–Moon and translunar lavender `#d1a9ff`, HEO teal
  `#4db99a`, SE-L1 gold `#e4bb1b`, SE-L2 orange `#e66c04`, heliocentric magenta `#d13ec7`, NEOCP rose `#d15a62` (other
  grey). Status colours never reuse a series hue: warnings are apricot, LIVE is the UI blue, Lagrange points pale gold.
  The two-row legend at the scene foot follows the layers (tracklets — beads on an ink hairline — with the tracklets layer,
  sight line, Sun −12°, GEO · Moon distance, natural); `?` repeats the colour key with the families from `catalog.js`.
- **Labels** never cover another marker: an ordinary label with no clear spot is dropped, and the always-on ones (Earth,
  the Moon, the frame's L points, the selection) move to the least crowded side.
- **Trail lengths** follow §9.4 except: cislunar HEO / HEO trails run half a period back (≤ 7 d) and a quarter ahead
  (≤ 1.5 d); while nothing is selected they idle at a quarter period back and 45 % alpha, and derelict trails at 70 %, so
  the Earth-centred tangle of 31 HEO orbits reads. Pointing at a marker or a trail raises that orbit to full length and
  alpha. In the EM frame the 30-day SE-L1/L2 and heliocentric trails are cut to 6 h back / 2.4 h ahead. Live NEOCP
  trails run from the first observation (− 0.5 d) to now and 1 d ahead. Selecting a target draws its whole window and
  dims every other trail to 30 %.
- **Close focus:** the focused target's own trail and tracklets are re-anchored at its position (§9.2), so at 1–2 km range
  they stay on the marker even at SE-L2 (a float32 trail head relative to Earth would sit ~100 px off). Focusing a cislunar
  HEO / HEO target backs the camera off until Earth stays in view and turns it to the frame's plane view, so the orbit
  reads as an ellipse about Earth.
- **Scale:** a scale bar (1–2–5 steps from 1 m, or 1 LD = 384,400 km) at the scene foot; an optional "Scale rings" layer
  draws GEO (42,164 km) and a ring at the Moon's instantaneous distance (356,000–407,000 km over a month, so it passes
  through the Moon) about Earth when Earth is at least 2 px. Ranges read km below 100,000 km, LD below 1 Mkm, Mkm beyond.
- **List and readouts:** the left list is grouped and searchable (name, NORAD, COSPAR, Horizons ID). Rows with no
  public ephemeris carry "listed, not drawn"; rows whose build failed carry "not drawn" with a short reason (the full
  error is in `build/validation_report.md`). The right panel shows the selected target's state, osculating orbit, source,
  quality, data age, the catalog caveat (⚠) and, below it, the build's own note (e.g. the extrapolation span); a GP
  target states its epoch once ("GP epoch 2026-08-05 · 78 d · stale"), in the tooltip as well. SE-L1/L2
  targets show their distance from the L point and the Sun–Earth-frame offset instead of an Earth conic; heliocentric
  targets show no conic. With nothing selected it shows a Situation summary (live NEOCP objects have their own row).
- **NEOCP:** live candidates have tracklet dots (station position + fitted range along the observed RA/Dec, at the
  light-time epoch) with optional sight lines; a hidden target hides its dots and lines. The astrometry of identified
  targets (Multijunk, Tiandu-1, Spektr-RG, Falcon Heavy-013 S2) is drawn the same way. Objects with no data at now
  (ZTF10Go, ZTF10Gu in this build) are listed but hidden by default; flybys with data at now are shown. When the
  `neocp.js` snapshot is more than 3 h old, every NEOCP object is marked stale (⚠, hollow marker) and the Situation
  panel's NEOCP section says so; the scene banner is kept for the data-window and missing-chunk warnings. Tracklet beads
  are coloured by station (six colours in first-seen order); the selected target's readout lists the stations as chips
  with their codes and observation counts. A tracklet is drawn only inside its target's drawn trail window, so beads never
  float where no trail is drawn: the idle quarter-period HEO trails hide Tiandu-1's and Multijunk's week-old beads, and in
  the EM frame SE-L1/L2 and heliocentric targets show none unless selected, hovered or focused; selecting a target draws
  its whole window and every bead.
- **Views:** Earth–Moon (opening), Earth, Moon, L1–L2, SE-L1, SE-L2. A preset turns the camera along a great circle and
  eases its distance over 1 s (instant with reduced motion). Double-click a marker or a Lagrange point to fly to it.
  Edge chevrons give the range from the focus point to whatever is off screen: the selected target, Earth, the Moon,
  SE-L1/L2 (with the number of shown targets of that regime off screen: "SE-L1 · 8 · 1.49 Mkm") and the natural objects
  (34–131 Mkm out, beyond the 38 Mkm camera limit), on a dark scrim so trails running toward them do not hide the text,
  clear of every on-screen marker, glyph and body; click one to fly there. The Sun indicator moves to the edge when it
  would sit on the SE-L1 glyph (SE-L1 lies on the Sun line) and slides clear of markers there.
- **Keys** (`?` shows them with the colour key and the glyph legend; the scene foot says "drag · scroll · dbl-click · ?
  keys" from 981 to 1600 px wide, the full sentence above): Space play, ←/→ ±1 h (Shift ±1 d), L live, F or Enter focus
  the selected target, E Earth, M Moon, / search, Esc deselect (or close the help), [ ] previous / next visible target.
- **Phone** (≤ 700 px): the same Earth–Moon opening view, the legend behind a "Legend" chip, a one-line banner, the frame
  note hidden, at most 16 target labels; the help lists the touch gestures (drag, pinch, tap, double-tap).
- **Time:** "Now" returns to LIVE when the wall clock is inside the window, otherwise to the build epoch. The slider has
  ticks at now, the selected target's segment joins and the NEOCP observation epochs. The Layers section starts closed so
  the target list sits higher.

## Publish (GitHub Pages)

The data pipeline needs local tools (find_orb, `~/tles`, `../neocpsat`, a Space-Track login), so the page is built
here and only the static output is published: `make publish` copies `dist/index.html` and `dist/data/*.js` into one
fresh orphan commit and force-pushes it to the `gh-pages` branch, which GitHub Pages serves at
<https://exoplanet5.github.io/cislunar-eye/>. The page needs no server and makes no network requests, so the
same files work from `file://` and from Pages. Refresh stays manual: `make daily` (or `make all`), then `make publish`.

## Build

Python is `~/.venvs/astro313/bin/python -I`; Node ≥ 20 with `npm ci` (esbuild, Playwright with system Chrome).
`CLE_NOW=<ISO UTC>` pins "now"; it is floored to the hour, and the window is now ± 15 d. Unset or empty, the Makefile
floors the wall clock once, so every step of one `make` shares one window (each target echoes `CLE_NOW=…`). The
export draws a target only from a `build/states_report.json` entry and a `build/states` file stamped with the current
window; anything else is listed "not drawn (adapter not run in this window)", so hourly and daily runs may interleave
as long as they share `CLE_NOW` (`ops/run.sh` takes care of that).

| make target | network | does |
|---|---|---|
| `setup` | npm | `npm ci`, MMX import |
| `extract` | no | `scripts/extract_all.py` → `catalog/sources/*.tsv` (skipped while the script does not exist) |
| `sweep` | Horizons | `scripts/horizons_sweep.py --now $CLE_NOW --in-scope-only --max-age 7` re-probes the in-scope IDs once the audit table's sweep is more than 7 d old (`SWEEP_MAX_AGE_D`; `FORCE=1` re-probes now); non-fatal, nothing is written when Horizons is down (the daily runs it before `catalog`) |
| `catalog` | no | `scripts/validate_source_tsv.py` over the merged sources, then `catalog/master_list.*` from `catalog/sources/*.tsv` + `catalog/overrides.tsv`, then `git diff --stat -- catalog/` |
| `neocp-live` | MPC, Project Pluto | `scripts/neocp_live.py` → `catalog/sources/neocp.tsv`; non-fatal (the daily runs it before `catalog`) |
| `update-horizons` | Horizons | Horizons vectors: one 2-row solution probe per target per UTC day, then only the 10-day tiles and window-edge pieces the cache lacks (`work/cache/horizons_eph/`); a normal daily ≈ 50 requests / ≈ 2 min, a cold cache or a re-delivered solution ≈ 460 requests / ≈ 17 min |
| `update-gp` | Space-Track | fresh `gp`/`gp_history` through `~/.claude/skills/space-track/st.py`, then the GP states; a failed pull is recorded as `gp_fetch` in `build/states_report.json` and the cached sets are used |
| `update-fits` | no | local find_orb refits of the neocpsat groups (one process behind `work/fo.lock`; one fit's legs and one-day chunks run 8 at a time under one hold), only when `scripts/fits_changed.py` exits 0 (inputs changed) or `FORCE=1` |
| `update-neocp` | MPC, Project Pluto | `scripts/neocp_live.py` → fo → tracklets → `build/neocp.json` → `dist/data/neocp.js` only (`manifest.js` keeps everything but its `neocp.js` entry) |
| `states` | no | every drawn tier-1/2 target from cached snapshots → `build/states/`, `build/neocp.json`. `CLE_JOBS=N` (default 8) builds the GP / Bill Gray / extrapolated targets in N worker processes while the find_orb targets run in order in one more and Horizons in the parent; `CLE_JOBS=1` is the sequential loop. `build/states_report.json` records `states_run {jobs, targets, wall_s}`; a target's `seconds` is its own work, time queued on `work/fo.lock` is `lock_wait_s` |
| `export` | no | `build/states` + DE440s → `dist/data/*.js` |
| `validate` | no | `build/validation_report.md` (coverage, midpoint errors, regime checks, failures with reasons) |
| `site` | no | `node scripts/build_site.mjs` → `dist/index.html` |
| `all` | no | `states export validate site` |
| `test` | no | Python unittest + `node --test` (incl. `tests/data_contract.test.mjs`, the field contract of `dist/data/*.js`; skipped until `make build`) |
| `test-browser` | no | Playwright on the fixture page and `dist/index.html` from `file://` (http(s) blocked); 29 screenshots → `build/screens/`, each compared with `build/screens_baseline/` (fail above 0.1 % of pixels off by > 8; diffs → `build/screens_diff/`); after an intended visual change `node tests/browser.mjs --dist --update-baselines` |
| `sanity` | no | `scripts/sanity_check.py` → `build/sanity_check.md`; exits 1 on a FAIL |
| `tles-status` | git (read) | `~/tles` against upstream; never pulls; non-fatal |
| `status` | git (read) | `scripts/status.py`: ages of the NEOCP snapshot, Horizons/GP/fo stamps, master list, `~/tles` HEAD, last logs; exit 1 when the snapshot is > 3 h or the last daily > 36 h old (or failed) |
| `install-launchd` / `uninstall-launchd` | no | opt-in: copy the `ops/launchd` templates to `~/Library/LaunchAgents` and load them / unload and remove them; never run by `setup` or `daily` |
| `prune` / `prune-apply` | no | `scripts/prune.py`: dry run / apply. `work/cache/fo` keeps the stamps `build/` references plus the previous one per fit; in kept stamps the other run directories (one-day chunks, legs, probes, rejected candidates) are trimmed to `obs.txt`, `elements.txt`, `command.json`, `fo_stdout.txt` and a gzipped `ephem.txt`; every other stamp is deleted. `work/cache/horizons_eph` keeps the referenced stamps plus the two newest. Stamps younger than 1 h are left alone; refuses while another run holds `work/make.lock` or `work/fo.lock` |
| `daily` | yes | `neocp-live extract sweep catalog update-horizons update-gp tles-status update-fits states export validate site test sanity test-browser`, then `prune-apply` (non-fatal) |

A full offline rebuild for the 2026-10-22T12:00Z window:

```sh
CLE_NOW=2026-10-22T12:00:00Z make all test test-browser sanity
```

`make all` takes about 1.5 min: `states` is ≈ 80 s wall with the default 8 jobs (≈ 115 s sequential, `CLE_JOBS=1`;
209 s before the review items), and that wall is the find_orb chain, which every process serialises on `work/fo.lock`
(6 find_orb + 8 NEOCP targets + 9 extrapolations that start from a find_orb epoch state; Falcon Heavy-013 S2 ≈ 20 s
with its 30 one-day refits pooled, 48 s before). The per-target `seconds` in `build/states_report.json` are the target's
own work and match between the pooled and the sequential run (sum ≈ 110 s either way); the time a pooled target spent
queued on the lock is `lock_wait_s` (≈ 150 s over the nine extrapolations and the find_orb lane with 8 jobs). Export,
validate and site add ≈ 3 s. Horizons is fully cached (0 requests
fetched, 748 from cache) and the extrapolation start-state fits are memoised in `work/cache/extrap/fits.json`
(`CLE_REFRESH=1` refits; 29 → 4 s).
Every find_orb run passes `-y 10` (fixed full-improvement steps): fo's automatic fit is wall-clock limited, so without
them a long fit (Falcon Heavy, 313 obs) landed in one of several minima ~300 km apart depending on machine load; now
it repeats to ≈ 0.05 km, and the NEOCP-class fits stay bitwise identical between runs.

**Refresh cadence.**
- Hourly: `make update-neocp`. The page re-injects `data/neocp.js` every 10 min and swaps it in when `built_utc` changes.
- Daily: `make daily`. "Now" moves on; Horizons re-probes each target's solution and fetches only the tiles and
  window-edge pieces the cache lacks (≈ 50 requests, ≈ 2 min; a re-delivered solution refetches that target). `neocp-live`
  and `tles-status` never abort the chain; a Space-Track outage is recorded and the cached GP sets are used. The daily
  ends with `prune-apply` (fo cache ≈ 0.3–0.5 GB steady state instead of +250 MB/day). A full `states` run writes one
  fo stamp of ≈ 54 MB (343 runs; the probes, legs, one-day chunks and short tables drop find_orb's side files at
  once), ≈ 29 MB after prune; an `update-fits` stamp (the six find_orb targets' runs alone) is ≈ 18 MB.
- Weekly: the daily's `sweep` step re-probes Horizons once the audit table is more than 7 d old (`make sweep FORCE=1`
  by hand). After `../neocpsat` changes: `make update-fits` (the daily runs it when `scripts/fits_changed.py` says so).
- The page warns when its data window is about to end (scene banner) and when the NEOCP snapshot is more than 3 h old
  (Situation panel).
- Nothing here pushes, deploys or writes to sibling projects.

**Ops** (`ops/`). `ops/run.sh <target> [make args]` is the unattended entry point: it takes `work/make.lock` (mkdir;
a lock whose pid is dead or older than 6 h is removed, exit 75 while another run holds it), pins `CLE_NOW` (for
`update-neocp` the daily's `now_utc` from `build/states_report.json` while the wall clock is inside that window, so
the hourly never moves the window), runs `make`, and logs to `work/logs/<target>-<UTC date>.log` with a 14-day
rotation and a final `ok` / `FAILED rc=N at step <target>` line. `make status` reads those logs and the data stamps.
`ops/launchd/com.cislunar-eye.neocp.plist` (hourly at :45, after Bill Gray's :35 regeneration) and
`com.cislunar-eye.daily.plist` (01:30 local) are templates with the absolute paths filled in; they are loaded only by
an explicit `make install-launchd` (copy to `~/Library/LaunchAgents` + `launchctl bootstrap`) and removed by
`make uninstall-launchd`.

## Outputs (2026-10-22T17:00Z daily build, `ops/run.sh daily` 243 s end to end with 15 Horizons requests; `make status` → ok)

- **Targets:** 102 listed, 94 with draw=Y, and 93 drawn: 38 Horizons, 27 GP, 14 extrapolated, 1 Bill Gray TLE, the rest
  find_orb and live NEOCP fits (two NEOCP objects newly listed on 2026-10-22; JUICE back in tier 1 after the first sweep).
- **Not drawn:** Chang'e-6 orbiter, because no coherent find_orb fit covers the window (details in `build/validation_report.md`). Queqiao-1/2, Tiandu-2 and the ended objects are listed but not drawn by design.
- **Page:** `dist/index.html` is 7.2 MiB with the night-lights texture (+0.3 MB; 6.9 without, 7.1 before the packed star catalogue); `dist/data/*.js` totals 4.1 MB (4.9 before
  the lossless sidecar changes), with `targets-cislunar.js` 1.7 MB and `targets-lunar.js` 1.4 MB.
- **Speed:** opening from local disk takes about 0.7 s (the load event at 0.56 s). The median frame is 3.0 ms with tier 1, tier 2 and natural targets visible.
- **Reports:**
  - `build/validation_report.md` covers per-target coverage, midpoint errors and regime checks.
  - `build/sanity_check.md` covers JS vs numpy, SE/EM frames, Lagrange points, regimes, GP vs Horizons and tracklet dots.
  - `build/screens/dist-*.png` holds the checklist views.

## Known issues

- **DRO-A and DRO-B are not in DROs in the current Space-Track data.** Each GP set, evaluated at its own epoch, puts
  them 39,000–64,000 km from the Moon. A single SDP4 velocity is not usable for a lunar-dominated orbit, so
  `gp.arc_fit` fits one Earth + Moon + Sun trajectory to the SDP4 positions (±12 h of each epoch) of the sets in the
  last 25 days. DRO-A: the 2026-09-22 and 10-12 sets agree to 6,211 km rms; the orbit is lunar-bound and eccentric,
  25,000–116,000 km from the Moon, with a predicted close pass of about 5,900 km on 2026-10-25 (extrapolated beyond
  the last set, uncertain). DRO-B: one set (2026-09-21), 10,000–42,000 km from the Moon. Spans reaching back to
  June/July do not fit (manoeuvres), and Bill Gray's TLEs end before them. Both are relabelled `EM_OTHER` in
  `catalog/overrides.tsv`.
- **ESCAPADE Blue/Gold** have finished their SE-L2 loiter and are on the return leg to the November 2026 Earth flyby;
  they are relabelled `EARTH_NEAR_HELIO` (group "Earth-return / near-Earth heliocentric") in `catalog/overrides.tsv`.
- **Chang'e-6 orbiter** is listed but not drawn: it manoeuvres, its 2026-10-01 page is 4 observations over 23 minutes
  (unbound fit), and every older bound fit misses those observations by 31–143°.
- **NEOCP flybys** (hyperbolic fits) get a 1 d past / 0.5 d future trail by default; selecting one draws its whole track.
- **TESS Space-Track GP sets are unusable**: up to 330,992 km from Horizons, so TESS uses Horizons. The other GP vs Horizons checks agree to 114 km (Titan-3C R/B) and 327 km (XMM R/B); both of those Horizons records are TLE-based.
- **Multijunk** (area-to-mass about 20 m²/kg): the Oct 15 fit does not describe the Oct 6–9 astrometry, so a few of its tracklet dots sit up to 7,120 km off the track.
- **New NEOCP designations** appear only after `make catalog`. `update-neocp` refreshes the objects already in the master list.
- Some extrapolated tier-2 positions are effectively unknown (σ in the caveat). Examples are the Chandrayaan-3 PM and Chimera GEO-1.
- **Bill Gray extrapolations (AM5)** start from one n-body state least-squares fitted to the file's own positions over the
  last 20 d of its Range; a single TLE's velocity is several m/s off the arc the file traces. Fitted rms: IBEX 119 km
  (single TLE 6,125 km), XL8D89E 27 km, Chimera GEO-1 10 km. **CDT9AZ2** fits no better than 15,725 km rms, so its
  caveat says the position is indicative only. Table: `build/validation_report.md`, "Extrapolation start states".

## Rebuilding the target list

| Step | Script | Network |
|---|---|---|
| Bill Gray `~/tles` | `scripts/extract_billgray.py` | none |
| neocpsat catalog and fits | `scripts/extract_neocpsat.py` | none (local `fo`) |
| rocketdebris wiki | `scripts/rocketdebris_fetch_spacetrack.py`, then `scripts/extract_rocketdebris.py` | Space-Track |
| lunarmission wiki | `scripts/extract_lunarmission.py` | none |
| Horizons sweep | `scripts/horizons_sweep.py` | Horizons (cached) |
| live NEOCP | `scripts/neocp_live.py` | MPC and Project Pluto, hourly |
| news | `scripts/build_news_tsv.py` (after a manual research pass) | none |
| merge | `scripts/build_master.py` (`make catalog`) | none |
| latest GP for objects without a fresh source | `scripts/extract_spacetrack_gp.py`, then the merge again | Space-Track |
| checks | `scripts/verify_master.py`, then the merge again so `TARGETS.md` shows the result | Space-Track SATCAT |

`catalog/TARGETS.md` is the list to review. Hand corrections go only in `catalog/overrides.tsv`. Raw downloads stay under
`work/cache/`, and `work/notes/` holds the MMX reuse study and the news sources.
