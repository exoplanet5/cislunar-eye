# Sourced by ops/server/*.sh on the LAN server. Everything lives under CLE_BASE, the project's parent directory: HOME
# is CLE_BASE for every job, so ~/.venvs/astro313, ~/JPLDE, ~/tles, ~/.find_orb, ~/.claude/skills/space-track and
# ~/.config/space-track resolve inside it, and npm, uv and astropy keep their caches there too.
CLE_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
CLE_BASE=$(dirname "$CLE_ROOT")
export CLE_ROOT CLE_BASE HOME=$CLE_BASE
export PATH=$CLE_BASE/.venvs/astro313/bin:$CLE_BASE/opt/node/bin:$CLE_BASE/opt/billgray/bin:$CLE_BASE/opt/bin:/usr/bin:/bin:/usr/sbin:/sbin
export PY=$CLE_BASE/.venvs/astro313/bin/python                          # the Makefile's PY
export CLE_FO=$CLE_BASE/opt/billgray/bin/fo                             # src/cle/adapters/fo.py, scripts/extract_neocpsat.py
export CLE_GET_VECT=$CLE_BASE/opt/billgray/src/sat_code/get_vect       # src/cle/adapters/bgtle.py
export UV_PYTHON_INSTALL_DIR=$CLE_BASE/opt/python UV_CACHE_DIR=$CLE_BASE/.cache/uv UV_PYTHON_PREFERENCE=only-managed
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1                               # tests/browser.mjs drives the system Chrome
export LANG=${LANG:-en_US.UTF-8} LC_ALL=${LC_ALL:-en_US.UTF-8}
# Mozilla's CA bundle (certifi, in the venv) for Python and git: macOS's /etc/ssl/cert.pem, the uv CPython's default,
# cannot verify ssd.jpl.nasa.gov's chain ("self-signed certificate in certificate chain")
for f in "$CLE_BASE"/.venvs/astro313/lib/python3*/site-packages/certifi/cacert.pem; do
  if [ -f "$f" ]; then export SSL_CERT_FILE=$f REQUESTS_CA_BUNDLE=$f; fi
done
# This server's own settings (deploy.sh allow writes CLE_ALLOW_HOSTS there; gitignored, never synced or overwritten)
if [ -f "$CLE_ROOT/ops/server/local.env" ]; then . "$CLE_ROOT/ops/server/local.env"; fi
