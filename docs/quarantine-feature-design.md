# Design Doc — Device Quarantine Feature

> Status: implemented (PRs 1–6 complete). Companion to `docs/prd.md` and
> `docs/siri-shortcut-setup.md`. Read this alongside
> `docs/as-built-project-guide.md` before extending anything here.

## Problem

Reservations in Kea are keyed on MAC address. A device that changes its MAC
address to dodge a reservation-based block simply falls outside that
reservation. We need a way to identify a device by something harder to
change (hostname + OS fingerprint), and to enforce a block across three
independent layers at once so a MAC change alone can't restore access:

1. Kea DHCP — deny new leases to the device
2. ARP — disrupt the device's current session on the LAN
3. Pi-hole — block DNS resolution for the device

Trigger is voice, via a Siri Shortcut, so the whole sequence needs to be
reachable as a single authenticated API call.

## Why This Can't Live Inside KeaNexus As-Is

KeaNexus is a Streamlit app — it has no route layer, so it can't expose a
webhook endpoint for Siri to call. This needs a small standalone service.
Proposed name: **`keanexus-quarantine`**, a separate FastAPI app, same repo,
own container, shares the KeaNexus SQLite volume (new tables, doesn't touch
`ipam_static`). KeaNexus keeps owning the dashboard; the quarantine service
owns the API and orchestration. KeaNexus gets a new UI tab that reads the
same tables read-only, to view/manage the registry and see quarantine
history without needing to hit the API.

**Decided: separate container.** Reasoning:

- Streamlit has no supported route layer — no ASGI mount point, no route
  decorator. Running FastAPI + Streamlit as two processes in one container
  means fighting Docker's one-process-per-container model (needs a
  supervisor, mixed stdout logs, ambiguous healthcheck/restart semantics).
- **Privilege separation.** ARP poisoning needs `CAP_NET_RAW`. KeaNexus
  itself needs zero elevated privileges — it only makes HTTP calls to Kea
  and Pi-hole. Bundling them would give the day-to-day dashboard container
  raw-socket capability it has no other reason to carry.
- **Network placement.** ARP poisoning must run on the same L2 segment as
  client devices, unrouted. The quarantine service can live wherever that
  access already exists (likely `netstack`) independent of wherever the
  KeaNexus dashboard container runs.
- **Blast radius / testability.** A bug in the scapy/nmap orchestration
  shouldn't be able to take the DHCP dashboard down with it, and a
  standalone FastAPI service is trivially testable with `httpx`'s test
  client, same pattern as the existing `http_mock` fixture for `kea.py`.
- Shared SQLite access is a volume mount either way, so splitting
  containers costs nothing there.

**Optional at install time.** The new service is gated behind a Docker
Compose profile (`profiles: ["quarantine"]` in `docker-compose.yml`), so it
never builds or starts unless someone explicitly runs
`docker compose --profile quarantine up` (or sets `COMPOSE_PROFILES=quarantine`
in `.env`). Anyone who only wants the DHCP dashboard is unaffected.

## Data Model (new tables, same SQLite DB)

```sql
CREATE TABLE device_registry (
	friendly_name       TEXT PRIMARY KEY,   -- "tommy_laptop"
	hostname             TEXT NOT NULL,
	group_tag            TEXT NOT NULL DEFAULT '',   -- "test_group_1", enables batch ops
	os_fingerprint        TEXT NOT NULL DEFAULT '',   -- last nmap -O signature
	last_seen_mac_address TEXT NOT NULL DEFAULT '',
	last_seen_ip_address  TEXT NOT NULL DEFAULT '',
	last_quarantined_at   TEXT NOT NULL DEFAULT '',   -- ISO8601 UTC
	notes                 TEXT NOT NULL DEFAULT ''
);

CREATE TABLE quarantine_log (
	log_id            INTEGER PRIMARY KEY AUTOINCREMENT,
	friendly_name      TEXT NOT NULL,
	action             TEXT NOT NULL,   -- "quarantine" | "release"
	step               TEXT NOT NULL,   -- "arp" | "pihole" | "kea" | "nmap_refresh"
	succeeded          INTEGER NOT NULL,   -- 0/1
	attempt_count       INTEGER NOT NULL,
	detail             TEXT NOT NULL DEFAULT '',
	occurred_at        TEXT NOT NULL   -- ISO8601 UTC
);
```

