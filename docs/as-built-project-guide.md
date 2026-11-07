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
├── auth.py             — Authentication and user management: argon2 login, roles, signed session cookie
├── kea.py              — Kea direct HTTP API client (Kea 3.0+)
├── pihole.py           — Pi-hole v6 REST API client (session auth); used by quarantine_service
├── quarantine_client.py — Thin client KeaNexus uses to call keanexus-quarantine's own API
├── helpers.py          — Cached data loaders, format utilities
├── dashboard_data.py   — Pure metric/chart shaping for the Dashboard tab
├── docs_library.py     — Allowlist + reader for the operator docs shown in-app
├── ui_docs.py          — Documentation sidebar entry and the article dialog
├── pool_history.py     — Background sampler building the pool utilisation time series
├── db.py               — SQLite persistence layer (IPAM static records, users, registry, logs)
├── ui_login.py         — Login page: logo, username/password form
├── ui_dashboard.py     — Dashboard tab: KPI tiles, gauge, service health, Altair charts
├── ui_leases.py        — Leases tab: HTML table with type classification
├── ui_ipam.py          — IPAM tab: full /24 subnet map + static entry management
├── ui_reservations.py  — Reservations tab: Kea config CRUD
├── ui_maintenance.py   — Maintenance tab: DHCP enable/disable, wipe leases
├── ui_quarantine.py    — Quarantine tab: device registry CRUD + read-only audit log
├── ui_settings.py      — Settings tab: subnet/pool/lease on one row, DHCP options in a 2x2 grid
├── ui_users.py         — Users panel (Settings → Users, admins only): add, edit, disable logins
├── ui_access.py        — Viewer read-only helpers: read_only(), the tab notice, refuse_if_viewer()
├── style.css           — Global CSS overrides for Streamlit internals
├── Makefile            — Developer setup (`make setup`) and test runner (`make test`)
├── static/             — Static assets (keanexus_logo.png)
├── quarantine_service/ — Optional keanexus-quarantine FastAPI service (compose profile "quarantine")
├── docs/               — PRD, this guide, design docs (incl. the FastAPI + HTMX migration plan)
├── .githooks/          — Committed git hooks (pre-commit, pre-push); activated per-clone with `make setup`
└── .streamlit/         — Streamlit config (theme)
```

Data persisted in Docker named volume `keanexus_data` mounted at `/app/data/`.

## Systems

### Authentication (`auth.py`)

- **Multi-user, two roles.** Users live in `db.users` with role `admin` or
  `viewer`. Passwords are stored only as argon2 hashes (`argon2-cffi`);
  `auth.py` is the only module that ever handles a plaintext password, and
  `db.py` only stores and returns hashes
- **First admin from the old env-var login.** `bootstrap_admin_from_env()`
  runs at startup (`app.py`, right after `init_db()`). When the users table is
  empty it creates an admin from `KEANEXUS_USERNAME` / `KEANEXUS_PASSWORD`, so
  an existing deployment keeps working on upgrade with no manual database step.
  A no-op once any user exists, so changing those env vars later does nothing.
  The minimum password length is deliberately not enforced on this one path:
  the env-var password predates the rule, and refusing it would lock the owner
  out of their own upgrade
- `attempt_login(username, password)` — username lookup is case-insensitive.
  Unknown and disabled users return the same `False` as a wrong password, after
  the same hashing work (a lazily built decoy hash), so login timing can't
  reveal which usernames exist
- `current_user()` returns `{"username", "role"}` or `None`, and **re-checks the
  database on every call**: the session stores the user's `session_version`,
  and if the stored version has moved on (password, role or disable change) or
  the user is gone, the session ends on the spot. So revoking someone takes
  effect at their next click, not their next login. `is_authenticated()` and
  `is_admin()` are thin wrappers over it
- `logout()` — clears the session user
- **Signed session cookie.** A full page reload starts a brand-new Streamlit
  session with blank `session_state`, so login persists through a browser
  cookie (`SESSION_COOKIE_NAME`). `session_token()` returns
  `username|session_version|signature`, signed with HMAC-SHA256 under
  `KEANEXUS_SECRET`. `restore_session_from_cookie(value)` rejects anything
  malformed, wrongly signed, or carrying an old `session_version`. Still
  stateless (no server-side session store). The previous design derived the
  cookie from the single shared password, which could not tell users apart or
  revoke one person; the signed username plus version replaces it. Usernames
  are restricted to letters, digits, `.`, `_` and `-`, which keeps the `|`
  separator out of them so the cookie splits unambiguously
- **`KEANEXUS_SECRET` unset:** `session_token()` returns `""` and
  `ui_login.py` skips setting the cookie. Login still works for that session;
  every reload asks again. Changing the secret signs everyone out
- **User-management rules live here, not in the UI**: `create_user`,
  `reset_password`, `change_role`, `set_disabled`. They raise
  `UserManagementError` (a `ValueError` whose message is safe to show) for bad
  usernames, passwords under `MIN_PASSWORD_LENGTH` (12), unknown roles,
  duplicates, demoting or disabling the last active admin, and an admin
  disabling their own account. Kept in `auth.py` so they are unit-tested,
  since `ui_*.py` is excluded from coverage
- `app.py` guards `main()` with `is_authenticated()` — unauthenticated requests
  see only the login page
- `app.py` owns the actual cookie I/O (via `extra_streamlit_components.CookieManager`,
  constructed each run by `_cookie_manager()`) and calls
  `restore_session_from_cookie` on every fresh session before falling back to
  the login page — `auth.py` itself stays free of the CookieManager/component
  dependency, just the token logic. `_cookie_manager()` is deliberately _not_
  `@st.cache_resource` — `CookieManager.__init__` itself calls a Streamlit
  component (a widget-like command), and Streamlit forbids that inside a cached
  function (`CachedWidgetWarning`); the component already stabilizes itself
  across reruns via its own internal `key="init"`, not Python object identity
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
- **The cookie is written and deleted one run later, never right before an
  `st.rerun()`.** `CookieManager` only acts when its component actually renders
  in the browser, and `st.rerun()` discards the current run's output first. Both
  login and Sign out used to call the cookie method and then rerun immediately,
  so the cookie was never written (every reload logged the user out) and never
  deleted (a reload after Sign out signed straight back in). Confirmed in a
  headless browser against the old code; both bugs predate multi-user. Now:
  `ui_login.py`'s `render_login` only sets `_session_cookie_pending` and reruns,
  and `write_pending_session_cookie(cookie_manager)`, called from `main()` right
  after the authentication gate, writes the cookie on that next full run. Sign
  out (`app.py`'s `render_sidebar`) sets `_cookie_delete_pending` and reruns;
  `_delete_cookie_after_sign_out` deletes it on the next run, and `main()` skips
  cookie restore on that run so it can't sign back in from the cookie it is
  deleting. The delete stays guarded against `KeyError`, since `.delete()`
  raises if the cookie hasn't synced into the component's cache yet
- The cookie has a 24h rolling expiry — the CookieManager library always
  attaches an expiry (no true zero-expiry "clears only when the browser closes"
  option), so this is the closest practical approximation

### Kea Client (`kea.py`)

- `KeaClient` — synchronous HTTP client talking directly to the `kea-dhcp4` HTTP listener (Kea 3.0+)
- Connection configured via `KEA_API_URL`, `KEA_API_USER`, `KEA_API_PASSWORD` env vars
- The `"service"` key is never sent — direct listeners don't support forwarding and reject it
- Key methods: `get_leases()`, `get_pool_stats()`, `get_config()`, `save_config()`, `get_status()`, `get_pool_range(config)`
- `get_status()` returns both `"ca"` and `"dhcp4"` keys for UI compatibility; `"ca"` is a static placeholder (`up=True`, `version="n/a"`) since the Control Agent no longer exists in Kea 3.0+
- `ReadOnlyKeaClient` — a `KeaClient` whose `call()` refuses any command outside
  `READ_ONLY_COMMANDS` with `KeaReadOnlyError`, before a request is sent. Handed to
  viewer sessions by `helpers.get_client()`. An allowlist on purpose: a write
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
- `trigger_liveness_sweep(ip_addresses)` — called by the Leases tab's "Check who's
  online" button. Returns the subset of the supplied addresses that answered an ARP
  probe. Short-circuits to `[]` without a request when given no addresses. Its own
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

- `APP_VERSION` — single string constant, rendered as the sidebar's top row
  (`app.py`'s `render_sidebar`)
- Bump on every release; no other source of truth for the app version

### Sidebar Version Block (`app.py`)

- Info rows above it: Pool Range, Subnet, Address Pool, Lease Time. Router and
  Stats Source were removed as noise
- `_sidebar_version_block(status)` — renders two info rows near the bottom of the sidebar
- Displays: Kea DHCP daemon version (from `get_status()`), API Mode (always "Direct API")
- Always rendered regardless of Kea connectivity — aids diagnosis when Kea is unreachable
- `_sidebar_item(label, value, show_divider=True)` — the Kea DHCP row passes
  `show_divider=False` so only the last row in the cluster (API Mode) keeps a
  trailing divider line
- `_sidebar_value_only(value, show_divider=True)` — same visual slot as
  `_sidebar_item` but without the uppercase label line above the value; used
  for the top `vX.X.X` row, where a "KeaNexus" heading would be redundant
  directly above the app's own logo image right below it. Tighter vertical
  padding than `_sidebar_item` too, so that row reads as sitting close above
  the logo rather than as another evenly-spaced row

### Tab Routing (`app.py`)

- `main()`'s `st.tabs([...], key="main_tabs")` — the `key` is load-bearing, not
  cosmetic: without it, `st.tabs()` has no persisted selection and always resets
  to the first tab on any rerun. Several tabs trigger an explicit `st.rerun()`
  after an action (e.g. `ui_quarantine.py`'s Quarantine/Release buttons, and its
  Add/Edit Device dialog's Save), which otherwise bounced the user to the Dashboard
  tab with the previous tab's content still visible fading out underneath —
  `key=` makes the active tab persist across that rerun like any other keyed
  widget's session-state value
- `_render_settings_tab(config)` — admins get `DHCP` and `Users` sub-tabs
  (`st.tabs(..., key="settings_tabs")`, keyed for the same rerun reason as
  `main_tabs`); viewers get the DHCP settings alone, with no Users tab at all
- **Nested tab bars must stay in normal flow.** `style.css` pins the tab bar
  with `position: fixed`, and that selector matches every tablist on the page.
  The Settings sub-tabs once inherited it, landed on the same spot as the main
  bar and covered it, so Settings had no way out. A nested-tablist rule resets
  them to `position: static`; any future `st.tabs()` inside a tab gets the same
  treatment for free

### Helpers (`helpers.py`)

- `get_client()` — the Kea client for this session: a cached `KeaClient` for
  admins, a cached `ReadOnlyKeaClient` for viewers (see `kea.py`). Not cached
  itself, since the answer depends on who is signed in. This is the real
  enforcement of read-only access for every Kea write
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

### Pool History Sampler (`pool_history.py`)

- Kea reports only the present — `stat-lease4-summary` answers "how full is the
  pool right now" and keeps no history — so any utilisation trend has to be
  accumulated from readings taken over time. This module is the only writer of
  `db.pool_samples`
- `start_pool_sampler()` starts one daemon thread per process, idempotent
  (a second loop would double every sample). `app.py` calls it behind
  `@st.cache_resource` so Streamlit runs it once per process rather than on
  every script rerun. `stop_pool_sampler()` and `is_sampling()` exist for tests;
  production never stops the loop, the daemon thread dies with the process
- `take_sample_now(kea=None)` takes and stores one reading; returns False rather
  than raising when Kea is unreachable, since that is an ordinary recoverable
  condition for a sampler and the series should resume on its own
- `_run_sampler_loop` catches every exception per pass — a sampler that dies on
  one bad pass stops producing history silently, and nothing notices until
  someone opens the chart and finds it frozen
- **Known limitation:** Streamlit does not execute `app.py` until a browser
  session connects (the same trap documented for `init_db()` under the
  quarantine service), so after a container restart sampling does not resume
  until somebody next opens KeaNexus. Once started it runs for the life of the
  process regardless of whether anyone is watching — which is the property that
  matters, and the reason this is a background thread rather than a sample taken
  during page render. Consumers must treat gaps as gaps and never interpolate
  across a period with no samples
- Configured via `POOL_SAMPLE_INTERVAL_SECONDS` (default 300, floored at 1) and
  `POOL_SAMPLE_RETENTION_DAYS` (default 30); each pass prunes older rows

### In-App Docs (`docs_library.py`, `ui_docs.py`)

- A **Documentation** expander near the bottom of the sidebar lists operator-facing
  articles; clicking one opens it in a wide modal (`st.dialog`). The sidebar is
  210px, enough for titles and nowhere near enough for articles full of code
  blocks and tables — so the sidebar is navigation only
- `docs_library.ARTICLES` is the allowlist: currently Siri Shortcut Setup and
  Quarantine Design. The as-built guide and PRD are developer notes and stay out
  of the app deliberately
- **`read_article` refuses anything not in `ARTICLES`, before any path is
  built.** The filename arrives from a UI control, so joining it onto the docs
  directory would be a path-traversal hole in a page one login away from the
  internet; the article set is known at build time, so there is no reason to
  accept arbitrary paths. Covered by tests for `../.env`, absolute paths and
  unlisted docs
- Article entries are `st.button`s styled as links in every state (rest, hover,
  focus): hover and keyboard focus underline rather than drawing a box, so they
  never look like a second Sign out button
- `available_articles()` hides an article whose file is missing, so a deployment
  that missed the `./docs` bind-mount offers nothing rather than entries that
  fail on click
- The button lives in the sidebar and the dialog opens from `main()`, wired
  through one session-state key that is cleared on read — Streamlit gives no
  callback for the reader dismissing a modal, so it must not reopen on the next
  rerun

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
- Table: `users` — app login accounts (username PK `COLLATE NOCASE`, password_hash,
  role `admin`/`viewer` enforced by a CHECK, session_version, is_disabled,
  created_at, last_login_at). Usernames compare case-insensitively so "Tom" and
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
  `record_login(username)`. The three updates each bump `session_version`; each query is a
  literal string, never assembled, so Bandit has nothing to flag. `USER_ROLES` is the role list

### Dashboard Data (`dashboard_data.py`)

Pure shaping for every number the Dashboard shows — plain data in, plain data
out, no Streamlit/Kea/SQLite. Lives outside `ui_dashboard.py` because `ui_*.py`
needs a live Streamlit server and is excluded from coverage, so logic placed
there would be untestable. 100% covered by `tests/test_dashboard_data.py`.

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

### Dashboard Tab (`ui_dashboard.py`)

Render-only; all figures come from `dashboard_data.py`. Layout top to bottom:
KPI tile row → gauge / service health / lease summary cards → utilisation trend
→ expiry distribution + lease composition.

- `render_dashboard(stats, config, status, leases=None)` — takes `leases` (added
  for the charts) and reads `pool_samples`/`device_registry` from `db` directly
- **Two rendering paths, deliberately.** Stat tiles, the gauge and health rows
  stay hand-written HTML, consistent with the "HTML tables (not st.dataframe)"
  and "CSS conic-gradient gauge" decisions. Charts are Altair (already a
  Streamlit dependency — nothing added to `requirements.txt`), themed to the
  app's monospace/palette, because hand-rolling axis ticks, binning and scales in
  SVG buys nothing the existing convention was protecting
- **Chart colours were validated, not chosen by eye.** `_SERIES_1` is the app's
  own accent `#0969da` (passes lightness band, chroma floor, 3:1 contrast on the
  white card surface). `_EXPIRY_RAMP` is a single-hue _ordinal_ ramp, because the
  expiry buckets are an ordered scale rather than identities — assigned
  darkest-first so the eye lands on the soonest-expiring bucket. Composition bars
  are nominal, so every bar takes the same slot-1 hue rather than spending the
  identity channel re-encoding bar length
