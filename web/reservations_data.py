"""
reservations_data.py — Pure shaping, rules and config edits for the
Reservations screen.

The config edits (with_reservation_saved / with_reservation_removed) live in
helpers.py and are re-exported here. They never rebuild a reservation from
the form: a reservation can carry fields KeaNexus doesn't show, above all the
quarantine service's client-classes ["DROP"], and rebuilding it would silently
let a quarantined device back onto DHCP. An edit copies the existing entry and
changes only the address and MAC.
"""

import ipaddress
import re
from typing import Optional

from helpers import (
	build_hostname_override_sets,
	is_quarantined_reservation,
	lease_for_reservation,
	real_hostname,
	with_reservation_removed,
	with_reservation_saved,
)
from kea import KeaClient

_MAC_PATTERN = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_MAX_LABEL_LENGTH = 64

# Real Hostname column states other than an actual name.
HOSTNAME_OFFLINE = "offline"
HOSTNAME_MASKED = "masked"
HOSTNAME_NOT_REPORTED = "not reported"


def _reservations(config: Optional[dict]) -> list[dict]:
	return ((config or {}).get("subnet4") or [{}])[0].get("reservations", [])


def _hostname_state(
	reservation: dict, leases: list[dict], override_ips: set, override_macs: set
) -> tuple[str, str]:
	"""(state, hostname). Four states, because an empty hostname alone can't
	tell "masked by a Kea hostname override" (resave fixes it) from "the device
	sent no name" (nothing to fix)."""
	lease = lease_for_reservation(reservation, leases)
	if lease is None:
		return HOSTNAME_OFFLINE, ""
	overridden = (
		lease.get("ip-address") in override_ips
		or (lease.get("hw-address") or "").lower() in override_macs
	)
	if overridden:
		return HOSTNAME_MASKED, ""
	hostname = real_hostname(lease, override_ips, override_macs)
	return (HOSTNAME_NOT_REPORTED, "") if not hostname else ("", hostname)


def _online(
	reservation: dict, leases: list[dict], responding: Optional[frozenset[str]]
) -> Optional[bool]:
	"""Did the device answer the latest ARP sweep? None before any sweep.

	A fixed reservation is checked at its reserved address. A name-only one has
	no address of its own, so it is checked at whatever its live lease holds,
	and counts as not answering when it holds none.
	"""
	if responding is None:
		return None
	address = reservation.get("ip-address")
	if not address:
		lease = lease_for_reservation(reservation, leases)
		address = lease.get("ip-address") if lease else ""
	return bool(address) and address in responding


def reservation_rows(
	config: Optional[dict],
	leases: list[dict],
	labels: dict[str, str],
	responding: Optional[frozenset[str]] = None,
) -> list[dict]:
	"""Fixed-address reservations first, by address, then name-only ones by MAC.

	`responding` is the latest ARP sweep's answering addresses, or None before
	the first sweep.
	"""
	override_ips, override_macs = build_hostname_override_sets(config or {})
	rows = []
	for reservation in _reservations(config):
		mac = (reservation.get("hw-address") or "").lower()
		state, hostname = _hostname_state(reservation, leases, override_ips, override_macs)
		rows.append(
			{
				"mac": mac,
				"ip": reservation.get("ip-address") or "",
				# A reservation saved before labels moved to KeaNexus still carries
				# its label in Kea's own hostname field until it is resaved.
				"label": labels.get(mac) or reservation.get("hostname") or "",
				"hostname": hostname,
				"hostname_state": state,
				"quarantined": is_quarantined_reservation(reservation),
				"online": _online(reservation, leases, responding),
				# Kea can also key a reservation on client-id, DUID or circuit-id.
				# KeaNexus only edits MAC reservations; the rest are shown, not touched.
				"editable": bool(mac),
			}
		)
	rows.sort(key=lambda row: (0, KeaClient.ip_to_int(row["ip"])) if row["ip"] else (1, row["mac"]))
	return rows


def validate_reservation(
	mac: str, ip: str, label: str, config: Optional[dict], original_mac: str = ""
) -> Optional[str]:
	"""The message to show for a bad reservation, or None when Kea can be asked."""
	if not _MAC_PATTERN.match(mac):
		return "MAC addresses look like aa:bb:cc:dd:ee:ff."
	if not label:
		return "Give the reservation a label, so the map and lists can name it."
	if len(label) > _MAX_LABEL_LENGTH:
		return f"Keep the label to {_MAX_LABEL_LENGTH} characters."
	others = [
		r for r in _reservations(config) if (r.get("hw-address") or "").lower() != original_mac
	]
	if any((r.get("hw-address") or "").lower() == mac for r in others):
		return f"{mac} already has a reservation."
	if not ip:
		return None
	try:
		address = ipaddress.IPv4Address(ip)
	except ValueError:
		return "Enter an IPv4 address such as 172.16.17.40, or leave it blank for a name-only reservation."
	subnet = ((config or {}).get("subnet4") or [{}])[0].get("subnet", "")
	if subnet and address not in ipaddress.IPv4Network(subnet, strict=False):
		return f"{ip} is outside the subnet {subnet}."
	if any(r.get("ip-address") == ip for r in others):
		return f"{ip} is already reserved for another device."
	return None


def find_reservation(config: Optional[dict], mac: str) -> Optional[dict]:
	return next(
		(r for r in _reservations(config) if (r.get("hw-address") or "").lower() == mac), None
	)


__all__ = [
	"find_reservation",
	"reservation_rows",
	"validate_reservation",
	"with_reservation_removed",
	"with_reservation_saved",
	"HOSTNAME_MASKED",
	"HOSTNAME_NOT_REPORTED",
	"HOSTNAME_OFFLINE",
]
