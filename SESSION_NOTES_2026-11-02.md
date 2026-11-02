# Session Notes - 2026-11-02

Shipped to production (netstack, `main` at `079b1a1`, app v1.5.0):
Streamlit 1.66.1, Pillow 13.0.0, all four PRs below.

## PRs merged

| PR  | Branch                             | What                                                                 |
| --- | ---------------------------------- | -------------------------------------------------------------------- |
| #38 | `feat/ipam-label-columns`          | IPAM: Label column, Hostname renamed Real Hostname, Expires column   |
| #39 | `fix/githooks-secrets-scope`       | pre-commit scans staged files only; new `.githooks/pre-push` gate    |
| #40 | `chore/streamlit-1.66.1-pillow-13` | Streamlit 1.62.0 -> 1.66.1, `pillow>=13.0.0`, sidebar attribute fix  |
| #41 | `fix/tab-bar-top-gap`              | Content no longer shows through the band above the tab bar on scroll |

## IPAM columns (#38)

- Order: IP Address, Status, Label, Real Hostname, MAC, Expires, Edit.
- **Label** is any admin-assigned name: reservation label (`db.reservation_labels`,
  legacy Kea `hostname` fallback), static entry hostname, and `gateway`.
- **Real Hostname** is only `helpers.real_hostname()` from a live lease. Same split
  as the Reservations tab.
- **Expires** replaced "Description". That column was really lease TTL for leases
  and description for statics. Static description and notes now show in the
  status-chip tooltip (HTML-escaped).
- Rebased onto v1.5.0; kept the viewer read-only guards (`disabled=viewer`).

## Git hooks (#39)

- `core.hooksPath=.githooks`, so **nothing in `.git/hooks/` ever ran**, including
  the old pre-push gate and Bandit. CLAUDE.md now describes the real layout.
- The old pre-commit compared a whole-tree secrets scan to the baseline for an
  exact match, so any baseline drift blocked every commit, even unrelated ones.
- Now: pre-commit runs `detect-secrets-hook` on staged files only. pre-push runs
  pytest + coverage, a full-tree scan compared by file + secret hash (not line
  number), and pip-audit (warn only). pytest moved out of pre-commit.
- `git rebase --continue` does **not** run pre-commit. Run tests yourself after a
  rebase.

## Streamlit 1.66.1 + Pillow 13 (#40)

- pip-audit flagged 39 advisories on pillow 12.3.0; fixed in 13.0.0.
- Streamlit 1.62 through 1.66.0 cap `pillow<13`. 1.66.1 is the first to allow it.
- **Regression found:** 1.66.1 renamed the sidebar's state attribute from
  `aria-expanded` to `data-collapsed`. Every `data-testid` still existed, so a
  test-id check passed, but the 210px width pin stopped matching. The sidebar
  widened to ~300px and covered the first tab. `style.css` now targets both.
- Lesson for the next bump: check **attributes**, not just test ids.

## Tab-bar band (#41)

- Pre-existing on 1.62 too. Bar sat at `top: 0.75rem`; the band above it was
  covered only by the transparent `stHeader`. Bar is now `top: 0` with the offset
  in `padding-top`.

## Repo housekeeping

- Local `feat/multi-user` was obsolete (merged into v1.5.0) and is deleted. Its
  upstream had been set to `origin/main`, so a bare `git push` would have
  pushed to `main`. Check `git branch -vv` on old branches.
- Two docs commits (`7bdccaa`, `7c5d36c`) show as Unverified on GitHub. Docs only.

## Gotchas hit today

- **Single-file bind mounts and replaced files.** When `style.css` was replaced
  (new inode) rather than edited in place, the container first served the old
  copy, then reported the file missing. `docker compose up -d --force-recreate
keanexus` fixed it both times. Likely root cause of the earlier "styling
  change not visible after refresh" mystery.
- **App is container-only.** `db.py` hardcodes `/app/data/keanexus.db`, so
  `streamlit run app.py` on the Mac fails. Test locally with
  `docker compose up --build keanexus` (port 8502).
- On netstack the repo is `/root/keanexus`, not `~`.

## Open follow-ups (one PR each)

1. Mount directories instead of single files in `docker-compose.yml` (fixes the
   bind-mount gotcha above; update `tests/test_docker_compose_mounts.py`).
2. `KEANEXUS_DB_PATH` env override in `db.py` so the app runs outside Docker.
3. Replace FastAPI `@app.on_event("startup")` in `quarantine_service/main.py`
   with a `lifespan` handler (deprecation warning).
4. Wire Bandit, import-linter and Vale into `.githooks/`.
5. Optional: `docker builder prune` on netstack (~3.5 GB reclaimable cache).
