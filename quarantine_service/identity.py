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

	Raises DeviceNotRegisteredError if the target matches no registry entry,
	and DeviceNotOnNetworkError if a registered device has no current lease
	— there's nothing to act on if the device isn't currently on the network.
	"""
	registry_entries = _matching_registry_entries(target, is_group)

	resolved_devices = []
	for entry in registry_entries:
		leases = kea.get_leases_by_hostname(entry["hostname"])
		if not leases:
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

	return resolved_devices


def _matching_registry_entries(target: str, is_group: bool) -> list[dict]:
	"""Return the device_registry rows matching target, by name or group tag."""
	if not is_group:
		device = get_device(target)
		if device is None:
			raise DeviceNotRegisteredError(f"No device_registry entry for '{target}'")
		return [device]

	matches = [device for device in get_devices() if device["group_tag"] == target]
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
		last_quarantined_at=device["last_quarantined_at"],
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
