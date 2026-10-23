#!/usr/bin/env bash
# Publish the locally built page to GitHub Pages: dist/index.html + dist/data/*.js -> the gh-pages branch.
#
# The data pipeline needs local tools (find_orb, ~/tles, ../neocpsat, a Space-Track login), so the site is built
# here (make daily / make all) and only the static output is pushed. Each publish is one fresh orphan commit,
# force-pushed, so the gh-pages branch never accumulates daily page snapshots.
#
#   make publish                 PAGES_REMOTE (default origin), PAGES_BRANCH (default gh-pages)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DIST=$ROOT/dist
BRANCH=${PAGES_BRANCH:-gh-pages}
URL=$(git -C "$ROOT" remote get-url "${PAGES_REMOTE:-origin}")

test -f "$DIST/index.html" && ls "$DIST"/data/*.js >/dev/null 2>&1 || { echo "publish: dist/ is not built (make all)" >&2; exit 1; }
field() { grep -o "\"$1\": *\"[^\"]*\"" "$2" | head -1 | cut -d'"' -f4 || true; }
built=$(field built_utc "$DIST/data/manifest.js")
window="$(field start_utc "$DIST/data/core.js")..$(field stop_utc "$DIST/data/core.js")"

STAGE=$(mktemp -d "${TMPDIR:-/tmp}/cle-pages.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
mkdir "$STAGE/data"
cp "$DIST/index.html" "$STAGE/"
cp "$DIST"/data/*.js "$STAGE/data/"
touch "$STAGE/.nojekyll"                                   # serve files as they are (no Jekyll build)
size=$(du -sh "$STAGE" | cut -f1)

cd "$STAGE"
git init -q -b "$BRANCH"
git add -A
git -c user.name="$(git -C "$ROOT" config user.name)" -c user.email="$(git -C "$ROOT" config user.email)" \
    commit -q -m "Publish page built $built ($window)"
helper=$(command -v gh >/dev/null && echo '!gh auth git-credential' || true)
git ${helper:+-c credential.helper="$helper"} push -q --force "$URL" "HEAD:refs/heads/$BRANCH"
echo "publish: $BRANCH <- page built $built ($window), $size"
