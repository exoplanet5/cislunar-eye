# Deploy to a LAN server

`ops/server/deploy.sh` puts cislunar-eye on a Mac on the local network and runs it there:

- the page and the NEOCP helper (`scripts/serve.py`) run around the clock at `http://<server>:8765/`
- NEOCP lookups work for anyone on the LAN, with find_orb running on the server
- a weekly cron job refreshes the target list and rebuilds the page

You run it on the dev machine. On the server, everything lives under one directory, `BASE`
(default `/Users/<user>/mickey`), and nothing is installed anywhere else: no Homebrew, no sudo, no LaunchAgents.
The only entries outside `BASE` are the project's lines in the user's crontab, which point back into `BASE`.

## Server

The server is named in `local-server` at the project root, one `key:value` per line:

```
username:<user>
server:<LAN address>
password:...
```

The file is gitignored, so it must never be committed. With a `password:` line, `deploy.sh` answers ssh's password
prompt from it through `SSH_ASKPASS`. Without one, ssh uses your key or asks once. Every step shares a single
connection through an SSH ControlMaster socket (`~/.ssh/cle-*`, kept 10 min). To use a different server, set
`CLE_SERVER=user@host` and `CLE_SERVER_BASE=/path`.

A key is safer than a password in a file:
`ssh-copy-id <user>@<LAN address>`, then delete the `password:` line.

## Commands

```
ops/server/deploy.sh            # = all: sync, setup, build, restart the helper, install cron (first time; safe to repeat)
ops/server/deploy.sh code       # after a code or catalog change: sync the project, build, restart the helper
ops/server/deploy.sh refresh    # run the weekly job now, in the background
ops/server/deploy.sh status     # helper, cron lines, latest logs, make status
ops/server/deploy.sh ssh        # a shell in the server's project with its environment (HOME=BASE, venv, node, fo)
ops/server/deploy.sh tles       # mirror ~/tles again after you edit tle_list.txt or add refits (sync copies it only once)
ops/server/deploy.sh sync|setup|build|helper|cron   # one step at a time
```

## Layout on the server

| Path under BASE | What | Comes from |
|---|---|---|
| `cislunar-eye/` | the project: the files git tracks or would track, `.git`, `work/cache` (copied once; files already on the server are kept) | `deploy.sh sync` |
| `neocpsat/ highartsat/ rocketdebris/ lunarmission/ hpop/` | the read-only inputs next to the project (`../`), mirrored | `deploy.sh sync` |
| `opt/billgray/src/{lunar,jpl_eph,sat_code,find_orb}` | Bill Gray's sources, as built on the dev machine (local edits included) | `deploy.sh sync` |
| `opt/billgray/{bin,lib,include,share}` | `fo` and `find_orb`, the libraries; `get_vect` stays in `src/sat_code/` | `setup.sh` |
| `.find_orb/` | find_orb's settings and data (`environ.dat` pointed at the DE440 file here) | `deploy.sh sync`, `setup.sh` |
| `JPLDE/de440s.bsp` | the DE440s kernel | `deploy.sh sync` |
| `.claude/skills/space-track/`, `.config/space-track/credentials.json` | the Space-Track CLI and its login (mode 600) | `deploy.sh sync` |
| `opt/bin/uv`, `opt/python/` | uv and the CPython 3.13 it manages | `setup.sh` |
| `.venvs/astro313/` | the venv: `requirements.txt` | `setup.sh` |
| `opt/node/` | Node v25.8.2; `npm ci` in the project | `setup.sh` |
| `tles/` | `~/tles` with its `.git` and your local edits, copied once (else a `git clone` of Bill-Gray/tles); pulled weekly over HTTPS | `deploy.sh sync` / `tles`, `weekly.sh` |

Every server job sources `ops/server/env.sh`, which sets `HOME=BASE`. The project's `~/...` paths
(`~/.venvs/astro313`, `~/JPLDE`, `~/tles`, `~/.find_orb`, the Space-Track CLI) therefore resolve inside `BASE`, and npm,
uv and astropy keep their caches there. `CLE_FO` and `CLE_GET_VECT` point the pipeline at the server's find_orb and
get_vect. `SSL_CERT_FILE` points at certifi's Mozilla bundle, because macOS's `/etc/ssl/cert.pem` cannot verify
JPL's certificate chain. `PATH` is the venv, Node, find_orb and the system directories only.