- Trend chart: single series so no legend; `%a %H:%M` axis labels because
  `%H:%M` alone repeats every label across a multi-day window; no interpolation
  curve, since readings are minutes apart and smoothing would draw values that
  were never measured; a transparent full-height rule per sample drives the
  crosshair and tooltip, because a 2px line is too small a hover target
- Both count axes use an explicit zero-anchored domain from `_count_headroom`,
  never `Scale(padding=...)`. Vega-Lite's scale padding expands the domain at
  _both_ ends, so on a count axis it pushes the minimum below zero and the axis
  draws `-1`/`-2` ticks for a quantity that cannot be negative. `tickMinStep=1`
  keeps ticks whole — a "0.5 leases" gridline is meaningless
- Both bar charts pass `alt.Data(values=...)` rather than a DataFrame. Altair
  turns a DataFrame into a _named_ dataset the spec only references, and Vega
  evaluates the spec before that name resolves — logging "Infinite extent for
  field count: [Infinity, -Infinity]" against an empty array on first paint.
  Inline values have nothing to resolve, and `dashboard_data` already returns
  JSON-shaped rows, so pandas is not in the path at all. The trend chart still
  uses a DataFrame — it needs pandas for the UTC-to-local conversion
- Both bar charts carry a value label past each bar's end via
  `_bar_value_labels`, never inside the bar — a short bar cannot fit one, and
  a label clipped by its own mark is worse than no label. The composition
  chart's x-scale gets extra padding so the label past the longest bar isn't
  clipped by the plot edge. Labels wear a text token, not the bar's colour
