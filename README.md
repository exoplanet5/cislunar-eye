# cislunar-eye

A NASA Eyes-style viewer of artificial objects in the Earth–Moon system and near Sun–Earth L1/L2: lunar orbiters,
Earth–Moon DROs and resonant orbits, cislunar HEOs (apogee ≥ 100,000 km), SE-L1/L2 spacecraft and Earth-returning
heliocentric objects. Objects on the MPC's NEO Confirmation Page are drawn **on request**: type a temporary
designation and the local helper fits it from MPC's observations and Bill Gray's neocp2 page. The viewer carries no
minimoons and no NEOCP archive (the master list still lists both; the viewer leaves them out).

It is one local page, `dist/index.html`, opened from `file://`. The page carries the code, Earth/Moon textures and
stars; the ephemerides sit beside it in classic-script sidecars `dist/data/*.js`. Every catalog position is computed
offline (`src/cle`, Python) and only interpolated in the page (cubic Hermite, never extrapolated). At runtime the page
makes one request of its own, MPC's `neocp.json` list (≥ 0.5 s after the first frame, then every 10 min); NEOCP
lookups go to the local helper `scripts/serve.py`, the only server, which runs only while `make serve` does. Without
it everything but the lookups works. Design: `ARCHITECTURE.md` (the "Amendments of 2026-11-01", then those of
2026-10-21, override the body).

## Open it

**Online:** <https://exoplanet5.github.io/cislunar-eye/> — the last published build (data window shown in the
page). It changes only when `make publish` is run after a refresh; the local `dist/index.html` is always the newest.

```sh
open dist/index.html                     # or: make open (everything but NEOCP lookups until a helper runs)
make serve                               # the same page plus NEOCP lookups: open http://127.0.0.1:8765/
open "dist/index.html?t=2026-10-21T13:00:00Z&frame=em&sel=dro-a"
```

It opens LIVE (wall clock) when the clock is inside the data window, otherwise at the build's "now".
URL keys: `t`, `frame` (`se` | `eci` | `em`), `view` (`em` | `earth` | `moon` | `wide` | `l1` | `l2`), `focus`, `sel`,
`layers`, `tiers` (`t1,t2,t3`), `trail`, `cam`, `neocp` (requested designations, comma-separated, exact case) and
`helper`. A preset travels as `view`; once the camera is dragged, `cam` replaces it. On `file://`, where a query rewrite
is refused, the hash carries the same keys.

**Helper address.** `?helper=<http(s) URL>` first, then `window.CLE_HELPER`, else the page's own origin when it is
served over http from 127.0.0.1 / localhost (`make serve`), else `http://127.0.0.1:8765`. So `file://`, Pages and the
helper's own page all reach a helper started with `make serve`; `?helper=http://127.0.0.1:<port>` reaches one started
with `--port`. The page probes `/api/health` at load only when it is served from 127.0.0.1 / localhost or has
designations to restore (`neocp=` or localStorage `cle.neocp`); otherwise on the first use of the lookup (focus, Fetch,
a list click).

## What you see

- **Layout** (desktop ≥ 1101 px): a 40 px title bar over three columns, Targets (left, 304 px) · the stage · the
  Inspector (right, 304 px; both 280 px at ≤ 1180 px). ◧ and ◨ hide a column and the scene takes the room.
  - **Title bar:** "◐ Cislunar Eye"; the one epoch clock (`EPOCH 2026-10-22 12:00:00 UTC`) with a pill that is both the
    state and the LIVE toggle (`LIVE`, `❚❚ PAUSED`, `▶ 1 h/s`; key L; marked disabled while the wall clock is outside
    the data window) and `Δ now −10 d 05 h` (`Δ build …` outside the window); the frame switch SE rotating | ECI | EM
    rotating (keys 1 2 3); `?`, `◧ Targets`, `Inspector ◨`.
  - **Stage header:** the view title and frame note ("Sun–Earth rotating · focus Earth", "Earth-centred · Sun fixed on
    −x · ẑ ecliptic normal"), the VIEW presets (a select at ≤ 1240 px) and `Layers 6/11 ▾`, a popover in three groups:
    Scene (trails, labels, Lagrange points, Moon trail, scale rings), Observations (tracklets, sight lines), Sky (stars,
    star names, RA/Dec grid, constellations), plus trail length and star brightness. Warnings (data window ending, wall
    clock outside the window, missing chunks) take a one-line strip between the header and the scene, never the render.
  - **On the render** only the scale bar (bottom right), the Sun indicator, edge chevrons, the tooltip and the `?` help;
    the legend, credit, view title and banner sit outside it.
- **Opening view:** the Earth–Moon system in the Sun–Earth rotating frame, seen 30° above the lunar orbit plane with the
  Sun on the left. The Moon's orbit spans about 60 % of the scene width and Earth is at least 5 px in radius (2.5 px on a
  phone). SE-L1 and SE-L2 lie off screen and get edge chevrons with their range (click one to fly there). The old
  SE-L1–SE-L2 framing is the **L1–L2** preset.
- **Frame:** Sun–Earth rotating by default. The Sun is fixed on −x, SE-L2 lies on +x, and screen-up is the ecliptic-like
  normal; each trail sample is rotated at its own epoch. ECI (ICRF) and Earth–Moon rotating (Moon on +x) are one click
  (or key) away.
