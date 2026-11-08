"""
settings_data.py — Pure reading, checking and applying of the subnet settings
the Settings screen edits: pool range, lease length and four DHCP options.

with_settings_applied returns a copy and touches only those fields, so the
caller can apply it to a config read fresh from Kea without disturbing
anything else in it (reservations and their DROP classes above all).
"""

import copy
import ipaddress
from typing import Optional

SECONDS_PER_HOUR = 3600
DEFAULT_LEASE_HOURS = 24
MIN_LEASE_HOURS = 1
MAX_LEASE_HOURS = 720  # 30 days
# RFC 2131's defaults: renew at half the lease, rebind at seven-eighths.
RENEW_FRACTION = 0.5
REBIND_FRACTION = 0.875

# Kea option names, with their DHCP option numbers.
OPTION_GATEWAY = "routers"  # 3
OPTION_DNS = "domain-name-servers"  # 6
OPTION_DOMAIN = "domain-name"  # 15
OPTION_NTP = "ntp-servers"  # 42
_OPTION_FIELDS = {
	"gateway": OPTION_GATEWAY,
	"dns": OPTION_DNS,
	"domain": OPTION_DOMAIN,
	"ntp": OPTION_NTP,
}


def _subnet(config: Optional[dict]) -> dict:
	return ((config or {}).get("subnet4") or [{}])[0]


def _option(subnet: dict, name: str) -> str:
	return next(
		(
			option.get("data", "")
			for option in subnet.get("option-data", [])
			if option.get("name") == name
		),
		"",
	)


def settings_values(config: Optional[dict]) -> dict:
	"""The current settings as form values (all strings)."""
	subnet = _subnet(config)
	pool = ((subnet.get("pools") or [{}])[0]).get("pool", "")
	pool_start, _, pool_end = pool.partition("-")
	lifetime_seconds = (
		subnet.get("valid-lifetime")
		or subnet.get("min-valid-lifetime")
		or DEFAULT_LEASE_HOURS * SECONDS_PER_HOUR
	)
	return {
		"subnet": subnet.get("subnet", ""),
		"pool_start": pool_start.strip(),
		"pool_end": pool_end.strip(),
		"lease_hours": str(lifetime_seconds // SECONDS_PER_HOUR),
		**{field: _option(subnet, name) for field, name in _OPTION_FIELDS.items()},
	}


def _address_errors(label: str, text: str, network: Optional[ipaddress.IPv4Network]) -> list[str]:
	try:
		address = ipaddress.IPv4Address(text)
	except ValueError:
		return [f"{label}: {text or 'blank'} isn't an IPv4 address."]
	if network is not None and address not in network:
		return [f"{label}: {text} is outside the subnet {network}."]
	return []


def _list_errors(label: str, text: str) -> list[str]:
	errors = []
	for item in (part.strip() for part in text.split(",")):
		errors.extend(_address_errors(label, item, None))
	return errors


def validate_settings(values: dict) -> list[str]:
	"""Every problem with the form, so the admin can fix them all in one go."""
	network = (
		ipaddress.IPv4Network(values["subnet"], strict=False) if values.get("subnet") else None
	)
	errors = _address_errors("Pool start", values["pool_start"], network)
	errors += _address_errors("Pool end", values["pool_end"], network)
	if not errors and ipaddress.IPv4Address(values["pool_start"]) > ipaddress.IPv4Address(
		values["pool_end"]
	):
		errors.append("The pool must start at or before where it ends.")
	try:
		hours = int(values["lease_hours"])
		if not MIN_LEASE_HOURS <= hours <= MAX_LEASE_HOURS:
			errors.append(f"Lease length must be {MIN_LEASE_HOURS} to {MAX_LEASE_HOURS} hours.")
	except ValueError:
		errors.append("Lease length is a whole number of hours.")
	if values["gateway"]:
		errors += _address_errors("Gateway", values["gateway"], network)
	if values["dns"]:
		errors += _list_errors("DNS servers", values["dns"])
	if values["ntp"]:
		errors += _list_errors("NTP servers", values["ntp"])
	return errors


def _normalized_list(text: str) -> str:
	return ", ".join(part.strip() for part in text.split(",") if part.strip())


def with_settings_applied(config: dict, values: dict) -> dict:
	"""A copy of config with the form's settings in its first subnet. Call validate first."""
	updated = copy.deepcopy(config)
	subnet = updated["subnet4"][0]
	pools = subnet.setdefault("pools", [{}]) or [{}]
	pools[0]["pool"] = f"{values['pool_start']}-{values['pool_end']}"
	subnet["pools"] = pools

	# One fixed lease length: Kea clamps the client's request into min..max.
	lifetime_seconds = int(values["lease_hours"]) * SECONDS_PER_HOUR
	subnet["valid-lifetime"] = lifetime_seconds
	subnet["min-valid-lifetime"] = lifetime_seconds
	subnet["max-valid-lifetime"] = lifetime_seconds
	subnet["renew-timer"] = int(lifetime_seconds * RENEW_FRACTION)
	subnet["rebind-timer"] = int(lifetime_seconds * REBIND_FRACTION)
	subnet.pop("valid-lft", None)  # a lease field, which Kea rejects in config

	options = [
		option
		for option in subnet.get("option-data", [])
		if option.get("name") not in _OPTION_FIELDS.values()
	]
	for field, name in _OPTION_FIELDS.items():
		data = values[field].strip()
		if field in ("dns", "ntp"):
			data = _normalized_list(data)
		# Blank means the admin wants the option gone, not sent empty.
		if data:
			options.append({"name": name, "data": data})
	subnet["option-data"] = options
	return updated
