# KeaNexus Session Notes — 2026-06-19

## Goal

Restore the Streamlit sidebar collapse/expand arrow (the ‹ › chevron) which had been hidden by our aggressive chrome-removal CSS rules.

## What We Know About the Sidebar Arrow

- The sidebar toggle button is rendered inside `[data-testid="stAppViewContainer"]`
- Tom identified the element as `stAppViewContainer` via browser inspector
- The existing CSS rule trying to restore it was:
    ```css
    [data-testid="collapsedControl"],
    [data-testid="baseButton-headerNoPadding"] {
    	display: flex !important;
    }
    ```
- This rule was NOT working — the arrow was still missing

## What We Tried

Added broader selectors to `style.css`:

```css
[data-testid="collapsedControl"],
[data-testid="baseButton-headerNoPadding"],
[data-testid="stAppViewContainer"] > section > div > button,
[data-testid="stAppViewContainer"] button[aria-label="Close sidebar"],
[data-testid="stAppViewContainer"] button[aria-label="Open sidebar"] {
	display: flex !important;
	visibility: visible !important;
	opacity: 1 !important;
}
```

## What Went Wrong

- The CSS change was committed and pushed to git
- The revert was applied locally on Mac and pushed, BUT the LXC container was rebuilt with the bad CSS still in it
- The container came up **unhealthy** — healthcheck failing with `curl: not found`
- App returns 200 on LXC localhost:8502 but unreachable from browser
- Session ended with container in broken state, app inaccessible

## Current State (end of session)

- `style.css` on Mac/git = **reverted** (back to original 2-selector rule)
- `style.css` in running container = **bad version** (broad selectors)
- Container status = **unhealthy** (curl not found in healthcheck — pre-existing issue)
- App = **unreachable** from browser despite 200 on localhost:8502
- NPM is pointed at port **8502**

## TODO for Next Session

1. Get the container running clean with reverted CSS:
    ```bash
    cd /root/keanexus && git fetch origin && git reset --hard origin/main && docker compose up --build -d
    ```
2. Verify app is reachable from browser at keanexus.cyberwraith.net
3. Fix the healthcheck — `curl` is not installed in the container image, use `wget` or a Python-based check instead
4. Then tackle the sidebar arrow properly — need Tom to inspect the exact DOM element in browser and report the actual `data-testid` Streamlit is rendering for the toggle button on this version
5. Apply a surgical CSS fix targeting only that specific testid

## Key Facts to Remember

- Container name: `keanexus`
- Image: `keanexus-keanexus`
- Port mapping: `0.0.0.0:8502->8501/tcp`
- LXC repo path: `/root/keanexus`
- Mac repo path: `~/Coding/keanexus`
- Correct redeploy: `docker compose up --build -d`
- Correct git sync on LXC: `git fetch origin && git reset --hard origin/main`
- NPM proxies `keanexus.cyberwraith.net` → `172.16.17.215:8502`