- **Bodies:** a Blue Marble Earth with city lights on the night side (NASA Earth Observatory, faded out across the
  terminator) and the Sun 0° terminator (`#ffd54f`; the Sun −12° twilight ring is gone), plus the LROC Moon, tidally
  oriented, with a trail over one synodic month (±14.75 d, so the lunar orbit closes in the opening view; 7 d back in the
  EM frame). Below 8 px Earth gets a thin ink ring 3 px outside its limb (a 7 px dot below 2 px), so it is never the
  hardest thing to find. Stars are real (Hipparcos, V < 6.6) but subdued, pure grey.
- **Lagrange points:** hollow diamonds with a centre dot (◈) for SE-L1/L2 and EM-L1…L5, so they never read as a target's
  hollow diamond. CR3BP roots are scaled to the instantaneous distances. The frame's own points carry short always-on
  labels (`SE-L2`); the tooltip gives the range and "nearly stationary in the Sun–Earth frame" or "co-moves with the
  Moon". The EM points appear only in the EM frame or zoomed to the Moon (the Moon, an EM point or an EM / lunar target
  focused); the SE-frame Earth–Moon opening view shows SE-L1/L2 only.
- **Targets:** tier 1 is on by default; the Tier 2 chip adds the 22 tier-2 rows (a Tier 3 chip shows only when the
  export carries tier-3 rows; it carries tiers 1–2). Toggling a chip restores each row's default. Colour shows the
  regime family; shape shows payload, rocket body or debris (markers 8.5 px; 10 hovered, 12 selected). A hollow marker
  means the position is not from a current ephemeris (extrapolated, or stale at 60 % alpha, §6.4); derelicts are dimmed
  to 70 % and GP markers to 82 % (a GP derelict 57 %). Past trails are solid and fade, future trails are thin and dashed
  with a fixed on-screen dash, extrapolated spans dotted. Lunar orbiters are drawn about the current Moon.
- **Colours:** seven regime families — lunar blue `#4f81ff`, Earth–Moon and translunar lavender `#d1a9ff`, HEO teal
  `#4db99a`, SE-L1 gold `#e4bb1b`, SE-L2 orange `#e66c04`, heliocentric magenta `#d13ec7`, NEOCP rose `#d15a62` (other
  grey; rose now marks requested NEOCP objects). Status colours never reuse a series hue: warnings are apricot, LIVE is
  the UI blue, Lagrange points pale gold. The two-row legend in the time dock follows the layers (tracklets — beads on an
  ink hairline — with the tracklets layer, sight line, GEO · Moon distance); `?` repeats the colour key with the
  families from `catalog.js`.
- **Labels** never cover another marker: an ordinary label with no clear spot is dropped, and the always-on ones (Earth,
  the Moon, the frame's L points, the selection) move to the least crowded side.
- **Trail lengths** follow §9.4 except: cislunar HEO / HEO trails run half a period back (≤ 7 d) and a quarter ahead
  (≤ 1.5 d); while nothing is selected they idle at a quarter period back and 45 % alpha, and derelict trails at 70 %, so
  the Earth-centred tangle of 31 HEO orbits reads. Pointing at a marker or a trail raises that orbit to full length and
  alpha. In the EM frame the 30-day SE-L1/L2 and heliocentric trails are cut to 6 h back / 2.4 h ahead. A requested
  NEOCP object's trail runs from its first observation − 0.5 d to now and 1 d ahead. Selecting a target draws its whole
  window and dims every other trail to 30 %.
- **Close focus:** the focused target's own trail and tracklets are re-anchored at its position (§9.2), so at 1–2 km range
  they stay on the marker even at SE-L2 (a float32 trail head relative to Earth would sit ~100 px off). Focusing a cislunar
  HEO / HEO target backs the camera off until Earth stays in view and turns it to the frame's plane view, so the orbit
  reads as an ellipse about Earth.
- **Scale:** a scale bar (1–2–5 steps from 1 m, or 1 LD = 384,400 km) at the scene's bottom right; an optional "Scale rings" layer
  draws GEO (42,164 km) and a ring at the Moon's instantaneous distance (356,000–407,000 km over a month, so it passes
  through the Moon) about Earth when Earth is at least 2 px. Ranges read km below 100,000 km, LD below 1 Mkm, Mkm beyond.
