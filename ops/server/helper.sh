#!/bin/bash
# ops/server/helper.sh start|stop|restart|status|ensure: scripts/serve.py for the LAN, in the background. It serves
# dist/ (re-read on every request, so a rebuild is live at once) and the on-request NEOCP API (MPC + neocp2 + find_orb).
#   Listens on 0.0.0.0:$CLE_PORT (8765). Host names it accepts (--allow-host): this Mac's LocalHostName.local, its
#   hostname, the IPv4 addresses of its interfaces and $CLE_ALLOW_HOSTS (space-separated).
#   ensure (cron: @reboot and every 5 min) starts it when it is not running and restarts it when that name list changed
#   (a new DHCP lease); it prints nothing when all is well.
#   pid work/helper.pid, names work/helper.hosts, log work/logs/helper.log (moved to helper.log.1 past 20 MB on start)
set -u
. "$(dirname "$0")/env.sh"
PORT=${CLE_PORT:-8765}
PID=$CLE_ROOT/work/helper.pid
HOSTS=$CLE_ROOT/work/helper.hosts
LOG=$CLE_ROOT/work/logs/helper.log
mkdir -p "$CLE_ROOT/work/logs"

names() {
  { scutil --get LocalHostName 2>/dev/null | sed 's/$/.local/'; hostname
    ifconfig 2>/dev/null | awk '$1 == "inet" && $2 !~ /^(127|169\.254)\./ {print $2}'
    for h in ${CLE_ALLOW_HOSTS:-}; do echo "$h"; done; } | tr 'A-Z' 'a-z' | sort -u
}
running() { [ -f "$PID" ] && ps -p "$(cat "$PID")" -o command= 2>/dev/null | grep -q "scripts/serve.py"; }
health() { curl -fsS -m 5 -H "Host: 127.0.0.1:$PORT" "http://127.0.0.1:$PORT/api/health" 2>/dev/null; }
urls() { sed "s|.*|http://&:$PORT/|" "$HOSTS" | tr '\n' ' '; }

start() {
  if running; then echo "helper: running (pid $(cat "$PID"))"; return 0; fi
  if [ -f "$LOG" ] && [ "$(stat -f %z "$LOG")" -gt 20000000 ]; then mv -f "$LOG" "$LOG.1"; fi
  names > "$HOSTS"
  args=()
  while read -r h; do args+=(--allow-host "$h"); done < "$HOSTS"
  cd "$CLE_ROOT" || return 1
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) helper.sh start" >> "$LOG"
  nohup "$PY" -I scripts/serve.py --bind 0.0.0.0 --port "$PORT" ${args[@]+"${args[@]}"} >> "$LOG" 2>&1 < /dev/null &
  echo $! > "$PID"
  for _ in $(seq 30); do
    if health > /dev/null; then echo "helper: started (pid $(cat "$PID")): $(urls)"; return 0; fi
    running || break
    sleep 1
  done
  echo "helper: did not come up, last lines of $LOG:"; tail -5 "$LOG"; return 1
}

stop() {
  if running; then
    kill "$(cat "$PID")"
    for _ in $(seq 10); do running || break; sleep 1; done
    if running; then kill -9 "$(cat "$PID")"; fi
    echo "helper: stopped"
  fi
  rm -f "$PID"
}

case ${1:-status} in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  ensure)  if ! running; then start; elif [ "$(names)" != "$(cat "$HOSTS" 2>/dev/null)" ]; then stop; start; fi ;;
  status)  if running && h=$(health); then echo "helper: up (pid $(cat "$PID")) $(urls)"; echo "  $h"
           else echo "helper: down (log: $LOG)"; exit 1; fi ;;
  *)       echo "usage: $0 start|stop|restart|status|ensure" >&2; exit 2 ;;
esac
