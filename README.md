# KeaNexus

A web dashboard for a home Kea DHCP server. Version **2.0.0**.

KeaNexus talks straight to the **kea-dhcp4 HTTP listener** (Kea 3.0+). It does
not use the Control Agent or a proxy. The UI is FastAPI with server-rendered
HTML, HTMX and Alpine.js. There is no Node build step, and nothing is loaded
from a CDN, so it keeps working when the internet connection is down.

## Screens

- **Overview**: pool use, active leases, new leases in the last 24 hours,
  reservations and registered devices. An address map shows all 256 addresses
  in the /24, and hovering over one shows its details, including the MAC that
  last answered an ARP sweep. Also charts and an activity feed.
- **Leases**: every lease, with a live filter, add and edit, and an Online column.
- **Reservations**: Kea host reservations with KeaNexus-only labels, add, edit
  and delete, and an Online column.
- **Devices**: the device registry. Give a device a friendly name, a group tag
  and notes; the table shows its current IP, MAC and whether it's online.
- **Maintenance**: turn DHCP off and on (with Kea's own re-enable timer),
  clear declined leases, and wipe the lease database.
- **Settings**: the pool range, lease length and DHCP options. Admins also
  manage **Users** here.
- **Search**: press ⌘K or Ctrl+K on any screen to find a device, IP address,
  MAC address, hostname or label.
- **Help**: how Devices and the "who's online" sweep work, and what to do when
  the sweep fails.

The header shows the health of Kea and the two Pi-holes (labelled DNS1 and DNS2).

## Users and roles

There are two roles. **Admins** can change things. **Viewers** can look but not
change anything: they get a read-only Kea client, so a viewer can't write to Kea
even by calling a route directly. Passwords are stored as argon2 hashes.

The first time KeaNexus starts with no users, it creates an admin from
`KEANEXUS_USERNAME` and `KEANEXUS_PASSWORD`. After that, manage users under
**Settings → Users**.

## Requirements

- **Kea 3.0+** with an HTTP listener on `kea-dhcp4` (see below)
- Docker and Docker Compose, on a host that sits on the LAN the leases are on.
  The container runs with host networking and `NET_RAW` so it can ARP-sweep
  the subnet: that's what fills the Online columns and the map's "On the wire"
  MAC addresses.

## Kea configuration

Add a `control-sockets` HTTP listener to `kea-dhcp4.conf`. KeaNexus does not use
the Control Agent (`kea-ctrl-agent`), so it should not be running.

```json
"control-sockets": [
  {
    "socket-type": "http",
    "socket-address": "127.0.0.1",
    "socket-port": 8004,
    "authentication": {
      "type": "basic",
      "realm": "kea-dhcp4",
      "clients": [{ "user": "admin", "password": "yourpassword" }]
    }
  }
]
```

> If KeaNexus runs in Docker, set `socket-address` to the host's LAN IP, not
> loopback. The container has its own network, so loopback inside it is not
> the host.

## Configuration

Copy `.env.example` to `.env` and fill it in.

| Variable | What it is for |
| --- | --- |
| `KEA_API_URL`, `KEA_API_USER`, `KEA_API_PASSWORD` | The Kea HTTP listener and its basic-auth login |
| `KEANEXUS_SECRET` | Signs the session cookie. **Required**: sign-in is refused without it. Changing it signs everyone out |
| `KEANEXUS_USERNAME`, `KEANEXUS_PASSWORD` | Used only once, to create the first admin |
| `PIHOLE_API_URL`, `PIHOLE_SECONDARY_API_URL` | Optional. Adds the DNS1 and DNS2 health dots. Include the admin port, for example `http://172.16.17.212:8080` |
| `POOL_SAMPLE_INTERVAL_SECONDS`, `POOL_SAMPLE_RETENTION_DAYS` | How often the pool chart takes a reading (default 300) and how long readings are kept (default 30 days) |
| `LIVENESS_SWEEP_INTERVAL_SECONDS`, `KEANEXUS_WEB_LIVENESS_SWEEP`, `ARP_INTERFACE` | Optional. The ARP sweep's interval (default 300), an `off` switch for running without host networking, and the interface to sweep on when the host has several |

## Running it

```bash
git clone git@github.com:tothomas69/Kea_Nexus.git keanexus && cd keanexus
cp .env.example .env    # then fill it in
docker compose up -d --build
```

The UI listens on port **8502** of the host itself (host networking, so there
is no port mapping; `PORT` in `docker-compose.yml` sets it).

### Updating

```bash
cd /root/keanexus && git fetch origin && git reset --hard origin/main
docker compose up -d --build --remove-orphans
```

Python modules are bind-mounted, so a code-only change takes effect after a
container restart. A dependency change needs `--build`.

## Development

```bash
make setup    # turns on the git hooks in .githooks/ and checks your dev tools
make test     # pytest with coverage
```

- The **pre-commit** hook runs Ruff and a secrets check on staged files.
- The **pre-push** hook runs the full test suite (80% coverage minimum),
  a whole-tree secrets scan, and pip-audit.

`docs/as-built-project-guide.md` describes every module, route and design
decision. Read it before adding anything.

## Layout

| Path | What it holds |
| --- | --- |
| `web/` | The UI: FastAPI app, routes, Jinja templates, static files and vendored JS |
| `kea.py` | The Kea HTTP client, plus the read-only client given to viewers |
| `auth.py` | Users, roles, argon2 passwords and the signed session token |
| `db.py` | SQLite: static records, labels, device registry, activity log, users, pool samples, the last sweep |
| `address_map.py`, `dashboard_data.py`, `helpers.py` | Pure shaping logic, unit-tested |
| `version.py` | The version number shown in the UI |
| `docs/` | The as-built guide, the migration notes, and the Help article |

## Extending

`KeaClient.call()` sends any Kea command. To list what your Kea supports:

```python
kea.call("list-commands")
```

The `service` key is never sent: the direct HTTP listener doesn't accept it.
