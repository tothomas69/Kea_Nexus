"""
pihole_block.py — Pi-hole enforcement action.

Blocks or unblocks a device's DNS resolution by assigning its IP to a
dedicated "keanexus_quarantine" Pi-hole group that has a blanket deny-all
regex scoped only to that group — other clients and the Default group are
untouched. This mirrors the group-based blocking approach already used
elsewhere on this network for parental controls, rather than introducing a
second, different mechanism.

Client identity here is the device's current IP address, not its MAC — the
Kea DROP-class deny (kea_deny.py) already prevents the device from getting
a new DHCP lease while quarantined, so its IP is effectively frozen for the
duration of a quarantine.
"""

from typing import Optional

from pihole import PiholeClient

_GROUP_NAME = "keanexus_quarantine"
_DENY_ALL_REGEX = "(.*)"


def block_via_pihole(pihole: PiholeClient, ip_address: str) -> None:
	"""Assign ip_address to the quarantine group, creating it if needed."""
	group_id = _ensure_quarantine_group(pihole)
	_ensure_deny_all_regex(pihole, group_id)
	pihole.request(
		"PUT",
		f"/clients/{ip_address}",
		json_body={"groups": [group_id], "comment": "Managed by KeaNexus quarantine"},
	)


def unblock_via_pihole(pihole: PiholeClient, ip_address: str) -> None:
	"""Remove any Pi-hole client override for ip_address.

	Deleting the client entry entirely reverts the device to Pi-hole's
	normal Default-group behavior, rather than leaving an empty override
	behind.
	"""
	pihole.request("DELETE", f"/clients/{ip_address}")


def _ensure_quarantine_group(pihole: PiholeClient) -> int:
	"""Return the quarantine group's ID, creating the group if it doesn't exist."""
	existing_group_id = _find_group_by_name(pihole)
	if existing_group_id is not None:
		return existing_group_id

	created = pihole.request(
		"POST",
		"/groups",
		json_body={"name": _GROUP_NAME, "comment": "Managed by KeaNexus quarantine"},
	)
	groups = created.get("groups") or []
	if not groups:
		raise ValueError(f"Pi-hole did not return the created group '{_GROUP_NAME}'")
	return groups[0]["id"]


def _find_group_by_name(pihole: PiholeClient) -> Optional[int]:
	response = pihole.request("GET", "/groups")
	for group in response.get("groups", []):
		if group.get("name") == _GROUP_NAME:
			return group["id"]
	return None


def _ensure_deny_all_regex(pihole: PiholeClient, group_id: int) -> None:
	"""Make sure the quarantine group has its blanket deny-all regex domain."""
	response = pihole.request("GET", "/domains/deny/regex")
	for domain in response.get("domains", []):
		if domain.get("domain") == _DENY_ALL_REGEX and group_id in (domain.get("groups") or []):
			return

	pihole.request(
		"POST",
		"/domains/deny/regex",
		json_body={
			"domain": _DENY_ALL_REGEX,
			"groups": [group_id],
			"comment": "Blocks all domains for the KeaNexus quarantine group only",
		},
	)
