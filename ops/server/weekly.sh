#!/bin/bash
# ops/server/weekly.sh (cron, once a week; deploy.sh refresh runs it now): pull Bill Gray's TLEs, then
# ops/run.sh weekly = make weekly: the NEOCP catalog source, the target list (extract, Horizons sweep, catalog) and the
# full daily refresh and rebuild of dist/ with tests. The helper serves the new page as soon as it is written.
# The tles pull keeps the dev machine's edits that deploy.sh copied (tle_list.txt includes, local refits): git stashes
# them, fast-forwards and puts them back; if upstream changed the same lines, upstream wins and the edits stay in
# `git stash list`.
# Log: work/logs/weekly-<UTC date>.log (ops/run.sh: one run at a time, 14-day rotation).
. "$(dirname "$0")/env.sh"
LOG=$CLE_ROOT/work/logs/weekly-$(date -u +%Y-%m-%d).log
T=$HOME/tles
mkdir -p "$CLE_ROOT/work/logs"
{ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) tles: git pull"
  git -C "$T" pull -q --ff-only --autostash 2>&1 || echo "tles: pull failed (kept)"
  conflicts=$(git -C "$T" diff --name-only --diff-filter=U)
  if [ -n "$conflicts" ]; then
    git -C "$T" checkout -q HEAD -- $conflicts
    echo "tles: local edits to $conflicts clash with upstream: upstream kept, the edits are in git stash"
  fi
  git -C "$T" log -1 --format='tles: %h %ci %s'; } >> "$LOG" 2>&1
exec "$CLE_ROOT/ops/run.sh" weekly
