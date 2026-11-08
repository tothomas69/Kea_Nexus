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
├── version.py          — APP_VERSION constant (single source of truth)
├── auth.py             — Authentication and user management: argon2 login, roles, signed session token
├── kea.py              — Kea direct HTTP API client (Kea 3.0+)
├── pihole.py           — Pi-hole v6 REST API client (session auth); used by quarantine_service
├── quarantine_client.py — Thin client KeaNexus uses to call keanexus-quarantine's own API
├── helpers.py          — Pure lease/reservation helpers shared by the web UI and the quarantine service
├── dashboard_data.py   — Pure metric shaping behind the Overview's figures and charts
├── dashboard_charts.py — Altair chart builders for the Overview
├── address_map.py      — Pure /24 classifier behind the Address Map
├── docs_library.py     — Allowlist + reader for the operator docs shown under Help
├── pool_history.py     — Background sampler building the pool utilisation time series
├── db.py               — SQLite persistence layer (static records, labels, registry, logs, users)
├── Makefile            — Developer setup (`make setup`) and test runner (`make test`)
├── static/             — Brand assets (logos, the orb favicon set), served at /brand
├── web/                — The KeaNexus UI: FastAPI + HTMX (routes/, templates/, static/)
├── quarantine_service/ — Optional keanexus-quarantine FastAPI service (compose profile "quarantine")
├── docs/               — PRD, this guide, design docs (incl. the FastAPI + HTMX migration record)
└── .githooks/          — Committed git hooks (pre-commit, pre-push); activated per-clone with `make setup`
```

Data persisted in Docker named volume `keanexus_data` mounted at `/app/data/`.

## Systems

### Authentication (`auth.py`)

- **Multi-user, two roles.** Users live in `db.users` with role `admin` or
  `viewer`. Passwords are stored only as argon2 hashes (`argon2-cffi`);
  `auth.py` is the only module that ever handles a plaintext password, and
  `db.py` only stores and returns hashes
- **First admin from the old env-var login.** `bootstrap_admin_from_env()`
  runs at startup (the web app's lifespan, right after `init_db()`). When the users table is
  empty it creates an admin from `KEANEXUS_USERNAME` / `KEANEXUS_PASSWORD`, so
  an existing deployment keeps working on upgrade with no manual database step.
  A no-op once any user exists, so changing those env vars later does nothing.
  The minimum password length is deliberately not enforced on this one path:
  the env-var password predates the rule, and refusing it would lock the owner
  out of their own upgrade
- **No UI framework and no session state in `auth.py`.** It holds the rules and
  the token logic; `web/deps.py` and `web/security.py` keep the session in a
  signed cookie
- `check_credentials(username, password)` — returns the stored user row or
  `None`, and records the login. Username lookup is case-insensitive. Unknown
  and disabled users return the same `None` as a wrong password, after the same
  hashing work (a lazily built decoy hash), so login timing can't reveal which
  usernames exist
- `session_user_if_valid(username, session_version)` — the per-request check:
  the stored user row, or `None` if the user is gone, disabled, or their
  `session_version` has moved on (password, role or disable change). So
  revoking someone takes effect at their next click, not their next login.
  `public_user(row)` strips a row down to `{"username", "role"}`
- **Signed session token.** `sign_session(username, session_version)` returns
  `username|session_version|signature`, signed with HMAC-SHA256 under
  `KEANEXUS_SECRET` (or `""` with no secret). `user_from_session_token(token)`
  rejects anything malformed, wrongly signed, or carrying an old
  `session_version`. It is the web UI's only session store: stateless, with no
  server-side session table. The previous design derived the
  cookie from the single shared password, which could not tell users apart or
  revoke one person; the signed username plus version replaces it. Usernames
  are restricted to letters, digits, `.`, `_` and `-`, which keeps the `|`
  separator out of them so the cookie splits unambiguously
- **`KEANEXUS_SECRET` unset:** `sign_session()` returns `""`, and the web UI,
  having no other session store, refuses sign-in with a message saying to set
  the secret. Changing the secret signs everyone out
- **User-management rules live here, not in the UI**: `create_user`,
  `reset_password`, `change_role`, `set_disabled`. They raise
  `UserManagementError` (a `ValueError` whose message is safe to show) for bad
  usernames, passwords under `MIN_PASSWORD_LENGTH` (12), unknown roles,
  duplicates, demoting or disabling the last active admin, and an admin
  disabling their own account. The Users panel (`web/routes/settings.py`)
  only shows their messages
### Kea Client (`kea.py`)

- `KeaClient` — synchronous HTTP client talking directly to the `kea-dhcp4` HTTP listener (Kea 3.0+)
- Connection configured via `KEA_API_URL`, `KEA_API_USER`, `KEA_API_PASSWORD` env vars
- The `"service"` key is never sent — direct listeners don't support forwarding and reject it
- Key methods: `get_leases()`, `get_pool_stats()`, `get_config()`, `save_config()`, `get_status()`, `get_pool_range(config)`
- `get_dhcp_enabled()` — `True`/`False` from `status-get`'s `dhcp-state.globally-disabled`, or `None` when Kea doesn't report it. `status-get` is in `READ_ONLY_COMMANDS`
- `get_status()` returns both `"ca"` and `"dhcp4"` keys for UI compatibility; `"ca"` is a static placeholder (`up=True`, `version="n/a"`) since the Control Agent no longer exists in Kea 3.0+
- `ReadOnlyKeaClient` — a `KeaClient` whose `call()` refuses any command outside
  `READ_ONLY_COMMANDS` with `KeaReadOnlyError`, before a request is sent. Handed to
  viewer sessions by `web/deps.py`'s `kea_client`. An allowlist on purpose: a write
  method added to `KeaClient` later is refused for viewers by default.
  `KeaReadOnlyError` subclasses `KeaError`, so every existing UI `except KeaError`
  shows the refusal as an ordinary error

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
- `trigger_liveness_sweep(ip_addresses)` — called by the web UI's sweep (button and
  background loop). Returns `{ip: mac}` for the addresses that answered an ARP probe;
  the MAC is `""` if the service is older and only sends `responding`. Short-circuits to `[]` without a request when given no addresses. Its own
  30s timeout (`_LIVENESS_SWEEP_TIMEOUT_SECONDS`) — a sweep is one batched send plus
  a single server-side wait regardless of address count, so it needs nothing like
  enforcement's 180s
- `_post(path, json_body, timeout_seconds)` — the one place the bearer token, base
  URL, unreachable-service error and HTTP-error detail parse live. Every public
  function here goes through it; there is deliberately no second request path
- Raises `QuarantineServiceError` uniformly for unreachable service, missing token
  config, or a non-2xx response (parses the FastAPI `detail` field out of the error
  body when present)

### Version (`version.py`)

- `APP_VERSION` — single string constant (2.0.0, the FastAPI + HTMX release),
  given to every template as `app_version` by `web/templating.py`. Shown as
  `v2.0.0` under the masthead logo and in the Help header
- Bump on every release; no other source of truth for the app version

### Helpers (`helpers.py`)

- Plain functions, shared by the web UI and the quarantine service
- `pool_stats_with_fallback(client)` — `stat-lease4-summary`, or counters
  worked out from the lease list and pool range when Kea lacks the `stat_cmds`
  hook (`cumulative` is 0 then); `None` if Kea can't be read at all
- `fmt_ttl(seconds)` — formats seconds to "Xh Ym" or "expired"
- `build_reservation_type_sets(config)` / `lease_type(lease, ...)` — classifies a
  lease as fixed/reserved/name-only/dynamic by cross-referencing it against Kea
  reservations.
  Purely descriptive — says nothing about whether the lease's hostname is real
- `build_hostname_override_sets(config)` / `real_hostname(lease, override_ips,
override_macs)` / `distinct_real_hostnames(leases, config)` — a Kea
  reservation's `hostname` field, when set to a **non-empty value**, is
  admin-typed free text that Kea echoes back on the live lease, discarding
  the device's actual DHCP-negotiated name. KeaNexus no longer
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
  `distinct_real_hostnames` feeds the Quarantine screen's Add/Edit device
  hostname picker so it only offers hostnames that will actually match a
  live lease, rather than a reservation label that never will
- `with_reservation_saved(config, original_mac, mac, ip)` /
  `with_reservation_removed(config, mac)` / `is_quarantined_reservation(r)` —
  reservation edits that return a copy, changing only MAC and address and
  keeping every other field (the quarantine service's DROP class above all).
  Kea's own `hostname` is always dropped. Callers pass a config read fresh
  from Kea
- `lease_for_reservation(reservation, leases)` — the live lease matching a
  reservation, by MAC (falling back to IP if the reservation has no
  hw-address), or `None` if the device isn't currently leased. Used by the
  Reservations tab's Real Hostname column to tell "device offline" (no
  matching lease at all) apart from "hostname hidden by an unmigrated Kea
  override" (a lease exists, but `real_hostname` blanks it) — the fix differs
  between the two, so collapsing both to a bare dash would hide that

### Pool History Sampler (`pool_history.py`)

- Kea reports only the present — `stat-lease4-summary` answers "how full is the
  pool right now" and keeps no history — so any utilisation trend has to be
  accumulated from readings taken over time. This module is the only writer of
  `db.pool_samples`
- `start_pool_sampler()` starts one daemon thread per process, idempotent
  (a second loop would double every sample). The web app's lifespan starts it
  at container start. `stop_pool_sampler()` and `is_sampling()` exist for tests;
  production never stops the loop, the daemon thread dies with the process
- `take_sample_now(kea=None)` takes and stores one reading; returns False rather
  than raising when Kea is unreachable, since that is an ordinary recoverable
  condition for a sampler and the series should resume on its own
- `_run_sampler_loop` catches every exception per pass — a sampler that dies on
  one bad pass stops producing history silently, and nothing notices until
  someone opens the chart and finds it frozen
- Runs for the life of the process whether or not anyone is watching, which is
  why it is a background thread rather than a sample taken during page render.
  Gaps still happen (container down, Kea down); consumers treat them as gaps
  and never interpolate across them
- Configured via `POOL_SAMPLE_INTERVAL_SECONDS` (default 300, floored at 1) and
  `POOL_SAMPLE_RETENTION_DAYS` (default 30); each pass prunes older rows

### In-App Docs (`docs_library.py`)

- Help in the masthead opens a table of contents built from each article's
  own `## ` headings (`web/help_content.py`); an article opens in the Help modal
