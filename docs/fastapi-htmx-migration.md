# Design Doc — Moving the KeaNexus UI from Streamlit to FastAPI + HTMX

Status: **proposed**. Nothing here is built yet. Each phase below ships as its own PR.

Target design: the "B2 · Clean Light, refined" artboard on the KeaNexus design canvas.

## Why

The B2 design needs things Streamlit cannot do well:

- a hover card on each of the 256 address squares
- click-to-pin, with actions in the pinned card
- quarantine switches that change state without a full page rerun
- a ⌘K search box and a Help panel

In Streamlit every click reruns the whole script and redraws the page. We
already pay for that today:

- `style.css` overrides Streamlit's own markup, which changes between releases.
  That is why Streamlit is pinned, and it caused the Settings tab and chip
  bugs fixed in PRs #42–#44.
- `auth.py` and `app.py` carry cookie workarounds that exist only because
  `CookieManager` acts one run late.
- `init_db()` and the pool sampler do not start until a browser connects.
- `ui_*.py` cannot be unit tested, so it is excluded from coverage.

## Decision

- **Server:** FastAPI, served by uvicorn. We already run FastAPI for
  `keanexus-quarantine`, so the patterns and test tools are the same.
- **Pages:** Jinja2 templates rendered on the server.
- **Partial updates:** HTMX. A switch, a form or a poll swaps one fragment of
  HTML from the server. No JSON API layer is needed for the UI itself.
- **In-page behaviour:** Alpine.js, for the hover card, pinning, the Help
  panel, ⌘K and dialogs.
- **Charts:** vega-embed. `dashboard_data.py` and the Altair specs carry over;
  Altair already outputs Vega-Lite.
- **No Node build step.** HTMX, Alpine.js and vega-embed are vendored into
  `web/static/vendor/` at pinned versions, with their hashes recorded in
  `web/static/vendor/VERSIONS.md`. No CDN: the app must work when the WAN is down,
  which is exactly when you are debugging DHCP.

**Rejected: React or Svelte.** Either would mean a JavaScript build chain and a
second codebase. That is not worth it for a single-operator tool.

## What carries over unchanged

`kea.py`, `db.py`, `pihole.py`, `quarantine_client.py`, `dashboard_data.py`,
`docs_library.py`, `pool_history.py`, `version.py`, and all of
`quarantine_service/`. Their tests carry over too.

## What changes

| Today | After |
| --- | --- |
| `app.py` (Streamlit entry, tabs, sidebar) | `web/main.py` (FastAPI app, lifespan) + `web/templates/base.html` |
| `ui_*.py` (one per tab) | `web/routes/<screen>.py` + `web/templates/<screen>/` |
| `style.css` (overrides Streamlit internals) | `web/static/app.css` (our own markup, no overrides) |
| `helpers.py` `@st.cache_*` | `web/cache.py`, a small TTL cache |
| `auth.py` session in `st.session_state` | the signed cookie, checked on every request |
| `ui_access.py` (`read_only()`, `refuse_if_viewer()`) | FastAPI dependencies `current_user`, `require_admin` |
| Sidebar Documentation expander | Help menu item that opens a docs table of contents |
| IPAM tab | merged into the Overview address map; static entries are edited from the pinned card |

### Auth

`auth.py` already has the right pieces. `session_token()` signs
`username|session_version`, and `restore_session_from_cookie()` checks it. The
change is to stop storing the user in `st.session_state` and instead:

- set the cookie in the login response (`HttpOnly`, `Secure`, `SameSite=Lax`)
- resolve it on every request in a `current_user` dependency, which re-checks
  `session_version` exactly as today

The CookieManager workaround and the one-run-late cookie writes go away
completely.

**CSRF:** the app is one login away from the internet, so every POST, PUT or
DELETE needs a CSRF token. It is a double-submit token: a cookie, echoed back
in an `X-CSRF-Token` header, which HTMX sends from `hx-headers` on `<body>`.
`SameSite=Lax` alone is not enough.

**Read-only viewers:** `get_client()` still hands viewers a
`ReadOnlyKeaClient`. That stays the real guard. Every write route also depends
on `require_admin`, which replaces `refuse_if_viewer()`.

