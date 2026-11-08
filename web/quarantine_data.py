"""
quarantine_data.py — Pure shaping and rules for the Quarantine screen.

No FastAPI, no Kea, no HTTP: plain data in, plain data out, so every rule
here is unit-tested. The routes in web/routes/quarantine.py only fetch, call
these, and render.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from web.overview_data import local_time

# Three failed releases before a forced delete is offered. Enough to rule out a
# passing hiccup (a Pi-hole restart, a slow nmap) without trapping a device that
# genuinely cannot be released (the service is down, the device is gone).
MAX_RELEASE_ATTEMPTS_BEFORE_FORCE = 3

# The service turns a spoken "tommy laptop" into tommy_laptop by keeping only
# letters and digits (quarantine_service/identity.py), and a name travels in a
# URL path. So a name is letters, digits, "_", "-" and ".", and must start with
# a letter or digit.
_FRIENDLY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
_MAX_FRIENDLY_NAME_LENGTH = 64
_TIME_FORMAT = "%m/%d %H:%M"
# GET /devices/new is the Add form, so a device called "new" could never be edited.
_RESERVED_NAMES = {"new"}

# Per step: (label shown in the result, key in the service's step_results).
ENFORCEMENT_STEP_KEYS = [
	("Kea", "kea_step_succeeded"),
	("ARP", "arp_step_succeeded"),
	("Pi-hole", "pihole_step_succeeded"),
	("Fingerprint", "fingerprint_refreshed"),
]


def device_rows(devices: list[dict]) -> list[dict]:
	"""The registry table, alphabetical. Missing values come back as ""."""
	rows = [
		{
			"name": device["friendly_name"],
			"hostname": device.get("hostname") or "",
			"group": device.get("group_tag") or "",
			"mac": device.get("last_seen_mac_address") or "",
			"ip": device.get("last_seen_ip_address") or "",
			"last_seen": local_time(device.get("last_seen_at"), _TIME_FORMAT),
			"last_quarantined": local_time(device.get("last_quarantined_at"), _TIME_FORMAT),
			"quarantined": bool(device.get("is_quarantined")),
			"notes": device.get("notes") or "",
		}
		for device in devices
	]
	rows.sort(key=lambda row: row["name"].lower())
	return rows


def group_tags(devices: list[dict]) -> list[dict]:
	"""Each group tag in use, with how many devices carry it and how many are quarantined."""
	groups: dict[str, dict] = {}
	for device in devices:
		tag = device.get("group_tag")
		if not tag:
			continue
		group = groups.setdefault(tag, {"tag": tag, "devices": 0, "quarantined": 0})
		group["devices"] += 1
		group["quarantined"] += 1 if device.get("is_quarantined") else 0
	return sorted(groups.values(), key=lambda group: group["tag"].lower())


def log_rows(entries: list[dict]) -> list[dict]:
	"""The audit log, newest first as db.get_quarantine_log returns it."""
	return [
		{
			"name": entry["friendly_name"],
			"action": entry["action"],
			"step": entry["step"],
			"ok": bool(entry["succeeded"]),
			"attempts": entry["attempt_count"],
			"when": local_time(entry["occurred_at"], _TIME_FORMAT) or entry["occurred_at"],
			"detail": entry.get("detail") or "",
		}
		for entry in entries
	]


def step_outcomes(result: dict) -> list[dict]:
	"""Per device, which enforcement steps worked, from the service's step_results."""
	outcomes = []
	for step_result in result.get("step_results", []):
		skipped = bool(step_result.get("skipped_due_to_identity_drift"))
		steps = (
			[]
			if skipped
			else [(label, bool(step_result.get(key))) for label, key in ENFORCEMENT_STEP_KEYS]
		)
		outcomes.append(
			{"name": step_result.get("friendly_name", ""), "skipped": skipped, "steps": steps}
		)
	return outcomes


def enforcement_succeeded(result: dict) -> bool:
	"""Whether a quarantine or release changed every enforcement layer on every device.

	Same rule as the service's _enforcement_succeeded: the fingerprint refresh is
	identity upkeep, so an inconclusive scan does not count against the action.
	An action that resolved no device did nothing, so it did not succeed.
	"""
	step_results = result.get("step_results", [])
	if not step_results:
		return False
	return all(
		not step.get("skipped_due_to_identity_drift")
		and step.get("kea_step_succeeded")
		and step.get("arp_step_succeeded")
		and step.get("pihole_step_succeeded")
		for step in step_results
	)


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


@dataclass
class ReleaseAttempts:
	"""Failed auto-releases per device, counted for the life of the process.

	Deleting a quarantined device's registry row orphans its Kea DROP class,
	Pi-hole overrides and ARP thread, since that row is the only way back to
	them (this happened for real: "Tommy PC" stayed cut off for two days). So
	Delete releases first, and only offers a forced delete after the release
	has failed MAX_RELEASE_ATTEMPTS_BEFORE_FORCE times. Kept in memory on
	purpose: a restart gives the automatic route a fresh chance.
	"""

	failures: dict[str, int] = field(default_factory=dict)

	def record_failure(self, friendly_name: str) -> int:
		self.failures[friendly_name] = self.failures.get(friendly_name, 0) + 1
		return self.failures[friendly_name]

	def count(self, friendly_name: str) -> int:
		return self.failures.get(friendly_name, 0)

	def clear(self, friendly_name: str) -> None:
		self.failures.pop(friendly_name, None)

	def may_force(self, friendly_name: str) -> bool:
		return self.count(friendly_name) >= MAX_RELEASE_ATTEMPTS_BEFORE_FORCE