- Every chart has a "Show the numbers" expander — a table-view twin, so no value
  is reachable only by reading a mark or hovering
- Degrades rather than throwing: fewer than two samples shows an explanatory
  caption instead of the chart; `leases_issued_since` returning `None` shows "—"
  with a "needs more history" note
- `_gauge(pct, used, total, avail)` — CSS conic-gradient donut ring; green <75%, amber 75–90%, red ≥90%
- `_stat_tile(label, value, color, note)` — one KPI tile
- `_health_row(label, detail, up)` — service health row with status badge
- `_metric_row(label, value)` — lease summary metric row

### IPAM Tab (`ui_ipam.py`)

- `render_ipam(leases, config)` — full /24 subnet map (256 addresses)
- `_classify_all(...)` — classifies each IP as: network, broadcast, gateway, leased, declined, reserved, static, scope (free in pool), or free
- Status priority: network/broadcast > gateway > declined lease > active lease with fixed reservation (shows as reserved) > active lease > reservation > static DB entry > in-scope free > free
- Static entries stored in SQLite; only permitted for out-of-scope addresses
- Edit is a per-row `st.dialog` (`_edit_dialog`); `upsert_static_entry` / `delete_static_entry`
- Columns: IP Address, Status, Label, Real Hostname, MAC, Expires. Label and Real
  Hostname follow the same split as the Reservations tab, so an admin-typed name
  never shows up as the device's own name:
    - **Label** is any admin-assigned name. For a reservation it comes from
      `db.reservation_labels` (by MAC), falling back to a legacy Kea-side
      `hostname` on a reservation not yet resaved. For a static entry it is the
      entry's `hostname`, since that is typed by an admin, not reported by a device.
      The gateway row's `gateway` is a label too
    - **Real Hostname** is only `helpers.real_hostname(...)` from a live lease. Rows
      with no lease (static, offline reservation, gateway) show `-`
    - **Expires** is the lease time left (`fmt_ttl`), or `offline` for a
      reservation with no current lease
