# cislunar-eye build (ARCHITECTURE.md §13). Every Python step runs with -I; CLE_NOW (ISO UTC) pins "now".
#   make daily            network refresh + offline rebuild + tests   (unattended: ops/run.sh daily)
#   make update-neocp     hourly live NEOCP -> dist/data/neocp.js    (unattended: ops/run.sh update-neocp)
#   make build            offline: dist/data/*.js + dist/index.html
#   CLE_NOW=2026-10-21T13:00:00Z make states export validate
# One make = one window: CLE_NOW is fixed once here (wall clock floored to the hour when unset or empty, DW-01) and
# exported to every step; each target echoes it. ops/run.sh pins the hourly update-neocp to the daily's window.
PY    ?= $(HOME)/.venvs/astro313/bin/python
NODE  ?= node
CLE   := $(PY) -I scripts/cle.py
ifeq ($(strip $(CLE_NOW)),)
CLE_NOW := $(shell date -u +%Y-%m-%dT%H:00:00Z)
endif
export CLE_NOW
DATE  := $(firstword $(subst T, ,$(CLE_NOW)))
NOW    = @echo "== $@  CLE_NOW=$(CLE_NOW)"
# SOURCES = build_master.SOURCES (the merged files only; horizons_all_spacecraft / neocpsat_spektr_rg_history are not sources)
SOURCES := $(wildcard $(addprefix catalog/sources/,$(addsuffix .tsv,news horizons lunarmission billgray neocpsat rocketdebris spacetrack neocp manual)))
# make extract EXTRACT_FLAGS=--offline skips the Space-Track extractor (also skipped without credentials)
EXTRACT_FLAGS ?=
# make sweep re-probes Horizons only when the audit table's sweep is older than this (days); FORCE=1 re-probes now
SWEEP_MAX_AGE_D ?= 7
LAUNCHD := $(HOME)/Library/LaunchAgents
AGENTS  := com.cislunar-eye.neocp com.cislunar-eye.daily

.PHONY: now setup extract sweep catalog neocp-live update-horizons update-gp update-fits update-neocp tles-status \
        states export validate site build test test-browser sanity status open publish install-launchd uninstall-launchd \
        prune prune-apply all daily

now:
	$(NOW)

setup:
	npm ci
	test ! -f scripts/import_mmx.py || $(PY) -I scripts/import_mmx.py

extract:                              # offline, idempotent: scripts/extract_*.py -> catalog/sources/*.tsv (DW-08)
	$(NOW)
	test ! -f scripts/extract_all.py || $(PY) -I scripts/extract_all.py $(EXTRACT_FLAGS)

sweep:                                # Horizons, non-fatal: re-probe the in-scope IDs for this window once the audit is > SWEEP_MAX_AGE_D old (DW-08)
	$(NOW)
	-test ! -f scripts/horizons_sweep.py || $(PY) -I scripts/horizons_sweep.py --now $(CLE_NOW) --in-scope-only $(if $(filter 1,$(FORCE)),,--max-age $(SWEEP_MAX_AGE_D))

catalog:                              # validate the merged sources (C4), merge, show what changed (C5, non-fatal)
	$(NOW)
	$(PY) -I scripts/validate_source_tsv.py $(SOURCES)
	$(PY) -I scripts/build_master.py --now $(DATE)
	-git diff --stat -- catalog/

neocp-live:                           # network, non-fatal: live NEOCP objects -> catalog/sources/neocp.tsv (daily)
	$(NOW)
	-$(PY) -I scripts/neocp_live.py

update-horizons:                      # network: Horizons snapshot for the Horizons-planned targets
	$(NOW)
	$(CLE) states --adapter horizons

update-gp:                            # network: fresh Space-Track gp + gp_history through the skill CLI, then the GP states
	$(NOW)
	$(CLE) states --adapter gp --fetch

