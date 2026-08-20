"""
nmap_fingerprint.py — OS fingerprint refresh via nmap.

Runs `nmap -O` against a device's current IP and persists the best-guess OS
match into device_registry.os_fingerprint, so a future MAC change still has
a durable identity signal to fall back on (see identity.py). Shells out to
the nmap binary and parses its XML output — nmap has no meaningful pure-
Python equivalent for real OS fingerprinting, so this is a subprocess call
by necessity. (This is the reverse of the ARP tool decision, which chose
scapy specifically to avoid shelling out — here nmap's actual OS-detection
engine is the thing needed, not just packet crafting.)

Requires the nmap package to be present in the container image and the
container to have CAP_NET_RAW/CAP_NET_ADMIN, same as the ARP disruption
action — OS fingerprinting crafts unusual packets that need raw socket
access. Running as root inside the container (the default, since no USER
is set in the Dockerfile) satisfies this without any extra nmap-side
capability configuration.
"""

import logging
import subprocess
import xml.etree.ElementTree as ElementTree

from db import get_device, upsert_device

logger = logging.getLogger(__name__)

_SCAN_TIMEOUT_SECONDS = 45
_HOST_TIMEOUT = "30s"


def refresh_os_fingerprint(friendly_name: str, target_ip: str) -> str:
	"""Run an nmap OS scan against target_ip and persist the result.

	Returns the best-guess OS name, or "" if the scan completed but nmap
	couldn't confidently identify the OS — that's a legitimate outcome, not
	a failure, so it's returned rather than raised. A subprocess failure
	(missing binary, timeout, nonzero exit) does raise, so the retry
	wrapper in main.py has something to retry against.
	"""
	fingerprint = _run_nmap_os_scan(target_ip)
	_persist_fingerprint(friendly_name, fingerprint)
	return fingerprint


def _run_nmap_os_scan(target_ip: str) -> str:
	"""Run nmap -O and return the top-accuracy OS match name, or "" if inconclusive."""
	result = subprocess.run(
		[
			"nmap",
			"-O",
			"--osscan-guess",
			"-T4",
			f"--host-timeout={_HOST_TIMEOUT}",
			"-oX",
			"-",
			target_ip,
		],
		capture_output=True,
		text=True,
		timeout=_SCAN_TIMEOUT_SECONDS,
		check=True,
	)
	return _parse_top_os_match(result.stdout)


def _parse_top_os_match(nmap_xml: str) -> str:
	"""Extract the highest-accuracy osmatch name from nmap's XML output."""
	root = ElementTree.fromstring(nmap_xml)
	os_element = root.find("host/os")
	if os_element is None:
		return ""

	best_match_name = ""
	best_accuracy = -1
	for match in os_element.findall("osmatch"):
		accuracy = int(match.get("accuracy", "0"))
		if accuracy > best_accuracy:
			best_accuracy = accuracy
			best_match_name = match.get("name", "")

	return best_match_name


def _persist_fingerprint(friendly_name: str, fingerprint: str) -> None:
	"""Write the new fingerprint into device_registry, preserving other fields.

	Skips the write entirely if fingerprint is empty (an inconclusive
	scan) — better to keep the last known good fingerprint than overwrite
	it with nothing over a single bad scan.
	"""
	if not fingerprint:
		return

	device = get_device(friendly_name)
	if device is None:
		logger.warning("Cannot persist fingerprint: '%s' is not in device_registry", friendly_name)
		return

	upsert_device(
		device["friendly_name"],
		device["hostname"],
		group_tag=device["group_tag"],
		os_fingerprint=fingerprint,
		last_seen_mac_address=device["last_seen_mac_address"],
		last_seen_ip_address=device["last_seen_ip_address"],
		last_seen_at=device["last_seen_at"],
		last_quarantined_at=device["last_quarantined_at"],
		is_quarantined=bool(device["is_quarantined"]),
		notes=device["notes"],
	)