- A static entry's description and notes have no column. They show as the
  hover tooltip on the status chip (`_chip` HTML-escapes the tooltip, since it is
  free text in an attribute)
- The filter box matches IP, label, real hostname, or MAC

### Leases Tab (`ui_leases.py`)

- `render_leases(leases, config)` — HTML table, sorted by IP
- Hostname column shows `helpers.real_hostname(lease, override_ips, override_macs)`
  — blank (`—`) only when the lease's matching reservation still carries a Kea
  `hostname` override, not based on lease type (see `helpers.py` above)
- **"Check who's online"** button (beside the filter box) ARP-sweeps every lease
  address via `quarantine_client.trigger_liveness_sweep` and redraws responding
  rows in green (`_TD_LIVE`). Answers a question Kea structurally cannot: a lease
  outlives the device by hours, so "leased" never meant "powered on"
    - Sweeps **all** leases, not the filtered subset — captured before the filter
      narrows the list, so the result doesn't go stale on every filter keystroke
    - Result lives in `st.session_state` under `_LIVE_IPS_KEY`. `None` (no sweep
      run yet) is deliberately distinct from an empty set (a sweep ran, nothing
      answered) — the first says nothing about liveness, the second says every
      lease looked dead, and collapsing them would make "never checked" render
      identically to "everything is off"
    - `_render_liveness_caption` states how many answered and how long ago.
      Without the age a sweep from an hour back looks identical to one from a
      second back, and a device switched off since would still read green
    - `QuarantineServiceError` is caught and shown via `st.error` — the sweep runs
      in an optional-profile service, so unreachable is an ordinary condition and
      must not take the tab down
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
  currently observed in leases at all. On Save, calls
  `quarantine_client.trigger_presence_check(name)` best-effort (wrapped in
  `st.spinner`, failures swallowed) so Last MAC/Last IP/Last Seen populate
  immediately rather than sitting blank until the quarantine service's
  5-minute background loop gets to the new device
