"""
leases_data.py — Pure shaping and input rules for the Leases screen.

Plain data in, plain data out, so every rule is unit-tested; the routes in
web/routes/leases.py only fetch, call these, and render.
"""

import ipaddress
import re
from typing import Optional

from helpers import (
	build_hostname_override_sets,
	build_reservation_type_sets,
	fmt_ttl,
	lease_type,
	real_hostname,
)
from kea import KeaClient

# Kea's own default when a lease carries no valid-lft.
DEFAULT_LEASE_SECONDS = 86400
MIN_LEASE_SECONDS = 60
MAX_LEASE_SECONDS = 7 * DEFAULT_LEASE_SECONDS
_MAC_PATTERN = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_LEASE_STATE_DEFAULT = 0


def lease_rows(
	leases: list[dict], config: Optional[dict], live_ips: Optional[frozenset[str]], now_epoch: int
) -> list[dict]:
	"""Every lease as a table row, sorted by address.

	online is None when no sweep has run yet, which is not the same as False:
	"never checked" must not look like "everything is off".
	"""
	fixed_ips, reserved_macs, name_hosts = build_reservation_type_sets(config)
	override_ips, override_macs = build_hostname_override_sets(config)
	rows = []
	for lease in sorted(
		leases, key=lambda item: KeaClient.ip_to_int(item.get("ip-address", "0.0.0.0"))
	):
		ip = lease.get("ip-address", "")
		seconds_left = (
			lease.get("cltt", 0) + lease.get("valid-lft", DEFAULT_LEASE_SECONDS) - now_epoch
		)
		rows.append(
			{
				"ip": ip,
				"hostname": real_hostname(lease, override_ips, override_macs),
				"mac": lease.get("hw-address") or "",
				"expires": fmt_ttl(seconds_left),
				"type": lease_type(lease, fixed_ips, reserved_macs, name_hosts),
				"declined": lease.get("state", _LEASE_STATE_DEFAULT) != _LEASE_STATE_DEFAULT,
				"online": None if live_ips is None else ip in live_ips,
			}
		)
	return rows


def filter_rows(rows: list[dict], query: str) -> list[dict]:
	"""Rows whose address, hostname or MAC contains the query, ignoring case."""
	needle = query.strip().lower()
	if not needle:
		return rows
	return [
		row for row in rows if any(needle in row[key].lower() for key in ("ip", "hostname", "mac"))
	]


def normalized_mac(mac: str) -> str:
	"""Lowercase, with dashes turned into colons, the way Kea stores it."""
	return mac.strip().lower().replace("-", ":")


def validate_lease(ip: str, mac: str, duration_text: str, subnet: str) -> tuple[Optional[str], int]:
	"""(message to show or None, duration in seconds) for an add or edit form."""
	try:
		address = ipaddress.IPv4Address(ip)
	except ValueError:
		return "Enter an IPv4 address such as 172.16.17.40.", 0
	if subnet and address not in ipaddress.IPv4Network(subnet, strict=False):
		return f"{ip} is outside the subnet {subnet}.", 0
	if not _MAC_PATTERN.match(mac):
		return "MAC addresses look like aa:bb:cc:dd:ee:ff.", 0
	try:
		duration_seconds = int(duration_text)
	except ValueError:
		return "Duration is a whole number of seconds.", 0
	if not MIN_LEASE_SECONDS <= duration_seconds <= MAX_LEASE_SECONDS:
		return f"Duration must be between {MIN_LEASE_SECONDS} and {MAX_LEASE_SECONDS} seconds.", 0
	return None, duration_seconds