- `docs_library.ARTICLES` is the allowlist: currently Siri Shortcut Setup and
  Quarantine Design. Both were brought up to date for 2.0.0 (screens, not
  Streamlit tabs). The README describes 2.0.0 too. The as-built guide and PRD are developer notes and stay out
  of the app deliberately
- **`read_article` refuses anything not in `ARTICLES`, before any path is
  built.** The filename arrives from a URL, so joining it onto the docs
  directory would be a path-traversal hole in a page one login away from the
  internet. Covered by tests for `../.env`, absolute paths and unlisted docs
- `available_articles()` hides an article whose file is missing, so a deployment
  that missed the `./docs` bind-mount offers nothing rather than entries that
  fail on click

### Database (`db.py`)

- SQLite at `/app/data/keanexus.db`
- Table: `ipam_static` — static IP records (ip_address PK, hostname, mac_address, description, notes)
- Table: `reservation_labels` — KeaNexus-only display label for a Reservations-tab
  entry (mac_address PK, label), entirely decoupled from Kea's own config. See
  `helpers.py`'s hostname-override note above for why this exists
- Table: `device_registry` — quarantine-feature identity registry (friendly_name PK,
  hostname, group_tag, os_fingerprint, last_seen_mac_address, last_seen_ip_address,
  last_quarantined_at, is_quarantined, notes). Keyed on friendly_name rather than
  MAC — MAC can be changed by the device, hostname/fingerprint can't. See
  `docs/quarantine-feature-design.md` for the full rationale.
  `is_quarantined` is a separate boolean from `last_quarantined_at` on purpose:
  the timestamp is deliberately never cleared on release (it means "last
  quarantined," not "last touched" — see `main.py`'s `_stamp_quarantine_state`
  below), so it can't answer "is this device quarantined right now." Set from
  the enforcement action actually taken (`True` on quarantine, `False` on
  release), not gated on individual step success — a partial failure is
  already visible in that action's own step-result checkmarks.
- Table: `pool_samples` — time series of Kea's pool counters (sample_id PK
  autoincrement, sampled_at, total, assigned, declined, available, cumulative),
  written only by `pool_history.py`. Indexed on `sampled_at`, since every read is
  "the samples since <time>" and an unindexed 30-day window scans the whole table
- Table: `quarantine_log` — append-only audit trail (log_id PK autoincrement,
  friendly_name, action, step, succeeded, attempt_count, detail, occurred_at).
  Written by the `keanexus-quarantine` service (see below) on every enforcement
  step attempt.
- Table: `activity_log` — who changed what through the web UI, one plain-words
  line per change (log_id PK autoincrement, occurred_at UTC, username,
  summary up to 300 characters), indexed on `occurred_at`. Written by every web
  write route after the change succeeded; refused changes are not logged.
  Quarantine and release are not written here, since `quarantine_log` already
  has them step by step. `insert_activity(username, summary)`,
  `get_activity(limit)` (newest first)
- Table: `users` — app login accounts (username PK `COLLATE NOCASE`, password_hash,
  role `admin`/`viewer` enforced by a CHECK, session_version, is_disabled,
  created_at, last_login_at, compact_header). Usernames compare case-insensitively so "Tom" and
  "tom" can't become two accounts. `session_version` is the logout lever: every
  password, role or disable change bumps it, which invalidates that user's open
  sessions and cookies (see `auth.py`). Stores hashes only, never passwords
- `init_db()` — idempotent schema creation, called at app startup
- `get_static_entries()`, `get_static_entry(ip)`, `upsert_static_entry(...)`, `delete_static_entry(ip)`
- `get_reservation_labels()`, `get_reservation_label(mac)`, `upsert_reservation_label(mac, label)`,
  `delete_reservation_label(mac)` — mac_address is always lowercased before storage/lookup
- `get_devices()`, `get_device(name)`, `upsert_device(...)`, `delete_device(name)`
- `insert_quarantine_log_entry(...)`, `get_quarantine_log(friendly_name=None, limit=100)`
- `insert_pool_sample(...)`, `get_pool_samples(since=None, limit=10000)` (oldest-first, since every caller plots on a time axis), `prune_pool_samples(retention_days)`
- `count_users()`, `count_active_admins()`, `get_user(username)`, `list_users()` (never returns hashes),
  `insert_user(...)`, `update_password_hash(...)`, `set_user_role(...)`, `set_user_disabled(...)`,
  `record_login(username)`, `set_compact_header(username, compact)` (a display
  preference: it deliberately does not bump `session_version`). The three updates each bump `session_version`; each query is a
  literal string, never assembled, so Bandit has nothing to flag. `USER_ROLES` is the role list

### Dashboard Data (`dashboard_data.py`)

Pure shaping for every number the Dashboard shows — plain data in, plain data
out, no web framework, Kea or SQLite, so every rule is unit-tested. 100% covered by `tests/test_dashboard_data.py`.