- Fingerprint and last-seen fields are displayed read-only in the dialog — they're
  written by the quarantine service, not editable from this tab
- `_render_enforcement_step_legend()` — a collapsed `st.expander` right below the
  tab's top caption, explaining in plain language what each of the four
  enforcement steps (Kea, ARP, Pi-hole, Fingerprint) actually does and why —
  the ✓/✗ summary line after a Quarantine/Release action only shows step
  names, which aren't self-explanatory on their own
- `_trigger_action(friendly_name, action)` — calls `trigger_quarantine`/
  `trigger_release`, stashes a pass/fail summary in `st.session_state`, then calls
  `st.rerun()`. The stash-then-rerun pattern is necessary because `st.rerun()` wipes
  whatever was drawn this run — `_render_pending_action_result()` displays and clears
  the stashed result at the top of the next run
- `_summarize_step_results(action_label, result)` — turns the API's `step_results`
  into a one-line-per-device pass/fail summary; flags `skipped_due_to_identity_drift`
  distinctly from a step actually failing
- `_row_cell_styles(is_quarantined)` — returns the device table's normal cell
  styles, or all-red (`_QUARANTINED_TEXT_COLOR`) variants when the device is
  currently quarantined, so the whole row reads as quarantined rather than a
  separate Status column. Exists because `last_quarantined_at` alone can't
  answer "is this quarantined right now" — it's never cleared on release
- `_render_log_table(entries)` — renders the 50 most recent `quarantine_log` rows

### Users Panel (`ui_users.py`)

- `render_users()` — the Settings tab's Users sub-tab, admins only. One table row
  per login (username, role chip, active/disabled, last login in local time) with
  an Edit button, plus `+ Add`
- Collects input only. Every rule (password length, last active admin, no
  disabling yourself) is enforced by `auth.py`'s `create_user` / `change_role` /
  `reset_password` / `set_disabled`, and their `UserManagementError` message is
  what the dialog shows. Keeps the rules unit-tested, since `ui_*.py` is excluded
  from coverage
- `_add_user_dialog()` / `_edit_user_dialog(user)`. Edit changes role and password
  in one Save, and has a Disable/Enable button. The password pair is checked
  (match and length) **before** anything is saved, so a combined role and
  password edit can't save the role and then refuse the password
- Each dialog re-checks `is_admin()` itself: a dialog survives reruns, and the
  admin's own role could change from another session while it is open
- Editing your own account shows a warning first, because a role or password
  change bumps `session_version` and ends your current session too
- Dialog errors are drawn below the button row, not inside a button column,
  where they were squeezed to a third of the dialog width
- Success uses the stash-then-rerun pattern (`_PENDING_NOTICE_KEY`), the same as
  `ui_quarantine.py`, because `st.rerun()` wipes anything drawn in that run

### Viewer Read-Only Access (`ui_access.py`)

Viewers can see every tab but change nothing. Three layers:

1. **Disabled buttons + notice.** Every write trigger passes `disabled=read_only()`,
   and each tab calls `read_only_notice()`, a one-line caption shown to viewers
   only. Search, Refresh and "Check who's online" stay enabled, since they change
   nothing. `style.css` dims disabled buttons, because the `_chip*` colour rules
   otherwise override Streamlit's disabled look and a disabled red button still
   looked live
2. **Streamlit drops clicks on disabled widgets server-side.** Confirmed with
   `AppTest`: clicking a disabled button as a viewer never ran the handler. Not
   relied on, just observed
3. **Guards at every write.** Kea writes are refused by `ReadOnlyKeaClient`
   through `helpers.get_client()`, one choke point for every tab. Writes that
   bypass Kea call `refuse_if_viewer()` first: IPAM static entries, Quarantine
   registry save and delete, and the quarantine service triggers
   (`_trigger_action`, delete with auto-release). Reservation labels are only
   written after a Kea save succeeds, so the Kea guard covers them

