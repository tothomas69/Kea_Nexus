# Session Notes — 2026-09-09

## Incident: Win11 PC permanently quarantined ("Tommy PC")

**Device:** `54:07:7d:82:6c:0a` / `172.16.17.143` (Windows 11, Wi-Fi)
**Symptom:** No DHCP lease. With a manual static IP, could not reach gateway
(`172.16.17.1`) or `8.8.8.8`. No ARP entry on `netstack`. DNS unresolvable.

### Root cause

Device was quarantined via the KeaNexus GUI on **2026-09-07 18:36**. Its
`device_registry` row (friendly_name `Tommy PC`) was subsequently **deleted
while still quarantined**. Because `/release` resolves targets through
`device_registry`, there was no longer anything to resolve — the release path
could never run, and all enforcement remained in place indefinitely.

`quarantine_log` evidence: `Tommy PC` last quarantine 2026-09-07T18:36,
last release 2026-09-05T00:01 (two days _earlier_). 54 quarantine step rows
vs 42 release step rows.

### Two enforcement layers left stranded

1. **Kea DROP class** — `kea_deny.py` wrote `"client-classes": ["DROP"]` into
   the host reservation for this MAC. Persisted to disk via
   `save_config()` → `config-write`, so it survived every Kea restart.
   _Effect: silent DHCP discard — no OFFER, no NAK._

2. **ARP disruption thread** — `arp_disrupt.py` spawns an in-memory thread
   poisoning the target's ARP. `keanexus-quarantine` container had been up
   continuously since **2026-08-26**, i.e. across the Sept 7 quarantine, so
   the thread was still running 2 days later.
   _Effect: layer-2 blackout — this was the cause of the ping/routing failure,
   not DNS._

### Fix applied (manual)

1. Backed up `/etc/kea/kea-dhcp4.conf` inside the `kea-dhcp4` container.
2. `sed -i '243s/\[ "DROP" \]/[ ]/'` — cleared DROP, preserving the
   reservation's fixed IP (`172.16.17.143`).
3. Validated with `kea-dhcp4 -t /etc/kea/kea-dhcp4.conf` — clean.
4. `docker restart kea-dhcp4`.
5. `docker restart keanexus-quarantine` — killed the orphaned ARP thread.
6. On the PC (as Administrator): `arp -d *`, `ipconfig /release`,
   `ipconfig /renew`, `ping 8.8.8.8` — **confirmed working**.

Verified no other DROP entries remain: `grep -B4 -A4 '"DROP"'` returns empty.

---

## Defects identified (not yet fixed)

### 1. Orphaned enforcement on registry deletion — HIGH

Deleting a `device_registry` row while `is_quarantined = 1` strands the Kea
DROP entry and the ARP thread with no index back to them. Enforcement state
lives in Kea config / Pi-hole / memory; identity lives in SQLite; nothing
reconciles the two.

Proposed: block deletion while `is_quarantined = 1` (force release first),
**or** run release automatically as part of deletion.

### 2. No reconciliation between Kea and registry — HIGH

Nothing ever compares DROP entries in Kea's config against registry owners.
A startup reconciliation check would have surfaced this in seconds.

Proposed: on service startup, list all reservations carrying DROP; any MAC
with no corresponding `device_registry` row with `is_quarantined = 1` is an
orphan — log loudly, optionally auto-clear.

### 3. Pi-hole unblock 404s on every release — MEDIUM

`unblock_via_pihole()` issues `DELETE /clients/{ip}`. When no client override
exists, Pi-hole returns **404**, which `PiholeClient.request()` raises as
`PiholeError`. `run_with_retries` then burns 3 attempts and logs
`succeeded: 0`.

Observed on **both** primary and secondary, on every release in the log:
`Pi-hole returned HTTP 404: {"took":0.00025...}`

A delete that finds nothing already achieved its goal. Proposed: treat 404 on
DELETE as success (idempotent delete).

### 4. `verify_identity_unchanged()` has a blind spot — LOW/DESIGN

`identity.py` resolves via `kea.get_leases_by_hostname(...)` and takes
`leases[0]` without checking for multiple matches. `verify_identity_unchanged()`
re-checks against the _same_ ambiguous `leases[0]`, so it cannot detect the
two-leases-one-hostname case it was written to guard against.

Not the cause of this incident (`tommy-kubuntu` returned exactly one lease),
but relevant to the known docked-laptop / dual-NIC scenario.

### 5. ARP disruption threads are memory-only — DESIGN

Threads do not survive a container restart, and there is no persisted record
of which are active. Consequences both ways:

- A restart silently releases every actively-quarantined device.
- A failed release leaves a thread running with no external record of it.

`_active_disruptions` cannot be inspected via `docker exec python3 -c` —
that spawns a separate interpreter and always shows `{}`. Misleading during
debugging.

---

## Debugging notes worth keeping

- `docker logs -f <container>` dumps the **entire** history before following.
  Use `--since` or `--tail` when looking for recent events.
- The quarantine DB is `/app/data/keanexus.db`, **not** `quarantine.db`.
  Connecting sqlite3 to a non-existent path silently creates an empty file.
- No `sqlite3` binary in the quarantine image — use
  `docker exec ... python3 -c "import sqlite3; ..."`.
- Registry column is `last_seen_mac_address`, not `mac`.
- `quarantine_log` is keyed by `friendly_name`, not MAC — orphaned devices are
  findable only by name.
- Shell env: `set -a; source .env; set +a` in `/root/keanexus` before any
  `curl` using `$KEA_API_PASSWORD`.
- `172.16.17.1` does not answer ICMP even when routing is healthy. Ping is an
  unreliable test on this network; use `ping 8.8.8.8` or `tracert`.
- Pool at time of incident: 49/85 assigned, 0 declined — exhaustion ruled out.

## Open / unrelated

- Two privacy-MAC devices (`40:aa:56:8f:2c:23`, `40:aa:56:8f:3e:8d`) DISCOVER
  every ~90s, receive an OFFER, and never REQUEST — consuming a new address
  each time. Visible in July logs and likely ongoing. Worth investigating for
  pool churn.
- `nmap` is installed in the `keanexus-quarantine` image. Used by
  `nmap_fingerprint.py`, but worth confirming the attack surface is intended.
