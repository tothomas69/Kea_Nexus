"""
arp_disrupt.py — ARP disruption enforcement action.

Continuously sends spoofed ARP replies to a target device, telling it the
network gateway lives at a bogus, unowned MAC address (a "blackhole").
Unlike the Kea deny, this isn't a durable state change — the target's ARP
cache naturally re-learns the real gateway MAC within roughly a minute if
nothing keeps re-poisoning it. So this action runs as a background loop per
device rather than a single fire-and-forget packet burst, and keeps running
until explicitly stopped via stop_arp_disruption() (called from /release).

Known limitation: loops are tracked in this process's memory only. If the
keanexus-quarantine container restarts while a device is under quarantine,
the loop is gone and nothing restarts it automatically — /quarantine has to
be called again. See docs/quarantine-feature-design.md.

Known risk: some managed switches pair DHCP snooping with Dynamic ARP
Inspection (DAI), which validates ARP traffic against the DHCP snooping
binding table and can silently drop exactly the spoofed packets this module
sends. If disruption doesn't appear to be working, check whether DAI is
enabled on the switch port this service's host is connected to.
"""

import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

from scapy.all import ARP, Ether, sendp

logger = logging.getLogger(__name__)

# Locally-administered, unowned MAC — the "02:" prefix marks it as locally
# administered so it's never confused with a real vendor-assigned address
# in an ARP table during debugging. Frames sent here go nowhere.
BLACKHOLE_MAC = "02:00:00:00:00:00"

DEFAULT_SEND_INTERVAL_SECONDS = 2.0
_STOP_JOIN_TIMEOUT_SECONDS = 5.0


@dataclass
class _DisruptionHandle:
	thread: threading.Thread
	stop_event: threading.Event


_active_disruptions: dict[str, _DisruptionHandle] = {}
_registry_lock = threading.Lock()


def start_arp_disruption(
	friendly_name: str,
	target_ip: str,
	target_mac: str,
	gateway_ip: str,
	send_interval_seconds: float = DEFAULT_SEND_INTERVAL_SECONDS,
) -> None:
	"""Start (or restart) a background ARP-poisoning loop for a device.

	Idempotent: calling this again for a friendly_name that already has an
	active loop stops the old one first, so a re-quarantine after an IP
	change doesn't leave two loops fighting each other.
	"""
	stop_arp_disruption(friendly_name)

	stop_event = threading.Event()
	interface = os.environ.get("ARP_INTERFACE") or None
	thread = threading.Thread(
		target=_disruption_loop,
		args=(target_ip, target_mac, gateway_ip, interface, send_interval_seconds, stop_event),
		daemon=True,
		name=f"arp-disrupt-{friendly_name}",
	)

	with _registry_lock:
		_active_disruptions[friendly_name] = _DisruptionHandle(thread, stop_event)
	thread.start()


def stop_arp_disruption(friendly_name: str) -> None:
	"""Stop a device's ARP-poisoning loop, if one is running. No-op otherwise.

	Raises TimeoutError if the loop doesn't stop within
	_STOP_JOIN_TIMEOUT_SECONDS, so the retry wrapper in main.py has
	something meaningful to retry against.
	"""
	with _registry_lock:
		handle = _active_disruptions.pop(friendly_name, None)
	if handle is None:
		return

	handle.stop_event.set()
	handle.thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
	if handle.thread.is_alive():
		raise TimeoutError(f"ARP disruption loop for '{friendly_name}' did not stop in time")


def is_disrupting(friendly_name: str) -> bool:
	"""True if a device currently has an active ARP-poisoning loop."""
	with _registry_lock:
		return friendly_name in _active_disruptions


def _disruption_loop(
	target_ip: str,
	target_mac: str,
	gateway_ip: str,
	interface: Optional[str],
	send_interval_seconds: float,
	stop_event: threading.Event,
) -> None:
	while not stop_event.is_set():
		try:
			send_poisoned_arp_reply(interface, target_ip, target_mac, gateway_ip)
		# A single dropped packet shouldn't kill the watchdog loop — log
		# and keep going, the next iteration will just try again.
		except Exception:
			logger.exception("ARP poison send failed for target %s", target_ip)
		stop_event.wait(send_interval_seconds)


def send_poisoned_arp_reply(
	interface: Optional[str], target_ip: str, target_mac: str, spoofed_ip: str
) -> None:
	"""Unicast an ARP reply to target_mac claiming spoofed_ip is at BLACKHOLE_MAC.

	Sent as a unicast frame directly to the target's own MAC, not broadcast
	to the whole segment — this is deliberately surgical: only the intended
	target's ARP cache is touched, which is both more correct and less
	likely to trip switch-level anomaly detection than a broadcast flood of
	spoofed ARP traffic.
	"""
	packet = Ether(dst=target_mac) / ARP(
		op=2,  # ARP reply ("is-at"), not a request
		psrc=spoofed_ip,
		hwsrc=BLACKHOLE_MAC,
		pdst=target_ip,
		hwdst=target_mac,
	)
	sendp(packet, iface=interface, verbose=False)
