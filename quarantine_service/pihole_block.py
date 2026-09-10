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

from pihole import PiholeClient, PiholeError

_GROUP_NAME = "keanexus_quarantine"
_DENY_ALL_REGEX = "(.*)"
_HTTP_NOT_FOUND = 404


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

	A 404 means Pi-hole had no client override for this IP, which is
	exactly the state this function exists to produce — so it counts as
	success, not failure. Deletes are idempotent by nature and the only
	honest question is whether the override is gone afterwards. Treating
	it as an error instead meant every release of a device that was never
	blocked (or was already unblocked) burned three retries and wrote a
	false `succeeded: 0` row into quarantine_log — noise that masked real
	Pi-hole failures for weeks.
	"""
	try:
		pihole.request("DELETE", f"/clients/{ip_address}")
	except PiholeError as exc:
		if exc.status_code != _HTTP_NOT_FOUND:
			raise


def list_blocked_ip_addresses(pihole: PiholeClient) -> list[str]:
	"""Every client IP currently assigned to the quarantine group.

	The Pi-hole side of the reconciliation check in reconcile.py. Returns an
	empty list when the group doesn't exist — nothing has ever been blocked
	on this instance, so there is nothing to reconcile.

	Pi-hole is queried per instance rather than once: the two run fully
	independently with no sync between them, so an override can exist on one
	and not the other. That asymmetry is itself worth surfacing.
	"""
	group_id = _find_group_by_name(pihole)
	if group_id is None:
		return []

	response = pihole.request("GET", "/clients")
	return [
		client["client"]
		for client in response.get("clients", [])
		if group_id in (client.get("groups") or []) and client.get("client")
	]


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
