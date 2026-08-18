# As-Built Project Guide

> This document serves two purposes:
>
> 1. **Discovery** - find what exists before building something new
> 2. **Architecture** - document how systems are built and why
>
> Update this file with every commit. Keep it minimal and scannable.

---

## How to Maintain This Document

Before committing, ask: did I add, remove, or move any systems, components, or endpoints?
If yes - update the relevant section below before committing.

Include:

- Folder-level structure (not individual files)
- Major systems with their entry points and key functions
- API endpoints with input shape and purpose
- Architecture decisions and why they were made

Do NOT include:

- Individual file listings (LSP handles this)
- Implementation details (read the code)
- Generic framework conventions (project-specific only)

---

## Directory Structure

```
keanexus/
├── app.py              — Entry point: page config, CSS, sidebar, tab routing
├── version.py          — APP_VERSION constant (single source of truth)
├── auth.py             — Authentication: env-var credential check, session state
├── kea.py              — Kea direct HTTP API client (Kea 3.0+)
├── pihole.py           — Pi-hole v6 REST API client (session auth); used by quarantine_service
├── quarantine_client.py — Thin client KeaNexus uses to call keanexus-quarantine's own API
├── helpers.py          — Cached data loaders, format utilities
├── db.py               — SQLite persistence layer (IPAM static records)
├── ui_login.py         — Login page: logo, username/password form
├── ui_pool.py          — Pool tab: utilisation gauge, service health, lease summary
├── ui_leases.py        — Leases tab: HTML table with type classification
├── ui_ipam.py          — IPAM tab: full /24 subnet map + static entry management
├── ui_reservations.py  — Reservations tab: Kea config CRUD
├── ui_maintenance.py   — Maintenance tab: DHCP enable/disable, wipe leases
├── ui_quarantine.py    — Quarantine tab: device registry CRUD + read-only audit log
├── ui_settings.py      — Settings tab
├── style.css           — Global CSS overrides for Streamlit internals
├── Makefile            — Developer setup (`make setup`) and test runner (`make test`)
├── static/             — Static assets (keanexus_logo.png)
├── quarantine_service/ — Optional keanexus-quarantine FastAPI service (compose profile "quarantine")
├── docs/               — PRD and this guide
├── .githooks/          — Committed git hooks; activated per-clone with `make setup`
└── .streamlit/         — Streamlit config (theme)
```

Data persisted in Docker named volume `keanexus_data` mounted at `/app/data/`.

## Systems

### Authentication (`auth.py`)

- Credentials read from `KEANEXUS_USERNAME` / `KEANEXUS_PASSWORD` env vars (set in `.env`)
- `is_authenticated()` — checks `st.session_state["authenticated"]`
- `attempt_login(username, password)` — constant-time compare via `hmac.compare_digest`; sets session state on success
- `logout()` — clears session state
- `app.py` guards `main()` with `is_authenticated()` — unauthenticated requests see only the login page
- Session lasts for the browser session only (no persistent cookie)

### Kea Client (`kea.py`)

- `KeaClient` — synchronous HTTP client talking directly to the `kea-dhcp4` HTTP listener (Kea 3.0+)
- Connection configured via `KEA_API_URL`, `KEA_API_USER`, `KEA_API_PASSWORD` env vars
- The `"service"` key is never sent — direct listeners don't support forwarding and reject it
- Key methods: `get_leases()`, `get_pool_stats()`, `get_config()`, `save_config()`, `get_status()`, `get_pool_range(config)`
- `get_status()` returns both `"ca"` and `"dhcp4"` keys for UI compatibility; `"ca"` is a static placeholder (`up=True`, `version="n/a"`) since the Control Agent no longer exists in Kea 3.0+

### Pi-hole Client (`pihole.py`)

- `PiholeClient` — synchronous client for Pi-hole's v6 REST API. Not currently used by
  the KeaNexus dashboard itself; lives at the repo root (like `kea.py`) so
  `quarantine_service` can import it, and so a future KeaNexus tab could reuse it
