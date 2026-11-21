#!/bin/bash
# ops/server/setup.sh: the LAN server's toolchain, all under CLE_BASE (ops/server/env.sh). Idempotent: deploy.sh runs
# it after every sync and it installs only what is missing or out of date.
#   opt/bin/uv               uv (Astral), its latest GitHub release
#   opt/python               CPython $CLE_PYTHON (3.13), uv-managed
#   .venvs/astro313          the venv: requirements.txt
#   opt/node                 Node $CLE_NODE (v25.8.2, as the dev machine), nodejs.org tarball (opt/dl); npm ci when
#                            package-lock.json changed
#   opt/billgray             lunar, jpl_eph, sat_code (+ get_vect), find_orb (fo, find_orb) built from opt/billgray/src
#                            (deploy.sh copies the dev machine's sources there)
#   tles                     ~/tles copied by deploy.sh, else a git clone of Bill-Gray/tles (weekly.sh pulls it)
#   build/fixture            the browser tests' fixture page (make fixture), when missing
#   .find_orb/environ.dat    LINUX_JPL_FILENAME pointed at .find_orb/ here
# Log of the compiler runs: work/logs/setup-billgray.log
set -euo pipefail
. "$(dirname "$0")/env.sh"
PYTHON=${CLE_PYTHON:-3.13}
NODE=${CLE_NODE:-v25.8.2}
VENV=$CLE_BASE/.venvs/astro313
BG=$CLE_BASE/opt/billgray
LOG=$CLE_ROOT/work/logs/setup-billgray.log
say() { echo "setup: $*"; }
case $(uname -m) in arm64) UVARCH=aarch64 NODEARCH=arm64 ;; *) UVARCH=x86_64 NODEARCH=x64 ;; esac
mkdir -p "$CLE_BASE/opt/bin" "$CLE_BASE/opt/dl" "$CLE_BASE/.venvs" "$CLE_ROOT/work/logs"
# fetch URL: the tarball in opt/dl, downloaded to a file, resumed and retried (a proxy may cut a long stream short)
fetch() {
  local out=$CLE_BASE/opt/dl/$(basename "$1")
  if ! tar -tzf "$out" > /dev/null 2>&1; then
    curl -fsSL --http1.1 --retry 8 --retry-all-errors --retry-delay 3 -C - -o "$out" "$1" ||
      curl -fsSL --http1.1 --retry 8 --retry-all-errors --retry-delay 3 -o "$out" "$1"
  fi
  echo "$out"
}

if [ ! -x "$CLE_BASE/opt/bin/uv" ]; then
  say "uv -> opt/bin"
  tar -xzf "$(fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-$UVARCH-apple-darwin.tar.gz")" \
    -C "$CLE_BASE/opt/bin" --strip-components 1
fi

if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import sys; sys.exit(sys.version_info[:2] != tuple(map(int, '$PYTHON'.split('.')[:2])))"; then
  say "python $PYTHON + venv -> .venvs/astro313"
  rm -rf "$VENV"
  uv venv -q --seed --python "$PYTHON" "$VENV"
fi
uv pip install -q --python "$VENV/bin/python" -r "$CLE_ROOT/requirements.txt"
say "$("$VENV/bin/python" --version), $(uv pip list -q --python "$VENV/bin/python" 2>/dev/null | wc -l | tr -d ' ') packages"

if [ "$(node --version 2>/dev/null)" != "$NODE" ]; then
  say "node $NODE -> opt/node"
  rm -rf "$CLE_BASE/opt/node" && mkdir -p "$CLE_BASE/opt/node"
  tar -xzf "$(fetch "https://nodejs.org/dist/$NODE/node-$NODE-darwin-$NODEARCH.tar.gz")" \
    -C "$CLE_BASE/opt/node" --strip-components 1
fi
lock=$(shasum -a 256 "$CLE_ROOT/package-lock.json" | cut -d' ' -f1)
if [ "$(cat "$CLE_ROOT/node_modules/.cle-lock" 2>/dev/null)" != "$lock" ]; then
  say "npm ci"
  (cd "$CLE_ROOT" && npm ci --no-audit --no-fund --loglevel=error) && echo "$lock" > "$CLE_ROOT/node_modules/.cle-lock"
fi
say "node $(node --version), esbuild $(cd "$CLE_ROOT" && node -e 'console.log(require("esbuild/package.json").version)')"

# Bill Gray's tools: libraries and headers under opt/billgray/{lib,include}, fo and find_orb in opt/billgray/bin,
# find_orb's data files in opt/billgray/share/findorb/data; get_vect stays in its source directory (CLE_GET_VECT).
if [ -d "$BG/src/find_orb" ]; then
  mkdir -p "$BG/bin" "$BG/lib" "$BG/include"
  : > "$LOG"
  mk() { make -C "$BG/src/$1" -j8 CLANG=Y PREFIX="$BG" "${@:2}" >> "$LOG" 2>&1 || { say "make -C $1 ${*:2} failed: tail $LOG"; tail -20 "$LOG"; exit 1; }; }
  mk lunar liblunar.a && mk lunar install
  mk jpl_eph libjpl.a && mk jpl_eph install
  mk sat_code libsatell.a get_vect && mk sat_code install_lib
  mk find_orb fo find_orb && mk find_orb install
  say "find_orb: $BG/bin/fo, get_vect: $CLE_GET_VECT"
else
  say "no Bill Gray sources in $BG/src (deploy.sh sync): fo / get_vect not built"
fi

if [ ! -d "$CLE_BASE/tles/.git" ]; then                                 # deploy.sh sync copies ~/tles first
  say "git clone Bill-Gray/tles -> tles"
  git -c http.version=HTTP/1.1 clone -q --depth 1 https://github.com/Bill-Gray/tles.git "$CLE_BASE/tles"
fi
git -C "$CLE_BASE/tles" config http.version HTTP/1.1                    # the proxy drops long HTTP/2 streams

if [ ! -f "$CLE_ROOT/build/fixture/index.html" ]; then                 # make test-browser needs it (a build product)
  say "browser-test fixture -> build/fixture"
  make -C "$CLE_ROOT" -s fixture > /dev/null
fi

ENV=$HOME/.find_orb/environ.dat
if [ -f "$ENV" ]; then
  jpl=$(sed -n 's/^LINUX_JPL_FILENAME=//p' "$ENV")
  here=$HOME/.find_orb/$(basename "${jpl:-none}")
  if [ -n "$jpl" ] && [ "$jpl" != "$here" ] && [ -f "$here" ]; then
    sed -i '' "s#^LINUX_JPL_FILENAME=.*#LINUX_JPL_FILENAME=$here#" "$ENV"
    say "environ.dat: LINUX_JPL_FILENAME=$here"
  fi
fi

for f in "$HOME/JPLDE/de440s.bsp" "$HOME/.claude/skills/space-track/st.py" "$HOME/.config/space-track/credentials.json" \
         "$HOME/.find_orb/ObsCodes.htm" "$CLE_FO" "$CLE_GET_VECT"; do
  [ -e "$f" ] || say "missing: $f"
done
say "done"