## What runs

| When | What | Log |
|---|---|---|
| `@reboot`, every 5 min | `ops/server/helper.sh ensure`: start the helper if it is down; restart it if the server's names or addresses changed | `work/logs/helper.log` |
| Monday 04:30 server time (`CLE_WEEKLY`) | `ops/server/weekly.sh`: `git pull` of `tles/`, then `ops/run.sh weekly` = `make weekly` = `neocp-live` + `daily` (extract, Horizons sweep, catalog = the target list; Horizons, Space-Track and find_orb refresh; states, export, validate, site; tests, sanity, browser tests; prune) | `work/logs/weekly-<UTC date>.log` |

The helper re-reads `dist/` on every request, so a rebuilt page is live as soon as it is written.

The helper listens on `0.0.0.0:8765`. It accepts the Host names the server is reached by: its `LocalHostName.local`,
its hostname, its IPv4 addresses, and any extra names in `CLE_ALLOW_HOSTS`. Any other Host header gets 421. It sends
`index.html` with `window.CLE_HELPER = ''`, so a page opened from the server sends its NEOCP requests back to that
server. The helper has no login, so anyone on the LAN can use it, find_orb runs included. Builds run one at a time,
and each request waits at most 30 s for its turn.

## Access from outside the LAN

If the router forwards a port to the server, outside visitors arrive with that public name in the `Host` header, for
example `example.com:8765`. The helper answers 421 `bad_host` to any name it was not told about. Allow the names:

```
ops/server/deploy.sh allow example.com www.example.com   # replaces the list; "allow none" clears it, "allow" shows it
```

The list is kept in the server's `ops/server/local.env`. It is gitignored, so a sync never ships or overwrites it, and
`env.sh` reads it, so the 5-minute cron check keeps the names. If the router maps a different outside port to 8765, the
`Host` header carries that port, which the helper does not accept: forward the same port number (8765 → 8765).
`work/logs/helper.log` records every rejected Host header (`bad_host: '…'`).

Once the port is public, so is the helper. It is plain HTTP with no login, anyone can start NEOCP fetches and
find_orb fits (one at a time, each answer cached 10 min), and it is the Python standard-library server, which is not
hardened against abuse. For anything beyond a short demo, put it behind a tunnel with a login
(Cloudflare Tunnel + Access, Tailscale) instead of an open port.

**Open, deferred (2026-11-19).** The port is open now, for testing. The plan is to replace it with Cloudflare
Tunnel + Cloudflare Access:
- `cloudflared` runs on the server under BASE and connects out, so the router needs no forward.
- Access asks for an email one-time code and lets in only the listed addresses.
- Visitors get HTTPS at a subdomain of your own domain, which `deploy.sh allow` admits.
- Afterwards, close the router forward and `deploy.sh allow none` the old names.

## Notes

- **Catalog edits.** A deploy copies this machine's `catalog/` over the server's. The next weekly run regenerates it,
  or run `deploy.sh refresh` to do that now.
- **Inputs on the server can go stale.** `../neocpsat` and the other sibling inputs are copies. The server sees new
  neocpsat MPECs, observations or wiki edits only after the next `deploy.sh sync` (or `all`/`code`).
- **Window between runs.** The data window is ±15 d around the build. With a weekly build, the page has about 8 days
  of future coverage just before the next run.
- **Your TLE edits.** The server's `tles/` started as a copy of `~/tles`, including your uncommitted
  `tle_list.txt` changes and local refits. The weekly `git pull --autostash` puts those edits back on top of
  upstream. If upstream changed the same lines, upstream wins and your edits are kept in `git stash list` (the log
  says so). New local edits reach the server only through `deploy.sh tles`.
- **The server's proxy.** Its internet goes through a proxy that is slow and drops long HTTP/2 downloads. `setup.sh`
  therefore downloads to files with resume and retries, git uses HTTP/1.1, and big inputs (`~/tles`, the caches) are
  copied over the LAN.
- **Deleted files.** A deploy only adds and overwrites project files. A file deleted here stays on the server until
  it is removed there.
- **Removing it all.** `deploy.sh ssh`, then `ops/server/helper.sh stop && ops/server/cron.sh remove`, then delete
  `BASE`.
