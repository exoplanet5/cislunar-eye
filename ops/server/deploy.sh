#!/bin/bash
# ops/server/deploy.sh [all|code|sync|setup|build|helper|cron|refresh|status|ssh]: run cislunar-eye on a LAN Mac.
# Run it on the dev machine. On the server everything goes under BASE (default /Users/<user>/mickey), and every job
# there runs with HOME=BASE (ops/server/env.sh):
#   BASE/cislunar-eye                  this project: the files git tracks or would track, .git, work/cache (copied once;
#                                      files the server already has are kept)
#   BASE/{neocpsat,highartsat,rocketdebris,lunarmission,hpop}   the read-only sibling inputs, mirrored from ../
#   BASE/opt/billgray/src/*            Bill Gray's lunar, jpl_eph, sat_code, find_orb sources (setup.sh builds them)
#   BASE/.find_orb  BASE/JPLDE/de440s.bsp  BASE/.claude/skills/space-track  BASE/.config/space-track/credentials.json
#   BASE/tles                          ~/tles, copied once (deploy.sh tles: again); weekly.sh pulls Bill-Gray/tles
#   BASE/opt, BASE/.venvs              made on the server by setup.sh (uv + CPython, the venv, Node, find_orb)
# Server: CLE_SERVER=user@host, else ./local-server ("username:", "server:" and optional "password:" lines, gitignored).
# With a password line ssh answers its prompt from that file (SSH_ASKPASS); else your key or a typed password. One
# connection is shared by every step (ControlMaster, ~/.ssh/cle-*), so a password is asked at most once.
#   all      sync + setup + build + helper restart + cron install (first deploy; safe to repeat)
#   code     sync the project only + build + helper restart (after a code or catalog change)
#   sync | setup | build | helper | cron        one step ("helper" restarts it, "cron" installs the crontab lines)
#   allow NAME ...   the extra Host names the helper answers to, e.g. a domain the router forwards to it (kept in
#            the server's ops/server/local.env; "allow none" clears them, "allow" alone shows them); restarts it
#   tles     mirror ~/tles again (after you edit tle_list.txt or add refits here; sync copies it only the first time)
#   refresh  start ops/server/weekly.sh on the server now, in the background (log: work/logs/weekly-<date>.log)
#   status   helper, crontab lines, last pipeline logs, make status
#   ssh      a shell in the project with the server environment
# Env: CLE_SERVER_BASE, CLE_BILLGRAY_SRC (~/Desktop/billgray), CLE_PORT (8765), CLE_WEEKLY (cron fields, "30 4 * * 1").
# A deploy overwrites the server's catalog/ with this machine's copy; its next weekly run regenerates it.

# SSH_ASKPASS hook: ssh runs this script again with CLE_ASKPASS_FILE set; print the password line and stop
if [ -n "${CLE_ASKPASS_FILE:-}" ]; then sed -n 's/^password:[[:space:]]*//p' "$CLE_ASKPASS_FILE" | tr -d '\r\n'; echo; exit 0; fi
set -euo pipefail
SELF=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PARENT=$(dirname "$ROOT")
CRED=$ROOT/local-server
field() { sed -n "s/^$1:[[:space:]]*//p" "$CRED" 2>/dev/null | tr -d '\r' | head -1; }
SERVER=${CLE_SERVER:-$(field username)@$(field server)}
[ "$SERVER" != "@" ] || { echo "deploy: set CLE_SERVER=user@host or write $CRED" >&2; exit 2; }
BASE=${CLE_SERVER_BASE:-/Users/${SERVER%@*}/mickey}
DEST=$BASE/$(basename "$ROOT")
BILLGRAY=${CLE_BILLGRAY_SRC:-$HOME/Desktop/billgray}
PORT=${CLE_PORT:-8765}
WEEKLY=${CLE_WEEKLY:-30 4 * * 1}
SSHOPT=(-o ControlMaster=auto -o "ControlPath=$HOME/.ssh/cle-%C" -o ControlPersist=10m -o ServerAliveInterval=30
        -o StrictHostKeyChecking=accept-new)
ASKPASS=()
if [ -z "${CLE_SERVER:-}" ] && [ -n "$(field password)" ]; then
  ASKPASS=(env SSH_ASKPASS="$SELF" SSH_ASKPASS_REQUIRE=force CLE_ASKPASS_FILE="$CRED")
fi
say() { echo "== deploy $*"; }
ssh_() { ${ASKPASS[@]+"${ASKPASS[@]}"} ssh "${SSHOPT[@]}" "$@"; }
rsync_() { ${ASKPASS[@]+"${ASKPASS[@]}"} rsync -a -e "ssh ${SSHOPT[*]}" "$@"; }
on() { ssh_ "$SERVER" "$@"; }
remote() { on "CLE_PORT=$PORT CLE_WEEKLY='$WEEKLY' /bin/bash $DEST/ops/server/$1"; }