- `lease_expiry_buckets(leases, now_epoch)` — counts active leases into the fixed
  `EXPIRY_BUCKETS` (`< 1h`, `1–6h`, `6–12h`, `12–24h`, `> 24h`). The 12h split is
  deliberate: a DHCP client renews at half its lease time, so with a common 24h
  lease almost every healthy device lands in the 6–24h band — leaving it whole put
  most of the network in one bar and said nothing about renewal timing. Declined leases excluded
  (they hold an address but aren't a device on a renewal timer); already-expired
  leases fall into the first bucket rather than being dropped, since a lease Kea
  hasn't reclaimed is exactly what's worth seeing. Empty buckets are still
  returned so the chart's axis doesn't reshape as data changes
- `lease_composition(leases, config)` — counts by reservation type in fixed
  `LEASE_TYPES` order, reusing `helpers.lease_type` so the Dashboard can never
  drift from the Leases/IPAM tabs
- `utilisation_points(samples, gap_seconds)` — pool samples to plottable points,
  **inserting a None-valued point wherever consecutive samples are further apart
  than `gap_seconds`** so Vega-Lite breaks the line. Drawing through a gap would
  assert a measurement never taken — an overnight outage would read as steady
  utilisation
- `leases_issued_since(samples, window_hours, now, baseline_tolerance_seconds=3600)`
  — new leases handed out, from the monotonic `cumulative-assigned-addresses`
  counter. **Deliberately not computed from lease data**: a lease's `cltt` is the
  client's _last_ transmission, rewritten on every renewal, so a device that
  renews hourly would look brand new forever. Returns `None` rather than a
  misleading figure in two cases — no sample old enough to measure against, and a
  baseline sitting further before the cutoff than the tolerance allows (with a
  real sampling gap the newest sample at-or-before the cutoff can be hours stale,
  and reporting ten hours of leases as "the last hour" would be worse than
  reporting nothing). A negative delta means Kea restarted and reset the counter;
  that also returns `None`
- `quarantined_count(devices)` — counts `is_quarantined`, never
  `last_quarantined_at`, which is never cleared on release

### Address Map (`address_map.py`)

- **Pure and shared.** The per-address classification that used to live inside
  old IPAM tab, moved out unchanged and unit-tested; the Address Map, search and
  static-entry routes all classify addresses through it
- `classify_addresses(prefix, pool_start_octet, pool_end_octet, gateway, leases,
  reservations, static_entries, labels, override_ips, override_macs, now_epoch)` —
  one `AddressRow` per address .0–.255: network, broadcast, gateway, leased,
  declined, reserved, static, scope (free in pool) or free. `now_epoch` is passed
  in so lease time-left is testable
- Status priority: network/broadcast > gateway > declined lease > active lease with fixed reservation (shows as reserved) > active lease > reservation > static DB entry > in-scope free > free
- `subnet_prefix(config)`, `gateway_ip(config)`, `subnet_reservations(config)`,
  `last_octet(ip)` — the config lookups both UIs need

### Web UI (`web/`) — FastAPI + HTMX

The only UI since the cutover (phase 7). The plan and the record of each phase
are in `docs/fastapi-htmx-migration.md`. Runs as the `keanexus` compose service
(`uvicorn web.main:app` on 8000, host port **8502**, the port the Streamlit UI
used, so Nginx Proxy Manager's upstream did not change). Phase 0 is the groundwork: sign
in/out, the page layout, Help and the health check. Phase 1 is the real
Overview, and 1b adds its charts. Phase 2 is the Quarantine screen, phase 3 the Leases screen and the background liveness sweep, phase 4 Reservations, phase 5 Maintenance, phase 6 Settings and Users. Every screen is now built; phase 7 is the cutover.

- `main.py` — `create_app(start_background_work, start_liveness_sweep)`. The
  lifespan runs `init_db()`, `bootstrap_admin_from_env()` and, when allowed, the
  pool sampler and the liveness sweep loop, all at container start. The sweep
  has its own switch (`KEANEXUS_WEB_LIVENESS_SWEEP`, default on) and only
  starts when `QUARANTINE_SERVICE_URL` is set. `KEANEXUS_WEB_BACKGROUND_WORK=off`
  stops the pool sampler; it was set during the overlap with Streamlit, which
  ran its own, and is unset since the cutover. Mounts
  `web/static` at `/static` and the repo's `static/` (logos) at `/brand`. No
  OpenAPI or docs pages
- `security.py` — the session cookie (`keanexus_web_session`: HttpOnly,
  SameSite=Lax, Secure when the request came over HTTPS, 24h) holding
  `auth.sign_session()`'s token, and **CSRF**: a double-submit token in its own
  cookie that every POST/PUT/DELETE/PATCH must echo in `X-CSRF-Token`.
  `require_csrf` is an app-wide dependency, so no route can forget it.
  `base.html` puts the header on every HTMX request via `hx-headers` on `<body>`
- `deps.py` — `current_user` (re-checks the token against the database on every
  request; raises `NotSignedIn`, which `main.py` turns into a redirect to
  `/login`, or `HX-Redirect` for HTMX), `require_admin` (replaces
  `refuse_if_viewer()`; every write route will depend on it) and `kea_client`
  (`ReadOnlyKeaClient` for viewers)
- `templating.py` — `render()` gives every template the CSRF token, nav and
  version and sets the CSRF cookie on first visit; `redirect()` sends
  `HX-Redirect` to HTMX (which would otherwise swap a whole page into a
  fragment) and a 303 to everything else. `NAV_ITEMS` marks unbuilt screens
  with a `None` path
- `help_content.py` — Help's table of contents is built from each allowlisted
  article's own `## ` headings (skipping code blocks), with anchors from the
  same `slugify` the markdown `toc` extension uses, so links can't drift from
  the docs. Articles render with `markdown` (`fenced_code`, `tables`, `toc`);
  only `docs_library`'s allowlist is ever read
- **Overview (`routes/overview.py`, `overview_data.py`, `static/overview.js`).**
  Key figures (pool use with a green/amber/red bar, active leases and new
  leases in 24 h, reservations, quarantined), the Address Map, the Quarantine
  card and Activity. All the shaping is in the pure `overview_data.py`
  (`pool_figures`, `map_cells`, `devices_by_ip`, `quarantine_rows`, `activity`);
  the route only fetches and renders
    - **Address Map:** 256 server-rendered squares plus every cell's details as
      one JSON blob (`#address-cells`), so the Alpine component in
      `overview.js` shows the hover card without a single request. Clicking
      pins the card, which then offers **Edit** (static entry, for addresses
      that can hold one) and **Quarantine/Release** (when a registered device
      sits there) to admins. Two display-only statuses sit on top of
      `address_map`: **quarantined** (a registered device with
      `is_quarantined`) and **unknown** (a dynamic lease with no hostname, no
      label and no registered device). The card's **On the wire** row shows the
      latest sweep: the MAC that answered and how long ago, "No answer", or
      "Not checked yet". When the answering MAC differs from the one Kea or the
      static record holds, the card warns that a different device answered.
      `map_cells(rows, devices, seen_macs, seen_age)` adds `seen`,
      `seen_mac`, `seen_age` and `seen_mismatch` to each cell Devices are placed by the hostname on
      their live lease first, the way the quarantine service finds them, and by
      `last_seen_ip_address` otherwise, since a quarantined device holds no lease
    - **Live redraws:** the map and Activity re-fetch every 30 s and on the
      `overview-changed` event, which every write route sends in `HX-Trigger`,
      so an action shows up at once. The Quarantine card polls every 30 s only:
      its own POST already returns the fresh card with the result notice
    - **Activity** folds `quarantine_log` (one row per step) into one line per
      Quarantine/Release, counting only the enforcement steps (Kea, ARP, both
      Pi-holes). Like `_enforcement_succeeded`, an inconclusive nmap refresh is
      not a problem. Rows for one action within 5 minutes are one line. Every
      other change comes from `activity_log` as "<user> <summary>" in blue,
      and the two are merged by timestamp
    - **Static entries** moved here from the IPAM tab: `GET /address/{ip}/static`
      (form into the page dialog), `POST` (save; MAC must look like
      `aa:bb:cc:dd:ee:ff`), `DELETE`. Admin-only, and refused (404) for any
      address `address_map` doesn't mark editable, i.e. inside the pool
    - **Quarantine/Release:** `POST /quarantine/{name}` and
      `POST /quarantine/{name}/release`, admin-only, 404 for an unregistered
      name. A `QuarantineServiceError` becomes a red notice on the card, never
      a 500
    - **Charts (1b):** the Dashboard's three charts, built by
      `dashboard_charts.py` and shipped as Vega-Lite JSON in
      `overview/_charts.html`; `overview.js`'s `drawCharts()` renders them as
      SVG with vega-embed at the card's width. Each has a "Show the numbers"
      table. They draw once on page load and are not re-polled: a 48 h trend
      gains nothing from a 30 s refresh
- **Quarantine (`routes/quarantine.py`, `quarantine_data.py`).** The registry
  table (quarantined rows in red with a chip), per-device and per-group
  Quarantine/Release, Add/Edit/Delete in the page dialog, the step explainer,
  the manual cleanup commands, and the audit log (50 newest rows). Rules and
  shaping live in the pure `quarantine_data.py`: `device_rows`, `group_tags`,
  `log_rows`, `step_outcomes`, `enforcement_succeeded` (same rule as the
  service: fingerprint doesn't count), `validate_device` and `ReleaseAttempts`
    - **Paths are `/devices/...` and `/groups/...`, not `/quarantine/...`.** The
      Overview already owns `POST /quarantine/{name}`, so a device called
      "devices" or a group called "release" would have collided with a sibling
      route. `new` is a reserved friendly name, since `GET /devices/new` is the
      Add form
    - **Delete releases first.** The registry row is the only way back to a
      device's Kea DROP class, Pi-hole overrides and ARP thread, so deleting a
      quarantined device runs a release and keeps the row unless every
      enforcement step came off. After `MAX_RELEASE_ATTEMPTS_BEFORE_FORCE` (3)
      failures the form offers a forced delete behind a checkbox; the route
      ignores `force=true` before then. Failures are counted in memory per
      process (`release_attempts`), shared by every admin
    - **Hostname picker** offers only hostnames on live leases
      (`distinct_real_hostnames`), plus the entry's own if it has gone quiet,
      and falls back to free text when Kea reports none. A save carries over
      every column the service owns (fingerprint, last-seen, quarantine state),
      then runs `trigger_presence_check` best effort
    - **Live updates:** an action returns the registry with the outcome (the
      service's summary plus a ✓/✗ per step per device) and sends
      `quarantine-changed`, which redraws the audit log. Dialog saves and
      deletes send `registry-changed, quarantine-changed, close-dialog`
    - **htmx gotcha:** `hx-swap` is inherited. A button inside an
      `hx-swap="outerHTML"` section that loads a form into `#dialog-body` must
      set `hx-swap="innerHTML"` itself, or it replaces `#dialog-body` and every
      later dialog has nowhere to load
- **Leases (`routes/leases.py`, `leases_data.py`).** Every lease sorted by
  address (hostname via `real_hostname`, so a reservation label never shows as
  the device's name), type chip, expiry, and an Online column. A live filter
  (`?q=`, 250 ms debounce) matches IP, hostname or MAC. Admins add or edit a
  lease in the page dialog (`lease4-add` / `lease4-update`); the form checks the
  address is in the subnet, the MAC's shape, and a duration of 60 s to one week
  before anything reaches Kea, and shows Kea's own refusal if it says no. A save
  forgets the cached leases and pool stats and sends `leases-changed`
    - **Online column:** `—` before any sweep, `yes` (green row) or `no answer`
      after one. Never "offline": an ARP sweep only proves presence. The caption
      says how many answered and how long ago
    - **Check who's online** (`POST /leases/sweep`) is open to viewers, as in
      Streamlit, since it changes nothing. It sweeps the whole subnet, not the
      filtered leases, so the result stays valid as the filter changes and the
      Address Map gets it too. A Kea failure is shown as a notice. A
      `QuarantineServiceError` becomes a notice above the table
- **Reservations (`routes/reservations.py`, `reservations_data.py`).** Kea's
  host reservations with KeaNexus labels, fixed first by address then
  name-only. Hostname shows four states: the name, `no lease`, `none sent`, or
  `hidden, save to fix` (an old Kea `hostname` override). An **Online** column
  reads the latest ARP sweep (`liveness.latest()`): `yes` (green row), `no
  answer`, or `—` before any sweep. A fixed reservation is checked at its
  reserved address, a name-only one at its live lease's address
  (`reservation_rows(config, leases, labels, responding)`). A quarantined
  device's row is red with a chip. Reservations Kea keys on something other
  than a MAC are listed but not editable. Admins add, edit and delete in the
  page dialog
    - **Writes read Kea's config fresh**, never from the cache, because the
      quarantine service edits the same config; saving a copy up to two minutes
      old would undo its change
    - **An edit keeps every field the form doesn't show**
      (`helpers.with_reservation_saved`, shared with the old Streamlit fix, copies
      the entry and changes only MAC and address). Kea's own `hostname` is always dropped, migrating a legacy
      label. The Streamlit tab rebuilt the entry from the form, which dropped
      the quarantine service's `client-classes: ["DROP"]` and silently put a
      quarantined device back on DHCP
    - **Delete refuses a quarantined device** (one with `DROP`): release it on
      the Quarantine screen first, so the service stays the only thing that
      adds or removes its DROP class
    - Labels are written only after Kea accepts the change (`config-set` +
      `config-write`), and move with the MAC when it changes
- **Maintenance (`routes/maintenance.py`, `maintenance_data.py`).** DHCP on/off,
  declined leases, and wiping the lease database; every action admin-only
    - **DHCP state comes from Kea**, via `KeaClient.get_dhcp_enabled()`
      (`status-get`'s `dhcp-state.globally-disabled`), read on every render and
      never cached. The Streamlit tab kept it in session state, which was wrong
      after a restart, in another browser, or once Kea's own re-enable timer
      fired. When Kea doesn't include `dhcp-state` the screen says so rather
      than guessing, and shows both buttons
    - Turning DHCP off goes through a dialog with Kea's auto re-enable timer
      (default 300 s, 0 to 3600, 0 = stay off). Kea runs the timer itself, so
      it works even if KeaNexus is down
    - Declined leases (state 1) can be cleared one by one or all at once. The
      single-address route refuses any address that isn't a declined lease, so
      it can never delete a live one
    - Wipe needs the word `WIPE` typed (checked in the browser and again on
      the server) and wipes the subnet id from Kea's own config instead of a
      hard-coded 1
- **Settings (`routes/settings.py`, `settings_data.py`).** `/settings` edits the
  subnet's pool range, lease length (sets `valid-lifetime` and its min and max
  to the same value, with renew/rebind at 50% and 87.5%) and four options
  (gateway, DNS, domain, NTP; blank removes an option). Every problem is listed
  at once; pool ends and gateway must sit inside the subnet and the pool must
  run forwards. Saved onto a config **read fresh from Kea**, touching only those
  fields, so reservations and their DROP classes are left alone. Viewers see a
  disabled form
- **Display (`/settings/display`, every signed-in user).** A per-user
  **compact header** switch, stored in `users.compact_header` and carried on
  the session user by `auth.public_user`, so `base.html` adds
  `class="header-compact"` to `<body>`. Compact mode (all in `.header-compact`
  CSS, default look unchanged): half the space above the logo, a search box half
  as wide and short, Sign out lined up with the status dots with the username
  in bold blue hanging centred below it, the version beside Help instead of
  under the logo, the page starting at the logo's bottom edge, and Overview key
  figures one size smaller with tighter spacing and a 12px gap above and below.
  Saving sends `HX-Redirect` back to the page, since the header is outside the form
- **Users (`/settings/users`, admins only).** List, add, edit role and
  password, disable and enable. All rules come from `auth.py`
  (`UserManagementError` is shown as-is); the route only checks the two
  password boxes match and are long enough **before** saving anything, so a
  role change can't land without its password. Changing your own role or
  password ends your session, so the response sends you to `/login`. You can't
  disable yourself (no button, and `auth.set_disabled` refuses)
- **Search (`routes/search.py`, `search_data.py`).** The header box; ⌘K or
  Ctrl+K focuses it from any screen, Escape clears it. Each keystroke (200 ms
  debounce) asks `GET /search?q=` for up to 8 links: registered devices first
  (by friendly name, hostname, last IP or MAC, linking to Quarantine), then
  map addresses (by IP, label, hostname or MAC). An address holding a lease
  links to Leases filtered to it; anything else links to the map. Free
  addresses match by IP only, and queries under 2 characters find nothing
- `liveness.py` — the latest ARP sweep, kept in memory for the process
  (`latest()`, `sweep_now(addresses)`, `sweep_network(kea)`), plus a daemon loop
  (`start_sweep_loop`, every `LIVENESS_SWEEP_INTERVAL_SECONDS`, default 300,
  floor 30) that sweeps through `quarantine_client.trigger_liveness_sweep`.
  **Every sweep covers the whole /24** (`.1`–`.254`, plus any leased address
  outside it, from `network_addresses`), not only leased addresses, and
  `SweepResult.seen_macs` keeps the MAC that answered for each address. A
  static device never talks to Kea, so an ARP reply is the only place
  KeaNexus can see it or its MAC. `responding` is derived from `seen_macs`. The loop catches every failure
  per pass, like the pool sampler, so an absent quarantine service never stops
  it. Feeds the Leases Online column and the Overview's "N online · swept
  X min ago" line. A failed sweep keeps the previous result
- `kea_reads.py` — `read_kea(kea)`: cached leases, config and pool stats plus a
  `kea_error` flag, shared by every screen. An unreachable Kea gives `None`s and
  a banner, never a 500
- The page dialog (`#dialog-body`, opened by `open-dialog`, closed by
  `close-dialog`) lives in `base.html`, so every screen shares one
- `cache.py` — `cached(key, ttl_seconds, load)`: a small TTL cache replacing
  `@st.cache_data` for Kea reads (leases 30 s, pool stats 30 s, config 120 s,
  health 20 s). A failed load is never cached
- `health.py` — the header's health dots: Kea via `version-get`;
  `keanexus-quarantine` via its `GET /health`; Pi-hole primary/secondary (labelled DNS1/DNS2) shown only when
  `PIHOLE_API_URL` / `PIHOLE_SECONDARY_API_URL` are set for this container, and
  counted up on **any** HTTP answer below 500 (a 401 included). That proves
  Pi-hole is answering without the web container ever holding a Pi-hole password.
  `pihole_auth_url` accepts the URL with or without `http://` and with or
  without a trailing `/api`; the Pi-hole probe skips TLS verification (Pi-hole
  v6's certificate is self-signed, and the probe sends nothing secret) and
  waits 5 s rather than 2. Each failed check is logged with its reason, so
  `docker logs keanexus` explains a red dot
- Routes: `GET/POST /login`, `POST /logout`, `GET /` (Overview),
  `GET /fragments/{address-map,quarantine,activity,health}`,
  `GET /quarantine`, `GET /fragments/{quarantine-registry,quarantine-log}`,
  `POST /devices/{name}/{quarantine,release}`, `POST /groups/{tag}/{quarantine,release}`,
  `GET /devices/new`, `GET /devices/{name}`, `POST /devices`,
  `PUT /devices/{name}`, `DELETE /devices/{name}[?force=true]` (all writes admin-only),
  `GET /leases[?q=]`, `GET /fragments/leases[?q=]`, `POST /leases/sweep` (any
  signed-in user), `GET /leases/new`, `GET /leases/{ip}`, `POST /leases`,
  `PUT /leases/{ip}` (admin), `GET /reservations`, `GET /fragments/reservations`,
  `GET /reservations/new`, `GET /reservations/{mac}`, `POST /reservations`,
  `PUT /reservations/{mac}`, `DELETE /reservations/{mac}` (admin),
  `GET /maintenance`, `GET /fragments/maintenance`, `GET`/`POST /maintenance/dhcp/disable`,
  `POST /maintenance/dhcp/enable`, `POST /maintenance/declined/clear`,
  `POST /maintenance/declined/{ip}/clear`, `GET`/`POST /maintenance/wipe` (admin),
  `GET /settings` (any user), `PUT /settings/dhcp`, `GET`/`PUT /settings/display`
  (any signed-in user, own preference only), `GET /settings/users`,
  `GET /fragments/users`, `GET /settings/users/new`, `GET /settings/users/{name}`,
  `POST /settings/users`, `PUT /settings/users/{name}`,
  `POST /settings/users/{name}/{disable,enable}` (admin),
  `GET /search?q=` (any signed-in user),
  `GET /help` (table of contents: fragment for HTMX, full page otherwise),
  `GET /help/{filename}` (404 off the allowlist), `GET /healthz` (no auth; the
  image's HEALTHCHECK), `GET /favicon.ico` (no auth; browsers request it at the
  root on their own). The favicon is a white orb with a light cyan core:
  `static/favicon.ico` (16/32/48), `favicon-32.png`, `favicon-192.png` and
  `apple-touch-icon.png` (on white, since iOS paints transparency black),
  linked from `base.html`
- `static/vendor/` — htmx, Alpine.js and vega / vega-lite / vega-embed, vendored at pinned versions with
  hashes in `VERSIONS.md`. No CDN (must work when the WAN is down) and no Node
  build step. Vega-Lite's version must match the schema Altair emits; see
  `VERSIONS.md`. Fonts fall back to the system stack until they are vendored too
- Templates: `base.html` (the B2 masthead with the real logo, the ⌘K search box,
  account + Sign out, tabs, Help modal), `login.html` + `_login_form.html`
  (no masthead, version, card or heading: a 240px logo over a 240px-wide form on the page background, with light grey centred placeholders as the visible labels and visually hidden `<label>` text for screen readers; a failed sign-in re-renders only the form, with status 200 because HTMX does
  not swap 4xx bodies), `overview.html`, `quarantine.html` + `quarantine/`, `leases.html` + `leases/`, `reservations.html` + `reservations/`, `maintenance.html` + `maintenance/`, `settings.html` + `settings/`, `help/`

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
      `_stamp_quarantine_state` to set `device_registry.is_quarantined = True` and
      record the current UTC timestamp in `last_quarantined_at`. Returns resolved
      devices plus per-device `step_results`, each including
      `skipped_due_to_identity_drift`
    - `POST /release` — same request/response shape and safety check as `/quarantine`;
      removes the Kea deny, stops ARP disruption, and removes the Pi-hole block instead
      of applying/starting them (fingerprint refresh still runs either way).
      `_stamp_quarantine_state` still runs and sets `is_quarantined = False`, but
      `last_quarantined_at` is deliberately **not** touched — it means exactly
      "last quarantined," not "last touched"
    - Both `/quarantine` and `/release` return a plain-English `summary` field
      alongside the structured `step_results` — built by `_human_summary`, which
      turns snake_case friendly_names into spoken-style names ("kids_ipad" →
      "Kids Ipad") and joins them into one sentence. Exists because a Siri
      Shortcut ending in **Get Contents of URL** shows the raw response body on
      the phone screen, which is JSON. A mixed outcome produces both a success
      and a failure sentence rather than rounding up to "done".
      `_enforcement_succeeded` backs it and deliberately ignores
      `fingerprint_refreshed` — the nmap refresh is identity upkeep that runs in
      both directions, so an inconclusive scan must not report failure to
      someone who only wants to know whether the wifi is off. See
      `docs/siri-shortcut-setup.md`
    - `GET /status/{friendly_name}` — returns recent `quarantine_log` rows for one device
    - `POST /presence-check/{friendly_name}` — immediately ARP-probes one registered
      device via `presence_check.probe_device_now`, bypassing that module's 5-minute
      background loop. Called by the web Quarantine screen right after a device is added or
      edited, so Last MAC/Last IP/Last Seen don't sit blank until the loop's next pass.
      Returns `{"friendly_name": ..., "seen": bool}`
    - `POST /liveness-sweep` `{"ip_addresses": [...]}` — ARP-sweeps the supplied
      addresses via `liveness.sweep` and returns `{"checked": N, "responding": [...],
      "seen": {ip: mac}}` (sorted, for a stable response; `responding` stays for
      an older KeaNexus). Stateless: writes nothing, and the addresses
      need not be registered devices. Backs the Leases tab's "Check who's online"
      button. Rejects more than `MAX_SWEEP_ADDRESSES` with HTTP 413 **at the route**,
      before a packet leaves the host
    - `GET /health` — `{"ok": true}`, **no auth on purpose**: it reveals only that
      the process is up, for the web UI's health dot and Docker. Every other route
      still needs the token
    - All the others require `Authorization: Bearer <token>` via `require_bearer_token`
    - `@app.on_event("startup")` calls `init_db()` eagerly. Discovered on first real
      deployment, when KeaNexus was still a Streamlit app: Streamlit deferred running
      its script, and so `init_db()`, until a browser session connected. A fresh deploy where nobody had opened the
      KeaNexus dashboard yet left `quarantine_log`/`device_registry` missing entirely,
      which this service hit before anyone ever loaded the dashboard. FastAPI runs
      startup handlers eagerly on process start, making this service self-sufficient
      regardless of dashboard usage. `init_db()` is idempotent, so this is safe
      alongside KeaNexus's own call to it
    - `_get_kea_client()` / `_get_pihole_clients()` are factories (not module-level
      singletons) specifically so tests can patch them per-call with a stub
    - `_get_gateway_ip(kea)` extracts the subnet's router IP from the live Kea config
      (same `option-data` lookup as `address_map.gateway_ip`) — needed by the ARP step to
      know which IP to impersonate
- `auth.py` — `require_bearer_token(authorization)`, a FastAPI dependency. Reads
  `QUARANTINE_API_TOKEN` from the environment on every call (not cached at import
  time) and compares with `hmac.compare_digest`, same rationale as `auth.py` in the
  main KeaNexus app
- `identity.py` — `resolve_target(kea, target, is_group)` resolves a `friendly_name`
  or `group_tag` against `device_registry`, then queries Kea's current leases by
  hostname (`KeaClient.get_leases_by_hostname`) to get the live MAC/IP.
  `_matching_registry_entries` matches leniently, via `_normalized` — a target
  arriving from a Siri Shortcut is mangled several ways at once: the iOS
  keyboard capitalizes the first letter, dictation appends a trailing period,
  autocorrect leaves a trailing space, and nobody says "underscore" out loud, so
  `tommy_laptop` comes back as `Tommy Laptop.` `_normalized` reduces a name to
  just its lowercased letters and digits, which makes all of those spellings
  compare equal without a rule per mangling. The exact match is tried first (a
  fast, indexed lookup for the common case, and it guarantees an
  exactly-registered name is never shadowed by a different entry that normalizes
  the same way); a normalized scan over `get_devices()` only runs as a fallback.
  A target that normalizes to the empty string (pure punctuation) is rejected
  outright rather than matched — an unset `group_tag` normalizes to `""` too, so
  without that guard a target of `"."` would sweep up every ungrouped device. Raises `DeviceNotRegisteredError` (mapped to HTTP 404).
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
- `liveness.py` — `sweep(ip_addresses, timeout_seconds, interface)` ARP-probes an
  arbitrary caller-supplied list of addresses and returns `{ip: mac}` (MAC
  lowercased, from the reply's `hwsrc`) for the ones that answered.
  Distinct from `presence_check.py` despite both sending ARP who-has requests: that
  one probes a single **registered** device on a timer and writes `last_seen_*`
  breadcrumbs, this one is stateless, touches no database, and the addresses need
  not belong to registered devices. All probes go out in **one** scapy `srp` call —
  scapy expands a list-valued `pdst` into one packet per address and waits `timeout`
  once for the whole batch, so a full /24 returns in about two seconds instead of
  256 sequential waits. Replies are filtered against what was asked for: a broadcast
  sweep can pick up gratuitous ARP or a proxy-ARP router answering for a range, and
  reporting those would mark leases live on evidence that isn't about them.
  `MAX_SWEEP_ADDRESSES` (1024) caps a single request so a malformed one can't become
  an unbounded packet flood
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
  `PIHOLE_API_URL`, `PIHOLE_API_PASSWORD`, `QUARANTINE_API_TOKEN`; optional <!-- pragma: allowlist secret -->
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

All endpoints except `GET /health` require `Authorization: Bearer <QUARANTINE_API_TOKEN>`.

- `GET /health` — unauthenticated liveness check; returns `{"ok": true}` and nothing else.

- `POST /quarantine` — body `{"target": "<friendly_name or group_tag>", "is_group": bool}`.
  Resolves identity, re-verifies each device's identity hasn't drifted immediately
  before enforcing (skips that device with `skipped_due_to_identity_drift: true` in
  its `step_results` entry if it has), then applies the Kea DROP-class deny, starts
  ARP disruption, applies the Pi-hole DNS block, and refreshes the OS fingerprint.
  This is the full orchestration from the design doc.
  Returns a human-readable `summary` sentence alongside `resolved_devices` and
  `step_results`, for the Siri Shortcut to display instead of raw JSON.
- `POST /release` — same request/response shape as `/quarantine`; removes the Kea
  deny, stops ARP disruption, and removes the Pi-hole block instead of
  applying/starting them (fingerprint refresh still runs either way).
- `GET /status/{friendly_name}` — returns recent `quarantine_log` rows for that device.
- `POST /presence-check/{friendly_name}` — immediately ARP-probes one registered
  device and stamps Last MAC/Last IP/Last Seen if it answers, bypassing the
  background presence-check loop's 5-minute interval. Returns
  `{"friendly_name": ..., "seen": bool}`.
- `POST /liveness-sweep` — body `{"ip_addresses": ["172.16.17.10", ...]}`. Batched
  ARP sweep of arbitrary addresses; returns `{"checked": N, "responding": [...],
  "seen": {ip: mac}}`.
  Stateless — writes nothing and the addresses need not be registered devices.
  HTTP 413 above 1024 addresses.

## Architecture Decisions

**Direct Kea HTTP listener (not Control Agent)** — Kea 3.0 deprecated and removed the Control Agent. `kea-dhcp4` now exposes its own HTTP listener on port 8004. KeaNexus talks directly to it; the `"service"` forwarding key is never sent.

**SQLite for IPAM static records** — Local SQLite in a named Docker volume. Static IP records are KeaNexus-managed metadata (not sent to Kea); they exist purely for documentation/visibility of non-DHCP addresses.

**UI moved from Streamlit to FastAPI + HTMX** — `docs/fastapi-htmx-migration.md`
records why, the route map, auth and CSRF, and each phase. The two UIs ran side
by side (the web UI on 8503) until the cutover removed Streamlit and moved the
web UI onto Streamlit's old host port, 8502. Streamlit's DOM was effectively an
API this app styled against, which made every Streamlit bump a styling risk;
the web UI owns its markup outright.

**Pool history sampled in-process, with gaps left as gaps** — A utilisation trend needs data Kea does not keep, so KeaNexus samples it itself. The sampler is a background thread started with the web app rather than a sample taken during page render, because rendering-time sampling only records while a browser is open and would draw a flat line across every overnight gap — a chart that lies. (Under Streamlit it only started once someone opened the app; the web app starts it at container start.) The two alternatives were rejected deliberately: the quarantine service's existing always-on loop is behind an optional Compose profile, so a core tab's history would silently be empty for anyone not running it, and a dedicated sampler container is more infrastructure than a home deployment warrants. Because gaps are therefore expected, nothing interpolates across them — `get_pool_samples` returns exactly the readings taken.

**KeaNexus's modules are bind-mounted file-by-file** — `docker-compose.yml` lists each Python module as its own `./x:/app/x` mount rather than mounting the directory (`web/` is the exception, mounted whole). Edits on the host take effect at the next container restart; uvicorn runs without `--reload`. File-by-file mounts drift silently: a renamed module leaves a mount pointing at a path that no longer exists, and Docker creates a _directory_ there; a new module goes unmounted, so the container runs a stale copy from the image alongside current mounted modules — which is how a Streamlit-era module rename turned into an ImportError at container start with nothing in the suite noticing. `tests/test_docker_compose_mounts.py` now fails on either condition. Modules the KeaNexus container genuinely never imports are listed in that test's `_NOT_REQUIRED_IN_KEANEXUS` (currently just `pihole.py`, which exists at the repo root only so `quarantine_service` can import it via its own Dockerfile `COPY`).

**Secret scanning split across two hooks** - `pre-commit` checks only staged files with `detect-secrets-hook`, which fails only on a secret missing from `.secrets.baseline`. The earlier hook scanned the whole tree and demanded an exact match with the baseline, so any drift anywhere (a branch's test fixtures, or a known secret moving to a new line) blocked every commit, including ones that touched none of those files. `pre-push` keeps the whole-tree check as a backstop, comparing by file and secret hash rather than line number, so moved code does not false-alarm. pytest and coverage moved from `pre-commit` to `pre-push`, which keeps commits fast. Both hooks share one exclude pattern (`SECRETS_EXCLUDE`); keep it in sync with the command used to regenerate the baseline.

**All times UTC internally** — Kea lease expiry timestamps are Unix epoch. Display formatting converts to human-readable durations via `fmt_ttl()`. `quarantine_log.occurred_at` is likewise stamped as UTC ISO8601 in `db.py`, not left to the caller.

**Reservation labels decoupled from Kea's `hostname` field** — Kea's own docs confirm host-level reservation values (including `hostname`) always take priority: once a reservation sets `hostname`, Kea echoes that admin-typed string back on every lease for the device, permanently discarding whatever hostname the client itself requests. That made `device_registry` entries built from a reservation's hostname unable to ever match a live lease during quarantine identity resolution (`resolve_target` would raise `DeviceNotOnNetworkError` for a device that was clearly online), and made the Leases tab show a made-up label instead of the device's real name. Reservations created or edited in KeaNexus no longer send `hostname` to Kea at all — the admin-facing label lives in `db.reservation_labels` instead, keyed by MAC, so Kea always preserves the real DHCP-negotiated hostname on the lease. The tradeoff: a reservation's hostname can no longer drive Kea's own DDNS registration for that device, since Kea has nothing to register.

**Lease liveness is swept by ARP from the quarantine service, never ICMP from
KeaNexus** — Kea reports which addresses are _leased_, which is not the same
question as which devices are _on_: a lease outlives the device that holds it by
hours, so the Leases tab was silently showing powered-off gear as current. The
sweep runs in `keanexus-quarantine` rather than in KeaNexus itself because that
service uses `network_mode: host` and so shares the LAN's L2 segment, where ARP
works and cannot be refused — a device that is on and on the segment must answer
ARP or become unreachable, since its host firewall sits above layer 2. KeaNexus is
on the default bridge network, off-segment, so nmap there could only fall back to
routed ICMP/TCP probes, and anything dropping ICMP from an off-subnet source
(Windows by default, plenty of IoT gear) would read as offline while sitting
happily online. Adding `nmap` to the KeaNexus image was rejected for that reason —
it would have been self-contained but wrong more often than not. The tradeoff
accepted instead: the button depends on the optional `quarantine` Compose profile,
so `QuarantineServiceError` is caught and surfaced as an ordinary message rather
than an exception, and the tab renders normally without it. The sweep reports
presence only, never absence — a non-responding address is drawn unchanged, not
marked dead, because a sweep can only ever prove the former.

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
module (`--cov=.` in `pyproject.toml`), `web/` included. That includes
`auth.py`, `db.py`, `kea.py`, `helpers.py`, `pihole.py`, and everything in
`quarantine_service/`.

| File                                        | What is tested                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/test_auth.py`                        | First-admin bootstrap; `check_credentials` (case-insensitive, wrong password, unknown/empty and disabled users, records login); `session_user_if_valid` (current vs old version, disabled, unknown); signed token round trip, no secret, stale version, tampered username, malformed and non-numeric values, `public_user` omits the hash; user-management rules (validation, duplicates, last-admin and self-disable protection)                                                                                                                                                                                                                                                                             |
| `tests/test_web.py`                         | Web UI phase 0 via `TestClient`: `/healthz`; signed-out redirect (303, or `HX-Redirect` for HTMX); login sets an HttpOnly/SameSite cookie, wrong password shows the error with no session, missing secret explained, plain-form 303; sign-out; sessions ended by password change and disable; CSRF refused without token, with a mismatched token, without the cookie, token written into the page and not rotated; `require_admin`/`kea_client` by role; Help table of contents (fragment vs full page, every link lands on a real heading id, off-allowlist and missing files 404, code blocks skipped); lifespan admin bootstrap and the sampler switch |
| `tests/test_address_map.py`                | `classify_addresses`: all 256 rows, network/broadcast/gateway, pool vs free and which are editable, dynamic lease (hostname, MAC, time left), declined beats reservation, leased reservation shows reserved with its stored label, legacy Kea hostname as label fallback, offline reservation, masked hostname never shown as real, static entry label/notes/editable, missing `valid-lft` uses Kea's default, malformed prefix rejected; the config helpers |
| `tests/test_overview_data.py`               | `local_time`; `pool_figures` (declined counted as used, ok/warn/full, zero pool, no stats); `devices_by_ip` (live hostname first, last-seen fallback); `map_cells` display statuses (quarantined, unknown, named and reserved leases not unknown) and the full cell shape; legend order; `quarantine_rows` ordering and meta; `activity` (one line per action, nmap failure ignored, failed step flagged, identity-drift skip, separate actions, limit) |
| `tests/test_web_overview.py`                | Overview routes with a stub Kea (plus `/search`: sign-in, results, no match, too short, live box): needs sign-in, 256 squares + figures + subnet, hover JSON names the registered device, card lists devices, viewer switches disabled, Kea down still renders with a banner, chart specs and table twins present, trend waits for two samples, all four fragments; Quarantine/Release (viewer 403, unknown 404, summary + `HX-Trigger`, release path, service failure shown, CSRF required); static entries (prefill, free address, pool/network/off-subnet refused, save + close trigger, bad MAC not written, delete, viewer 403); `cache` (reuse, expiry, failures not cached, forget, TTL check); `health` (Kea only, Kea down, Pi-hole any non-5xx is up, quarantine needs 2xx, unreachable is down) |
| `tests/test_quarantine_data.py`             | `device_rows` (order, fields, blanks), `group_tags` counts, `log_rows` (unreadable time shown raw), `step_outcomes` incl. skipped devices, `enforcement_succeeded` (fingerprint ignored, any other failure or nothing resolved fails), `validate_device` (bad, reserved and duplicate names, hostname required, edits skip name rules), `ReleaseAttempts` threshold and clear |
| `tests/test_web_quarantine.py`              | Quarantine screen with the stub Kea and a patched service: sign-in, devices/groups/log listed, nav link, viewer view, fragments, empty registry; actions (viewer 403, CSRF, unknown 404, steps + `quarantine-changed`, partial failure, service down, group calls with `is_group`, unknown group); forms (lease hostnames offered, quiet hostname kept, free text when Kea is down, admin-only, unknown 404); create/update (presence check, its failure ignored, duplicate and bad names, viewer refused, service-owned fields kept, hostname required); delete (plain, release first, failed release keeps the row, force refused early, force offered and honoured after 3, viewer, unknown) |
| `tests/test_leases_data.py`                 | `lease_rows` (numeric sort, fields, masked hostname, type from reservation, online none/true/false, declined, Kea's default lifetime), `filter_rows`, `validate_lease` (bad address, outside subnet, bad MAC, duration not a number or out of range), `normalized_mac` |
| `tests/test_web_liveness.py`                | `sweep_now` keeps the latest and dedupes the count, a failed sweep keeps the previous result, leases without an address skipped; the loop runs, survives a failed pass, is idempotent and stops; interval env var (default, floor, junk); `age_label` |
| `tests/test_web_leases.py`                  | Leases screen with a recording stub Kea: sign-in, table, filter and no match, Kea down, viewer sees no edit controls; sweep (viewer allowed, sweeps every lease not just the filtered ones, online rows, service down as a notice, CSRF) and the Overview's online count; forms (new, prefilled edit, unknown 404, admin-only); writes (add with MAC normalised, invalid not sent, Kea refusal shown, update, unknown 404, viewer 403); the liveness switch and the lifespan starting the loop |
| `tests/test_reservations_data.py`           | `reservation_rows` (order, online before/after a sweep for fixed and name-only, all four hostname states, label from KeaNexus then legacy Kea hostname, quarantined flag, non-MAC reservations not editable), `validate_reservation` (MAC, label, duplicates, address checks, editing itself), config edits (add leaves the input untouched, edit keeps DROP and other fields and drops Kea's hostname, remove, find) |
| `tests/test_web_reservations.py`            | Reservations screen with an in-memory Kea config: table with labels, viewer view, Kea down; forms (new, edit by any-case MAC, unknown 404); writes (add writes Kea then the label, invalid not sent, Kea refusal leaves the label alone, Kea unreachable, edit reads a fresh config and keeps a DROP added after page load, label moves with the MAC, vanished reservation 404, delete, delete refused for a quarantined device, delete failures, viewer 403, CSRF) |
| `tests/test_maintenance_data.py`            | `declined_rows` (only declined, numeric order, Kea's default lifetime), `validate_auto_enable` bounds, `wipe_confirmed` exact word |
| `tests/test_web_maintenance.py`             | Maintenance screen with a recording stub Kea: on/off/unknown/unreachable states, clean pool, Kea down; disable with timer, bad timer not sent, refusals, enable; clear all and one declined, a live lease refused, failures reported; wipe needs the word, wipes the configured subnet, refusal shown; every route refused to viewers and without CSRF |
| `tests/test_search_data.py`                 | `search`: too-short queries, devices before addresses, label as title with hostname and MAC in the detail, link to Leases only when an address holds a lease, empty addresses match by IP only, capped at 8 |
| `tests/test_settings_data.py`               | `settings_values` (fields, lifetime fallbacks), `validate_settings` (every field, pool order, subnet-less config), `with_settings_applied` (pool, lifetimes and timers, list normalising, blank option removed, unrelated options and reservations kept, input untouched, no pools yet, `valid-lft` stripped) |
| `tests/test_web_settings.py`                | Settings screen: DHCP page, viewer read-only, save onto a fresh config keeps a DROP added after page load, invalid not sent, Kea failures, viewer and CSRF refused, Kea down; Users: list and forms, viewer refused, create, create refusals (mismatch, short, missing, bad name, duplicate), edit another user, mismatch saves nothing, last admin can't be demoted, own password change signs you out, own no-op save doesn't, disable and enable, can't disable yourself |
| `tests/test_db_users.py`                    | `users` table storage: case-insensitive usernames, duplicate rejection, role CHECK, list omits hashes, active-admin count, `session_version` bumps on every change, `record_login`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `tests/test_db.py`                          | Full CRUD on `ipam_static`, `reservation_labels`, `device_registry`, `quarantine_log` and `activity_log` (newest first, long summaries trimmed) via `temp_db` fixture (SQLite in tmp dir)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `tests/test_kea.py`                         | All `KeaClient` methods; `ReadOnlyKeaClient` passes every read command and refuses every write and unknown command before any request; `httpx` patched via `http_mock` fixture                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `tests/test_helpers.py`                     | `fmt_ttl`, `build_reservation_type_sets`, `lease_type`, `build_hostname_override_sets`, `real_hostname`, `distinct_real_hostnames`, `lease_for_reservation`, `pool_stats_with_fallback` (summary, lease-count fallback, unreachable)                                                                                                                                                                                                                                                                                                                                                                                                               |
| `tests/test_dashboard_data.py`              | `lease_expiry_buckets` (bucket boundaries, expired leases, declined excluded, missing `valid-lft`), `lease_composition`, `utilisation_points` (gap breaking, declined counted as used, zero total), `leases_issued_since` (cumulative delta, stale baseline rejected, counter reset, insufficient history), `quarantined_count`                                                                                                                                                                                                                                                                                                                                                             |
| `tests/test_dashboard_charts.py`            | Chart builders: trend needs two samples, timestamps keep their UTC offset (regression), gaps break the line, table newest first; expiry and composition tables and plain inline-data specs; `count_headroom` never zero |
| `tests/test_version.py`                     | `APP_VERSION` is plain `MAJOR.MINOR.PATCH`, since the UI renders it as `v` + the value; the masthead and Help both show it, the sign-in page doesn't                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `tests/test_docs_library.py`                | Article allowlist (listed files exist, developer docs excluded, missing file hidden) and `read_article` rejecting path traversal, absolute paths and unlisted names                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `tests/test_docker_compose_mounts.py`       | Every `./x:/app/x` mount source in `docker-compose.yml` exists on disk (except gitignored runtime-state mounts in `_RUNTIME_STATE_MOUNTS`, currently `./data`, which a fresh clone doesn't have; a guard test checks each one really is gitignored), and every repo-root module is mounted (minus a checked exclusion list) — catches a rename or addition that would leave the container running stale code                                                                                                                                                                                                                                                                                |
| `tests/test_pihole.py`                      | `PiholeClient` session auth (caching, reuse, re-auth on expiry), CSRF header logic, error mapping, constructor overrides (base_url/password); `httpx` patched via `pihole_http_mock` fixture                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `tests/test_quarantine_client.py`           | `trigger_quarantine`/`trigger_release` — request shape, auth header, is_group passthrough, missing-token error, connect error, HTTP error detail parsing; `trigger_liveness_sweep` — request shape, responders returned, empty list short-circuits without a request, missing `responding` key, missing token, unreachable service; `httpx` patched via `quarantine_client_http_mock` fixture                                                                                                                                                                                                                                                                                               |
| `tests/test_quarantine_auth.py`             | `require_bearer_token` — missing config, missing/malformed header, wrong token, success                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `tests/test_quarantine_identity.py`         | `resolve_target` — single device and group resolution, case-insensitive friendly_name/group_tag matching, unregistered target, no-lease case, last-seen refresh; `verify_identity_unchanged` — match, IP drift, MAC drift, lease gone — using `StubKeaClient`. Target leniency is parametrized over every spelling iOS can produce (case, trailing period, trailing/leading space, spaces or hyphens for underscores), plus exact-match precedence and the punctuation-only guard                                                                                                                                                                                                           |
| `tests/test_quarantine_kea_deny.py`         | `apply_drop_class` / `remove_drop_class` (pure config mutation), `deny_via_kea` / `undo_deny_via_kea` (via `StubKeaClient`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `tests/test_quarantine_arp_disrupt.py`      | `send_poisoned_arp_reply` packet construction, `start_arp_disruption` / `stop_arp_disruption` loop lifecycle, idempotent restart, survives a failed send, no packets after stop — `sendp` always patched                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `tests/test_quarantine_pihole_block.py`     | `block_via_pihole` / `unblock_via_pihole` — group/regex creation vs reuse, unrelated groups ignored, client PUT/DELETE calls, using `StubPiholeClient`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `tests/test_quarantine_nmap_fingerprint.py` | `refresh_os_fingerprint` — persists top-accuracy match, preserves other registry fields, inconclusive scan doesn't overwrite existing fingerprint, subprocess failure propagates; `subprocess.run` always patched                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `tests/test_quarantine_liveness.py`         | `sweep` — only responders returned, every address probed in one batched `srp` call, targets deduped/sorted/whitespace-stripped, replies from unrequested addresses ignored, empty input sends nothing, `ARP_INTERFACE` honoured (explicit arg wins, blank env var means auto-select), over-cap input rejected; `srp` always patched                                                                                                                                                                                                                                                                                                                                                         |
| `tests/test_quarantine_retry.py`            | `run_with_retries` — first-attempt success, success-after-failures, exhausted-retries, custom `max_attempts`, one-row-per-step logging                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `tests/test_quarantine_main.py`             | FastAPI routes via `TestClient` — `/health` answers without a token, auth enforcement, 404/409 error mapping, all four enforcement steps wired correctly, group requests, pre-fire safety check skips a device whose identity drifted between resolution and enforcement, `/presence-check/{friendly_name}`, `is_quarantined`/`last_quarantined_at` stamping on quarantine vs. release; `_human_summary` sentence construction (single/multiple devices, partial failure, identity drift, inconclusive fingerprint) and its presence in both endpoints' responses; `/liveness-sweep` auth, sorted responders, passthrough to `sweep`, distinct-address `checked` count, empty request, and over-cap rejection without sweeping |
| `tests/test_quarantine_presence_check.py`   | `probe_device_now` — unregistered device, no current lease, Kea unreachable, no ARP reply, ARP reply stamps last-seen fields, default-constructed `KeaClient`; `_run_one_pass` probes every registered device                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `tests/test_pool_history.py`                | `take_sample_now` (writes a row, derives `available`, survives an unreachable Kea), sampler lifecycle (start records, idempotent start, stop, safe stop when never started, a failing pass does not kill the loop), and interval/retention env-var handling                                                                                                                                                                                                                                                                                                                                                                                                                                 |

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

## Validation and Error Handling Standards

- Web routes catch recoverable errors (`KeaError`, `QuarantineServiceError`, `UserManagementError`) and show them in the form or a notice; an unreachable Kea gives a banner, never a 500
- Internal modules raise `KeaError` for Kea API failures; callers handle at the tab level
- Static entries are refused (404) for any address `address_map` doesn't mark editable: inside the pool, network, broadcast or off-subnet
- DB functions assert IP is a non-empty string; SQLite PRIMARY KEY enforces uniqueness
- `upsert_device` asserts friendly_name and hostname are non-empty; friendly_name is the SQLite PRIMARY KEY
- `insert_quarantine_log_entry` asserts friendly_name is non-empty, action is `"quarantine"` or `"release"`, and attempt_count is at least 1
