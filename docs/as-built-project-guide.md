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
- Login persists across a page reload via a real browser cookie, not just
  `st.session_state` — a full reload starts a brand-new Streamlit session
  with blank session*state, which used to force a re-login every time.
  `SESSION_COOKIE_NAME` is the cookie name; `session_token()` returns the
  expected value, an HMAC of `KEANEXUS_PASSWORD` (stateless — no server-side
  session store, so restarting the app doesn't invalidate cookies already
  issued, and no separate secret needs configuring).
  `restore_session_from_cookie(cookie_value)` re-authenticates from that
  cookie via `hmac.compare_digest` if `is_authenticated()` is False. `app.py`
  owns the actual cookie I/O (via `extra_streamlit_components.CookieManager`,
  constructed each run by `_cookie_manager()`) and calls this on every fresh
  session before falling back to the login page — `auth.py` itself stays
  free of the CookieManager/component dependency, just the token logic.
  `_cookie_manager()` is deliberately \_not* `@st.cache_resource` —
  `CookieManager.__init__` itself calls a Streamlit component (a
  widget-like command), and Streamlit forbids that inside a cached function
  (`CachedWidgetWarning`); the component already stabilizes itself across
  reruns via its own internal `key="init"`, not Python object identity
- `main()` waits for the CookieManager component's first round trip before
  deciding a session is unauthenticated: `cookie_manager.get_all()` returns
  `{}` on a brand-new session regardless of what's actually stored in the
  browser, until the component's JS reports back. `_COOKIE_SYNC_FLAG` in
  `st.session_state` gates a single `st.stop()` per session on that first
  empty result — a changed component return value always triggers exactly
  one automatic Streamlit rerun, so this waits for that rerun instead of
  racing it and incorrectly falling through to the login page. Gated to at
  most once per session (not called unconditionally on every empty result)
  so a genuinely cookie-less visitor still reaches the login page normally
  rather than getting stuck