- Session-based auth (v6 replaced v5's static token scheme): first call triggers
  `POST /api/auth`, caching the returned `sid`/`csrf` until the session's validity
  window is nearly up, then transparently re-authenticates — callers never manage
  the session themselves
- `request(method, path, json_body=None)` — generic authenticated request. CSRF
  header is only attached for state-changing methods (POST/PUT/DELETE/PATCH), matching
  Pi-hole's double-submit CSRF scheme
- Connection configured via `PIHOLE_API_URL`, `PIHOLE_API_PASSWORD` env vars
- Built against Pi-hole's documented v6 API and community references, not verified
  against a live instance — see the caveat under `pihole_block.py` below

### Quarantine Client (`quarantine_client.py`)

- `trigger_quarantine(target, is_group=False)` / `trigger_release(target, is_group=False)` —
  called by the Quarantine tab's Quarantine/Release buttons. Same API a Siri Shortcut
  calls, just from inside KeaNexus itself
- Configured via `QUARANTINE_SERVICE_URL`, `QUARANTINE_SERVICE_TOKEN` (must match
  `QUARANTINE_API_TOKEN` in `quarantine_service/.env`)
- `QUARANTINE_SERVICE_URL` **must be the Docker host's real LAN IP, not
  localhost** — `keanexus-quarantine` runs with `network_mode: host` while
  KeaNexus itself is on the default bridge network, so `localhost` from inside the
  KeaNexus container resolves to its own network namespace, not the Docker host
- 180s request timeout, deliberately generous: each of the four enforcement steps on
  the far end retries up to 3 times with a 2s backoff, and the nmap step alone can
  take up to ~45s per attempt — a slow-but-working retry sequence shouldn't look
  like a client timeout from KeaNexus's side
- Raises `QuarantineServiceError` uniformly for unreachable service, missing token
  config, or a non-2xx response (parses the FastAPI `detail` field out of the error
  body when present)

### Version (`version.py`)

- `APP_VERSION` — single string constant, imported by `app.py` for sidebar display
- Bump on every release; no other source of truth for the app version

### Sidebar Version Block (`app.py`)

- `_sidebar_version_block(status)` — renders three info rows at the bottom of the sidebar
- Displays: KeaNexus version (from `APP_VERSION`), Kea DHCP daemon version (from `get_status()`), API Mode (always "Direct API")
- Always rendered regardless of Kea connectivity — aids diagnosis when Kea is unreachable

### Helpers (`helpers.py`)

- `get_client()` — `@st.cache_resource` singleton KeaClient
- `load_leases/load_pool_stats/load_config/load_status` — `@st.cache_data` with TTL
- `fmt_ttl(seconds)` — formats seconds to "Xh Ym" or "expired"

### Database (`db.py`)

- SQLite at `/app/data/keanexus.db`
- Table: `ipam_static` — static IP records (ip_address PK, hostname, mac_address, description, notes)
- Table: `device_registry` — quarantine-feature identity registry (friendly_name PK,
  hostname, group_tag, os_fingerprint, last_seen_mac_address, last_seen_ip_address,
  last_quarantined_at, notes). Keyed on friendly_name rather than MAC — MAC can be
  changed by the device, hostname/fingerprint can't. See
  `docs/quarantine-feature-design.md` for the full rationale.
- Table: `quarantine_log` — append-only audit trail (log_id PK autoincrement,
  friendly_name, action, step, succeeded, attempt_count, detail, occurred_at).
  Written by the `keanexus-quarantine` service (see below) on every enforcement
  step attempt.
- `init_db()` — idempotent schema creation, called at app startup
- `get_static_entries()`, `get_static_entry(ip)`, `upsert_static_entry(...)`, `delete_static_entry(ip)`
- `get_devices()`, `get_device(name)`, `upsert_device(...)`, `delete_device(name)`
- `insert_quarantine_log_entry(...)`, `get_quarantine_log(friendly_name=None, limit=100)`

### Pool Tab (`ui_pool.py`)

- `render_pool(stats, config, status)` — 3-column card layout
- `_gauge(pct, used, total, avail)` — CSS conic-gradient donut ring; green <75%, amber 75–90%, red ≥90%
- `_health_row(label, detail, up)` — service health row with status badge
- `_metric_row(label, value)` — lease summary metric row

### IPAM Tab (`ui_ipam.py`)

- `render_ipam(leases, config)` — full /24 subnet map (256 addresses)
- `_classify_all(...)` — classifies each IP as: network, broadcast, gateway, leased, declined, reserved, static, scope (free in pool), or free
- Status priority: network/broadcast > gateway > declined lease > active lease with fixed reservation (shows as reserved) > active lease > reservation > static DB entry > in-scope free > free
- Static entries stored in SQLite; only permitted for out-of-scope addresses
- Edit form in expander below table; `upsert_static_entry` / `delete_static_entry`

### Leases Tab (`ui_leases.py`)

- `render_leases(leases, config)` — HTML table, sorted by IP
- `_build_type_sets(config)` / `_lease_type(lease, ...)` — classifies lease as fixed/reserved/name-only/dynamic
- Dialogs: `add_lease_dialog()`, `edit_lease_dialog(lease)`

### Reservations Tab (`ui_reservations.py`)

- `render_reservations(config)` — CSS-grid header + `st.columns` data rows with per-row Edit buttons
- `_sorted_reservations(reservations)` — fixed-IP entries first (by IP), then name-only
- Dialogs: `add_reservation_dialog(config)`, `_edit_dialog(reservation, config)` (includes Delete)
- Each save writes immediately via `config-set` + `config-write`; no deferred "Save" step

### Quarantine Tab (`ui_quarantine.py`)

- `render_quarantine()` — device registry table with per-device Quarantine/Release
  buttons, plus a read-only quarantine audit log
- Manages `device_registry` directly (add/edit/delete via `_edit_dialog`), and
  triggers enforcement via `quarantine_client.py`, which calls the
  `keanexus-quarantine` service's own API — same endpoint a Siri Shortcut would call
- `_edit_dialog(friendly_name)` — add/edit/delete a registry entry; friendly_name is
  immutable once created (delete and re-add instead of renaming)
- Fingerprint and last-seen fields are displayed read-only in the dialog — they're
  written by the quarantine service, not editable from this tab
- `_trigger_action(friendly_name, action)` — calls `trigger_quarantine`/
  `trigger_release`, stashes a pass/fail summary in `st.session_state`, then calls
  `st.rerun()`. The stash-then-rerun pattern is necessary because `st.rerun()` wipes
  whatever was drawn this run — `_render_pending_action_result()` displays and clears
  the stashed result at the top of the next run
- `_summarize_step_results(action_label, result)` — turns the API's `step_results`
  into a one-line-per-device pass/fail summary; flags `skipped_due_to_identity_drift`
  distinctly from a step actually failing
- `_render_log_table(entries)` — renders the 50 most recent `quarantine_log` rows

### keanexus-quarantine Service (`quarantine_service/`)

Separate FastAPI service, own container, gated behind the `quarantine` Docker
Compose profile so it never builds or starts unless explicitly enabled. Full
rationale for every architecture choice below is in
`docs/quarantine-feature-design.md` — this section is discovery only.

Full orchestration is implemented: identity resolution, the pre-fire safety check,
and all four enforcement steps (Kea deny, ARP disruption, Pi-hole block, nmap
fingerprint refresh). Deployment fixes discovered during first real rollout are
folded into the descriptions below rather than tracked as separate PR numbers.

- `main.py` — FastAPI app (`app`). Routes:
    - `POST /quarantine` `{"target": str, "is_group": bool}` — resolves identity, then
      for each resolved device: calls `verify_identity_unchanged` immediately before
      enforcing; if it fails, logs a failed `identity_resolution` entry and skips that
      device entirely (`_skipped_step_result`) rather than acting on a possibly-stale
      IP. Otherwise runs `_enforce_device`: applies the Kea DROP-class deny, starts ARP
      disruption, applies the Pi-hole DNS block, and refreshes the OS fingerprint via
      nmap (all four through `retry.run_with_retries`), then calls
      `_stamp_last_quarantined` to record the current UTC timestamp in
      `device_registry.last_quarantined_at` — the field the Quarantine tab displays.
      Returns resolved devices plus per-device `step_results`, each including
      `skipped_due_to_identity_drift`
    - `POST /release` — same request/response shape and safety check as `/quarantine`;
      removes the Kea deny, stops ARP disruption, and removes the Pi-hole block instead
      of applying/starting them (fingerprint refresh still runs either way, but
      `last_quarantined_at` is deliberately **not** touched on release — it means
      exactly "last quarantined," not "last touched")
    - `GET /status/{friendly_name}` — returns recent `quarantine_log` rows for one device
    - All three require `Authorization: Bearer <token>` via `require_bearer_token`
    - `@app.on_event("startup")` calls `init_db()` eagerly. Discovered on first real
      deployment: KeaNexus's own Streamlit script also calls `init_db()`, but Streamlit
      defers running `app.py` until a browser session connects — it doesn't execute
      just because the container started. A fresh deploy where nobody had opened the
      KeaNexus dashboard yet left `quarantine_log`/`device_registry` missing entirely,
      which this service hit before anyone ever loaded the dashboard. FastAPI runs
      startup handlers eagerly on process start, making this service self-sufficient
      regardless of dashboard usage. `init_db()` is idempotent, so this is safe
      alongside KeaNexus's own call to it
    - `_get_kea_client()` / `_get_pihole_clients()` are factories (not module-level
      singletons) specifically so tests can patch them per-call with a stub
    - `_get_gateway_ip(kea)` extracts the subnet's router IP from the live Kea config
      (same `option-data` lookup pattern as `ui_ipam.py`) — needed by the ARP step to
      know which IP to impersonate
- `auth.py` — `require_bearer_token(authorization)`, a FastAPI dependency. Reads
  `QUARANTINE_API_TOKEN` from the environment on every call (not cached at import
  time) and compares with `hmac.compare_digest`, same rationale as `auth.py` in the
  main KeaNexus app
- `identity.py` — `resolve_target(kea, target, is_group)` resolves a `friendly_name`
  or `group_tag` against `device_registry`, then queries Kea's current leases by
  hostname (`KeaClient.get_leases_by_hostname`) to get the live MAC/IP. Raises
  `DeviceNotRegisteredError` (mapped to HTTP 404) or `DeviceNotOnNetworkError`
  (mapped to HTTP 409). Refreshes `last_seen_mac_address` / `last_seen_ip_address`
  in `device_registry` on every successful resolution via `upsert_device`.
  `verify_identity_unchanged(kea, device)` is the pre-fire safety check — called from
  `main.py` immediately before enforcing (not at resolution time), it re-queries the
  hostname's current lease and returns False if the IP or MAC has drifted since
  `resolve_target` first captured them. Matters most for group requests, where a
  device late in the list can sit for tens of seconds (an nmap scan alone can take up
  to 45s) before its turn comes
- `kea_deny.py` — `deny_via_kea(kea, mac)` / `undo_deny_via_kea(kea, mac)` fetch the
  live Dhcp4 config, mutate it, and push it back via `KeaClient.save_config`. Pure
  mutation logic lives in `apply_drop_class` / `remove_drop_class`, tested
  independently of any Kea client. Existing reservation fields (fixed IP, hostname)
  are always preserved — only `client-classes` is touched. `remove_drop_class`
  deletes the reservation entirely if DROP removal leaves it with no remaining
  purpose, rather than leaving an empty orphan reservation behind
- `arp_disrupt.py` — `start_arp_disruption(friendly_name, target_ip, target_mac,
gateway_ip)` / `stop_arp_disruption(friendly_name)`. Unlike the Kea deny, ARP
  disruption isn't a durable state change — a target's ARP cache self-heals within
  roughly a minute without continued reinforcement — so this runs as a background
  thread per device, sending a unicast spoofed ARP reply every
  `DEFAULT_SEND_INTERVAL_SECONDS` (2.0) claiming `gateway_ip` lives at `BLACKHOLE_MAC`
  (`02:00:00:00:00:00`, a locally-administered sentinel, never a real device).
  Active loops are tracked in an in-memory `_active_disruptions` dict keyed by
  friendly_name, guarded by `_registry_lock`. **Two known limitations, documented in
  the module docstring:** (1) loops live in process memory only — a
  `keanexus-quarantine` container restart silently drops all active loops with no
  auto-resume; (2) switches running Dynamic ARP Inspection alongside DHCP snooping
  may drop these spoofed packets entirely — worth checking if disruption doesn't
  appear to work. `start_arp_disruption` is idempotent (restarting for an
  already-active friendly_name stops the old loop first). `stop_arp_disruption` raises
  `TimeoutError` if the loop doesn't join within `_STOP_JOIN_TIMEOUT_SECONDS` (5.0),
  giving `retry.run_with_retries` something to retry against. `ARP_INTERFACE` env var
  optionally pins the send interface; unset lets scapy auto-select
- `retry.py` — `run_with_retries(step, friendly_name, action, attempt_fn, ...)`,
  shared by all four enforcement steps (Kea, ARP, Pi-hole, nmap refresh). Retries up
  to `MAX_ATTEMPTS` (3) with `BACKOFF_SECONDS` (2.0) delay between attempts, then
  writes exactly **one** `quarantine_log` row recording the final outcome and how
  many attempts it took — not one row per attempt. Catches any exception type
  deliberately, since Kea/ARP/Pi-hole/nmap all fail differently
- `pihole_block.py` — `block_via_pihole(pihole, ip)` / `unblock_via_pihole(pihole,
ip)`. Blocking works by assigning the device's current IP to a dedicated
  `keanexus_quarantine` Pi-hole group (auto-created if missing) that has a blanket
  deny-all regex (`(.*)`) scoped **only** to that group — other clients and the
  Default group are untouched. Mirrors the group-based blocking approach already
  used elsewhere on this network for parental controls rather than introducing a
  second mechanism. Client identity is IP, not MAC — safe because the Kea DROP-class
  deny already freezes the device off DHCP, so its IP doesn't change mid-quarantine.
  Unblocking deletes the Pi-hole client override entirely (reverts to Default group)
  rather than leaving an empty override behind. **Built against Pi-hole's documented
  v6 REST API and community references, not verified against a live instance** —
  check the `/clients` and `/groups` request/response shapes against this Pi-hole's
  own self-hosted docs at `http://pi.hole/api/docs` before relying on it.

            **Writes to both primary and secondary Pi-hole instances.** Discovered during
            deployment that the two Pi-hole instances on this network (172.16.17.212 primary,
            172.16.17.252 secondary on the TerraMaster NAS) are fully independent — no
            Nebula/Gravity/Orbital Sync between them — so blocking only the primary would
            leave a real gap if a device's DNS ever gets served by the secondary. `main.py`'s
            `_get_pihole_clients()` always includes the primary and adds the secondary only
            when `PIHOLE_SECONDARY_API_URL` is set; `_apply_pihole_step` writes to each with
            its own independent retry and its own audit log row (`pihole_primary` /
            `pihole_secondary` steps), so a partial failure on one instance is visible rather
            than collapsed into one ambiguous result. `PiholeClient.__init__` accepts optional
            `base_url`/`password` overrides (falling back to env vars) specifically to support
            constructing a second client pointed at the secondary instance.

- `nmap_fingerprint.py` — `refresh_os_fingerprint(friendly_name, target_ip)` shells
  out to `nmap -O --osscan-guess` (no meaningful pure-Python equivalent exists for
  real OS detection) and parses the XML output for the highest-accuracy `osmatch`.
  An inconclusive scan (no confident OS match) returns `""` and **does not overwrite**
  the existing fingerprint in `device_registry` — a single bad scan shouldn't erase
  previously good identity data. A subprocess failure (missing binary, timeout,
  nonzero exit) raises, giving `retry.run_with_retries` something to retry against.
  Runs for both quarantine and release — its purpose is keeping the identity signal
  fresh whenever a live IP is available, independent of enforcement direction
- Imports `db.py`, `kea.py`, and `pihole.py` directly from the repo root — not a
  shared installable package, just copied into the image by
  `quarantine_service/Dockerfile` alongside the service code. Both containers
  read/write the identical SQLite file via the same `keanexus_data` volume
- Package name uses an underscore (`quarantine_service`, not `quarantine-service`) so
  it's a valid Python import target — tests live in the shared root `tests/` directory
  and run through the existing pytest/coverage setup rather than a second test config
- Deployment: `network_mode: host` plus `cap_add: [NET_RAW, NET_ADMIN]` in
  `docker-compose.yml`, scoped to this container only — KeaNexus and Kea itself stay
  unprivileged. Configured via `quarantine_service/.env` (see `.env.example` for
  required vars: `KEA_API_URL`, `KEA_API_USER`, `KEA_API_PASSWORD`,
  `PIHOLE_API_URL`, `PIHOLE_API_PASSWORD`, `QUARANTINE_API_TOKEN`; optional
  `ARP_INTERFACE`, `PIHOLE_SECONDARY_API_URL`, `PIHOLE_SECONDARY_API_PASSWORD`)

## API Endpoints

All data flows through the Kea `kea-dhcp4` direct HTTP listener (port 8004). No Control Agent.

Key Kea commands used:

- `lease4-get-all` → all active leases
- `stat-lease4-summary` → pool utilisation counters
- `config-get` / `config-set` / `config-write` → full DHCPv4 config
- `version-get` → service health check and daemon version
- `dhcp-enable` / `dhcp-disable` → DHCP service control

### keanexus-quarantine HTTP API (optional service, port 8600)

All endpoints require `Authorization: Bearer <QUARANTINE_API_TOKEN>`.

- `POST /quarantine` — body `{"target": "<friendly_name or group_tag>", "is_group": bool}`.
  Resolves identity, re-verifies each device's identity hasn't drifted immediately
  before enforcing (skips that device with `skipped_due_to_identity_drift: true` in
  its `step_results` entry if it has), then applies the Kea DROP-class deny, starts
  ARP disruption, applies the Pi-hole DNS block, and refreshes the OS fingerprint.
  This is the full orchestration from the design doc.
- `POST /release` — same request/response shape as `/quarantine`; removes the Kea
  deny, stops ARP disruption, and removes the Pi-hole block instead of
  applying/starting them (fingerprint refresh still runs either way).
- `GET /status/{friendly_name}` — returns recent `quarantine_log` rows for that device.

## Architecture Decisions

**Direct Kea HTTP listener (not Control Agent)** — Kea 3.0 deprecated and removed the Control Agent. `kea-dhcp4` now exposes its own HTTP listener on port 8004. KeaNexus talks directly to it; the `"service"` forwarding key is never sent.

**CSS conic-gradient gauge (not SVG)** — Pool utilisation shown as a donut ring using `conic-gradient`. Simpler than SVG with equivalent visual quality; no JS required.

**SQLite for IPAM static records** — Local SQLite in a named Docker volume. Static IP records are KeaNexus-managed metadata (not sent to Kea); they exist purely for documentation/visibility of non-DHCP addresses.

**HTML tables (not st.dataframe)** — All data tables rendered as raw HTML to allow custom chip/badge formatting. Streamlit's native components don't support per-cell HTML.

**All times UTC internally** — Kea lease expiry timestamps are Unix epoch. Display formatting converts to human-readable durations via `fmt_ttl()`. `quarantine_log.occurred_at` is likewise stamped as UTC ISO8601 in `db.py`, not left to the caller.

**Device identity keyed on friendly_name, not MAC** — `device_registry` treats
MAC and current IP as disposable breadcrumbs rather than identity, because a
device can change its own MAC address. hostname and an nmap OS fingerprint
(refreshed by `quarantine_service/nmap_fingerprint.py` on every quarantine/release)
are the durable signal. Full rationale in `docs/quarantine-feature-design.md`.

**Pre-fire safety check skips, never auto-recovers** — when
`verify_identity_unchanged` finds a device's IP/MAC has drifted since resolution, the
device is skipped for that call rather than silently re-resolved and acted on with
the new values. A retry request re-resolves cleanly on its own; auto-recovering
inside the same request would mean mutating identity the caller already treated as
fixed for the duration of one call, adding a second code path to reason about for
little benefit over just asking the caller to retry.

## Testing

Unit tests live in `tests/`. Coverage is measured over every testable back-end
module (`--cov=.` in `pyproject.toml`, with `app.py` and `ui_*.py` explicitly omitted
since they require a live Streamlit server and can't be unit-tested). That includes
`auth.py`, `db.py`, `kea.py`, `helpers.py`, `pihole.py`, and everything in
`quarantine_service/`.

| File                                        | What is tested                                                                                                                                                                                                                        |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/test_auth.py`                        | `is_authenticated`, `attempt_login` (all credential paths), `logout`                                                                                                                                                                  |
| `tests/test_db.py`                          | Full CRUD on `ipam_static`, `device_registry`, and `quarantine_log` via `temp_db` fixture (SQLite in tmp dir)                                                                                                                         |
| `tests/test_kea.py`                         | All `KeaClient` methods; `httpx` patched via `http_mock` fixture                                                                                                                                                                      |
| `tests/test_helpers.py`                     | `fmt_ttl`, `chip`, `leases_to_df` (pure functions only)                                                                                                                                                                               |
| `tests/test_pihole.py`                      | `PiholeClient` session auth (caching, reuse, re-auth on expiry), CSRF header logic, error mapping, constructor overrides (base_url/password); `httpx` patched via `pihole_http_mock` fixture                                          |
| `tests/test_quarantine_client.py`           | `trigger_quarantine`/`trigger_release` — request shape, auth header, is_group passthrough, missing-token error, connect error, HTTP error detail parsing; `httpx` patched via `quarantine_client_http_mock` fixture                   |
| `tests/test_quarantine_auth.py`             | `require_bearer_token` — missing config, missing/malformed header, wrong token, success                                                                                                                                               |
| `tests/test_quarantine_identity.py`         | `resolve_target` — single device and group resolution, unregistered target, no-lease case, last-seen refresh; `verify_identity_unchanged` — match, IP drift, MAC drift, lease gone — using `StubKeaClient`                            |
| `tests/test_quarantine_kea_deny.py`         | `apply_drop_class` / `remove_drop_class` (pure config mutation), `deny_via_kea` / `undo_deny_via_kea` (via `StubKeaClient`)                                                                                                           |
| `tests/test_quarantine_arp_disrupt.py`      | `send_poisoned_arp_reply` packet construction, `start_arp_disruption` / `stop_arp_disruption` loop lifecycle, idempotent restart, survives a failed send, no packets after stop — `sendp` always patched                              |
| `tests/test_quarantine_pihole_block.py`     | `block_via_pihole` / `unblock_via_pihole` — group/regex creation vs reuse, unrelated groups ignored, client PUT/DELETE calls, using `StubPiholeClient`                                                                                |
| `tests/test_quarantine_nmap_fingerprint.py` | `refresh_os_fingerprint` — persists top-accuracy match, preserves other registry fields, inconclusive scan doesn't overwrite existing fingerprint, subprocess failure propagates; `subprocess.run` always patched                     |
| `tests/test_quarantine_retry.py`            | `run_with_retries` — first-attempt success, success-after-failures, exhausted-retries, custom `max_attempts`, one-row-per-step logging                                                                                                |
| `tests/test_quarantine_main.py`             | FastAPI routes via `TestClient` — auth enforcement, 404/409 error mapping, all four enforcement steps wired correctly, group requests, pre-fire safety check skips a device whose identity drifted between resolution and enforcement |

`conftest.py` provides `temp_db` (redirects `_DB_PATH` to a temp file), `http_mock`
(patches `kea.httpx.Client`), `pihole_http_mock` (patches `pihole.httpx.Client`,
same pattern), `quarantine_client_http_mock` (patches `quarantine_client.httpx.Client`,
same pattern again), `stub_kea_client` (factory fixture for `StubKeaClient`, fakes
lease lookups and Kea config get/save), and `stub_pihole_client` (factory fixture for
`StubPiholeClient`, fakes Pi-hole's `request()` with canned `(method, path)` ->
response JSON). The two Stub classes are exposed as fixtures rather than plain class
imports because `tests/__init__.py` makes `tests` a package, so pytest registers this
module as `tests.conftest` — a bare `from conftest import StubKeaClient` in another
test file won't resolve, but fixtures always will. Both stubs record every call made
(`saved_configs` / `calls`) so tests can assert on exactly what would have been sent.

**Streamlit cached loaders** (`load_leases`, `load_pool_stats`, `load_config`, `load_status` in `helpers.py`) are not tested — they require a `ScriptRunContext` that only exists inside a running Streamlit app.

## Validation and Error Handling Standards

- UI components catch all errors and display via `st.error()`
- Internal modules raise `KeaError` for Kea API failures; callers handle at the tab level
- IPAM edit form validates: IP must be in subnet, must be out-of-scope, must not be network/broadcast
- DB functions assert IP is a non-empty string; SQLite PRIMARY KEY enforces uniqueness
- `upsert_device` asserts friendly_name and hostname are non-empty; friendly_name is the SQLite PRIMARY KEY
- `insert_quarantine_log_entry` asserts friendly_name is non-empty, action is `"quarantine"` or `"release"`, and attempt_count is at least 1
