# ◈ KeaNexus

Kea DHCP management dashboard for cyberwraith.net.

Built with Streamlit + httpx. Talks directly to the Kea Control Agent
(basic auth) — no proxy layer needed.

## Features

- **Pool** — live utilization metrics, pool health at a glance
- **Leases** — filterable table of all active/declined leases
- **Reservations** — inline CRUD editor, writes to `kea-dhcp4.conf` via `config-set` + `config-write`
- **Maintenance** — declined lease cleanup

## Local development

```bash
# 1. Clone the repo
git clone <your-repo-url> keanexus && cd keanexus

# 2. Create .env from the template
cp .env.example .env
# Edit .env — fill in KEA_CA_PASSWORD

# 3. Start (hot-reload enabled)
docker compose up

# Open http://localhost:8501
```

Changes to `app.py` and `kea.py` reload automatically (bind-mounted + `--server.runOnSave=true`).

NOTE: Two things are happening that make the hot-reload work:
Bind-mount means Docker isn't keeping its own copy of your files inside the container — instead it's pointed directly at the files sitting on your Mac's disk. So when you edit app.py in your code editor and hit save, the container is already looking at the new version. No rebuild, no restart needed.
--server.runOnSave=true is a Streamlit flag that tells it to watch the files it's running and automatically refresh whenever it detects a change. So the moment you save in your editor, Streamlit notices the file changed and reloads the app.
Together: you edit → you save → Streamlit sees the change → app refreshes in the browser. Usually within a second or two. It's the same experience as live-reload in web development — you don't have to stop the container, rebuild, and start it again every time you want to see a change.

## Production deployment on netstack

```bash
# On netstack, pull the repo and build
git clone <your-repo-url> /opt/keanexus && cd /opt/keanexus
cp .env.example .env && nano .env     # fill in password

# Build and run (no volume mounts in production)
docker build -t keanexus:latest .
docker run -d --name keanexus --restart unless-stopped \
  --env-file .env \
  -p 8501:8501 \
  keanexus:latest
```

Then add to Nginx Proxy Manager as `keanexus.cyberwraith.net → 172.16.17.215:8501`
and a Pi-hole local DNS record pointing the name at NPM (172.16.17.212).

## Files

| File                     | Purpose                                  |
| ------------------------ | ---------------------------------------- |
| `app.py`                 | Streamlit app — tabs, rendering, dialogs |
| `kea.py`                 | Kea Control Agent HTTP client            |
| `.streamlit/config.toml` | Dark theme configuration                 |
| `Dockerfile`             | Production container                     |
| `docker-compose.yml`     | Local dev (bind-mounts for hot-reload)   |
| `.env.example`           | Environment variable template            |

## Extending

`kea.py` exposes a clean `KeaClient` with `call()` for any arbitrary Kea command,
plus higher-level helpers. All Kea commands available in your deployment can be
queried with `kea.call("list-commands", service="dhcp4")`.
