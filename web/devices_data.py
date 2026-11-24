"""
devices_data.py — Pure shaping and rules for the Devices screen.

The device registry gives a device a friendly name, an optional group tag and
notes, and finds it on the network by the hostname it reports on its lease
(a MAC can change; a laptop on a dock even holds two). The Address Map hover
and Search use the same names.

No FastAPI, no Kea, no HTTP: plain data in, plain data out, so every rule here
is unit-tested. The routes in web/routes/devices.py fetch, call these, render.
"""

import re
from typing import Mapping, Optional

# A friendly name travels in a URL path, so: letters, digits, "_", "-" and ".",
# starting with a letter or digit.
_FRIENDLY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
_MAX_FRIENDLY_NAME_LENGTH = 64
# "new" is the add-device form's own path (/devices/new).
_RESERVED_NAMES = {"new"}


def device_rows(
	devices: list[dict], leases: list[dict], seen_macs: Optional[Mapping[str, str]]
) -> list[dict]:
	"""The registry table, alphabetical, with where each device is right now.

	ip and mac come from the live lease carrying the device's hostname, so
	they're current, never a stale breadcrumb. online is None before any
	sweep (so "not checked" never reads as "off"), else whether that IP
	answered the latest one. A device with no lease has no IP and online None.
	"""
	lease_by_hostname = {}
	for lease in leases:
		hostname = lease.get("hostname") or ""
		# First lease wins, as on the Address Map: one device, one address shown.
		if hostname and hostname not in lease_by_hostname:
			lease_by_hostname[hostname] = lease

	rows = []
	for device in devices:
		hostname = device.get("hostname") or ""
		lease = lease_by_hostname.get(hostname, {})
		ip = lease.get("ip-address") or ""
		online = None if seen_macs is None or not ip else ip in seen_macs
		rows.append(
			{
				"name": device["friendly_name"],
				"hostname": hostname,
				"group": device.get("group_tag") or "",
				"ip": ip,
				"mac": lease.get("hw-address") or "",
				"online": online,
				"notes": device.get("notes") or "",
			}
		)
	rows.sort(key=lambda row: row["name"].lower())
	return rows


def group_tags(devices: list[dict]) -> list[dict]:
	"""Each group tag in use, with how many devices carry it."""
	groups: dict[str, dict] = {}
	for device in devices:
		tag = device.get("group_tag")
		if tag:
			groups.setdefault(tag, {"tag": tag, "devices": 0})["devices"] += 1
	return sorted(groups.values(), key=lambda group: group["tag"].lower())


def validate_device(
	friendly_name: str, hostname: str, is_new: bool, name_taken: bool
) -> Optional[str]:
	"""The message to show for a bad registry entry, or None when it is fine."""
	if is_new and not friendly_name:
		return "A friendly name is required."
	if is_new and len(friendly_name) > _MAX_FRIENDLY_NAME_LENGTH:
		return f"Keep the friendly name to {_MAX_FRIENDLY_NAME_LENGTH} characters."
	if is_new and not _FRIENDLY_NAME_PATTERN.match(friendly_name):
		return (
			"Use letters, digits, _ - and . in the friendly name, starting with a letter or digit."
		)
	if is_new and friendly_name.lower() in _RESERVED_NAMES:
		return f'"{friendly_name}" is reserved. Pick another friendly name.'
	if is_new and name_taken:
		return f"{friendly_name} is already registered. Edit that entry instead."
	if not hostname:
		return "Pick the hostname the device reports on its lease."
	return None