`quarantine_log` is what makes retries and idempotency legible later — if
the same device keeps cycling MACs and re-triggering, this table is where
that pattern shows up.

## Identity Resolution

Reservations and ARP/MAC data are treated as disposable, not authoritative.
Source of truth for "who is this" is `device_registry`, keyed on
`friendly_name`.

Resolve order on every trigger:

1. Look up `friendly_name` (or every row matching `group_tag`) in
   `device_registry` → get `hostname` and last-known `os_fingerprint`
2. Query current Kea leases for a lease matching that `hostname`
   → this gives the live `ip_address` and current `mac_address`, whatever
   it currently is
3. If the hostname alone matches but seems ambiguous or spoofed (i.e.
   another registry entry could also match), re-run an nmap OS scan
   against the candidate IP and use `os_fingerprint` as the tiebreaker
4. Refresh `last_seen_mac_address` / `last_seen_ip_address` in the
   registry regardless of outcome — these are breadcrumbs, not identity

## Trigger / API

```
POST /quarantine   { "target": "tommy_laptop" }        -- single device
POST /quarantine   { "target": "test_group_1", "is_group": true }
POST /release       { "target": "tommy_laptop" }
GET  /status/{friendly_name}
```

Auth: static bearer token from an env var (`QUARANTINE_API_TOKEN`),
checked with `hmac.compare_digest` the same way `auth.py` does for
KeaNexus login. Siri Shortcut stores the token and sends it as an
`Authorization: Bearer` header. This is the piece that actually matters
most from a security standpoint — the endpoint is only as safe as that
token, so it needs to not be logged anywhere and to be rotatable without
a code change.

## Orchestration (per resolved device)

Runs all three enforcement actions together, not as a fallback chain:

1. **Kea deny** — `config-set` a `DROP`-class host reservation (decided
   below) for the current MAC, `config-write` to persist
2. **ARP disruption** — send poisoned ARP replies for the device's current
   IP on the LAN segment

    **Decided during implementation: sustained loop, not a one-shot burst.**
    ARP cache entries expire (typically under a minute) and get relearned
    whenever the target does a fresh resolution, so a single burst only
    disrupts the session for a short window before the OS relearns the real
    gateway MAC — unlike the Kea deny, which is a durable state change. The
    quarantine service runs a background thread per device
    (`arp_disrupt.py`) that keeps re-sending every 2 seconds until `/release`
    explicitly stops it. Trade-off accepted knowingly: these loops live in
    the service's process memory only, so a `keanexus-quarantine` container
    restart silently drops every active loop with no auto-resume —
    `/quarantine` has to be called again after a restart. The Kea deny and
    Pi-hole block are unaffected by a restart since both are durable state
    on the far end (Kea's config, Pi-hole's client group), not something the
    quarantine service itself has to keep re-asserting.

3. **Pi-hole block** — call the Pi-hole API to add the device's current IP
   to a block group
4. **nmap OS fingerprint refresh** — runs after the above three, updates
   `os_fingerprint` in the registry for next time

Each of steps 1–3 retries up to 3 times with backoff on failure; failures
after 3 attempts are logged to `quarantine_log` with `succeeded = 0` and
surfaced in the KeaNexus dashboard, not rolled back automatically — partial
success is visible and retriable rather than silently undone.

Immediately before firing, re-check that the resolved IP still matches the
hostname/fingerprint — closes the race where the device hopped IPs in the
seconds between the voice command and script execution.

`/release` reverses all three: removes the Kea deny, clears the ARP
poisoning, removes the Pi-hole block group entry. Same retry/logging
behavior.

## Open Decisions Before Writing Code