- `ui_login.py`'s `render_login(cookie_manager)` sets the cookie via
  `cookie_manager.set(...)` on successful login, with a 24h rolling expiry —
  the CookieManager library always attaches an expiry (no true zero-expiry
  "clears only when the browser closes" option), so this is the closest
  practical approximation. Sign-out (`app.py`'s `render_sidebar`) deletes it,
  guarded against `KeyError` since `.delete()` raises if the cookie hasn't
  synced into the component's internal cache yet

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

- `APP_VERSION` — single string constant, rendered as the sidebar's top row
  (`app.py`'s `render_sidebar`)
- Bump on every release; no other source of truth for the app version

### Sidebar Version Block (`app.py`)

- `_sidebar_version_block(status)` — renders two info rows near the bottom of the sidebar
- Displays: Kea DHCP daemon version (from `get_status()`), API Mode (always "Direct API")
- Always rendered regardless of Kea connectivity — aids diagnosis when Kea is unreachable
- `_sidebar_item(label, value, show_divider=True)` — the top KeaNexus/version row
  and the Kea DHCP row both pass `show_divider=False` so only the last row in
  each cluster (Stats Source above, API Mode here) keeps a trailing divider line

### Helpers (`helpers.py`)

- `get_client()` — `@st.cache_resource` singleton KeaClient
- `load_leases/load_pool_stats/load_config/load_status` — `@st.cache_data` with TTL
- `fmt_ttl(seconds)` — formats seconds to "Xh Ym" or "expired"
- `html_safe_mac(mac)` — HTML-entity-encodes a MAC address's colons before it's
  interpolated into an `st.markdown(unsafe_allow_html=True)` string. Streamlit's
  markdown renderer expands `:xx:` shortcodes into emoji (Slack/GitHub-style),
  and a MAC's hex byte pairs can spell a two-letter ISO country code between
  colons — confirmed live: `d2:de:e1:...` rendered the `de` byte as a German
  flag in the IPAM table. Used at every MAC-address render site: `ui_ipam.py`,
  `ui_leases.py`, `ui_quarantine.py`, `ui_reservations.py`
- `build_reservation_type_sets(config)` / `lease_type(lease, ...)` — classifies a
  lease as fixed/reserved/name-only/dynamic by cross-referencing it against Kea
  reservations (moved here from `ui_leases.py` so it's shared and unit-testable).
  Purely descriptive — says nothing about whether the lease's hostname is real
- `build_hostname_override_sets(config)` / `real_hostname(lease, override_ips,
override_macs)` / `distinct_real_hostnames(leases, config)` — a Kea
  reservation's `hostname` field, when set to a **non-empty value**, is
  admin-typed free text that Kea echoes back on the live lease, discarding
  the device's actual DHCP-negotiated name. `ui_reservations.py` no longer
  writes that field to Kea at all (the admin label lives in
  `db.reservation_labels` instead — see below), but a reservation created
  before that change, or added to `kea-dhcp4.conf` by hand, can still carry
  the override. So real-vs-label is decided **per reservation** (does _this_
  reservation's config have a non-empty `hostname` value), not by lease type
  — a `fixed`/`reserved` lease with no override present is just as real as a
  `dynamic` one. **Checking mere key presence is not enough** — confirmed
  live via `config-get`, Kea always includes every reservation field,
  `hostname` included, defaulting to `""` when never set. An earlier version
  of this check used `"hostname" in r`, which treated every reservation as
  permanently overridden regardless of value, making the masked state
  unfixable by any resave since there was nothing left to strip.
  `distinct_real_hostnames` feeds the Quarantine tab's Add/Edit device
  hostname picker so it only offers hostnames that will actually match a
  live lease, rather than a reservation label that never will
- `lease_for_reservation(reservation, leases)` — the live lease matching a
  reservation, by MAC (falling back to IP if the reservation has no
  hw-address), or `None` if the device isn't currently leased. Used by the
  Reservations tab's Real Hostname column to tell "device offline" (no
  matching lease at all) apart from "hostname hidden by an unmigrated Kea
  override" (a lease exists, but `real_hostname` blanks it) — the fix differs
  between the two, so collapsing both to a bare dash would hide that

### Database (`db.py`)

- SQLite at `/app/data/keanexus.db`
- Table: `ipam_static` — static IP records (ip_address PK, hostname, mac_address, description, notes)
- Table: `reservation_labels` — KeaNexus-only display label for a Reservations-tab
  entry (mac_address PK, label), entirely decoupled from Kea's own config. See
  `helpers.py`'s hostname-override note above for why this exists
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
- `get_reservation_labels()`, `get_reservation_label(mac)`, `upsert_reservation_label(mac, label)`,
  `delete_reservation_label(mac)` — mac_address is always lowercased before storage/lookup
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
- Hostname column shows `helpers.real_hostname(lease, override_ips, override_macs)`
  — blank (`—`) only when the lease's matching reservation still carries a Kea
  `hostname` override, not based on lease type (see `helpers.py` above)
- Dialogs: `add_lease_dialog()`, `edit_lease_dialog(lease)`

### Reservations Tab (`ui_reservations.py`)

- `render_reservations(config, leases)` — CSS-grid header + `st.columns` data
  rows with per-row Edit buttons
- Reservations sent to Kea carry only `hw-address` (+ `ip-address` when fixed) —
  no `hostname` field. The "Label" column/input is a KeaNexus-only display name
  stored in `db.reservation_labels` (keyed by MAC), never written to Kea, so Kea
  always preserves the device's real DHCP-negotiated hostname on the lease —
  see `helpers.py`'s hostname-override note above
- "Real Hostname" column — `_real_hostname_cell(reservation, leases,
override_ips, override_macs)` shows what Kea actually sees the device
  broadcast, distinct from the admin-chosen Label. Four states, not two —
  `real_hostname()` returning `""` is ambiguous on its own (masked vs.
  genuinely empty), so this checks override membership directly rather than
  inferring it from an empty return value. **`offline`**: no matching lease
  at all (`lease_for_reservation` returned `None`). **`masked — resave to
fix`**: a lease exists and its matching reservation still carries a
  Kea-side `hostname` override; editing and saving that reservation clears
  it, same as the Label-migration path below. **`no hostname reported`**: a
  lease exists, no override is in effect, but the device simply didn't send
  a hostname on this lease (some clients, notably recent iOS, don't always
  broadcast one) — nothing to fix here. Otherwise, the real hostname: a
  lease exists, no override, and the device did report one
- `_labels_by_mac()` — `{mac_address: label}` from `db.reservation_labels`, built
  once per render and threaded through to the table and dialogs
- A reservation saved before this change (or hand-edited in `kea-dhcp4.conf`)
  can still carry Kea's own `hostname` field; `_render_table` falls back to that
  value only when no `reservation_labels` row exists yet for the MAC. Editing
  and saving that reservation migrates it automatically — `updated` never
  includes `hostname`, so it's stripped from Kea's config, and the label moves
  to `reservation_labels` in the same save
- `_sorted_reservations(reservations)` — fixed-IP entries first (by IP), then name-only
- Dialogs: `add_reservation_dialog(config)`, `_edit_dialog(reservation, config,
current_label)` (includes Delete). Deleting a reservation, or changing its MAC
  on save, also deletes/moves its `reservation_labels` row
- Each save writes immediately via `config-set` + `config-write`; no deferred "Save" step

### Quarantine Tab (`ui_quarantine.py`)

- `render_quarantine(leases, config)` — device registry table with per-device
  Quarantine/Release buttons, plus a read-only quarantine audit log. Takes
  `leases`/`config` (passed down from `app.py`) purely to populate the
  Add/Edit dialog's hostname picker
- Manages `device_registry` directly (add/edit/delete via `_edit_dialog`), and
  triggers enforcement via `quarantine_client.py`, which calls the
  `keanexus-quarantine` service's own API — same endpoint a Siri Shortcut would call
- `_edit_dialog(friendly_name, leases, config)` — add/edit/delete a registry entry;
  friendly*name is immutable once created (delete and re-add instead of renaming).
  Hostname is a dropdown built from `helpers.distinct_real_hostnames(leases, config)`
  rather than free text — picking from hostnames Kea has actually observed on a
  live lease avoids registering a device with a Kea reservation \_label*
  (see `helpers.py` above), which would never match and would make
  `quarantine_service/identity.py`'s `resolve_target` fail with
  `DeviceNotOnNetworkError` even though the device is online. Falls back to a
  free-text input, with an explanatory caption, only when no real hostnames are
  currently observed in leases at all
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
    - `POST /presence-check/{friendly_name}` — immediately ARP-probes one registered
      device via `presence_check.probe_device_now`, bypassing that module's 5-minute
      background loop. Called by `ui_quarantine.py` right after a device is added or
      edited, so Last MAC/Last IP/Last Seen don't sit blank until the loop's next pass.
      Returns `{"friendly_name": ..., "seen": bool}`
    - All four require `Authorization: Bearer <token>` via `require_bearer_token`
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
  hostname (`KeaClient.get_leases_by_hostname`) to get the live MAC/IP.
  `_matching_registry_entries` matches case-insensitively — `target` typically
  arrives dictated via a Siri Shortcut, which capitalizes the first letter of
  whatever was said regardless of how the device was actually registered. The
  exact match is tried first (a fast, indexed lookup for the common case where
  casing already matches); a case-insensitive scan over `get_devices()` only
  runs as a fallback. Raises `DeviceNotRegisteredError` (mapped to HTTP 404).
  For a single-device target,
  raises `DeviceNotOnNetworkError` (mapped to HTTP 409) if it has no current
  lease. For a group target, a device with no current lease is skipped rather
  than aborting the whole group — `DeviceNotOnNetworkError` is only raised if
  none of the group's devices are currently on the network. Refreshes
  `last_seen_mac_address` / `last_seen_ip_address` in `device_registry` on every
  successful resolution via `upsert_device`.
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
- `presence_check.py` — independent of the quarantine/release enforcement flow
  entirely (`identity.py` only refreshes last-seen breadcrumbs when a device is
  actually quarantined or released, which for most registered devices never
  happens). `start_presence_check_loop()` runs a daemon thread
  (`PRESENCE_CHECK_INTERVAL_SECONDS`, default 300s) that calls `_run_one_pass()`
  every interval, which probes every `device_registry` row via
  `probe_device_now(friendly_name, kea=...)`: looks up the hostname's current
  Kea lease, sends a real ARP "who-has" request (scapy `srp` — send **and**
  receive, unlike `arp_disrupt.py`'s fire-and-forget `sendp`), and if anything
  answers, calls `db.touch_last_seen` to stamp `last_seen_mac_address`/
  `last_seen_ip_address`/`last_seen_at`. A device with no current lease is
  skipped outright. `probe_device_now` also backs `main.py`'s
  `POST /presence-check/{friendly_name}` for an on-demand probe outside the
  loop's interval — see below
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
- `POST /presence-check/{friendly_name}` — immediately ARP-probes one registered
  device and stamps Last MAC/Last IP/Last Seen if it answers, bypassing the
  background presence-check loop's 5-minute interval. Returns
  `{"friendly_name": ..., "seen": bool}`.

## Architecture Decisions

**Direct Kea HTTP listener (not Control Agent)** — Kea 3.0 deprecated and removed the Control Agent. `kea-dhcp4` now exposes its own HTTP listener on port 8004. KeaNexus talks directly to it; the `"service"` forwarding key is never sent.

**CSS conic-gradient gauge (not SVG)** — Pool utilisation shown as a donut ring using `conic-gradient`. Simpler than SVG with equivalent visual quality; no JS required.

**SQLite for IPAM static records** — Local SQLite in a named Docker volume. Static IP records are KeaNexus-managed metadata (not sent to Kea); they exist purely for documentation/visibility of non-DHCP addresses.

**HTML tables (not st.dataframe)** — All data tables rendered as raw HTML to allow custom chip/badge formatting. Streamlit's native components don't support per-cell HTML.

**All times UTC internally** — Kea lease expiry timestamps are Unix epoch. Display formatting converts to human-readable durations via `fmt_ttl()`. `quarantine_log.occurred_at` is likewise stamped as UTC ISO8601 in `db.py`, not left to the caller.

**Reservation labels decoupled from Kea's `hostname` field** — Kea's own docs confirm host-level reservation values (including `hostname`) always take priority: once a reservation sets `hostname`, Kea echoes that admin-typed string back on every lease for the device, permanently discarding whatever hostname the client itself requests. That made `device_registry` entries built from a reservation's hostname unable to ever match a live lease during quarantine identity resolution (`resolve_target` would raise `DeviceNotOnNetworkError` for a device that was clearly online), and made the Leases tab show a made-up label instead of the device's real name. Reservations created or edited via `ui_reservations.py` no longer send `hostname` to Kea at all — the admin-facing label lives in `db.reservation_labels` instead, keyed by MAC, so Kea always preserves the real DHCP-negotiated hostname on the lease. The tradeoff: a reservation's hostname can no longer drive Kea's own DDNS registration for that device, since Kea has nothing to register.

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

| File                                        | What is tested                                                                                                                                                                                                                                                           |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `tests/test_auth.py`                        | `is_authenticated`, `attempt_login` (all credential paths), `logout`, `session_token`, `restore_session_from_cookie`                                                                                                                                                     |
| `tests/test_db.py`                          | Full CRUD on `ipam_static`, `reservation_labels`, `device_registry`, and `quarantine_log` via `temp_db` fixture (SQLite in tmp dir)                                                                                                                                      |
| `tests/test_kea.py`                         | All `KeaClient` methods; `httpx` patched via `http_mock` fixture                                                                                                                                                                                                         |
| `tests/test_helpers.py`                     | `fmt_ttl`, `chip`, `leases_to_df`, `build_reservation_type_sets`, `lease_type`, `build_hostname_override_sets`, `real_hostname`, `distinct_real_hostnames`, `lease_for_reservation` (pure functions only)                                                                |
| `tests/test_pihole.py`                      | `PiholeClient` session auth (caching, reuse, re-auth on expiry), CSRF header logic, error mapping, constructor overrides (base_url/password); `httpx` patched via `pihole_http_mock` fixture                                                                             |
| `tests/test_quarantine_client.py`           | `trigger_quarantine`/`trigger_release` — request shape, auth header, is_group passthrough, missing-token error, connect error, HTTP error detail parsing; `httpx` patched via `quarantine_client_http_mock` fixture                                                      |
| `tests/test_quarantine_auth.py`             | `require_bearer_token` — missing config, missing/malformed header, wrong token, success                                                                                                                                                                                  |
| `tests/test_quarantine_identity.py`         | `resolve_target` — single device and group resolution, case-insensitive friendly_name/group_tag matching, unregistered target, no-lease case, last-seen refresh; `verify_identity_unchanged` — match, IP drift, MAC drift, lease gone — using `StubKeaClient`            |
| `tests/test_quarantine_kea_deny.py`         | `apply_drop_class` / `remove_drop_class` (pure config mutation), `deny_via_kea` / `undo_deny_via_kea` (via `StubKeaClient`)                                                                                                                                              |
| `tests/test_quarantine_arp_disrupt.py`      | `send_poisoned_arp_reply` packet construction, `start_arp_disruption` / `stop_arp_disruption` loop lifecycle, idempotent restart, survives a failed send, no packets after stop — `sendp` always patched                                                                 |
| `tests/test_quarantine_pihole_block.py`     | `block_via_pihole` / `unblock_via_pihole` — group/regex creation vs reuse, unrelated groups ignored, client PUT/DELETE calls, using `StubPiholeClient`                                                                                                                   |
| `tests/test_quarantine_nmap_fingerprint.py` | `refresh_os_fingerprint` — persists top-accuracy match, preserves other registry fields, inconclusive scan doesn't overwrite existing fingerprint, subprocess failure propagates; `subprocess.run` always patched                                                        |
| `tests/test_quarantine_retry.py`            | `run_with_retries` — first-attempt success, success-after-failures, exhausted-retries, custom `max_attempts`, one-row-per-step logging                                                                                                                                   |
| `tests/test_quarantine_main.py`             | FastAPI routes via `TestClient` — auth enforcement, 404/409 error mapping, all four enforcement steps wired correctly, group requests, pre-fire safety check skips a device whose identity drifted between resolution and enforcement, `/presence-check/{friendly_name}` |
| `tests/test_quarantine_presence_check.py`   | `probe_device_now` — unregistered device, no current lease, Kea unreachable, no ARP reply, ARP reply stamps last-seen fields, default-constructed `KeaClient`; `_run_one_pass` probes every registered device                                                            |

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
