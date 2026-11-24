# Session Notes — 2026-11-24: quarantine removed

Quarantine wasn't working reliably, so it's gone from the code base.

## What changed

- **Deleted:** `quarantine_service/` (the keanexus-quarantine container: Kea DROP,
  ARP disruption, Pi-hole block, nmap fingerprint, presence check, Siri API),
  `quarantine_client.py`, `pihole.py` (only the service used it), the Quarantine
  screen, the Overview's Quarantine card and Quarantined tile, the hover card's
  Quarantine/Release button, the "Quarantine svc" health dot, and the three
  quarantine/Siri Help articles. Their tests went with them.
- **Kept, as Devices:** the device registry (friendly name, hostname, group tag,
  notes) at `/devices`. IP, MAC and Online now come live from the device's lease
  and the latest sweep instead of the service's last-seen breadcrumbs. Groups are
  labels only. The map hover and Search still show the names.
- **Moved:** the "who's online" ARP sweep, from the service into the web app
  (`web/arp_sweep.py`). The `keanexus` container now runs with
  `network_mode: host` + `cap_add: NET_RAW`, and uvicorn binds **8502** itself
  (`PORT`), so NPM's upstream is unchanged. A missing capability shows on the
  Leases tab as "The sweep couldn't run: …".
- **Overview:** the 4th KPI tile is now Devices (registered, N online). Activity
  is the admin activity log only.
- **Reservations:** a DROP-class reservation shows a neutral `DROP` chip and can
  be deleted like any other.
- **Database:** no migration. The quarantine-era `device_registry` columns and the
  `quarantine_log` table stay in the file, unread.
- New Help article: `docs/devices-and-whos-online.md`.

## Deploying on netstack

1. **Before deploying**, release every quarantined device in the current UI.
   After this change nothing can undo a DROP class, a Pi-hole block or an ARP
   disruption loop.
2. `git fetch origin && git reset --hard origin/main`
3. `docker compose up -d --build --remove-orphans` (no `--profile quarantine`;
   `--remove-orphans` removes the old keanexus-quarantine container)
4. Check: Leases → Check who's online fills the Online column.

Optional tidy-up: delete `quarantine_service/.env` on netstack (untracked; holds
the API token and a Pi-hole password), drop `QUARANTINE_SERVICE_URL` /
`QUARANTINE_SERVICE_TOKEN` from `.env` (now ignored), the empty
`keanexus_quarantine` group in both Pi-holes, and the Siri Shortcut.
