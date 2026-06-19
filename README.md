# ◈ KeaNexus

Kea DHCP management dashboard for the home user.

Built with Streamlit and talks directly to the **kea-dhcp4 HTTP listener** (Kea 3.0+) — no Control Agent or proxy layer needed.

## Features

- **Pool** — live utilisation gauge, service health, lease summary metrics
- **Leases** — filterable table of all active/declined leases with add/edit dialogs
- **IPAM** — full /24 subnet map (all 256 addresses) with SQLite-backed static records for out-of-scope IPs
- **Reservations** — per-row add/edit/delete dialogs, writes directly to `kea-dhcp4.conf` via `config-set` + `config-write`
- **Maintenance** — DHCP enable/disable and declined lease wipe
- **Sidebar** — live KeaNexus version, Kea daemon version, and API mode display

## Requirements

- **Kea 3.0+** with a direct HTTP listener configured on `kea-dhcp4` (see below)
- Docker + Docker Compose

## Kea configuration

KeaNexus requires a `control-sockets` HTTP listener block in `kea-dhcp4.conf`.
The Control Agent (`kea-ctrl-agent`) is not used and should not be running.

```json
"control-sockets": [
  {
    "socket-type": "http",
    "socket-address": "127.0.0.1",
    "socket-port": 8004,
    "authentication": {
      "type": "basic",
      "realm": "kea-dhcp4",
      "clients": [
        {
          "user": "admin",
          "password": "yourpassword"
        }
      ]
    }
  }
]
```

> Bind `socket-address` to the LAN IP (not loopback) if KeaNexus runs in Docker,
> since Docker is a separate network namespace from the host.

## Local development

```bash
# 1. Clone the repo
git clone git@github.com:tothomas69/Kea_Nexus.git keanexus && cd keanexus

# 2. Create .env from the template
cp .env.example .env
# Edit .env — fill in KEA_API_URL and credentials

# 3. Start (hot-reload enabled via bind-mounts + --server.runOnSave=true)
docker compose up

# Open http://localhost:8502
```

## Production deployment

```bash
# On the target host, pull and build
git clone git@github.com:tothomas69/Kea_Nexus.git /opt/keanexus && cd /opt/keanexus
cp .env.example .env && nano .env   # fill in KEA_API_URL and credentials

docker compose up -d --build
```

Then add to Nginx Proxy Manager as `keanexus.cyberwraith.net → <host>:8502`
and a Pi-hole local DNS record pointing at NPM (`172.16.17.212`).

## Files

| File                     | Purpose                                                |
| ------------------------ | ------------------------------------------------------ |
| `app.py`                 | Entry point: page config, CSS, sidebar, tab routing    |
| `version.py`             | Single source of truth for the KeaNexus version string |
| `kea.py`                 | Kea direct HTTP API client (Kea 3.0+)                  |
| `auth.py`                | Login / session authentication                         |
| `helpers.py`             | Cached data loaders (`@st.cache_data`) and utilities   |
| `db.py`                  | SQLite persistence layer for IPAM static records       |
| `ui_pool.py`             | Pool tab                                               |
| `ui_leases.py`           | Leases tab                                             |
| `ui_ipam.py`             | IPAM tab                                               |
| `ui_reservations.py`     | Reservations tab                                       |
| `ui_maintenance.py`      | Maintenance tab                                        |
| `ui_settings.py`         | Settings tab                                           |
| `ui_login.py`            | Login page                                             |
| `style.css`              | Global CSS overrides for Streamlit internals           |
| `Dockerfile`             | Production container image                             |
| `docker-compose.yml`     | Compose file (bind-mounts for hot-reload in dev)       |
| `.streamlit/config.toml` | Theme configuration                                    |

## Extending

`kea.py` exposes a `KeaClient` with `call()` for any arbitrary Kea command, plus higher-level helpers.
All commands available in your deployment can be listed with:

```python
kea.call("list-commands")
```

> Note: unlike Kea 2.x, the `service` parameter is never sent when using the direct HTTP listener.