Verified end to end against a fake Kea listener that logged every command: a
viewer session sent only read commands, and calling the write functions
directly as a viewer was refused with nothing reaching Kea or the quarantine
service.

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
      background loop. Called by `ui_quarantine.py` right after a device is added or
      edited, so Last MAC/Last IP/Last Seen don't sit blank until the loop's next pass.
      Returns `{"friendly_name": ..., "seen": bool}`
    - `POST /liveness-sweep` `{"ip_addresses": [...]}` — ARP-sweeps the supplied
      addresses via `liveness.sweep` and returns `{"checked": N, "responding": [...]}`
      (sorted, for a stable response). Stateless: writes nothing, and the addresses
      need not be registered devices. Backs the Leases tab's "Check who's online"
      button. Rejects more than `MAX_SWEEP_ADDRESSES` with HTTP 413 **at the route**,
      before a packet leaves the host
    - All five require `Authorization: Bearer <token>` via `require_bearer_token`
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
  arbitrary caller-supplied list of addresses and returns the subset that answered.
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

All endpoints require `Authorization: Bearer <QUARANTINE_API_TOKEN>`.

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
  ARP sweep of arbitrary addresses; returns `{"checked": N, "responding": [...]}`.
  Stateless — writes nothing and the addresses need not be registered devices.
  HTTP 413 above 1024 addresses.

## Architecture Decisions

**Direct Kea HTTP listener (not Control Agent)** — Kea 3.0 deprecated and removed the Control Agent. `kea-dhcp4` now exposes its own HTTP listener on port 8004. KeaNexus talks directly to it; the `"service"` forwarding key is never sent.

**CSS conic-gradient gauge (not SVG)** — Pool utilisation shown as a donut ring using `conic-gradient`. Simpler than SVG with equivalent visual quality; no JS required.

**SQLite for IPAM static records** — Local SQLite in a named Docker volume. Static IP records are KeaNexus-managed metadata (not sent to Kea); they exist purely for documentation/visibility of non-DHCP addresses.

**HTML tables (not st.dataframe)** — All data tables rendered as raw HTML to allow custom chip/badge formatting. Streamlit's native components don't support per-cell HTML.

**Planned: UI moves from Streamlit to FastAPI + HTMX** — Proposed, not built.
`docs/fastapi-htmx-migration.md` has the plan: why, the route map, auth and
CSRF, deployment beside the current UI on port 8503, and a phase-by-phase PR
order. Until cutover (phase 7), everything below about Streamlit stays true.
Check that doc before adding new Streamlit-only UI, which would only have to be
rebuilt.

