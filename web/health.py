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

Pi-hole URLs are read the way people actually write them: with or without
"http://", with or without a trailing "/api", and over https with Pi-hole v6's
self-signed certificate. Any of those used to read as "down" while the Pi-hole
was serving DNS happily. A failed check is logged with its reason, so
`docker logs keanexus` says why a dot is red.
"""

import logging
import os
from typing import Optional

import httpx

from kea import KeaClient

logger = logging.getLogger(__name__)

_CHECK_TIMEOUT_SECONDS = 2.0
# Pi-hole's web server answers slower than Kea under load (gravity updates,
# FTL restarts), and a slow answer is still an answer.
_PIHOLE_CHECK_TIMEOUT_SECONDS = 5.0
_DEFAULT_SCHEME = "http://"
_API_SUFFIX = "/api"
_QUARANTINE_URL_ENV = "QUARANTINE_SERVICE_URL"
_PIHOLE_URL_ENVS = (("DNS1", "PIHOLE_API_URL"), ("DNS2", "PIHOLE_SECONDARY_API_URL"))
_HTTP_SERVER_ERROR = 500


def _answers(
	url: str,
	require_success: bool,
	timeout_seconds: float = _CHECK_TIMEOUT_SECONDS,
	verify_tls: bool = True,
) -> bool:
	try:
		response = httpx.get(url, timeout=timeout_seconds, verify=verify_tls)
	except httpx.HTTPError as error:
		logger.warning("Health check for %s failed: %s", url, error)
		return False
	if require_success:
		return response.is_success
	return response.status_code < _HTTP_SERVER_ERROR


def pihole_auth_url(base_url: str) -> str:
	"""The URL of Pi-hole's /api/auth, however the base URL was written."""
	assert base_url.strip(), "Expected a non-empty Pi-hole URL"
	url = base_url.strip().rstrip("/")
	if "://" not in url:
		url = _DEFAULT_SCHEME + url
	if url.endswith(_API_SUFFIX):
		url = url[: -len(_API_SUFFIX)]
	return url + _API_SUFFIX + "/auth"


def _pihole_answers(base_url: str) -> bool:
	# verify_tls is off because Pi-hole v6 ships a self-signed certificate and
	# this probe sends nothing secret: no password, no cookie, no body. All it
	# learns is whether something answers HTTP at that address.
	return _answers(
		pihole_auth_url(base_url),
		require_success=False,
		timeout_seconds=_PIHOLE_CHECK_TIMEOUT_SECONDS,
		verify_tls=False,
	)


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
		if base_url and base_url.strip():
			entries.append({"name": name, "up": _pihole_answers(base_url)})
	quarantine_url = os.environ.get(_QUARANTINE_URL_ENV)
	if quarantine_url:
		entries.append(
			{
				"name": "Quarantine svc",
				"up": _answers(quarantine_url.rstrip("/") + "/health", True),
			}
		)
	return entries
