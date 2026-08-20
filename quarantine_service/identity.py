"""
identity.py — Resolve a quarantine target (friendly_name or group_tag) to
live network identity.

device_registry is the source of truth for "who is this" (see
docs/quarantine-feature-design.md); Kea's current leases are queried fresh
on every resolution to find whatever MAC and IP that hostname is presently
using, since MAC can change between quarantine triggers. Reservations and
ARP data are never treated as identity — only hostname is, backed by
device_registry.
"""

from dataclasses import dataclass

from db import get_device, get_devices, upsert_device
from kea import KeaClient


class DeviceNotRegisteredError(Exception):
	"""Raised when a target name matches no device_registry entry."""


class DeviceNotOnNetworkError(Exception):
	"""Raised when a registered device's hostname has no current Kea lease."""


@dataclass
class ResolvedDevice:
	"""A device_registry entry cross-referenced against its current Kea lease."""

	friendly_name: str
	hostname: str
	mac_address: str
	ip_address: str


def resolve_target(kea: KeaClient, target: str, is_group: bool) -> list[ResolvedDevice]:
	"""Resolve a friendly_name or group_tag to live network identity.

	Raises DeviceNotRegisteredError if the target matches no registry entry.
	For a single-device target, raises DeviceNotOnNetworkError if it has no
	current lease. For a group target, a device with no current lease is
	skipped rather than aborting the whole group — an offline sibling
	shouldn't block enforcement on the devices that are actually online.
	DeviceNotOnNetworkError is only raised for a group if none of its
	devices are currently on the network.
	"""
	registry_entries = _matching_registry_entries(target, is_group)

	resolved_devices = []
	offline_hostnames = []
	for entry in registry_entries:
		leases = kea.get_leases_by_hostname(entry["hostname"])
		if not leases:
			if is_group:
				offline_hostnames.append(entry["hostname"])
				continue
			raise DeviceNotOnNetworkError(
				f"No current lease found for hostname '{entry['hostname']}' "
				f"(friendly_name '{entry['friendly_name']}')"
			)
		mac_address = leases[0].get("hw-address", "") or ""
		ip_address = leases[0].get("ip-address", "") or ""
		_refresh_last_seen(entry, mac_address, ip_address)
		resolved_devices.append(
			ResolvedDevice(
				friendly_name=entry["friendly_name"],
				hostname=entry["hostname"],
				mac_address=mac_address,
				ip_address=ip_address,
			)
		)

	if is_group and not resolved_devices:
		raise DeviceNotOnNetworkError(
			f"None of the devices in group '{target}' are currently on the network "
			f"(checked: {', '.join(offline_hostnames)})"
		)

	return resolved_devices


def _matching_registry_entries(target: str, is_group: bool) -> list[dict]:
	"""Return the device_registry rows matching target, by name or group tag.

	Case-insensitive: target typically arrives dictated via a Siri Shortcut,
	which capitalizes the first letter of whatever was said regardless of
	how the friendly_name/group_tag was actually registered. The exact match
	is tried first (a fast, indexed lookup for the common case where casing
	already matches); a case-insensitive scan only runs as a fallback.
	"""
	target_lower = target.lower()
	if not is_group:
		device = get_device(target)
		if device is None:
			device = next(
				(d for d in get_devices() if d["friendly_name"].lower() == target_lower), None
			)
		if device is None:
			raise DeviceNotRegisteredError(f"No device_registry entry for '{target}'")
		return [device]

	matches = [device for device in get_devices() if device["group_tag"].lower() == target_lower]
	if not matches:
		raise DeviceNotRegisteredError(f"No device_registry entries with group_tag '{target}'")
	return matches


def _refresh_last_seen(device: dict, mac_address: str, ip_address: str) -> None:
	"""Persist the current MAC/IP as breadcrumbs. Identity itself is untouched."""
	upsert_device(
		device["friendly_name"],
		device["hostname"],
		group_tag=device["group_tag"],
		os_fingerprint=device["os_fingerprint"],
		last_seen_mac_address=mac_address,
		last_seen_ip_address=ip_address,
		last_seen_at=device["last_seen_at"],
		last_quarantined_at=device["last_quarantined_at"],
		is_quarantined=bool(device["is_quarantined"]),
		notes=device["notes"],
	)


def verify_identity_unchanged(kea: KeaClient, device: ResolvedDevice) -> bool:
	"""Re-check that device's hostname still resolves to the same IP and MAC.

	Meant to be called immediately before firing an enforcement action, not
	at resolution time. For a group quarantine, later devices in the list
	can sit for tens of seconds (an nmap scan alone can take up to 45s)
	between when resolve_target() first captured their IP and when their
	turn to actually be acted on arrives. If a device's lease changed in
	that window, acting on the stale IP risks poisoning ARP or blocking DNS
	for whatever device has since been handed that address — not the
	target. Returns False if the hostname now has no lease at all, or if
	its current IP or MAC no longer match what was resolved.
	"""
	current_leases = kea.get_leases_by_hostname(device.hostname)
	if not current_leases:
		return False

	current_ip = current_leases[0].get("ip-address", "") or ""
	current_mac = current_leases[0].get("hw-address", "") or ""
	return current_ip == device.ip_address and current_mac == device.mac_address
