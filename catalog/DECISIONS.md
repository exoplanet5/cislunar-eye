# cislunar-eye target list — decisions, watch list, open questions

Hand-maintained companion of `catalog/TARGETS.md` (which `scripts/build_master.py` regenerates from the data
on every merge). Dated entries record when a decision was taken; the generated list never repeats them.

## Decisions

### 2026-10-21

- IBEX and CAPSTONE are tier 1 on their latest orbit, with the caveat shown.
- Queqiao-1, Queqiao-2 and Tiandu-2 are listed in tier 1 but not drawn (no public ephemeris).
- Natural minimoons and quasi-satellites are kept, in their own group at tier 2.
- Viewer default: Sun–Earth rotating frame, tier 1 only, window now−15 d → now+15 d.

### 2026-10-22 (review DW-07)

- Space-Track GP primaries are graded by age: ≤ 30 d covers the window; 30–120 d keeps the GP adapter but is
  marked `covers_window = stale-gp` with the caveat "stale: Space-Track GP N d old" (tier unchanged); older sets
  are unusable and the object falls back to its latest-orbit extrapolation (tier 2). The page reads this flag
  (`staleness` in `catalog.js`) instead of its own 14-day constant.

## Watch list (not launched yet)

Griffin-1 (Falcon Heavy, NET Nov–Dec 2026). Chang'e-7, IM-3, Blue Moon MK1 and Blue Ghost M2 / Lunar
Pathfinder slipped to 2027 (`work/notes/news_sources.md`). NEO Surveyor's Horizons ephemeris starts 2027-09.

## Open questions (answered items are under Decisions)

1. **2.4 d threshold.** Nine objects with a current ephemeris reach apogee ≥ 100 000 km but have P ≤ 2.4 d: SMILE
   (2.11 d), XMM-Newton (2.00 d), the Vela Titan 3C transtages, the YZ-1S stage and others. They are tier 2 now.
   *Default: keep them tier 2, but promote SMILE and XMM-Newton (active missions).*
2. **Stale but real.** IBEX (active; last ephemeris 2026-01-01), IMP-8, CAPSTONE (last fit 2026-09-12),
   Chandrayaan-3 PM (last fit 2026-08-11). Vela 6A/6B were dropped to tier 3: their last GP is 2025-06-11.
   *Default: tier 2, propagated from the last state and drawn as "extrapolated".*
3. **No public ephemeris.** Queqiao-1 (EM-L2 halo), Queqiao-2 (lunar ELFO) and Tiandu-2 are active but
   untracked. *Default: list only; an indicative orbit in phase 2.*
4. **Natural objects.** 2024 PT5, 2025 PN7, 2006 RH120, 2000 SG344 and 2008 UA202 are natural minimoons or
   quasi-satellites. *Default: tier 2 under a separate "natural" filter, or drop.*
5. **Replays.** Artemis II, the Blue Ghost F9 stage (lunar impact 2026-08-05) and the Cluster reentries are
   tier 2 replays. *Default: keep.*
6. **Heliocentric radius.** "Near Earth" means within 1.0e7 km at some point in the window, which gives JUICE
   (Earth flyby late Sep 2026), MMX, the Roman Falcon Heavy stage and P22qGC1. Cert-2 Centaur reaches only
   11 Mkm by 20 Nov 2026, so it is tier 2. *Default: keep 1.0e7 km.*
7. **Source order** when several cover the window: Horizons > Bill Gray TLE > Space-Track GP ≤ 30 d >
   neocpsat fit > pseudo-MPEC > GP ≤ 120 d. *Default: keep.*