update-fits:                          # local find_orb re-runs, only when scripts/fits_changed.py exits 0 (inputs changed) or FORCE=1
	$(NOW)
	@if [ "$(FORCE)" = 1 ] || [ ! -f scripts/fits_changed.py ]; then rc=0; else $(PY) -I scripts/fits_changed.py; rc=$$?; fi; \
	case $$rc in 0) $(CLE) states --adapter fo && { test ! -f scripts/fits_changed.py || $(PY) -I scripts/fits_changed.py --mark || true; } ;; \
	              1) echo "update-fits: neocpsat inputs unchanged (FORCE=1 to rerun)" ;; *) exit $$rc ;; esac

update-neocp:                         # hourly: live NEOCP -> fo -> tracklets -> dist/data/neocp.js only (manifest: its entry)
	$(NOW)
	$(PY) -I scripts/neocp_live.py
	$(CLE) states --adapter neocp
	$(CLE) export --neocp-only

tles-status:                          # read-only, non-fatal: never fetches into or pulls ~/tles
	$(NOW)
	-@git -C $(HOME)/tles log -1 --format='local  %h %ci %s'
	-@git -C $(HOME)/tles ls-remote origin HEAD | awk '{print "remote", substr($$1, 1, 7)}'

states:                               # offline: cached snapshots -> build/states
	$(NOW)
	$(CLE) states --offline

export:
	$(NOW)
	$(CLE) export

validate:
	$(NOW)
	$(CLE) validate

site:
	$(NOW)
	$(NODE) scripts/build_site.mjs

build: export site

test:
	$(NOW)
	$(PY) -I -m unittest discover -s tests -p 'test_*.py'
	test ! -f package.json || npm test

test-browser:                         # fixture page + dist/index.html from file://; screenshots -> build/screens/
	$(NOW)
	$(NODE) tests/browser.mjs --dist

sanity:                               # numerical checks of dist/data (frames, Lagrange, regimes, GP vs Horizons) -> build/sanity_check.md
	$(NOW)
	$(PY) -I scripts/sanity_check.py > /dev/null; s=$$?; cat build/sanity_check.md | tail -4; exit $$s

status:                               # data ages (wall clock); exit 1 when the NEOCP snapshot > 3 h or the last daily > 36 h
	$(PY) -I scripts/status.py

open:
	open dist/index.html

publish:                              # push the built page (dist/index.html + dist/data) to gh-pages -> GitHub Pages; manual, after a refresh
	$(NOW)
	bash scripts/publish_pages.sh

prune:                                # dry run: what prune-apply would delete / gzip under work/cache/{fo,horizons_eph} (EFF-9, DW-05)
	$(NOW)
	$(PY) -I scripts/prune.py

prune-apply:                          # keep the stamps build/ references (+ the previous fit per target, + the 2 newest Horizons), gzip unchosen ephem.txt, delete the rest
	$(NOW)
	$(PY) -I scripts/prune.py --apply

install-launchd:                      # opt-in (ARCHITECTURE Q8): copy ops/launchd/*.plist to ~/Library/LaunchAgents and load them
	@echo "installing $(AGENTS) into $(LAUNCHD): hourly update-neocp at :45, daily at 01:30 local, via ops/run.sh (logs: work/logs/)"
	mkdir -p $(LAUNCHD) work/logs
	for a in $(AGENTS); do cp ops/launchd/$$a.plist $(LAUNCHD)/$$a.plist; launchctl bootout gui/$$(id -u)/$$a 2>/dev/null; \
	  launchctl bootstrap gui/$$(id -u) $(LAUNCHD)/$$a.plist; done
	launchctl list | grep cislunar-eye

uninstall-launchd:                    # unload the agents and remove the plists; nothing else changes
	@echo "unloading $(AGENTS) and removing them from $(LAUNCHD)"
	for a in $(AGENTS); do launchctl bootout gui/$$(id -u)/$$a 2>/dev/null; rm -f $(LAUNCHD)/$$a.plist; done

all: states export validate site     # then: make test test-browser sanity

daily: neocp-live extract sweep catalog update-horizons update-gp tles-status update-fits states export validate site test sanity test-browser  # then prune (EFF-9), non-fatal
	$(NOW)
	-$(PY) -I scripts/prune.py --apply
