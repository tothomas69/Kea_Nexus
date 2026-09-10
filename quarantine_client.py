"""
quarantine_client.py — Thin client for KeaNexus itself to call the sibling
keanexus-quarantine service's API (the Quarantine/Release buttons in
ui_quarantine.py).

This is a distinct concern from kea.py/pihole.py, which talk to Kea and
Pi-hole directly — this talks sideways to KeaNexus's own sibling service.
Kept as its own thin module, matching the same encapsulation pattern, so
the HTTP concern stays out of the UI layer.

Environment variables:
  QUARANTINE_SERVICE_URL   — base URL of keanexus-quarantine, e.g.
                             http://<netstack-lan-ip>:8600. NOT localhost —
                             keanexus-quarantine runs with network_mode:
                             host while KeaNexus itself is on the default
                             bridge network, so "localhost" from inside the
                             KeaNexus container means its own network
                             namespace, not the Docker host.
  QUARANTINE_SERVICE_TOKEN — must match QUARANTINE_API_TOKEN in
                             quarantine_service/.env exactly.
"""

import os
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

# A single quarantine call can legitimately take a while: each of the four
# enforcement steps retries up to 3 times with a 2s backoff, and the nmap
# step alone can take up to ~45s per attempt if a scan hangs. Generous
# timeout so a slow-but-working retry sequence doesn't look like a client
# timeout from KeaNexus's side.
_REQUEST_TIMEOUT_SECONDS = 180.0

# A presence check is a single ARP probe (2s server-side timeout) plus one
# Kea lease lookup — nowhere near as slow as full enforcement, so it gets
# its own much shorter timeout rather than inheriting the 180s above.
_PRESENCE_CHECK_TIMEOUT_SECONDS = 10.0

# A sweep is one batched ARP send plus a single server-side wait for
# replies, regardless of how many addresses were asked about — so it needs
# only a little more headroom than that wait, not the enforcement timeout.
_LIVENESS_SWEEP_TIMEOUT_SECONDS = 30.0


class QuarantineServiceError(Exception):
	"""Raised when a call to keanexus-quarantine fails or is unreachable."""


def trigger_quarantine(target: str, is_group: bool = False) -> dict:
	"""Call POST /quarantine on the keanexus-quarantine service."""
	return _call("/quarantine", target, is_group)


def trigger_release(target: str, is_group: bool = False) -> dict:
	"""Call POST /release on the keanexus-quarantine service."""
	return _call("/release", target, is_group)


def trigger_presence_check(friendly_name: str) -> dict:
	"""Call POST /presence-check/{friendly_name} on the keanexus-quarantine
	service — an immediate ARP probe, bypassing that service's background
	5-minute loop, so a freshly added/edited device doesn't sit with blank
	Last MAC/Last IP/Last Seen in the Quarantine tab until the loop's next pass.
	"""
	return _post(f"/presence-check/{friendly_name}", None, _PRESENCE_CHECK_TIMEOUT_SECONDS)


def trigger_liveness_sweep(ip_addresses: list[str]) -> list[str]:
	"""Call POST /liveness-sweep and return the subset of `ip_addresses` that
	answered an ARP probe.

	Backs the Leases tab's "Check who's online" button. The sweep has to run
	in keanexus-quarantine rather than here because that service uses
	network_mode: host and so shares the LAN's L2 segment, while KeaNexus
	sits on the default bridge network where ARP reaches nothing — see
	quarantine_service/liveness.py.
	"""
	if not ip_addresses:
		return []

	payload = _post(
		"/liveness-sweep", {"ip_addresses": ip_addresses}, _LIVENESS_SWEEP_TIMEOUT_SECONDS
	)
	return payload.get("responding", [])


def fetch_orphaned_enforcement() -> dict:
	"""Call GET /orphans — enforcement with no quarantined registry owner.

	Backs the Quarantine tab's orphan banner. Read-only and quick (one Kea
	config read plus one client list per Pi-hole), so it gets the short
	timeout rather than the enforcement one.
	"""
	return _get("/orphans", _PRESENCE_CHECK_TIMEOUT_SECONDS)


def _base_url() -> str:
	return os.getenv("QUARANTINE_SERVICE_URL", "http://localhost:8600").rstrip("/")


def _token() -> str:
	return os.getenv("QUARANTINE_SERVICE_TOKEN", "")


def _call(path: str, target: str, is_group: bool) -> dict:
	return _post(path, {"target": target, "is_group": is_group}, _REQUEST_TIMEOUT_SECONDS)


def _get(path: str, timeout_seconds: float) -> dict:
	"""GET from the quarantine service and return the decoded JSON body.

	Separate from _post rather than a method parameter on it: every other
	endpoint here is a POST that changes something, and keeping the read-only
	call visibly distinct makes it obvious at the call site which is which.
	"""
	return _request("GET", path, None, timeout_seconds)


def _post(path: str, json_body: Optional[dict], timeout_seconds: float) -> dict:
	"""POST to the quarantine service and return the decoded JSON body.

	Every endpoint here needs the same four things — the bearer token, the
	base URL, an unreachable-service error and an HTTP-error detail parse —
	so they live here once rather than once per endpoint wrapper.
	"""
	return _request("POST", path, json_body, timeout_seconds)


def _request(method: str, path: str, json_body: Optional[dict], timeout_seconds: float) -> dict:
	"""Issue an authenticated request and return the decoded JSON body.

	Every endpoint here needs the same four things — the bearer token, the
	base URL, an unreachable-service error and an HTTP-error detail parse —
	so they live here once rather than once per endpoint wrapper.
	"""
	token = _token()
	if not token:
		raise QuarantineServiceError(
			"QUARANTINE_SERVICE_TOKEN is not configured in KeaNexus's own .env"
		)

	try:
		with httpx.Client(timeout=timeout_seconds) as client:
			# Dispatched rather than sent via client.request(): a GET carries no
			# body at all, and calling the verb-specific method keeps that
			# distinction explicit instead of relying on json=None to mean it.
			if method == "GET":
				resp = client.get(
					f"{_base_url()}{path}",
					headers={"Authorization": f"Bearer {token}"},
				)
			else:
				resp = client.post(
					f"{_base_url()}{path}",
					json=json_body,
					headers={"Authorization": f"Bearer {token}"},
				)
		resp.raise_for_status()
	except httpx.ConnectError as exc:
		raise QuarantineServiceError(f"Cannot reach keanexus-quarantine at {_base_url()}") from exc
	except httpx.HTTPStatusError as exc:
		detail = _error_detail(exc)
		raise QuarantineServiceError(f"HTTP {exc.response.status_code}: {detail}") from exc

	return resp.json()


def _error_detail(exc: httpx.HTTPStatusError) -> str:
	if not exc.response.content:
		return exc.response.text
	try:
		return exc.response.json().get("detail", exc.response.text)
	except ValueError:
		return exc.response.text
