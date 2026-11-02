#!/bin/bash
# ops/run.sh <make target> [make args]: one pipeline run at a time, CLE_NOW pinned, logged (REVIEW DW-13, C7).
#   ops/run.sh daily              ops/run.sh daily -n
# Lock:   work/make.lock (mkdir, atomic); a lock whose pid is dead, or older than CLE_LOCK_STALE_S (6 h), is removed.
# Window: CLE_NOW from the environment wins; otherwise make floors the wall clock to the hour (DW-01/02).
# Log:    work/logs/<target>-<UTC date>.log (make output, start/end lines, the failing step), 14-day rotation.
# Exit:   make's status; 75 when another run holds the lock. CLE_WORK overrides work/ (tests).
set -u -o pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TARGET=${1:?usage: ops/run.sh <make-target> [make args]}; shift
WORK=${CLE_WORK:-$ROOT/work}; LOCK=$WORK/make.lock; LOGS=$WORK/logs
mkdir -p "$LOGS"
LOG=$LOGS/$TARGET-$(date -u +%Y-%m-%d).log
find "$LOGS" -maxdepth 1 -name '*.log' -mtime +14 -delete 2>/dev/null
say() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG"; }

if ! mkdir "$LOCK" 2>/dev/null; then
  pid=$(cat "$LOCK/pid" 2>/dev/null); age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && [ "$age" -lt "${CLE_LOCK_STALE_S:-21600}" ]; then
    say "$TARGET: skipped, $LOCK held by pid $pid for $age s"; exit 75
  fi
  say "$TARGET: removing stale lock (pid ${pid:-?}, $age s)"; rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || { say "$TARGET: lock race, giving up"; exit 75; }
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT; trap 'exit 130' INT; trap 'exit 143' TERM

cd "$ROOT" || exit 1
say "$TARGET: start (make $TARGET $*)"; t0=$(date +%s)
if [ -t 1 ]; then make "$TARGET" "$@" 2>&1 | tee -a "$LOG"; rc=$?; else make "$TARGET" "$@" >> "$LOG" 2>&1; rc=$?; fi
if [ "$rc" -eq 0 ]; then say "$TARGET: ok rc=0 in $(( $(date +%s) - t0 )) s"; exit 0; fi
step=$(grep -o 'make[^:]*: \*\*\* \[[^]]*\]' "$LOG" | tail -1 | sed 's/.*\[\(.*\)\].*/\1/; s/^.*: //')
say "$TARGET: FAILED rc=$rc at step ${step:-?} in $(( $(date +%s) - t0 )) s (log: $LOG)"
exit "$rc"
