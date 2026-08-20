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
	token = _token()
	if not token:
		raise QuarantineServiceError(
			"QUARANTINE_SERVICE_TOKEN is not configured in KeaNexus's own .env"
		)

	try:
		with httpx.Client(timeout=_PRESENCE_CHECK_TIMEOUT_SECONDS) as client:
			resp = client.post(
				f"{_base_url()}/presence-check/{friendly_name}",
				headers={"Authorization": f"Bearer {token}"},
			)
		resp.raise_for_status()
	except httpx.ConnectError as exc:
		raise QuarantineServiceError(f"Cannot reach keanexus-quarantine at {_base_url()}") from exc
	except httpx.HTTPStatusError as exc:
		detail = _error_detail(exc)
		raise QuarantineServiceError(f"HTTP {exc.response.status_code}: {detail}") from exc

	return resp.json()


def _base_url() -> str:
	return os.getenv("QUARANTINE_SERVICE_URL", "http://localhost:8600").rstrip("/")


def _token() -> str:
	return os.getenv("QUARANTINE_SERVICE_TOKEN", "")


def _call(path: str, target: str, is_group: bool) -> dict:
	token = _token()
	if not token:
		raise QuarantineServiceError(
			"QUARANTINE_SERVICE_TOKEN is not configured in KeaNexus's own .env"
		)

	try:
		with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
			resp = client.post(
				f"{_base_url()}{path}",
				json={"target": target, "is_group": is_group},
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
