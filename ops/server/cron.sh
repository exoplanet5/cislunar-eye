#!/bin/bash
# ops/server/cron.sh install|remove|show: this project's lines in the user's crontab, between two marker comments
# (other crontab lines are kept as they are):
#   @reboot, */5 * * * *    ops/server/helper.sh ensure      the LAN helper (page + NEOCP requests) stays up
#   $CLE_WEEKLY             ops/server/weekly.sh             target list + full refresh (default Monday 04:30 local)
set -euo pipefail
. "$(dirname "$0")/env.sh"
WEEKLY=${CLE_WEEKLY:-30 4 * * 1}
BEGIN="# >>> cislunar-eye $CLE_ROOT"
END="# <<< cislunar-eye $CLE_ROOT"
others() { { crontab -l 2>/dev/null || true; } | awk -v b="$BEGIN" -v e="$END" '$0 == b {skip = 1; next} $0 == e {skip = 0; next} !skip'; }

case ${1:-show} in
  install)
    { others
      echo "$BEGIN"
      echo "@reboot $CLE_ROOT/ops/server/helper.sh ensure >/dev/null 2>&1"
      echo "*/5 * * * * $CLE_ROOT/ops/server/helper.sh ensure >/dev/null 2>&1"
      echo "$WEEKLY $CLE_ROOT/ops/server/weekly.sh >/dev/null 2>&1"
      echo "$END"; } | crontab -
    crontab -l | awk -v b="$BEGIN" -v e="$END" '$0 == b {on = 1} on; $0 == e {on = 0}' ;;
  remove)
    others | crontab -
    echo "cron: cislunar-eye lines removed" ;;
  show)
    crontab -l 2>/dev/null | awk -v b="$BEGIN" -v e="$END" '$0 == b {on = 1} on; $0 == e {on = 0}' ;;
  *) echo "usage: $0 install|remove|show" >&2; exit 2 ;;
esac
