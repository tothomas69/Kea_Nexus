# Siri Shortcut Setup — Device Quarantine

> Companion to `docs/quarantine-feature-design.md`. This is configuration,
> not code — nothing here lives in the repo except this doc.

## Before you start

1. `keanexus-quarantine` is deployed and running:
    ```
    docker compose --profile quarantine up -d
    ```
2. `quarantine_service/.env` has a real `QUARANTINE_API_TOKEN` (generate one
   with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`),
   and real `PIHOLE_API_PASSWORD`.
3. At least one device is registered in the KeaNexus **Quarantine** tab
   (friendly_name + hostname).
4. You know the service's address — `http://<netstack-ip>:8600` (netstack is
   LXC 108; confirm its actual IP if you're not sure it's still 172.16.17.215).

## Test the API directly first

Don't debug the Shortcut and the service at the same time — confirm the API
itself works with `curl` before touching the Shortcuts app:

```bash
curl -X POST http://<netstack-ip>:8600/quarantine \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{"target": "tommy_laptop"}'
```

A working call returns JSON with `resolved_devices` and `step_results`. Check
the KeaNexus Quarantine tab's audit log afterward — you should see five new
rows (`identity_resolution`, `kea`, `arp`, `pihole`, `nmap_refresh`). Then
test release the same way against `/release`.

If this doesn't work, nothing below will either — fix it here first.

## Building the Shortcut

Shortcuts doesn't turn free-form dictation into structured JSON well without
extra logic, so the simplest reliable pattern is **one Shortcut per device or
group, named so Siri can trigger it by name** — e.g. "Quarantine Tommy's
Laptop" rather than a single generic "Quarantine" shortcut that asks you to
say a name out loud.

In the Shortcuts app (exact menu wording may vary slightly by iOS version):

1. **New Shortcut.**
2. Add the **Get Contents of URL** action.
3. Set the URL to `http://<netstack-ip>:8600/quarantine`.
4. Expand **Show More** and set:
    - **Method**: POST
    - **Headers**: add two —
        - `Authorization` → `Bearer <your-token>`
        - `Content-Type` → `application/json`
    - **Request Body**: JSON, with one field:
        - `target` (Text) → `tommy_laptop` (the friendly_name from the registry)
5. Rename the Shortcut to something you'd naturally say — "Quarantine
   Tommy's Laptop."
6. Repeat for release, pointing at `/release` instead of `/quarantine`,
   named "Release Tommy's Laptop."

**For a whole group** instead of one device, use the same steps but set the
JSON body to two fields: `target` → the group_tag (e.g. `kids`), and
`is_group` (Boolean) → true. Name it something like "Quarantine the Kids."

## Wiring it up to Siri

Building the Shortcut isn't the same as making it voice-triggerable — two ways to connect them:

**Name-based (usually enough).** Once a Shortcut exists, Siri can run it
automatically by saying "Hey Siri, [exact shortcut name]" — no extra step.
This is why the naming in the steps above matters.

**Explicit phrase (more reliable for names that don't parse well from
speech).** In the Shortcut's detail view, look for **Add to Siri** and
record a custom phrase — it doesn't have to match the Shortcut's name, so
you can bind something short like "Quarantine Tommy" instead. Exact
placement of this button shifts a bit between iOS versions.

**Test by tapping before testing by voice.** Run the Shortcut manually from
the app first — that isolates "Siri misheard me" from "the API call
failed," which matters given how many pieces sit between your voice and
enforcement actually happening.

**HomePod needs a separate toggle.** If you want this triggerable from a
HomePod specifically, Personal Requests has to be enabled for your voice in
the Home app — HomePod won't run a Shortcut that touches your network or
personal data without it, independent of anything configured on your phone.

## Verifying it end to end

Say "Hey Siri, Quarantine Tommy's Laptop." Then check the KeaNexus
Quarantine tab's audit log — same five-row pattern as the `curl` test above
confirms the whole path from voice to enforcement actually worked.

## Things worth knowing before relying on this

- **This only works on your home network.** The Shortcut talks directly to
  `netstack`'s LAN address — away from home (cellular, a coffee shop) it'll
  just fail, unless you're on a VPN back into the house.
- **ARP disruption doesn't survive a `keanexus-quarantine` container
  restart.** If the container restarts while a device is quarantined, the
  Kea deny and Pi-hole block stay in effect, but the ARP loop is gone and
  won't restart itself — say the Shortcut again to re-establish it.
- **The token lives in the Shortcut itself.** Shortcuts data is
  device/iCloud-sandboxed, but treat it like any other credential — don't
  paste it somewhere it'll get logged or synced elsewhere.
- **Dynamic ARP Inspection**, if enabled on the Catalyst 4948 alongside its
  existing DHCP snooping, may silently drop the ARP disruption packets.
  Worth confirming disruption actually happens against a real test device
  before trusting it for anything that matters.
