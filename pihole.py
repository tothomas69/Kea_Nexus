"""
pihole.py — Pi-hole v6 REST API client.

Talks to Pi-hole's v6 API (session-based auth, not the old v5 api.php +
static token scheme). Used by quarantine_service/pihole_block.py for DNS
blocking; not currently wired into the KeaNexus dashboard itself, but kept
at the repo root alongside kea.py in case a future KeaNexus tab wants
Pi-hole status — same shared-module pattern as kea.py.

Built against Pi-hole's documented v6 REST API and current community
references, not against a live instance. Verify the /clients and /groups
request/response shapes against this Pi-hole's own self-hosted docs at
http://pi.hole/api/docs before relying on this in production — the v6 API
is newer and some client-management edge cases are still under discussion
upstream (see FTL issue #1943).

Environment variables:
  PIHOLE_API_URL      — base URL to the Pi-hole instance (default http://172.16.17.212)
  PIHOLE_API_PASSWORD — Pi-hole admin password or an application password
"""

import os
import time
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()


class PiholeError(Exception):
	"""Raised when a Pi-hole API call fails or the server is unreachable."""


class PiholeClient:
	"""
	Synchronous client for Pi-hole's v6 REST API.

	Session-based: the first authenticated call triggers a login (POST
	/api/auth) that returns a session ID (sid) and CSRF token (csrf). Both
	are cached and reused until the session's stated validity window is
	about to expire, at which point the client re-authenticates
	automatically — callers never need to think about the session.
	"""

	# Refresh this many seconds before the session's actual expiry, so a
	# request that starts just before expiry doesn't get cut off mid-flight.
	_EXPIRY_SAFETY_MARGIN_SECONDS = 30

	def __init__(self) -> None:
		base = os.getenv("PIHOLE_API_URL", "http://172.16.17.212").rstrip("/")
		self.base_url = base + "/api"
		self.password = os.getenv("PIHOLE_API_PASSWORD", "")
		self._timeout = 8.0
		self._sid: Optional[str] = None
		self._csrf: Optional[str] = None
		self._sid_expires_at = 0.0

	def _authenticate(self) -> None:
		"""Log in and cache the session ID and CSRF token."""
		try:
			with httpx.Client(timeout=self._timeout) as client:
				resp = client.post(f"{self.base_url}/auth", json={"password": self.password})
			resp.raise_for_status()
		except httpx.ConnectError as exc:
			raise PiholeError(f"Cannot reach Pi-hole at {self.base_url}") from exc
		except httpx.HTTPStatusError as exc:
			raise PiholeError(f"Pi-hole returned HTTP {exc.response.status_code} on auth") from exc

		session = resp.json().get("session", {})
		if not session.get("valid"):
			raise PiholeError(
				f"Pi-hole authentication failed: {session.get('message', 'unknown error')}"
			)

		self._sid = session["sid"]
		self._csrf = session["csrf"]
		validity_seconds = session.get("validity", 300)
		self._sid_expires_at = time.time() + validity_seconds - self._EXPIRY_SAFETY_MARGIN_SECONDS

	def _ensure_authenticated(self) -> None:
		if self._sid is None or time.time() >= self._sid_expires_at:
			self._authenticate()

	def request(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
		"""
		Issue an authenticated request against the Pi-hole API and return
		the parsed JSON body (empty dict for 204/empty responses).

		CSRF token is only attached for state-changing methods, matching
		Pi-hole's double-submit CSRF scheme — GET requests don't need it.
		"""
		self._ensure_authenticated()
		headers = {"X-FTL-SID": self._sid}
		if method.upper() in ("POST", "PUT", "DELETE", "PATCH"):
			headers["X-FTL-CSRF"] = self._csrf

		try:
			with httpx.Client(timeout=self._timeout) as client:
				resp = client.request(
					method, f"{self.base_url}{path}", json=json_body, headers=headers
				)
			resp.raise_for_status()
		except httpx.ConnectError as exc:
			raise PiholeError(f"Cannot reach Pi-hole at {self.base_url}") from exc
		except httpx.HTTPStatusError as exc:
			raise PiholeError(
				f"Pi-hole returned HTTP {exc.response.status_code}: {exc.response.text}"
			) from exc

		if not resp.content:
			return {}
		return resp.json()
