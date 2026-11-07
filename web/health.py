"""
health.py — The header's service health dots.

Each check answers one question, "is this service answering?", quickly and
without credentials:

- Kea: version-get through the read-only client.
- keanexus-quarantine: its unauthenticated GET /health.
- Pi-hole primary and secondary: any HTTP response from the instance at all,
  even a 401, means it is up. No password is needed, so the web container
  never holds Pi-hole credentials. Shown only when PIHOLE_API_URL /
  PIHOLE_SECONDARY_API_URL are set for this container.
"""

import os
from typing import Optional

import httpx

from kea import KeaClient

_CHECK_TIMEOUT_SECONDS = 2.0
_QUARANTINE_URL_ENV = "QUARANTINE_SERVICE_URL"
_PIHOLE_URL_ENVS = (("Pi-hole 1", "PIHOLE_API_URL"), ("Pi-hole 2", "PIHOLE_SECONDARY_API_URL"))
_HTTP_SERVER_ERROR = 500


def _answers(url: str, require_success: bool) -> bool:
	try:
		response = httpx.get(url, timeout=_CHECK_TIMEOUT_SECONDS)
	except httpx.HTTPError:
		return False
	if require_success:
		return response.is_success
	return response.status_code < _HTTP_SERVER_ERROR


def _kea_entry(kea: KeaClient) -> dict:
	dhcp4 = kea.get_status()["dhcp4"]
	version = dhcp4.version if dhcp4.up and dhcp4.version not in ("ok", "—") else ""
	name = f"Kea {version}" if version else "Kea"
	return {"name": name, "up": dhcp4.up}


def service_health(kea: KeaClient) -> list[dict]:
	"""[{"name", "up"}] for every service this deployment knows about."""
	entries = [_kea_entry(kea)]
	for name, env_name in _PIHOLE_URL_ENVS:
		base_url: Optional[str] = os.environ.get(env_name)
		if base_url:
			entries.append(
				{"name": name, "up": _answers(base_url.rstrip("/") + "/api/auth", False)}
			)
	quarantine_url = os.environ.get(_QUARANTINE_URL_ENV)
	if quarantine_url:
		entries.append(
			{
				"name": "Quarantine svc",
				"up": _answers(quarantine_url.rstrip("/") + "/health", True),
			}
		)
	return entries
