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
├── helpers.py          — Cached data loaders, format utilities
├── db.py               — SQLite persistence layer (IPAM static records)
├── ui_login.py         — Login page: logo, username/password form
├── ui_pool.py          — Pool tab: utilisation gauge, service health, lease summary
├── ui_leases.py        — Leases tab: HTML table with type classification
├── ui_ipam.py          — IPAM tab: full /24 subnet map + static entry management
├── ui_reservations.py  — Reservations tab: Kea config CRUD
├── ui_maintenance.py   — Maintenance tab: DHCP enable/disable, wipe leases
├── ui_settings.py      — Settings tab
├── style.css           — Global CSS overrides for Streamlit internals
├── Makefile            — Developer setup (`make setup`) and test runner (`make test`)
├── static/             — Static assets (keanexus_logo.png)
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

## API Endpoints

All data flows through the Kea `kea-dhcp4` direct HTTP listener (port 8004). No Control Agent.

Key Kea commands used:

- `lease4-get-all` → all active leases
- `stat-lease4-summary` → pool utilisation counters
- `config-get` / `config-set` / `config-write` → full DHCPv4 config
- `version-get` → service health check and daemon version
- `dhcp-enable` / `dhcp-disable` → DHCP service control

## Architecture Decisions

**Direct Kea HTTP listener (not Control Agent)** — Kea 3.0 deprecated and removed the Control Agent. `kea-dhcp4` now exposes its own HTTP listener on port 8004. KeaNexus talks directly to it; the `"service"` forwarding key is never sent.

**CSS conic-gradient gauge (not SVG)** — Pool utilisation shown as a donut ring using `conic-gradient`. Simpler than SVG with equivalent visual quality; no JS required.

**SQLite for IPAM static records** — Local SQLite in a named Docker volume. Static IP records are KeaNexus-managed metadata (not sent to Kea); they exist purely for documentation/visibility of non-DHCP addresses.

**HTML tables (not st.dataframe)** — All data tables rendered as raw HTML to allow custom chip/badge formatting. Streamlit's native components don't support per-cell HTML.

**All times UTC internally** — Kea lease expiry timestamps are Unix epoch. Display formatting converts to human-readable durations via `fmt_ttl()`.

## Testing

Unit tests live in `tests/`. Coverage is measured only over the four testable back-end modules (`auth.py`, `db.py`, `kea.py`, `helpers.py`). Streamlit UI files (`app.py`, `ui_*.py`) are excluded from coverage — they require a live Streamlit server and cannot be unit-tested.

| File                    | What is tested                                                       |
| ----------------------- | -------------------------------------------------------------------- |
| `tests/test_auth.py`    | `is_authenticated`, `attempt_login` (all credential paths), `logout` |
| `tests/test_db.py`      | Full CRUD on `ipam_static` via `temp_db` fixture (SQLite in tmp dir) |
| `tests/test_kea.py`     | All `KeaClient` methods; `httpx` patched via `http_mock` fixture     |
| `tests/test_helpers.py` | `fmt_ttl`, `chip`, `leases_to_df` (pure functions only)              |

`conftest.py` provides two shared fixtures: `temp_db` (redirects `_DB_PATH` to a temp file) and `http_mock` (patches `kea.httpx.Client` so no real HTTP calls are made).

**Streamlit cached loaders** (`load_leases`, `load_pool_stats`, `load_config`, `load_status` in `helpers.py`) are not tested — they require a `ScriptRunContext` that only exists inside a running Streamlit app.

## Validation and Error Handling Standards

- UI components catch all errors and display via `st.error()`
- Internal modules raise `KeaError` for Kea API failures; callers handle at the tab level
- IPAM edit form validates: IP must be in subnet, must be out-of-scope, must not be network/broadcast
- DB functions assert IP is a non-empty string; SQLite PRIMARY KEY enforces uniqueness