sync() {
  say "sync -> $SERVER:$BASE"
  on "mkdir -p '$DEST/work/cache' '$BASE/opt/billgray/src' '$BASE/JPLDE' '$BASE/.find_orb' '$BASE/.claude/skills' '$BASE/.config/space-track'"
  (cd "$ROOT" && { git ls-files -co --exclude-standard -z; printf '.git\0'; } |
    COPYFILE_DISABLE=1 tar --null -T - -czf -) | on "tar -xzf - -C '$DEST'"
  echo "  project: $(cd "$ROOT" && git ls-files -co --exclude-standard | wc -l | tr -d ' ') files + .git"
  rsync_ --ignore-existing "$ROOT/work/cache/" "$SERVER:$DEST/work/cache/"
  echo "  work/cache ($(du -sh "$ROOT/work/cache" | cut -f1); existing server files kept)"
  for d in neocpsat highartsat rocketdebris lunarmission hpop; do
    if [ -d "$PARENT/$d" ]; then
      rsync_ --delete --exclude .git --exclude __pycache__ --exclude .DS_Store "$PARENT/$d/" "$SERVER:$BASE/$d/"
      echo "  ../$d"
    else echo "  ../$d: not found here, skipped"; fi
  done
  for d in lunar jpl_eph sat_code find_orb; do
    rsync_ --exclude .git --exclude '*.o' --exclude '*.a' --exclude prefix.h --exclude PREFIX \
      "$BILLGRAY/$d/" "$SERVER:$BASE/opt/billgray/src/$d/"
  done
  echo "  Bill Gray sources from $BILLGRAY"
  if [ -d "$HOME/tles/.git" ] && ! on "test -d '$BASE/tles/.git'"; then tles; fi
  rsync_ --exclude '*.bak*' "$HOME/.find_orb/" "$SERVER:$BASE/.find_orb/"
  rsync_ "$HOME/JPLDE/de440s.bsp" "$SERVER:$BASE/JPLDE/"
  rsync_ --exclude __pycache__ "$HOME/.claude/skills/space-track" "$SERVER:$BASE/.claude/skills/"
  echo "  ~/.find_orb, ~/JPLDE/de440s.bsp, the space-track CLI"
  if [ -f "$HOME/.config/space-track/credentials.json" ]; then
    on "umask 077; cat > '$BASE/.config/space-track/credentials.json'" < "$HOME/.config/space-track/credentials.json"
    echo "  Space-Track credentials (mode 600)"
  fi
}

tles() {                              # mirror ~/tles (its .git and your local edits); weekly.sh pulls on top
  rsync_ --delete "$HOME/tles/" "$SERVER:$BASE/tles/"
  on "git -C '$BASE/tles' remote set-url origin https://github.com/Bill-Gray/tles.git"
  echo "  ~/tles mirrored (weekly.sh pulls Bill-Gray/tles on top)"
}

allow() {
  local f=$DEST/ops/server/local.env names=$*
  if [ $# -gt 0 ]; then
    case $names in *[!A-Za-z0-9.:\ -]*) echo "deploy: allow takes host names / addresses" >&2; exit 2 ;; esac
    [ "$names" != none ] || names=
    on "touch $f && { grep -v '^export CLE_ALLOW_HOSTS=' $f || true; } > $f.tmp && echo 'export CLE_ALLOW_HOSTS=\"$names\"' >> $f.tmp && mv $f.tmp $f"
    remote "helper.sh restart"
  fi
  on "grep '^export CLE_ALLOW_HOSTS=' $f 2>/dev/null || echo 'no extra host names'"
}

build() {
  say "build (make all on the server)"
  on "/bin/bash -c '. $DEST/ops/server/env.sh && cd $DEST && ops/run.sh all; rc=\$?; tail -3 work/logs/all-\$(date -u +%Y-%m-%d).log; exit \$rc'"
}

case ${1:-all} in
  all)     sync; say setup; remote setup.sh; build; say helper; remote "helper.sh restart"; say cron; remote "cron.sh install" ;;
  code)    sync; build; say helper; remote "helper.sh restart" ;;
  sync)    sync ;;
  tles)    tles ;;
  allow)   shift; allow "$@" ;;
  setup)   remote setup.sh ;;
  build)   build ;;
  helper)  remote "helper.sh restart" ;;
  cron)    remote "cron.sh install" ;;
  refresh) on "nohup /bin/bash $DEST/ops/server/weekly.sh >/dev/null 2>&1 < /dev/null &"
           say "refresh started: $DEST/work/logs/weekly-$(date -u +%Y-%m-%d).log (deploy.sh status)" ;;
  status)  remote "helper.sh status" || true; remote "cron.sh show" || true
           on "cd '$DEST/work/logs' 2>/dev/null && for f in \$(ls -t weekly-*.log all-*.log 2>/dev/null | head -2); do echo \"-- \$f\"; tail -3 \$f; done"
           on "/bin/bash -c '. $DEST/ops/server/env.sh && cd $DEST && make -s status'" || true ;;
  ssh)     ssh_ -t "$SERVER" "/bin/bash -c '. $DEST/ops/server/env.sh && cd $DEST && exec /bin/bash -i'" ;;
  *)       sed -n '2,26p' "$SELF" >&2; exit 2 ;;
esac