**Streamlit is pinned, not floated** — `requirements.txt` pins `streamlit==1.66.1` (bumped from 1.62.0 because 1.62 through 1.66.0 cap `pillow<13`, and only Pillow 13 fixes the advisories pip-audit flagged; `pillow>=13.0.0` is listed as a floor so the fix can't silently regress). It previously allowed `>=1.40.0`, which let a container rebuild land on a Streamlit whose tabs are React Aria (`data-rac`, `react-aria-*` ids, a `.react-aria-SelectionIndicator` child per tab) while the local venv had one whose tabs are BaseWeb (`data-baseweb="tab-highlight"`). Different DOM, different CSS hooks — so `style.css` selectors verified locally silently matched nothing in production, and reading the local bundle to diagnose a live styling bug gave confidently wrong answers. This app styles Streamlit's internals heavily, so its DOM is effectively an API here: bump the pin deliberately and re-check `style.css`'s widget selectors when you do. For the 1.66.1 bump, every `data-testid` `style.css` targets was confirmed present in the new frontend bundle and tabs are still React Aria, but the sidebar's state attribute changed from `aria-expanded` to `data-collapsed`, which a test-id check cannot catch: the width pin silently stopped matching and the sidebar widened under the tab bar. `style.css` now targets both attributes. Check attributes, not just test ids, on the next bump; `stDecoration` and BaseWeb's `tab-highlight` match nothing in either version and are kept only as harmless fallbacks. `style.css` still targets both libraries' selection indicators, since that cost nothing.

**Chip labels are pinned to their own line box (`style.css`)** — inside `st.columns`, Streamlit 1.66.1 renders a button's label `<p>` inline inside a wrapper with a 22.4px line-height, so table-row chips drew their text about 10px from the top and 5px from the bottom. One shared rule gives every chip family's wrapper the chip's own line-height and makes the `<p>` a block. Re-check this on the next Streamlit bump along with the other widget selectors.

**Sidebar collapsible at every viewport (`style.css`)** — One behaviour, not two. This app hides Streamlit's whole `stHeader` bar, and Streamlit renders the re-open arrow inside it, so collapsing the sidebar originally left no control to bring it back; the first fix pinned the panel open, and a later one pinned it open on desktop only. Both were working around a hidden control rather than un-hiding it. Now both controls are always visible — the in-sidebar collapse button (Streamlit reveals it on hover only, which a touch screen never triggers and a desktop user rarely discovers) and the header's re-open arrow, exposed by rendering `stHeader` as a transparent, click-through strip with pointer events re-enabled on that one button. The header needs `background: transparent` and `pointer-events: none` forced, because Streamlit gives it an opaque background and `pointer-events: auto` exactly when the expand button is present, which would otherwise paint over and swallow clicks in the top ~60px of the page. `stToolbarActions`/`stStatusWidget`/`stAppDeployButton`/`stMainMenu` are hidden individually rather than by hiding the header, since the expand button sits in a separate branch of the header tree.

Two consequences worth knowing. The width pin is scoped to `[aria-expanded="true"]`: Streamlit collapses by animating `min-width`/`max-width` to `0` alongside a `translateX`, so an unconditional `!important` pin overrides the `0` and leaves the panel holding 210px of layout while sliding out of sight — a dead gutter, and a collapse that appears to do nothing. And the tab bar is `position: fixed`, so it cannot reflow with the content: its `left` offset follows the sidebar's state explicitly via `body:has([data-testid="stSidebar"][aria-expanded="false"])`, dropping to `0` with padding for the re-open arrow, or a collapsed sidebar leaves a 210px gap beside the tabs. The only genuinely phone-specific rule left is horizontal scrolling on the tab bar, since seven tabs never fit a ~390px screen. The bar is anchored at `top: 0` with its visual offset in `padding-top`, not at `top: 0.75rem`: the band above a top-offset bar is covered only by the transparent `stHeader`, so scrolled content showed through it.

**Pool history sampled in-process, with gaps left as gaps** — A utilisation trend needs data Kea does not keep, so KeaNexus samples it itself. The sampler is a background thread in the Streamlit container rather than a sample taken during page render, because rendering-time sampling only records while a browser is open and would draw a flat line across every overnight gap — a chart that lies. The tradeoff is that Streamlit does not execute `app.py` until a session connects, so sampling does not resume after a container restart until somebody opens KeaNexus; once started it runs for the process's life regardless of who is watching. The two alternatives were rejected deliberately: the quarantine service's existing always-on loop is behind an optional Compose profile, so a core tab's history would silently be empty for anyone not running it, and a dedicated sampler container is more infrastructure than a home deployment warrants. Because gaps are therefore expected, nothing interpolates across them — `get_pool_samples` returns exactly the readings taken.

**KeaNexus's modules are bind-mounted file-by-file** — `docker-compose.yml` lists each Python module as its own `./x:/app/x` mount (so `--server.runOnSave` picks up host edits), rather than mounting the directory. That drifts silently: a renamed module leaves a mount pointing at a path that no longer exists, and Docker creates a _directory_ there; a new module goes unmounted, so the container runs a stale copy from the image alongside current mounted modules — which is how `ui_pool.py` → `ui_dashboard.py` turned into an ImportError at container start with nothing in the suite noticing. `tests/test_docker_compose_mounts.py` now fails on either condition. Modules the KeaNexus container genuinely never imports are listed in that test's `_NOT_REQUIRED_IN_KEANEXUS` (currently just `pihole.py`, which exists at the repo root only so `quarantine_service` can import it via its own Dockerfile `COPY`).

**Secret scanning split across two hooks** - `pre-commit` checks only staged files with `detect-secrets-hook`, which fails only on a secret missing from `.secrets.baseline`. The earlier hook scanned the whole tree and demanded an exact match with the baseline, so any drift anywhere (a branch's test fixtures, or a known secret moving to a new line) blocked every commit, including ones that touched none of those files. `pre-push` keeps the whole-tree check as a backstop, comparing by file and secret hash rather than line number, so moved code does not false-alarm. pytest and coverage moved from `pre-commit` to `pre-push`, which keeps commits fast. Both hooks share one exclude pattern (`SECRETS_EXCLUDE`); keep it in sync with the command used to regenerate the baseline.

**All times UTC internally** — Kea lease expiry timestamps are Unix epoch. Display formatting converts to human-readable durations via `fmt_ttl()`. `quarantine_log.occurred_at` is likewise stamped as UTC ISO8601 in `db.py`, not left to the caller.

**Reservation labels decoupled from Kea's `hostname` field** — Kea's own docs confirm host-level reservation values (including `hostname`) always take priority: once a reservation sets `hostname`, Kea echoes that admin-typed string back on every lease for the device, permanently discarding whatever hostname the client itself requests. That made `device_registry` entries built from a reservation's hostname unable to ever match a live lease during quarantine identity resolution (`resolve_target` would raise `DeviceNotOnNetworkError` for a device that was clearly online), and made the Leases tab show a made-up label instead of the device's real name. Reservations created or edited via `ui_reservations.py` no longer send `hostname` to Kea at all — the admin-facing label lives in `db.reservation_labels` instead, keyed by MAC, so Kea always preserves the real DHCP-negotiated hostname on the lease. The tradeoff: a reservation's hostname can no longer drive Kea's own DDNS registration for that device, since Kea has nothing to register.

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
module (`--cov=.` in `pyproject.toml`, with `app.py` and `ui_*.py` explicitly omitted
since they require a live Streamlit server and can't be unit-tested). That includes
`auth.py`, `db.py`, `kea.py`, `helpers.py`, `pihole.py`, and everything in
`quarantine_service/`.

| File                                        | What is tested                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/test_auth.py`                        | First-admin bootstrap; login (case-insensitive, wrong password, unknown and disabled users, corrupt hash); `current_user`/`is_admin` including sessions ended by password change, disable and deletion; signed cookie round trip, stale `session_version`, tampered username, wrong secret, malformed values, no secret; user-management rules (validation, duplicates, last-admin and self-disable protection)                                                                                                                                                                                                                                                                             |
| `tests/test_db_users.py`                    | `users` table storage: case-insensitive usernames, duplicate rejection, role CHECK, list omits hashes, active-admin count, `session_version` bumps on every change, `record_login`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `tests/test_db.py`                          | Full CRUD on `ipam_static`, `reservation_labels`, `device_registry`, and `quarantine_log` via `temp_db` fixture (SQLite in tmp dir)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `tests/test_kea.py`                         | All `KeaClient` methods; `ReadOnlyKeaClient` passes every read command and refuses every write and unknown command before any request; `httpx` patched via `http_mock` fixture                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `tests/test_helpers.py`                     | `get_client` (admins get `KeaClient`, viewers `ReadOnlyKeaClient`), `fmt_ttl`, `chip`, `leases_to_df`, `build_reservation_type_sets`, `lease_type`, `build_hostname_override_sets`, `real_hostname`, `distinct_real_hostnames`, `lease_for_reservation` (pure functions only)                                                                                                                                                                                                                                                                                                                                                                                                               |
| `tests/test_dashboard_data.py`              | `lease_expiry_buckets` (bucket boundaries, expired leases, declined excluded, missing `valid-lft`), `lease_composition`, `utilisation_points` (gap breaking, declined counted as used, zero total), `leases_issued_since` (cumulative delta, stale baseline rejected, counter reset, insufficient history), `quarantined_count`                                                                                                                                                                                                                                                                                                                                                             |
| `tests/test_version.py`                     | `APP_VERSION` is plain `MAJOR.MINOR.PATCH`, since the sidebar renders it as `v` + the value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `tests/test_docs_library.py`                | Article allowlist (listed files exist, developer docs excluded, missing file hidden) and `read_article` rejecting path traversal, absolute paths and unlisted names                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `tests/test_ui_docs_render.py`              | Headless render via `AppTest` — one button per article, clicking one opens the dialog with the article's body, nothing opens without a click                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `tests/test_docker_compose_mounts.py`       | Every `./x:/app/x` mount source in `docker-compose.yml` exists on disk (except gitignored runtime-state mounts in `_RUNTIME_STATE_MOUNTS`, currently `./data`, which a fresh clone doesn't have; a guard test checks each one really is gitignored), and every repo-root module is mounted (minus a checked exclusion list) — catches a rename or addition that would leave the container running stale code                                                                                                                                                                                                                                                                                |
| `tests/test_ui_quarantine_render.py`        | Headless render via `AppTest` — renders with devices registered and with an empty registry, row buttons present, a quarantined row uses the alert colour, and a malformed `last_seen_at` doesn't crash the table                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `tests/test_ui_leases_render.py`            | Headless render via `AppTest` — the tab draws, the sweep button is present, no row is green before a sweep, clicking sweeps every lease address, a responding row gets the live cell style and a non-responding one doesn't, the caption reports the count, and a `QuarantineServiceError` is shown rather than raised; `trigger_liveness_sweep` always patched                                                                                                                                                                                                                                                                                                                             |
| `tests/test_ui_dashboard_render.py`         | Headless render via Streamlit's `AppTest` — page executes without raising, every section reaches the output, each chart has a table-view expander, missing stats shows an error not a crash, and an empty `pool_samples` degrades to a caption                                                                                                                                                                                                                                                                                                                                                                                                                                              |
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
| `tests/test_quarantine_main.py`             | FastAPI routes via `TestClient` — auth enforcement, 404/409 error mapping, all four enforcement steps wired correctly, group requests, pre-fire safety check skips a device whose identity drifted between resolution and enforcement, `/presence-check/{friendly_name}`, `is_quarantined`/`last_quarantined_at` stamping on quarantine vs. release; `_human_summary` sentence construction (single/multiple devices, partial failure, identity drift, inconclusive fingerprint) and its presence in both endpoints' responses; `/liveness-sweep` auth, sorted responders, passthrough to `sweep`, distinct-address `checked` count, empty request, and over-cap rejection without sweeping |
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

**Streamlit cached loaders** (`load_leases`, `load_pool_stats`, `load_config`, `load_status` in `helpers.py`) are not tested — they require a `ScriptRunContext` that only exists inside a running Streamlit app.

## Validation and Error Handling Standards

- UI components catch all errors and display via `st.error()`
- Internal modules raise `KeaError` for Kea API failures; callers handle at the tab level
- IPAM edit form validates: IP must be in subnet, must be out-of-scope, must not be network/broadcast
- DB functions assert IP is a non-empty string; SQLite PRIMARY KEY enforces uniqueness
- `upsert_device` asserts friendly_name and hostname are non-empty; friendly_name is the SQLite PRIMARY KEY
- `insert_quarantine_log_entry` asserts friendly_name is non-empty, action is `"quarantine"` or `"release"`, and attempt_count is at least 1
