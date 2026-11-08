"""
search_data.py — What the header's search (⌘K) finds: any address in the
map with a label, a hostname or a MAC, and any device in the quarantine
registry. Pure, so the matching rules are unit-tested.
"""

from address_map import STATUS_DECLINED, STATUS_FREE, STATUS_LEASED, STATUS_SCOPE, AddressRow

MIN_QUERY_LENGTH = 2
MAX_RESULTS = 8
# Statuses whose row says nothing beyond its address, so they only match by IP.
_EMPTY_STATUSES = {STATUS_FREE, STATUS_SCOPE}


def _address_result(row: AddressRow) -> dict:
	name = row.label or row.hostname or row.ip
	detail = " · ".join(
		part
		for part in (
			row.ip if name != row.ip else "",
			row.hostname if row.label else "",
			row.mac,
			row.status,
		)
		if part
	)
	# Anything holding a lease is best seen on Leases, filtered to just this one;
	# everything else (static, gateway, an offline reservation) lives on the map.
	has_lease = bool(row.hostname) or row.status in (STATUS_LEASED, STATUS_DECLINED)
	href = f"/leases?q={row.ip}" if has_lease else "/"
	return {"title": name, "detail": detail, "href": href, "kind": "address"}


def _device_result(device: dict) -> dict:
	state = "quarantined" if device.get("is_quarantined") else "registered"
	detail = " · ".join(
		part for part in (device.get("hostname"), device.get("last_seen_ip_address"), state) if part
	)
	return {
		"title": device["friendly_name"],
		"detail": detail,
		"href": "/quarantine",
		"kind": "device",
	}


def search(rows: list[AddressRow], devices: list[dict], query: str) -> list[dict]:
	"""Up to MAX_RESULTS matches, registered devices first, ignoring case.

	A query shorter than MIN_QUERY_LENGTH finds nothing: one character matches
	nearly every address and only hides what the person is looking for.
	"""
	needle = query.strip().lower()
	if len(needle) < MIN_QUERY_LENGTH:
		return []
	device_hits = [
		_device_result(device)
		for device in devices
		if any(
			needle in (device.get(key) or "").lower()
			for key in (
				"friendly_name",
				"hostname",
				"last_seen_ip_address",
				"last_seen_mac_address",
			)
		)
	]
	address_hits = [
		_address_result(row)
		for row in rows
		if (row.status not in _EMPTY_STATUSES or needle in row.ip)
		and any(needle in value.lower() for value in (row.ip, row.label, row.hostname, row.mac))
	]
	return (device_hits + address_hits)[:MAX_RESULTS]