1. **Deployment shape** — resolved above ("Why This Can't Live Inside
   KeaNexus As-Is"): separate container, gated behind a compose profile.
2. **Decided: `scapy`.** Reasoning:
    - **In-process, not shelled out.** `arpspoof` is a compiled binary
      invoked via `subprocess` — every action means building a command
      string, spawning a process, and parsing stdout/stderr for success.
      `scapy` builds and sends ARP packets directly from Python, so the ARP
      step is a function call with a normal return value like any other
      step in the orchestration.
    - **Testability matches the coverage gate.** `test_kea.py` already
      mocks `kea.httpx.Client` via the `http_mock` fixture so tests never
      touch the real network. Scapy packets are plain Python objects, so
      `scapy.sendp` can be patched the same way — real assertions on real
      packet contents, no root, no NIC, no network namespace needed in CI.
      Testing a shelled-out `arpspoof` meaningfully would need a real
      network namespace, or reduces to asserting the subprocess call
      string looked right without exercising real logic.
    - **Fits the required control flow.** The design needs a safety check
      immediately before firing (re-verify IP still matches
      hostname/fingerprint) plus per-attempt retry/backoff logged to
      `quarantine_log`. Scapy gives full control over exactly when a
      packet goes out and what happens on failure. `arpspoof` is built to
      be started once and left running on an internal timer — awkward to
      wrap in a fire-once/verify/retry sequence.
    - **No extra OS package.** `scapy` is a pip dependency like everything
      else in `requirements.txt`. `arpspoof` (from `dsniff`) is an
      apt-installed OS package in the image — one more thing to pin and
      track for CVEs, on top of the existing Gitea 1.25.3 backlog.
    - Counter-case noted for the record: `arpspoof` is a small, extremely
      well-worn tool that's done one job reliably for 20+ years. Not chosen
      here because the retry/safety-check/logging requirements fit scapy's
      control model better, not because arpspoof is unreliable.
3. **Decided: built-in `DROP` class**, not a fixed-unreachable-IP
   reservation. Kea has a purpose-built mechanism for this: any client
   added to the special `DROP` class (via a host reservation matching its
   MAC, `"client-classes": ["DROP"]`, no `ip-address`) has its DHCP
   traffic silently ignored — no offer, no NAK. `keanexus-quarantine`
   upserts this reservation against whatever MAC the identity-resolution
   step just found live for the target hostname.

    Rejected alternative: reserving the MAC to a deliberately unreachable
    IP (out-of-subnet or a TEST-NET range). The device gets a real lease
    and believes it's online; the failure mode if the throwaway IP is ever
    miscalculated or later collides with a real allocation is handing out
    a _working_ address to a device meant to be blocked. DROP has no
    equivalent unsafe-failure path — worst case is no lease at all, which
    is the correct default. Side note: after enough failed DHCP attempts
    some OSes self-assign a 169.254.x.x link-local address, but that has
    no gateway/DNS/off-segment reach, which is exactly why ARP + Pi-hole
    still matter here rather than being pure redundancy on the Kea deny.

4. **Decided: `netstack` (LXC 108), as a third service in Kea's existing
   Docker networking setup, with `network_mode: host` on the quarantine
   container specifically.** Kea's own container already runs on host
   networking — confirmed via `docker inspect --format
'{{.HostConfig.NetworkMode}}'` — which makes sense in hindsight: DHCP
   relies on L2 broadcast (DHCPDISCOVER), and Docker's default bridge NATs
   traffic and never forwards broadcast frames into a container, so Kea
   could not be serving real leases today without already having real L2
   access. The quarantine container inherits nothing automatically from
   sibling containers, though — KeaNexus's own `docker-compose.yml` uses a
   published-port bridge network (`8502:8501`), not host mode, proof that
   containers on the same LXC don't share a network mode by default. The
   new compose service needs its own explicit `network_mode: host` (or at
   minimum `cap_add: [NET_RAW, NET_ADMIN]` on a macvlan) set deliberately,
   not inherited.
5. **PR/branch split** — this is genuinely more than one PR under
   `CLAUDE.md`'s "keep PRs focused" rule. Actual split, all complete:
    - PR 1 (done): `device_registry` + `quarantine_log` schema, KeaNexus
      read-only UI tab
    - PR 2 (done): `keanexus-quarantine` service scaffold + auth + identity
      resolution (no enforcement actions yet, just resolves and logs)
    - PR 3 (done): Kea deny action — also wired `/release` for the Kea step
      at the same time, since deny/undo-deny share one code path
    - PR 4 (done): ARP disruption action (sustained loop, see above)
    - PR 5 (done): Pi-hole block action + nmap fingerprint refresh
    - PR 6 (done): pre-fire safety check (`verify_identity_unchanged`) +
      Siri Shortcut setup. `/release` itself needed no new PR by this point
      since PRs 3–5 already wired both directions of every step as they
      landed — see `docs/siri-shortcut-setup.md` for the Shortcut config

## Out of Scope (for now)

- Alexa integration — dropped in favor of Siri Shortcuts only
- Automatic rollback on partial failure — logged and manually retried instead