### Startup

`init_db()`, `bootstrap_admin_from_env()` and `start_pool_sampler()` move into
the FastAPI lifespan, so they run when the container starts. This removes the
known limitation that sampling does not resume until someone opens the page.

**During the overlap only one UI may sample.** Both containers write the same
`pool_samples` table, so two samplers would record every reading twice. Compose
sets `KEANEXUS_WEB_BACKGROUND_WORK=off` for `keanexus-web` until cutover, when it
flips on and Streamlit goes away. (Found while building phase 0.)

## Address map hover data

On page load the Overview route renders all 256 cells and embeds their details
as one JSON object (label, hostname, MAC, lease, last seen, status). Alpine.js
shows the hover card from that object, so hovering makes **no** requests. The
pinned card's Edit and Quarantine buttons use HTMX against the routes below.
The address map is re-rendered every 30 s with `hx-trigger="every 30s"`.

## New data B2 needs that does not exist yet

These need a decision before their phase starts:

1. **Activity feed.** `quarantine_log` covers quarantine and release.
   Reservation, static-entry and DHCP enable/disable changes are not logged
   anywhere. Proposal: a new append-only `activity_log` table, written by the
   web app's write routes. New leases are not logged: Kea does not report them
   as events, and polling for differences is noisy.
2. **Service health dots.**
   - Kea: `get_status()`, already there.
   - Pi-hole ×2: *(settled in phase 1)* no credentials needed after all. Any
     HTTP answer from the instance, even a 401, proves it is up, so the web
     container only needs `PIHOLE_API_URL` / `PIHOLE_SECONDARY_API_URL`, never
     the password.
   - Quarantine service: new `GET /health` on `keanexus-quarantine`, with no
     auth and no data, returning `{"ok": true}`.
3. **"Online now" KPI.** Today the ARP sweep only runs when someone presses the
   button. Proposal: a background sweep every 5 min from the web app, through
   `trigger_liveness_sweep`, with the result cached in memory. The KPI shows
   "—" when the quarantine profile is not running.

## Routes

All routes are HTML. Fragment routes return a partial for HTMX. All need a
signed-in user. Routes marked **admin** also depend on `require_admin`.

| Route | Purpose |
| --- | --- |
| `GET /login`, `POST /login`, `POST /logout` | sign in and out |
| `GET /` | Overview: KPIs, address map, quarantine card, activity, charts |
| `GET /fragments/address-map` | address map refresh (30 s poll) |
| `GET /fragments/health` | header health dots (30 s poll) |
| `POST /address/{ip}/static` **admin** | create or edit a static entry (out-of-scope IPs only) |
| `DELETE /address/{ip}/static` **admin** | delete a static entry |
| `GET /quarantine` | registry, group actions, audit log |
| `POST /quarantine/{name}` · `POST /quarantine/{name}/release` **admin** | quarantine or release one device |
| `POST /devices/{name}/quarantine` · `.../release` **admin** | the Quarantine screen's per-device actions |
| `POST /groups/{tag}/quarantine` · `.../release` **admin** | group actions |
| `GET /devices/new` · `GET /devices/{name}` · `POST /devices` · `PUT`/`DELETE /devices/{name}` **admin** | registry CRUD (moved out of `/quarantine/...` in phase 2: `POST /quarantine/{name}` would have collided with a device called "devices") |
| `GET /leases` | leases table, filter, search by MAC or hostname |
| `POST /leases/sweep` | "Check who's online" (allowed for viewers, as today) |
| `POST /leases`, `PUT /leases/{ip}` **admin** | add or edit a lease |
| `GET /reservations` · `POST`/`PUT`/`DELETE /reservations/{mac}` **admin** | reservations + labels |
| `GET /maintenance` · `POST /maintenance/dhcp/enable` · `.../disable` · `POST /maintenance/wipe` **admin** | maintenance |
| `GET /settings` · `PUT /settings/dhcp` **admin** | DHCP settings |
| `GET /settings/users` · `POST`/`PUT /settings/users/{username}` **admin** | users |
| `GET /help` · `GET /help/{article}` | docs table of contents and article (allowlist in `docs_library.py`) |
| `GET /search?q=` | ⌘K results: IP, MAC, label, hostname |
| `GET /healthz` | container healthcheck |

