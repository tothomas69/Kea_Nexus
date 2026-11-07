"""
address_map.py — Classify every address in the /24 for the IPAM tab and the
web UI's Address Map.

Pure: config, leases and stored records in, one AddressRow per address out.
No UI framework, no Kea or database calls, so both UIs share it and it is
unit-tested. Moved here from ui_ipam.py unchanged in behaviour.
"""

from dataclasses import dataclass
from typing import Optional

from helpers import fmt_ttl, real_hostname

STATUS_NETWORK = "network"
STATUS_BROADCAST = "broadcast"
STATUS_GATEWAY = "gateway"
STATUS_LEASED = "leased"
STATUS_DECLINED = "declined"
STATUS_RESERVED = "reserved"
STATUS_STATIC = "static"
STATUS_SCOPE = "scope"  # in the DHCP pool, currently unassigned
STATUS_FREE = "free"  # outside the pool, no record

ADDRESSES_PER_SUBNET = 256
NETWORK_OCTET = 0
BROADCAST_OCTET = 255
# Kea's own default when a lease carries no valid-lft.
DEFAULT_LEASE_SECONDS = 86400
_DECLINED_LEASE_STATE = 1


@dataclass
class AddressRow:
	"""Fully resolved state for a single address in the subnet."""

	ip: str
	last_octet: int
	status: str
	label: str = ""  # admin-assigned name: reservation label, static hostname, or "gateway"
	hostname: str = ""  # ONLY the device's real DHCP-negotiated hostname, from a live lease
	mac: str = ""
	info: str = ""  # time-to-expiry for leases, "offline" for reservations with no lease
	notes: str = ""  # static entries: description + notes
	editable: bool = False  # True when this address accepts a static record


def subnet_prefix(config: Optional[dict]) -> str:
	"""The first three octets of the subnet: "172.16.17.0/24" becomes "172.16.17"."""
	subnet = ((config or {}).get("subnet4") or [{}])[0].get("subnet", "172.16.17.0/24")
	return ".".join(subnet.split("/")[0].split(".")[:3])


def gateway_ip(config: Optional[dict]) -> str:
	"""The subnet's "routers" option, or "" when none is configured."""
	for option in ((config or {}).get("subnet4") or [{}])[0].get("option-data", []):
		if option.get("name") == "routers":
			return option.get("data", "")
	return ""


def subnet_reservations(config: Optional[dict]) -> list[dict]:
	return ((config or {}).get("subnet4") or [{}])[0].get("reservations", [])


def last_octet(ip: str) -> int:
	return int(ip.split(".")[-1])


def _reservation_label(reservation: dict, fallback_mac: str, labels: dict[str, str]) -> str:
	"""Same fallback as the Reservations tab: the stored label first, then any
	legacy Kea "hostname" on a reservation not yet resaved."""
	mac = (reservation.get("hw-address", "") or fallback_mac).lower()
	return labels.get(mac, "") or reservation.get("hostname", "") or ""


def classify_addresses(
	prefix: str,
	pool_start_octet: int,
	pool_end_octet: int,
	gateway: str,
	leases: list[dict],
	reservations: list[dict],
	static_entries: list[dict],
	labels: dict[str, str],
	override_ips: set,
	override_macs: set,
	now_epoch: int,
) -> list[AddressRow]:
	"""Return one AddressRow for every address .0–.255 in the subnet.

	Priority: network/broadcast > gateway > declined lease > active lease with a
	fixed reservation (shows as reserved) > active lease > reservation > static
	record > in-pool free > free.

	`labels` maps lowercase MAC -> reservation label. The override sets come
	from build_hostname_override_sets() so a legacy Kea-side reservation
	"hostname" never masquerades as the device's real hostname.
	"""
	assert prefix.count(".") == 2, f"expected a three-octet prefix, got {prefix!r}"
	lease_map = {lease["ip-address"]: lease for lease in leases if "ip-address" in lease}
	reservation_map = {r["ip-address"]: r for r in reservations if "ip-address" in r}
	static_map = {s["ip_address"]: s for s in static_entries}

	rows = []
	for octet in range(ADDRESSES_PER_SUBNET):
		ip = f"{prefix}.{octet}"
		row = AddressRow(ip=ip, last_octet=octet, status=STATUS_FREE)

		if octet == NETWORK_OCTET:
			row.status = STATUS_NETWORK
		elif octet == BROADCAST_OCTET:
			row.status = STATUS_BROADCAST
		elif ip == gateway:
			row.status = STATUS_GATEWAY
			row.label = "gateway"
			row.editable = True
		elif ip in lease_map:
			lease = lease_map[ip]
			if lease.get("state", 0) == _DECLINED_LEASE_STATE:
				row.status = STATUS_DECLINED
			elif ip in reservation_map:
				# Active lease for a fixed reservation: show the permanent status.
				row.status = STATUS_RESERVED
			else:
				row.status = STATUS_LEASED
			# real_hostname() returns "" when Kea is echoing a reservation override.
			row.hostname = real_hostname(lease, override_ips, override_macs) or ""
			row.mac = lease.get("hw-address", "") or ""
			if ip in reservation_map:
				row.label = _reservation_label(reservation_map[ip], row.mac, labels)
			ttl_seconds = (
				lease.get("cltt", 0) + lease.get("valid-lft", DEFAULT_LEASE_SECONDS) - now_epoch
			)
			row.info = fmt_ttl(ttl_seconds)
		elif ip in reservation_map:
			reservation = reservation_map[ip]
			row.status = STATUS_RESERVED
			row.mac = reservation.get("hw-address", "") or ""
			row.label = _reservation_label(reservation, row.mac, labels)
			# No lease means no real hostname to show.
			row.info = "offline"
		elif ip in static_map:
			entry = static_map[ip]
			row.status = STATUS_STATIC
			# A static "hostname" is admin-typed, not device-reported, so it is a label.
			row.label = entry.get("hostname", "") or ""
			row.mac = entry.get("mac_address", "") or ""
			row.notes = " — ".join(
				part for part in (entry.get("description", ""), entry.get("notes", "")) if part
			)
			row.editable = True
		elif pool_start_octet <= octet <= pool_end_octet:
			row.status = STATUS_SCOPE
		else:
			row.editable = True

		rows.append(row)
	return rows
