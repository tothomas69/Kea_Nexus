# Quarantine Troubleshooting Runbook

> Operator-facing. For the design rationale behind these three enforcement
> layers, see `docs/quarantine-feature-design.md`.

Quarantine enforces across three independent layers. Any one of them can be
left behind on its own, and each fails differently. **Identify which layer is
still active before changing anything** — the symptoms overlap enough that
guessing wastes time.

| Layer          | Where it lives                  | Survives restart? | Symptom when stuck                                      |
| -------------- | ------------------------------- | ----------------- | ------------------------------------------------------- |
| Kea DROP class | `kea-dhcp4.conf` on disk        | Yes               | No DHCP lease at all — no OFFER, no NAK                 |
| ARP disruption | Thread in service memory        | No                | Can't reach gateway or any IP; no ARP entry on the host |
| Pi-hole block  | Client override on each Pi-hole | Yes               | IPs reachable, names don't resolve                      |

---

## Step 1 — Narrow it down

Run these on the affected device, in order. Each result points somewhere
different.

```
ipconfig /all          # Windows — check IPv4, Default Gateway, DNS Servers
ping 8.8.8.8           # routing, no DNS involved
ping google.com        # DNS
```

- **No address, or 169.254.x.x** → Kea DROP class (step 2)
- **Has an address, `ping 8.8.8.8` fails** → ARP disruption (step 3)
- **`ping 8.8.8.8` works, `ping google.com` fails** → Pi-hole block (step 4)

**Do not test with a ping to the gateway.** On this network `172.16.17.1`
does not answer ICMP even when routing is perfectly healthy. It will look
broken when it isn't.

---

## Step 2 — Kea DROP class

Blocks DHCP silently. Written by `kea_deny.py` and persisted to disk via
`config-write`, so it survives every Kea restart.

**Check whether the device has a lease:**

```bash
cd /root/keanexus && set -a; source .env; set +a

curl -s -u "$KEA_API_USER:$KEA_API_PASSWORD" \
  -H "Content-Type: application/json" \
  -d '{"command":"lease4-get-by-hw-address",
       "arguments":{"hwaddr":"DEVICE_MAC"}}' \
  http://172.16.17.215:8004/ | jq
```

**Find any DROP entries:**

```bash
docker exec kea-dhcp4 grep -n -B4 -A4 '"DROP"' /etc/kea/kea-dhcp4.conf
```

Empty output means this layer is clean. Otherwise note the line number of the
`client-classes` line.

**Clear it:**

```bash
# Always back up first
docker exec kea-dhcp4 cp /etc/kea/kea-dhcp4.conf \
  /etc/kea/kea-dhcp4.conf.bak-$(date +%Y%m%d-%H%M%S)

# LINE_NO from the grep above
docker exec kea-dhcp4 sed -i 'LINE_NOs/\[ "DROP" \]/[ ]/' \
  /etc/kea/kea-dhcp4.conf

# Validate BEFORE restarting — malformed JSON takes DHCP down for everyone
docker exec kea-dhcp4 kea-dhcp4 -t /etc/kea/kea-dhcp4.conf

docker restart kea-dhcp4
```

Clearing only the class preserves the reservation's fixed IP and hostname. A
reservation that had a purpose beyond the DROP keeps it.

---

## Step 3 — ARP disruption

Severs layer 2. `arp_disrupt.py` runs one thread per quarantined device,
re-sending poisoned ARP replies every 2 seconds.

**Is the device reachable at layer 2?**

```bash
arp -a | grep -i "DEVICE_MAC_PREFIX"
```

No entry, while the device believes it's on the network, points here.

**Was the container running when the quarantine happened?**

```bash
docker inspect keanexus-quarantine --format '{{.State.StartedAt}}'
```

Threads are memory-only. If the container started _after_ the quarantine, the
thread is already gone and this layer is not your problem.

**Clear it:**

```bash
docker restart keanexus-quarantine
```

> This also silently releases every device legitimately quarantined right now.
> There's no persisted record of active disruptions to restore from.

`_active_disruptions` cannot be inspected with `docker exec python3 -c` — that
spawns a separate interpreter and always prints `{}` regardless of what the
running service is doing. Misleading; ignore it.

---

## Step 4 — Pi-hole block

Assigns the device's IP to the `keanexus_quarantine` group, which carries a
deny-all regex. Applied to **both** instances independently — there's no sync
between them, so both must be cleared.

**List client overrides on both:**

