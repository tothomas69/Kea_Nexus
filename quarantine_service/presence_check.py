"""
presence_check.py — Periodic ARP presence probe for device_registry.

Independent of the quarantine/release enforcement flow: identity.py only
refreshes last_seen_mac_address/last_seen_ip_address/last_seen_at when a
device is actually quarantined or released, which for most registered
devices never happens. This module runs on a timer for every registered
device regardless of quarantine state, and stamps last_seen_at whenever
the device answers a real ARP request — this is what actually powers the
"Last Seen" column in the KeaNexus UI.

Design note: this sends a genuine ARP "who-has" request and listens for a
real reply (scapy srp — send AND receive), unlike arp_disrupt.py's sendp,
which fires spoofed replies and never listens. Presence checking and
disruption are opposite operations that happen to share a library.
"""

import logging
import os
import threading
from typing import Optional

from scapy.all import ARP, Ether, srp

from db import get_devices, touch_last_seen
from kea import KeaClient, KeaError

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 300.0  # 5 minutes
DEFAULT_PROBE_TIMEOUT_SECONDS = 2.0

_stop_event = threading.Event()


def start_presence_check_loop() -> None:
	"""Start the background probe thread. Call once, from FastAPI startup.

	Runs as a daemon thread so it never blocks process shutdown. There's
	deliberately no stop_presence_check_loop() counterpart to arp_disrupt's
	stop function — this loop is meant to run for the lifetime of the
	container, not be started/stopped per device.
	"""
	# `.get(..., default)` only falls back when the key is fully absent —
	# a blank `PRESENCE_CHECK_INTERVAL_SECONDS=` line in .env still sets it
	# to "", which would otherwise reach float("") and crash on startup.
	interval = float(os.environ.get("PRESENCE_CHECK_INTERVAL_SECONDS") or DEFAULT_INTERVAL_SECONDS)
	thread = threading.Thread(target=_loop, args=(interval,), daemon=True, name="presence-check")
	thread.start()
	logger.info("Presence check loop started, interval=%.0fs", interval)


def _loop(interval_seconds: float) -> None:
	while not _stop_event.is_set():
		try:
			_run_one_pass()
		# Broad except deliberate: one bad device (Kea hiccup, malformed
		# lease, a single scapy send failure) shouldn't kill the whole
		# background loop for every other device — log it and move on,
		# the next pass tries again.
		except Exception:
			logger.exception("Presence check pass failed")
		_stop_event.wait(interval_seconds)


def _run_one_pass() -> None:
	"""Probe every registered device once, stamping last_seen_at for any
	that currently have a live Kea lease AND answer the ARP request.

	A device with no current lease is skipped outright — nothing to send
	the probe to, and identity.py already treats "no lease" as "not on
	the network" for the same reason.
	"""
	kea = KeaClient()
	interface = os.environ.get("ARP_INTERFACE") or None
	for device in get_devices():
		try:
			leases = kea.get_leases_by_hostname(device["hostname"])
		except KeaError:
			logger.warning("Kea unreachable during presence check for %s", device["friendly_name"])
			continue
		if not leases:
			continue

		ip_address = leases[0].get("ip-address", "") or ""
		mac_address = leases[0].get("hw-address", "") or ""
		if not ip_address:
			continue

		if _probe(interface, ip_address):
			touch_last_seen(device["friendly_name"], mac_address, ip_address)


def _probe(
	interface: Optional[str], target_ip: str, timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS
) -> bool:
	"""Send a real ARP who-has request for target_ip and return True if
	anything answers within `timeout` seconds.
	"""
	packet = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(op=1, pdst=target_ip)
	answered, _unanswered = srp(packet, iface=interface, timeout=timeout, verbose=False)
	return len(answered) > 0
