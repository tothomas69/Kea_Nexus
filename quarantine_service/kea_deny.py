"""
kea_deny.py — Kea DROP-class enforcement action.

Adds or removes a device's MAC address from Kea's built-in DROP class via a
host reservation, so its DHCP traffic is silently ignored (no offer, no
NAK). See "Decided: built-in DROP class" in docs/quarantine-feature-design.md
for why this was chosen over a fake-unreachable-IP reservation.

Existing reservation fields for the MAC (a fixed IP, a hostname) are always
preserved — only the client-classes list is touched, so this action never
clobbers a reservation that exists for some other reason.
"""

from typing import Optional

from kea import KeaClient

_DROP_CLASS = "DROP"


def deny_via_kea(kea: KeaClient, mac_address: str) -> None:
	"""Add mac_address to the DROP class and push the config to Kea."""
	dhcp4_config = kea.get_config()
	apply_drop_class(dhcp4_config, mac_address)
	kea.save_config(dhcp4_config)


def undo_deny_via_kea(kea: KeaClient, mac_address: str) -> None:
	"""Remove mac_address from the DROP class and push the config to Kea."""
	dhcp4_config = kea.get_config()
	remove_drop_class(dhcp4_config, mac_address)
	kea.save_config(dhcp4_config)


def apply_drop_class(dhcp4_config: dict, mac_address: str) -> dict:
	"""Mutate dhcp4_config in place, adding mac_address to the DROP class.

	Creates a new reservation if none exists for this MAC. If a reservation
	already exists, DROP is appended to its client-classes without touching
	any other field. Safe to call repeatedly — DROP is never duplicated.
	"""
	subnet = _find_subnet(dhcp4_config)
	reservations = subnet.setdefault("reservations", [])
	reservation = _find_reservation(reservations, mac_address)

	if reservation is None:
		reservations.append({"hw-address": mac_address, "client-classes": [_DROP_CLASS]})
		return dhcp4_config

	classes = reservation.setdefault("client-classes", [])
	if _DROP_CLASS not in classes:
		classes.append(_DROP_CLASS)
	return dhcp4_config


def remove_drop_class(dhcp4_config: dict, mac_address: str) -> dict:
	"""Mutate dhcp4_config in place, removing mac_address from the DROP class.

	If the reservation has no purpose left after DROP is removed (no other
	classes, no fixed IP, no hostname), the reservation entry is deleted
	entirely rather than left behind as an empty orphan. No-op if the MAC
	has no reservation or isn't currently in DROP.
	"""
	subnet = _find_subnet(dhcp4_config)
	reservations = subnet.get("reservations") or []
	reservation = _find_reservation(reservations, mac_address)
	if reservation is None:
		return dhcp4_config

	classes = reservation.get("client-classes") or []
	if _DROP_CLASS in classes:
		classes.remove(_DROP_CLASS)

	if _reservation_has_no_remaining_purpose(reservation):
		reservations.remove(reservation)

	return dhcp4_config


def _find_subnet(dhcp4_config: dict) -> dict:
	"""Return the first subnet4 entry. This deployment has exactly one."""
	subnets = dhcp4_config.get("subnet4") or []
	if not subnets:
		raise ValueError("Kea config has no subnet4 entries")
	return subnets[0]


def _find_reservation(reservations: list[dict], mac_address: str) -> Optional[dict]:
	mac_lower = mac_address.lower()
	for reservation in reservations:
		if (reservation.get("hw-address") or "").lower() == mac_lower:
			return reservation
	return None


def _reservation_has_no_remaining_purpose(reservation: dict) -> bool:
	"""True if a reservation has nothing left beyond hw-address and an empty class list."""
	meaningful_keys = set(reservation.keys()) - {"hw-address", "client-classes"}
	has_meaningful_fields = bool(meaningful_keys)
	has_remaining_classes = bool(reservation.get("client-classes"))
	return not has_meaningful_fields and not has_remaining_classes