```bash
docker exec keanexus-quarantine python3 -c "
import os
from pihole import PiholeClient
for label, url, pw in [
    ('primary',   os.environ.get('PIHOLE_API_URL',''),
                  os.environ.get('PIHOLE_API_PASSWORD','')),
    ('secondary', os.environ.get('PIHOLE_SECONDARY_API_URL',''),
                  os.environ.get('PIHOLE_SECONDARY_API_PASSWORD','')),
]:
    if not url: continue
    try:
        c = PiholeClient(base_url=url, password=pw)
        for cl in c.request('GET','/clients').get('clients',[]):
            print(label, cl.get('client'), cl.get('groups'), cl.get('comment'))
    except Exception as e:
        print(label,'ERR',e)
"
```

**Clear one device from both:**

```bash
docker exec keanexus-quarantine python3 -c "
import os
from pihole import PiholeClient
for url, pw in [
    (os.environ.get('PIHOLE_API_URL',''),
     os.environ.get('PIHOLE_API_PASSWORD','')),
    (os.environ.get('PIHOLE_SECONDARY_API_URL',''),
     os.environ.get('PIHOLE_SECONDARY_API_PASSWORD','')),
]:
    if not url: continue
    try:
        PiholeClient(base_url=url, password=pw).request(
            'DELETE', '/clients/DEVICE_IP')
        print(url, 'cleared')
    except Exception as e:
        print(url, 'ERR', e)
"
```

---

## Step 5 — On the device

Enforcement leaves cached state behind that outlives the fix.

```
arp -d *              # as Administrator — drops the poisoned gateway entry
ipconfig /release
ipconfig /renew
ipconfig /flushdns
ping 8.8.8.8
```

---

## Inspecting service state

No `sqlite3` binary in the image; use Python. The database is
`/app/data/keanexus.db` — **not** `quarantine.db`. Connecting sqlite3 to a
path that doesn't exist silently creates an empty file rather than erroring,
which looks like an empty database.

**Registry contents:**

```bash
docker exec keanexus-quarantine python3 -c "
import sqlite3
db=sqlite3.connect('/app/data/keanexus.db'); db.row_factory=sqlite3.Row
for r in db.execute('''SELECT friendly_name, hostname,
                              last_seen_mac_address, last_seen_ip_address,
                              is_quarantined
                       FROM device_registry'''):
    print(dict(r))
"
```

**Recent enforcement log:**

```bash
docker exec keanexus-quarantine python3 -c "
import sqlite3
db=sqlite3.connect('/app/data/keanexus.db'); db.row_factory=sqlite3.Row
for r in db.execute('''SELECT * FROM quarantine_log
                       ORDER BY occurred_at DESC LIMIT 20'''):
    print(dict(r))
"
```

**Every device ever acted on** — including ones no longer in the registry.
This is how you find an orphan:

```bash
docker exec keanexus-quarantine python3 -c "
import sqlite3
db=sqlite3.connect('/app/data/keanexus.db'); db.row_factory=sqlite3.Row
for r in db.execute('''SELECT friendly_name, action, COUNT(*) c,
                              MAX(occurred_at) last
                       FROM quarantine_log
                       GROUP BY friendly_name, action'''):
    print(dict(r))
"
```

A name whose most recent `quarantine` is later than its most recent `release`,
and which no longer appears in `device_registry`, is an orphan. Its
enforcement is still live with nothing tracking it.

Note `quarantine_log` is keyed on `friendly_name`, not MAC — an orphaned
device is findable only by the name it had at the time.

---

## Gotchas

- `docker logs -f` dumps the **entire** history before following. Use
  `--since 10m` or `--tail 100` when looking for recent events.
- Registry column is `last_seen_mac_address`, not `mac`.
- Shell needs the env loaded before any `curl` using `$KEA_API_PASSWORD`:
  `cd /root/keanexus && set -a; source .env; set +a`
- `/var/lib/kea/kea-leases4.csv` does not exist on this installation. All
  lease data comes through the Control Agent API.
- Kea reservations here live in the config file, not a host database, so
  `reservation-del`/`reservation-add` are unavailable — they need the
  `host_cmds` hook and a database backend. Edit the config instead.
- Running a single test file fails the 80% coverage gate by design. Run the
  full suite, or pass `--no-cov`.

---

## Redeploy

```bash
# Mac
git add -A && git commit -m "..." && git push

# netstack — never `git pull`, it accumulates divergent commits
cd /root/keanexus
git fetch origin && git reset --hard origin/main
docker compose --profile quarantine up -d --build
```

Verify the container actually received a change before concluding a fix is
live — a bind-mounted file that looks right on the Mac may not be what's
running:

```bash
docker exec keanexus grep -c "SOME_NEW_STRING" /app/ui_quarantine.py
```

Non-zero means the code is deployed and any remaining problem is browser or
Streamlit caching, not deployment.