- **Targets panel (left):** the NEOCP lookup on top (next section), then TARGETS with its count ("56 shown · 74
  drawable · 82 listed"), a search box (`/`; name, NORAD, COSPAR, Horizons ID), the tier chips with their counts and
  the grouped list. Group heads stick while the list scrolls and carry an eye, the family square, a fold caret (kept in
  localStorage) and shown / all. A row is eye · glyph (family colour; hollow, dimmed or empty as on the marker) · name
  (T2 tag) · source letter and quality word (`H ref`, `G gp`, `F extr ⚠`, `S fit` for a state vector + HPOP) · range
  now · ⌖ (fly there). Rows with no
  public ephemeris carry "listed, not drawn"; rows whose build failed carry "not drawn" with a short reason (the full
  error is in `build/validation_report.md`). The list is one tab stop: ↑ ↓ Home End move, Enter selects (on the selected
  row it flies), Space shows or hides the row or the whole group. Requested NEOCP objects are listed in the lookup, not
  here.
- **Inspector (right):** three tabs. **Target** (opens on a selection; ‹ › step through the visible targets): an
  identity card (⌖ Fly to, ◉ hide, × deselect; family, type, status, NORAD / COSPAR / Horizons / tier), the catalog
  caveat (⚠) and below it the build's own note (e.g. the extrapolation span); for a requested object a NEOCP REQUEST
  card; GEOCENTRIC STATE (range in km, R⊕ and LD, speed, Moon distance, RA / Dec, Sun–Earth–object angle,
  selenocentric range); POINTING, folded by default (sky rate and position angle, range rate, light time, 1σ on the
  sky, parallax bound; geocentric, not topocentric); OSCULATING ORBIT (Earth or Moon conic with C3; SE-L1/L2 targets
  show their distance from the L point and the Sun–Earth-frame offset instead; heliocentric targets show none); DATA
  (source, quality and fit arc or TLE span, data age and its basis, σ, a coverage bar with the epoch tick, notes,
  links); TRACKLETS (station chips in the bead colours with their observation counts). A GP target states its epoch
  once ("GP epoch 2026-08-05 · 78 d · stale"), in the tooltip as well. **Situation** (nothing selected): REGIMES
  (shown | live | all per regime, a "NEOCP requested" row that counts the requested objects, Total; a row jumps to its
  group in the list, the NEOCP row to the lookup), STALE OR EXTRAPOLATED (basis and age, oldest first) and DATA WINDOW
  (from, to, build, newest Horizons retrieval, newest GP epoch, newest find_orb arc end, "NEOCP on request · MPC list
  hh:mm", targets whose track ends soon, the source-letter key). It is rebuilt only when its rows change; the numbers
  update in place at most once a second. **About**: frames, Lagrange points, data quality, NEOCP on request, textures
  and data credits.
- **Tracklets:** an observation is a dot at the station position + fitted range along the observed RA/Dec, at the
  light-time epoch, with optional sight lines; a hidden target hides its dots and lines. The astrometry of identified
  targets (Multijunk, Tiandu-1, Spektr-RG, Falcon Heavy-013 S2) rides in their chunk payloads; a requested object's
  comes with the helper's answer. Beads are coloured by station (six colours in first-seen order). A tracklet is drawn
  only inside its target's drawn trail window, so beads never float where no trail is drawn: the idle quarter-period HEO
  trails hide Tiandu-1's and Multijunk's week-old beads, and in the EM frame SE-L1/L2 and heliocentric targets show none
  unless selected, hovered or focused; selecting a target draws its whole window and every bead.
- **Views:** Earth–Moon (opening), Earth, Moon, L1–L2, SE-L1, SE-L2. A preset turns the camera along a great circle and
  eases its distance over 1 s (instant with reduced motion). Double-click a marker or a Lagrange point to fly to it.
  Edge chevrons give the range from the focus point to whatever is off screen: the selected target, Earth, the Moon,
  SE-L1/L2 (with the number of shown targets of that regime off screen: "SE-L1 · 8 · 1.49 Mkm") and requested NEOCP
  objects, on a dark scrim so trails running toward them do not hide the text, at least 8 px inside the scene edges and
  clear of every on-screen marker, glyph and body; click one to fly there. The Sun indicator moves to the edge when it
  would sit on the SE-L1 glyph (SE-L1 lies on the Sun line) and slides clear of markers there.
- **Time dock** (below the scene): the transport (◀◀ reverse, ▶ Play, the rate 1× … 1 d/s with 1 h/s the default and
  "real time" while LIVE, −1 d −1 h ● Now +1 h +1 d) and on the right "±15 d window · build 10-22 12:00", or with a
  selection the key to what the timeline shows. "● Now" goes LIVE when the wall clock is inside the window, otherwise
  to the build epoch. The timeline runs over the data window: the played part filled, the selected target's coverage
  band (family colour over data and fit, an apricot hatch over extrapolation), the now line and label, day ticks with
  majors and MM-DD labels every 5 d (10 d on a narrow track), the requested objects' observation epochs as rose bars
  (the selected one outlined) and the selected target's tracklet epochs as ink ticks; the slider snaps to now, segment
  joins and observation epochs. Under it the legend strip: the two-row legend, the mouse hint ("drag orbit · scroll
  zoom · click select · dbl-click fly", short form at ≤ 1600 px, hidden at ≤ 1280 px) and the credit **Illustration:
  Mickey** at the bottom right, outside the render. Texture and data credits are in About.
- **Keys** (`?` shows them with the colour key and the glyph legend): Space play, ←/→ ±1 h (Shift ±1 d), L live,
  1 / 2 / 3 SE / ECI / EM frame, F or Enter fly to the selected target, E Earth, M Moon, [ ] previous / next visible
  target, / search, N NEOCP lookup, Esc closes the innermost thing (help, Layers, legend, a drawer or sheet) and then
  deselects, ? help. Keys with Cmd/Ctrl/Alt are left to the browser, and a focused button or link takes Space and Enter.
- **Narrower windows:** at ≤ 1280 px the kickers, the dock's right-hand key and the mouse hint hide and labels shorten
  (legend "R/B", "Sun 0°", "GEO·LD"); at 701–1100 px the Inspector stays docked (272 px) and Targets becomes a drawer
  (◧; Esc or a click on the scene closes it, the canvas does not resize).
- **Phone** (≤ 700 px): a 48 px title with a two-line clock and SE | ECI | EM; a tab bar at the bottom (☰ Targets · n,
  ⊕ NEOCP lookup, ⓘ Inspector) opens Targets / NEOCP as a full-height sheet and the Inspector as a half sheet (⤢ full
  height); a pick in a sheet closes it, and a peek row above the transport shows the selection (ⓘ opens the
  Inspector, × deselects). Same Earth–Moon opening view, the legend behind a "Legend ▴" chip, at most 16 target labels;
  the help lists the touch gestures (drag, pinch, tap, double-tap).

## NEOCP on request

The lookup at the top of the Targets panel (key N) takes one temporary designation: 5–8 letters or digits, case kept
everywhere (ZTF10Gu and ZTF10GU are different objects). Under the field, the objects on the NEOCP now, read by the page
itself from MPC's `neocp.json` ("25 on NEOCP · MPC 12:58", ↻ re-reads it): designation, FLAG (neocp2 "geo", only
while the helper runs, with a `geo only` chip), score, V, obs, arc, days since seen; geo first, then score, then the
latest update. Five rows show (three once something is requested), "+ N more" opens the rest; the field's ▾ opens the
same list filtered by what is typed. A click fills the field, a double-click or Enter fetches.

- **Request** (`GET /api/neocp/<DESIG>?t0=&t1=&now=` to the helper, one at a time; × aborts the one in flight, a
  cancelled refetch keeps the earlier drawing). The row under REQUESTED reads `queued`, then `◌ MPC + neocp2 + fit · 4 s`
  with live seconds, then `✓ MPC 25 obs ✓ neocp2 ✓ find_orb · rms 0.46″ · 2.6 d` ("neocp2 vector" or "neocp2 table"
  when find_orb was not used), or the helper's message verbatim in apricot with ↻. "left the NEOCP · drawn from neocp2"
  when MPC no longer lists the object; the drawing stays. A changed "Updated" on MPC's list refits the object. With no
  helper the row waits ("helper not running: run make serve"); the helper is probed every 15 s and the row retries when
  it answers. The page gives up on a request after 150 s.
- **Drawn as** target `req-<desig>` in the group "NEOCP · requested": NEOCP rose (regime `NEOCP_GEO`) for a geocentric
  fit unless the track at now classifies as lunar, SE-L1/L2 or near-Earth heliocentric, and near-Earth heliocentric for
  a heliocentric neocp2 orbit. Its label reads "<desig> 1.21 LD", its tracklets and sight lines are drawn like the
  catalog ones, off screen it gets a chevron, and its observation epochs mark the timeline. After a request the clock
  moves into the track's coverage if it is outside and the object is selected (⌖ flies there). The Inspector adds a NEOCP
  REQUEST card: requested time, NEOCP status and MPC listing (score, V, updated), neocp2 page time, orbit source, fit,
  arc and observations, observed span, the pseudo-MPEC link, Refetch and Remove. Designations persist in the URL
  (`neocp=`) and localStorage `cle.neocp` and are requested again on reload.
- **Sources.** The page: MPC `https://www.minorplanetcenter.net/Extended_Files/neocp.json` (CORS `*`, so also from
  `file://`), ≥ 0.5 s after the first frame and then every 10 min; a failure shows "MPC list unavailable" and Fetch
  still works. The helper, because browsers cannot call either: Bill Gray's pseudo-MPEC
  `https://www.projectpluto.com/neocp2/mpecs/<D>.htm` (astrometry, elements, find_orb's state vectors at the elements
  epoch, the 48-h geocentric ephemeris; no CORS, needs browser-like headers) and MPC's observations
  `https://data.minorplanetcenter.net/api/get-obs-neocp` (a GET with the JSON body
  `{"trksubs":[D],"output_format":["OBS80"]}`; empty once D is off the NEOCP), in two threads with 30 s timeouts and one
  retry; for the FLAG column `https://www.projectpluto.com/neocp2/summary.htm` ("(geo)": Bill Gray's best fit is
  geocentric) with score / NObs / arc from MPC `neocp.json` (`/api/neocp/summary`).
- **Orbit.** The page's and MPC's observations are merged (deduplicated by astrometric key); while MPC lists none (the
  object left the NEOCP, or MPC failed) the ones seen by earlier fetches are kept. find_orb refits them in a run
  directory of its own, `work/cache/request/fo/<stamp>`, under the shared `work/fo.lock` (waiting at most 20 s); its
  track spans the first observation − 2 d to max(now, last observation) + 7 d and ends where find_orb's σ on the sky
  passes 1°. When find_orb is missing, busy, fails, disagrees with the page or exceeds 1° everywhere, Bill Gray's state
  vector from the page (geocentric when his fit is, else heliocentric + the Sun) is propagated with `cle.nbody` (Earth +
  Moon + Sun point masses, Earth J2) from the first observation − 1 d to max(now, last observation) + 3 d, cut where the
  page's 48-h table σ passes 1°; it has no covariance. The page's table is the last resort. `fit.mode` says which
  (`fo`, `fo_daily`, `page_sv`, `page_table`), `fit.fallback` why. On 2026-11-01 the state-vector tracks stayed within
  15 km of find_orb's over 11 d for 8NIQ001 (r up to 16 Mkm; 0.02″ from Bill Gray's table), 10 km for C46PP91 and 81 km
  at 15 Mkm for C1GZ065.
- **Window.** Each request carries the page's data window (`t0`, `t1`, ≤ 45 d apart) and `now` (clamped into it); the
  track is clipped to the window, and an object observed and fitted entirely outside it answers "no data in the page's
  ±15 d window". A requested object therefore lives in the build's window like everything else.
- **Caching.** Upstream pages are cached per designation in `work/cache/request/<name>/` for 10 min and never across
  Bill Gray's hourly regeneration at about hh:35; failed fetches are not cached, and a failing source falls back to its
  cached copy with a warning. Answers are cached in memory for 10 min under (designation, t0, t1, the page's Created
  stamp, sha256 of the observations used); a repeat answers in milliseconds and never waits for another build (it is
  sent with `request.cached: true`, its own `seconds` and `served_utc`; `fetched_utc` stays the upstream read). find_orb
  run directories older than 6 h are deleted at the next request. One build runs at a time; another request waits up to
  30 s, then gets "helper busy with another request".
- **Who may call it.** The helper binds 127.0.0.1 only and refuses to start when another program already accepts on
  that port. The Host header must be `127.0.0.1:<port>` or `localhost:<port>` (else 421, the DNS-rebinding guard).
  `/api/` requests and their preflights must come from its own pages (same origin), `file://` (Origin `null`), any http(s)
  loopback origin, `https://exoplanet5.github.io` or an `--allow-origin URL`; anything else, including a cross-site
  `<img>` or no-cors request without an Origin, gets 403 `bad_origin`. Static files are `dist/` only (no listing, no
  dot files). Every answer carries `Access-Control-Allow-Origin: *` and `Cache-Control: no-store`; preflights answer
  `Access-Control-Allow-Private-Network: true`.
- **Latency** (live, 2026-11-01): C1GZ065 (25 observations, 2.6-d arc) 2.6 s through find_orb (23 of 25 used, rms
  0.46″, 9 tracklets from 8 stations), the identical repeat 3 ms from the answer cache; or09061 (14 observations, 12.0-d
  arc) drawn in Chrome 3.5 s after Fetch; the summary 0.9 s. The upstream pages take about 1 s each in parallel and a
  find_orb fit 0.8–6 s (≈ 19 s under load).
- **Answers.** `/api/health` → `{ok, service, api: 1, fo, now_utc}`; `/api/neocp/summary` → `{ok, regenerated_utc,
  fetched_utc, objects: {<desig>: {geo, moid_au, score, n_obs, arc_d}}}`; `/api/neocp/<DESIG>` → `cle/neocp-request@1`
  `{ok, schema, desig, built_utc, window, object: {cid, catalog, track, fit, caveat, tracklets, rms_arcsec, request}}`
  (catalog.js entry, §11 track, tracklets as in the chunk payloads). Errors are `{ok: false, error, message}`:

  | code | HTTP | message (shown verbatim) |
  |---|---|---|
  | `bad_designation` / `bad_request` | 400 | not a temporary designation (5–8 letters/digits) / t0 < t1 required (ms), at most 45 d apart |
  | `bad_origin` | 403 | page origin not allowed by the helper |
  | `not_found` | 404 | not on the NEOCP and no neocp2 page |
  | `bad_host` | 421 | use http://127.0.0.1 or localhost |
  | `no_orbit` | 422 | neocp2 page not generated yet (≈ hh:35) · fit failed: N obs, need ≥ 3 · neocp2 page has no orbit · no usable orbit on the neocp2 page |
  | `outside_window` | 422 | no data in the page's ±15 d window |
  | `internal` | 500 | helper error: \<Type\> |
  | `upstream` | 502 | neocp2 unreachable (…) · MPC unreachable (…) · MPC and neocp2 unreachable (…) · neocp2 summary unreachable (…) |
  | `busy` | 503 | helper busy with another request |

## Publish (GitHub Pages)

The data pipeline needs local tools (find_orb, `~/tles`, `../neocpsat`, a Space-Track login), so the page is built
here and only the static output is published: `make publish` copies `dist/index.html` and `dist/data/*.js` into one
fresh orphan commit and force-pushes it to the `gh-pages` branch, which GitHub Pages serves at
<https://exoplanet5.github.io/cislunar-eye/>. The page needs no server, so the same files work from `file://` and
from Pages; its only request of its own is MPC's `neocp.json`. NEOCP lookups from the Pages copy go to a helper on the
reader's own machine (`make serve` in a checkout; the helper admits the Pages origin), else the lookup says "helper
offline". Refresh stays manual: `make daily` (or `make all`), then `make publish`.

## Build

Python is `~/.venvs/astro313/bin/python -I`; Node ≥ 20 with `npm ci` (esbuild, Playwright with system Chrome).
`CLE_NOW=<ISO UTC>` pins "now"; it is floored to the hour, and the window is now ± 15 d. Unset or empty, the Makefile
floors the wall clock once, so every step of one `make` shares one window (each target echoes `CLE_NOW=…`). The
export draws a target only from a `build/states_report.json` entry and a `build/states` file stamped with the current
window; anything else is listed "not drawn (adapter not run in this window)". One `make` shares one window; separate
runs combine only when they pass the same `CLE_NOW`.

| make target | network | does |
|---|---|---|
| `setup` | npm | `npm ci`, MMX import |
| `extract` | no | `scripts/extract_all.py` → `catalog/sources/*.tsv` (skipped while the script does not exist) |
| `sweep` | Horizons | `scripts/horizons_sweep.py --now $CLE_NOW --in-scope-only --max-age 7` re-probes the in-scope IDs once the audit table's sweep is more than 7 d old (`SWEEP_MAX_AGE_D`; `FORCE=1` re-probes now); non-fatal, nothing is written when Horizons is down (the daily runs it before `catalog`) |
| `catalog` | no | `scripts/validate_source_tsv.py` over the merged sources, then `catalog/master_list.*` from `catalog/sources/*.tsv` + `catalog/overrides.tsv`, then `git diff --stat -- catalog/` |
| `neocp-live` | MPC, Project Pluto | manual: `scripts/neocp_live.py` → `catalog/sources/neocp.tsv` (then `make catalog`); non-fatal; not part of `daily` |
| `update-horizons` | Horizons | Horizons vectors: one 2-row solution probe per target per UTC day, then only the 10-day tiles and window-edge pieces the cache lacks (`work/cache/horizons_eph/`); a normal daily ≈ 50 requests / ≈ 2 min, a cold cache or a re-delivered solution ≈ 460 requests / ≈ 17 min |
| `update-gp` | Space-Track | fresh `gp`/`gp_history` through `~/.claude/skills/space-track/st.py`, then the GP states; a failed pull is recorded as `gp_fetch` in `build/states_report.json` and the cached sets are used |
| `update-fits` | no | local find_orb refits of the neocpsat groups (one process behind `work/fo.lock`; one fit's legs and one-day chunks run 8 at a time under one hold), only when `scripts/fits_changed.py` exits 0 (inputs changed) or `FORCE=1` |
| `states` | no | every drawn tier-1/2 viewer target (the master list minus `catalog.VIEWER_EXCLUDED_GROUPS`: NEOCP live / archive, natural objects) from cached snapshots → `build/states/`, then the NEOCP / neocpsat tracklets of identified targets → `build/tracklets.json`. `CLE_JOBS=N` (default 8) builds the GP / Bill Gray / extrapolated targets in N worker processes while the find_orb targets run in order in one more and Horizons in the parent; `CLE_JOBS=1` is the sequential loop. `build/states_report.json` records `states_run {jobs, targets, wall_s}`; a target's `seconds` is its own work, time queued on `work/fo.lock` is `lock_wait_s` |
| `export` | no | `build/states` + `build/tracklets.json` + DE440s → `dist/data/*.js` (a target's tracklets ride in its `targets-<chunk>.js` payload; stale `neocp.js` / `targets-neocp.js` are deleted) |
| `validate` | no | `build/validation_report.md` (coverage, midpoint errors, regime checks, failures with reasons) |
| `site` | no | `node scripts/build_site.mjs` → `dist/index.html` |
| `serve` | MPC, Project Pluto (per request) | `scripts/serve.py`, the local helper on `http://127.0.0.1:8765/`: `dist/` plus the on-request NEOCP API (`/api/health`, `/api/neocp/summary`, `/api/neocp/<DESIG>?t0=&t1=&now=`; see "NEOCP on request"). A request fetches the neocp2 pseudo-MPEC and MPC's observations (cached 10 min in `work/cache/request/`), refits them with find_orb (waits at most 20 s on `work/fo.lock`), else propagates Bill Gray's state vector. `--port 0` takes any free port (the start line prints it), `--no-fo` never runs find_orb, `--allow-origin URL` admits another page origin; never run by `daily` or launchd; Ctrl-C stops it |
| `all` | no | `states export validate site` |
| `test` | no | Python unittest (incl. `tests/test_request.py`: neocp2 parsers, request builds, the helper on an ephemeral port, all without network) + `node --test` (incl. `tests/data_contract.test.mjs`, the field contract of `dist/data/*.js` and of the canned helper answer, skipped until `make build` / `npm run fixture`; `tests/neocp.test.mjs` and `tests/timeline.test.mjs` for the lookup and the timeline marks) |
| `test-browser` | no | Playwright on the fixture page and `dist/index.html` from `file://`. MPC `neocp.json` and the helper are route-fulfilled (the helper's answer for C46FX01 is `build/fixture/request/C46FX01.json`, written by `npm run fixture`; one pass plays the helper offline); any other http(s) request fails the run. Screenshots → `build/screens/`, each compared with `build/screens_baseline/` (fail above 0.1 % of pixels off by > 8; diffs → `build/screens_diff/`); after an intended visual change `node tests/browser.mjs --dist --update-baselines` |
| `sanity` | no | `scripts/sanity_check.py` → `build/sanity_check.md`; exits 1 on a FAIL |
| `tles-status` | git (read) | `~/tles` against upstream; never pulls; non-fatal |
| `status` | git (read) | `scripts/status.py`: ages of the Horizons/GP/fo stamps, master list, `~/tles` HEAD, last daily log; exit 1 when the last daily is > 36 h old (or failed) |
| `install-launchd` / `uninstall-launchd` | no | opt-in: copy the `ops/launchd` template (daily) to `~/Library/LaunchAgents` and load it / unload and remove it, plus the retired hourly `com.cislunar-eye.neocp` agent if a machine still has it; never run by `setup` or `daily` |
| `prune` / `prune-apply` | no | `scripts/prune.py`: dry run / apply. `work/cache/fo` keeps the stamps `build/` references plus the previous one per fit; in kept stamps the other run directories (one-day chunks, legs, probes, rejected candidates) are trimmed to `obs.txt`, `elements.txt`, `command.json`, `fo_stdout.txt` and a gzipped `ephem.txt`; every other stamp is deleted. `work/cache/horizons_eph` keeps the referenced stamps plus the two newest. Stamps younger than 1 h are left alone; refuses while another run holds `work/make.lock` or `work/fo.lock` |
| `daily` | yes | `extract sweep catalog update-horizons update-gp tles-status update-fits states export validate site test sanity test-browser`, then `prune-apply` (non-fatal) |

A full offline rebuild for the 2026-10-22T12:00Z window:

```sh
CLE_NOW=2026-10-22T12:00:00Z make all test test-browser sanity
```

`CLE_NOW=2026-10-22T12:00:00Z make states export validate sanity` takes about 1 min (53 s on 2026-11-01). `states` is
49.7 s wall for 74 targets with the default 8 jobs, and that wall is the find_orb chain, which every process serialises
on `work/fo.lock` (5 find_orb targets, Falcon Heavy-013 S2 the longest at 19 s with its one-day refits pooled, and the
extrapolations that start from a find_orb epoch state). The per-target `seconds` in `build/states_report.json` are the
target's own work (sum 85 s); the time a pooled target spent queued on the lock is `lock_wait_s` (6 s in all). On
2026-10-22, with 8 NEOCP targets also in the find_orb chain, it was ≈ 80 s. Horizons comes from the cache
(`states --offline`), and the extrapolation start-state fits are memoised in `work/cache/extrap/fits.json`
(`CLE_REFRESH=1` refits).
Every find_orb run passes `-y 10` (fixed full-improvement steps): fo's automatic fit is wall-clock limited, so without
them a long fit (Falcon Heavy, 313 obs) landed in one of several minima ~300 km apart depending on machine load; now
it repeats to ≈ 0.05 km, and the NEOCP-class fits stay bitwise identical between runs.

**Refresh cadence.**
- NEOCP: nothing scheduled. The hourly `update-neocp` and `data/neocp.js` are retired; NEOCP objects are requested one
  temporary designation at a time through the local helper (`make serve`). `make neocp-live` remains as a manual
  refresh of `catalog/sources/neocp.tsv`.
- Daily: `make daily`. "Now" moves on; Horizons re-probes each target's solution and fetches only the tiles and
  window-edge pieces the cache lacks (≈ 50 requests, ≈ 2 min; a re-delivered solution refetches that target). `sweep`
  and `tles-status` never abort the chain; a Space-Track outage is recorded and the cached GP sets are used. The daily
  ends with `prune-apply` (fo cache ≈ 0.3–0.5 GB steady state instead of +250 MB/day). A full `states` run writes one
  fo stamp of ≈ 21 MB (134 runs on 2026-11-01; 54 MB and 343 runs while the NEOCP targets were built; the probes,
  legs, one-day chunks and short tables drop find_orb's side files at once); an `update-fits` stamp (the find_orb
  targets' runs alone) is ≈ 18 MB. Requests to the helper run find_orb in `work/cache/request/fo/`, outside prune.
- Weekly: the daily's `sweep` step re-probes Horizons once the audit table is more than 7 d old (`make sweep FORCE=1`
  by hand).
- By hand: a new state vector replaces its file in `catalog/statevec/`. Set the matching `[date..date]` in
  `catalog/sources/manual.tsv` (a test checks it), then run `make catalog states export site` or wait for the daily. After `../neocpsat` changes: `make update-fits` (the daily runs it when `scripts/fits_changed.py` says so).
- The page warns when its data window is about to end (the banner strip above the scene). A requested NEOCP object is
  fitted inside the build's window, so new NEOCP objects need a current build too.
- Nothing here pushes, deploys or writes to sibling projects.

**Ops** (`ops/`). `ops/run.sh <target> [make args]` is the unattended entry point: it takes `work/make.lock` (mkdir;
a lock whose pid is dead or older than 6 h is removed, exit 75 while another run holds it), leaves `CLE_NOW` to make
(one window per make), runs `make`, and logs to `work/logs/<target>-<UTC date>.log` with a 14-day
rotation and a final `ok` / `FAILED rc=N at step <target>` line. `make status` reads those logs and the data stamps.
`ops/launchd/com.cislunar-eye.daily.plist` (01:30 local) is a template with the absolute paths filled in; it is loaded
only by an explicit `make install-launchd` (copy to `~/Library/LaunchAgents` + `launchctl bootstrap`) and removed by
`make uninstall-launchd`; both also unload and remove the retired hourly `com.cislunar-eye.neocp` agent.

## Outputs (offline rebuild of the 2026-10-22T12:00Z window on 2026-11-01, from the caches of the 2026-10-22T17:00Z daily)

- **Targets:** 82 listed (60 tier 1, 22 tier 2), 74 with draw=Y, and 73 drawn: 33 Horizons, 27 GP, 7 extrapolated,
  5 find_orb, 1 Bill Gray TLE. Before the viewer exclusion the same list gave 102 listed and 93 drawn; the 20 rows that
  left are 5 natural objects, 8 NEOCP live and 7 NEOCP archive rows. Catalog tracklets: Falcon Heavy-013 S2 47
  (`targets-helio.js`), Tiandu-1 14 and Multijunk 11 (`targets-cislunar.js`), Spektr-RG 14 (`targets-se.js`).
- **Not drawn:** Chang'e-6 orbiter, because no coherent find_orb fit covers the window (details in `build/validation_report.md`). Queqiao-1/2, Tiandu-2 and the ended objects are listed but not drawn by design.
- **Page:** `dist/index.html` is 7.3 MiB (7,644,935 bytes with the Earth, night-lights and Moon textures and the packed
  star catalogue); `dist/data/*.js` totals 3.6 MB (4.1 MB with the NEOCP and natural rows), with `targets-cislunar.js`
  1.6 MB and `targets-lunar.js` 1.4 MB; there is no `neocp.js` any more.
- **Speed** (headless Chrome, 1440×900, 3 runs): opening from local disk takes about 0.6 s (the load event at 0.59 s);
  the median frame is 3.1–4.3 ms with tier 1 and tier 2 visible (73 targets), 2.4–3.6 ms with tier 1 alone. The only
  request at load is MPC's `neocp.json`.
- **Reports:**
  - `build/validation_report.md` covers per-target coverage, midpoint errors and regime checks.
  - `build/sanity_check.md` covers JS vs numpy, SE/EM frames, Lagrange points, regimes, GP vs Horizons and tracklet dots.
  - `build/screens/dist-*.png` holds the checklist views.

## Known issues

- **DRO-A and DRO-B come from hand-supplied state vectors, not from Space-Track** (ARCHITECTURE AM14). The files
  `catalog/statevec/DRO-A.txt` (2026-10-25T00:00Z) and `DRO-B.txt` (2026-10-12T00:00Z) use hpop's paste format (m, m/s,
  Earth-centred ICRF, UTC). They are propagated with `../hpop` (`src/cle/adapters/hpop.py`; badge **S**), backward and
  forward from the epoch across the window. Both are retrograde DRO loops 62,000–90,000 km from the Moon, so they are
  `EM_DRO` again in `catalog/overrides.tsv`. hpop and `cle.nbody` started from the same state agree to 16 km (DRO-A) and
  36 km (DRO-B) over the window. Quality is `fit` within 30 d of the vector's epoch and `extrap` beyond. The catalog
  caveat says "stale" once the vector is more than 30 d from now. The earlier GP-fitted tracks put DRO-A 13,700–50,800 km
  from the Moon ("lunar-bound, not a DRO") and were 105,000 km off the new vector at 2026-10-25. That came from SDP4
  mean elements fitted to a DRO, which are not a Kepler orbit. The GP stays as the secondary ephemeris.
- **ESCAPADE Blue/Gold** have finished their SE-L2 loiter and are on the return leg to the November 2026 Earth flyby;
  they are relabelled `EARTH_NEAR_HELIO` (group "Earth-return / near-Earth heliocentric") in `catalog/overrides.tsv`.
- **Chang'e-6 orbiter** is listed but not drawn: it manoeuvres, its 2026-10-01 page is 4 observations over 23 minutes
  (unbound fit), and every older bound fit misses those observations by 31–143°.
- **TESS Space-Track GP sets are unusable**: up to 330,992 km from Horizons, so TESS uses Horizons. The other GP vs Horizons checks agree to 114 km (Titan-3C R/B) and 327 km (XMM R/B); both of those Horizons records are TLE-based.
- **Multijunk** (area-to-mass about 20 m²/kg): the Oct 15 fit does not describe the Oct 6–9 astrometry, so a few of its tracklet dots sit up to 7,120 km off the track.
- **NEOCP and natural objects** are no longer in the viewer's catalog: the master list keeps its NEOCP live / archive
  and natural groups, but `catalog.VIEWER_EXCLUDED_GROUPS` (plus `obj_type=natural`) drops them from states, export,
  validate and the sanity check, and `cle states` deletes what earlier runs built for them. Identified debris filed under
  the archive group, `vela-2b-fragment`, goes too. `make neocp-live` still refreshes `catalog/sources/neocp.tsv`, which
  the viewer then ignores.
- **On-request NEOCP limits.**
  - Without a neocp2 page there is no orbit: an object posted before Bill Gray's next hourly run (≈ hh:35) answers
    "neocp2 page not generated yet"; nothing is fitted from MPC's observations alone. Bill Gray's summary and his pages
    are not regenerated strictly together (a page of 13:35 next to a summary of 12:35 was seen).
  - The state-vector path (`page_sv`) has no covariance: no σ, and its 1° cut is a linear estimate from the page's 48-h
    table. Area-to-mass (`-g 17`) fits are not modelled on that path.
  - A daily build holding `work/fo.lock` for more than 20 s sends requests to the state-vector path ("find_orb busy:
    fo.lock held > 20 s" in the request's warnings).
  - The answer cache ignores `now`: a re-request within 10 min with another `now` gets the first answer, whose trail
    start was computed at the first `now`.
  - `work/cache/request/` is not pruned by `scripts/prune.py`; the per-designation caches (tens of KB each) accumulate.
  - The neocp2 "(geo)" flag rule is unverified: no summary seen so far carried one, so the FLAG column, `geo only` and
    the geo-first sort were checked with mocked flags only.
  - From the Pages copy the lookup calls `http://127.0.0.1:8765` cross-origin; the helper answers the browser's
    private-network preflight, but no real browser has exercised that path yet. `file://` and the helper's own page
    work.
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
| live NEOCP | `scripts/neocp_live.py` (`make neocp-live`) | MPC and Project Pluto, manual |
| news | `scripts/build_news_tsv.py` (after a manual research pass) | none |
| merge | `scripts/build_master.py` (`make catalog`) | none |
| latest GP for objects without a fresh source | `scripts/extract_spacetrack_gp.py`, then the merge again | Space-Track |
| checks | `scripts/verify_master.py`, then the merge again so `TARGETS.md` shows the result | Space-Track SATCAT |

`catalog/TARGETS.md` is the list to review. Hand corrections go only in `catalog/overrides.tsv`. Raw downloads stay under
`work/cache/`, and `work/notes/` holds the MMX reuse study and the news sources.
