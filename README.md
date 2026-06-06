# ◈ KeaNexus

Kea DHCP management dashboard for cyberwraith.net.

Built with Streamlit and talks directly to the Kea Control Agent (basic auth) — no proxy layer needed.

## Features

- **Pool** — live utilisation gauge, service health, lease summary metrics
- **Leases** — filterable table of all active/declined leases with add/edit dialogs
- **IPAM** — full /24 subnet map (all 256 addresses) with SQLite-backed static records for out-of-scope IPs
- **Reservations** — per-row add/edit/delete dialogs, writes directly to `kea-dhcp4.conf` via `config-set` + `config-write`
- **Maintenance** — DHCP enable/disable and declined lease wipe

## Local development

```bash
# 1. Clone the repo
git clone git@github.com:tothomas69/Kea_Nexus.git keanexus && cd keanexus

# 2. Create .env from the template
cp .env.example .env
# Edit .env — fill in KEA_CA_URL and KEA_CA_PASSWORD

# 3. Start (hot-reload enabled via bind-mounts + --server.runOnSave=true)
docker compose up

# Open http://localhost:8501
```

## Production deployment

```bash
# On the target host, pull and build
git clone git@github.com:tothomas69/Kea_Nexus.git /opt/keanexus && cd /opt/keanexus
cp .env.example .env && nano .env   # fill in credentials

docker build -t keanexus:latest .
docker run -d --name keanexus --restart unless-stopped \
  --env-file .env \
  -v keanexus_data:/app/data \
  -p 8501:8501 \
  keanexus:latest
```

Then add to Nginx Proxy Manager as `keanexus.cyberwraith.net → 172.16.17.215:8501`
and a Pi-hole local DNS record pointing at NPM (172.16.17.212).

## Files

| File                     | Purpose                                              |
| ------------------------ | ---------------------------------------------------- |
| `app.py`                 | Entry point: page config, CSS, sidebar, tab routing  |
| `kea.py`                 | Kea Control Agent HTTP client                        |
| `helpers.py`             | Cached data loaders (`@st.cache_data`) and utilities |
| `db.py`                  | SQLite persistence layer for IPAM static records     |
| `ui_pool.py`             | Pool tab                                             |
| `ui_leases.py`           | Leases tab                                           |
| `ui_ipam.py`             | IPAM tab                                             |
| `ui_reservations.py`     | Reservations tab                                     |
| `ui_maintenance.py`      | Maintenance tab                                      |
| `style.css`              | Global CSS overrides for Streamlit internals         |
| `Dockerfile`             | Production container image                           |
| `docker-compose.yml`     | Local dev (bind-mounts for hot-reload)               |
| `.streamlit/config.toml` | Theme configuration                                  |

## Extending

`kea.py` exposes a `KeaClient` with `call()` for any arbitrary Kea command, plus higher-level helpers.
All commands available in your deployment can be listed with:

```python
kea.call("list-commands", service="dhcp4")
```
