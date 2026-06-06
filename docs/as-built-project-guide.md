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
├── auth.py             — Authentication: env-var credential check, session state
├── kea.py              — Kea Control Agent HTTP client
├── helpers.py          — Cached data loaders, format utilities
├── db.py               — SQLite persistence layer (IPAM static records)
├── ui_login.py         — Login page: logo, username/password form
├── ui_pool.py          — Pool tab: utilisation gauge, service health, lease summary
├── ui_leases.py        — Leases tab: HTML table with type classification
├── ui_ipam.py          — IPAM tab: full /24 subnet map + static entry management
├── ui_reservations.py  — Reservations tab: Kea config CRUD
├── ui_maintenance.py   — Maintenance tab: DHCP enable/disable, wipe leases
├── style.css           — Global CSS overrides for Streamlit internals
├── static/             — Static assets (keanexus_logo.png)
├── docs/               — PRD and this guide
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

- `KeaClient` — synchronous HTTP client to Kea Control Agent
- Key methods: `get_leases()`, `get_pool_stats()`, `get_config()`, `save_config()`, `get_status()`, `get_pool_range(config)`
- All commands proxied through CA at `KEA_CA_URL` (env var)

### Helpers (`helpers.py`)

- `get_client()` — `@st.cache_resource` singleton KeaClient
- `load_leases/load_pool_stats/load_config/load_status` — `@st.cache_data` with TTL
- `fmt_ttl(seconds)` — formats seconds to "Xh Ym" or "expired"

### Database (`db.py`)

- SQLite at `/app/data/keanexus.db`
- Table: `ipam_static` — static IP records (ip_address PK, hostname, mac_address, description, notes)
- `init_db()` — idempotent schema creation, called at app startup
- `get_static_entries()`, `get_static_entry(ip)`, `upsert_static_entry(...)`, `delete_static_entry(ip)`

### Pool Tab (`ui_pool.py`)

- `render_pool(stats, config, status)` — 3-column card layout
- `_gauge(pct, used, total, avail)` — CSS conic-gradient donut ring; green <75%, amber 75–90%, red ≥90%
- `_health_row(label, detail, up)` — service health row with status badge
- `_metric_row(label, value)` — lease summary metric row

### IPAM Tab (`ui_ipam.py`)

- `render_ipam(leases, config)` — full /24 subnet map (256 addresses)
- `_classify_all(...)` — classifies each IP as: network, broadcast, gateway, leased, declined, reserved, static, scope (free in pool), or free
- Status priority: network/broadcast > gateway > active lease > reservation > static DB entry > in-scope free > free
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

## API Endpoints

All data flows through the Kea Control Agent REST API (no direct endpoints exposed by KeaNexus itself).

Key Kea commands used:

- `lease4-get-all` → all active leases
- `stat-lease4-summary` → pool utilisation counters
- `config-get` / `config-set` / `config-write` → full DHCPv4 config
- `version-get` → service health check
- `dhcp-enable` / `dhcp-disable` → DHCP service control

## Architecture Decisions

**CSS conic-gradient gauge (not SVG)** — Pool utilisation shown as a donut ring using `conic-gradient`. Simpler than SVG with equivalent visual quality; no JS required.

**SQLite for IPAM static records** — Local SQLite in a named Docker volume. Static IP records are KeaNexus-managed metadata (not sent to Kea); they exist purely for documentation/visibility of non-DHCP addresses.

**HTML tables (not st.dataframe)** — All data tables rendered as raw HTML to allow custom chip/badge formatting. Streamlit's native components don't support per-cell HTML.

**All times UTC internally** — Kea lease expiry timestamps are Unix epoch. Display formatting converts to human-readable durations via `fmt_ttl()`.

## Validation and Error Handling Standards

- UI components catch all errors and display via `st.error()`
- Internal modules raise `KeaError` for Kea API failures; callers handle at the tab level
- IPAM edit form validates: IP must be in subnet, must be out-of-scope, must not be network/broadcast
- DB functions assert IP is a non-empty string; SQLite PRIMARY KEY enforces uniqueness