## Deployment

- New compose service `keanexus-web`, built from the same image. It runs
  `uvicorn web.main:app --host 0.0.0.0 --port 8000` and is published on host
  port **8503**.
- It shares the `./data` volume. SQLite is already shared with
  `keanexus-quarantine`, so a third reader and writer on the same file is fine
  at this scale. Writes stay short.
- The `web/` package is mounted **as a directory** (`./web:/app/web`), not file
  by file. `tests/test_docker_compose_mounts.py` gets updated to expect that.
- **Both UIs run side by side** until cutover: Streamlit on its current port,
  the new UI on 8503. Nginx Proxy Manager keeps pointing at Streamlit until the
  last phase.
- The healthcheck moves from `/_stcore/health` to `/healthz` at cutover.

## Testing

- **Routes:** FastAPI `TestClient` with the existing stubs
  (`stub_kea_client`, `temp_db`, `quarantine_client_http_mock`). Assert on
  status codes, the HTML returned, and what was written.
- **Coverage:** `web/` is measured. The `ui_*.py` omit goes away with those
  files.
- **Auth and CSRF:** every write route is tested three ways: as a viewer
  (refused), with no CSRF token (refused), and as an admin (allowed).
- **Hover data:** a unit test on the function that builds the 256-cell
  object, so the hover card can never drift from the map.

## Phases

Each phase is one PR. The order puts the most-used screen first and leaves the
old UI working the whole time.

0. **Groundwork.** Decouple `auth.py` and `helpers.py` from Streamlit, with a
   thin adapter so the old UI keeps working. Add the `web/` skeleton: lifespan,
   login, base layout with the header and logo, Help panel, CSRF, vendored
   assets, the `keanexus-web` compose service. Add `GET /health` to the
   quarantine service.
1. **Overview.** KPIs with the pool bar, the address map with hover and pin,
   the quarantine card, health dots. Activity uses `quarantine_log` only until
   decision 1 is made. *(Done. The charts were split out to keep this PR
   reviewable.)*
   - **1b. Charts.** The utilisation trend, expiry and composition charts from
     `ui_dashboard.py`, via vendored vega-embed. *(Done. The builders moved to
     `dashboard_charts.py`, shared by both UIs, which also fixed the trend
     axis showing UTC as local time.)*
2. **Quarantine.** *(Done.)*
3. **Leases**, with the background liveness sweep (decision 3). *(Done. The sweep has its own switch, `KEANEXUS_WEB_LIVENESS_SWEEP`, and runs during the overlap, since Streamlit has no background sweep to collide with. The "Online now" figure sits in the Active leases tile rather than a fifth tile.)*
4. **Reservations.** *(Done. Edits keep fields the form doesn't show, writes read the config fresh, and delete refuses a quarantined device; see the as-built guide.)*
5. **Maintenance.** *(Done. DHCP state is now read from Kea's `status-get`, not remembered in the session. The by-MAC/hostname lookup was dropped, since the Leases filter covers it.)*
6. **Settings + Users.** *(Done. Users sit at `/settings/users`; DHCP saves are applied to a fresh config.)*
7. **Cutover.** Point NPM at `keanexus-web`, then remove `app.py`, `ui_*.py`,
   `style.css`, `.streamlit/`, the Streamlit pin, `extra-streamlit-components`,
   pandas (if nothing else needs it), and the Streamlit compose service.
   ⌘K search lands here, or right after.

## Risks

- **Scope.** This rewrites about 4,100 lines of UI. Each phase is sized to
  ship on its own, and nothing is lost if work stops after any phase.
- **Two UIs writing the same Kea config** during the overlap. That is no worse
  than today, where two browser tabs can do it. Kea's `config-set` is the
  single writer either way.
- **Session cookie change.** The new UI's cookie has a different name and
  path, so the two UIs never read each other's cookies. Everyone signs in once
  more at cutover.
