"""
ui_settings.py - Settings tab for KeaNexus.

Displays and allows editing of subnet-level DHCP parameters:
lease length, pool range, gateway, DNS, domain name, and NTP servers.

Subnet CIDR is shown read-only — changing it requires a Kea restart
and cannot be applied safely via config-set alone.

Save path: kea.save_config() → config-set (live) + config-write (disk).
Changes apply immediately to new leases; existing leases keep their
original expiry until they renew.
"""

import streamlit as st

from helpers import get_client, load_config
from kea import KeaError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Seconds per hour — used when converting between UI (hours) and Kea (seconds)
SECONDS_PER_HOUR = 3600

# Kea option names we care about, keyed by a friendly label
OPTION_NAME_GATEWAY = "routers"  # DHCP option 3
OPTION_NAME_DNS = "domain-name-servers"  # DHCP option 6
OPTION_NAME_DOMAIN = "domain-name"  # DHCP option 15
OPTION_NAME_NTP = "ntp-servers"  # DHCP option 42

# Minimum and maximum lease lengths we'll accept in the UI
MIN_LEASE_HOURS = 1
MAX_LEASE_HOURS = 720  # 30 days

# ---------------------------------------------------------------------------
# Config helpers — read values out of the Kea config dict
# ---------------------------------------------------------------------------


def _get_subnet(config: dict) -> dict:
	"""Return the first (and typically only) subnet4 entry."""
	subnets = config.get("subnet4") or []
	# Early return with empty dict if there are no subnets configured
	if not subnets:
		return {}
	return subnets[0]


def _get_option_value(subnet: dict, option_name: str) -> str:
	"""
	Find a named option in the subnet's option-data list.
	Returns the data string, or empty string if the option isn't set.
	"""
	for option in subnet.get("option-data", []):
		if option.get("name") == option_name:
			return option.get("data", "")
	return ""


def _get_pool_range(subnet: dict) -> tuple[str, str]:
	"""
	Extract pool start and end IPs from the first pool entry.
	Kea stores pools as 'start - end' strings.
	Returns ("", "") if no pool is defined.
	"""
	pools = subnet.get("pools") or []
	if not pools:
		return "", ""

	pool_string = pools[0].get("pool", "")
	if "-" not in pool_string:
		return "", ""

	# Split on ' - ' but strip whitespace in case formatting varies
	parts = pool_string.split("-", 1)
	return parts[0].strip(), parts[1].strip()


# ---------------------------------------------------------------------------
# Config helpers — write values back into the Kea config dict
# ---------------------------------------------------------------------------


def _set_option_value(subnet: dict, option_name: str, data: str) -> None:
	"""
	Set a named option in the subnet's option-data list.
	Updates in place if the option already exists, appends if it doesn't.
	Removes the option entirely if data is blank — no point sending empty options.
	"""
	option_list = subnet.setdefault("option-data", [])

	# Find existing entry for this option name
	for index, option in enumerate(option_list):
		if option.get("name") == option_name:
			if data.strip():
				option_list[index]["data"] = data.strip()
			else:
				# Blank value means the user wants to remove this option
				option_list.pop(index)
			return

	# Option doesn't exist yet — only add it if there's actual data
	if data.strip():
		option_list.append({"name": option_name, "data": data.strip()})


def _set_pool_range(subnet: dict, start_ip: str, end_ip: str) -> None:
	"""
	Write the pool range back into the subnet config.
	Kea expects the format: 'x.x.x.x-y.y.y.y' (no spaces around the dash).
	"""
	pools = subnet.setdefault("pools", [{}])
	pools[0]["pool"] = f"{start_ip.strip()}-{end_ip.strip()}"


def _save_config(config: dict) -> None:
	"""Push config to Kea (memory + disk) and clear the cached config loader."""
	get_client().save_config(config)
	# Clear the cache so the next render fetches fresh config from Kea
	load_config.clear()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_ip(value: str, field_name: str) -> list[str]:
	"""
	Validate a single IP address string.
	Returns a list of error messages (empty list = valid).
	"""
	errors = []
	parts = value.strip().split(".")

	if len(parts) != 4:
		errors.append(f"{field_name}: must be a valid IPv4 address (e.g. 192.168.1.1)")
		return errors

	for part in parts:
		if not part.isdigit() or not (0 <= int(part) <= 255):
			errors.append(f"{field_name}: '{value}' is not a valid IPv4 address")
			break

	return errors


def _validate_ip_list(value: str, field_name: str) -> list[str]:
	"""
	Validate a comma-separated list of IP addresses.
	Returns a list of error messages (empty list = valid).
	"""
	errors = []
	if not value.strip():
		return errors  # Empty is allowed — means remove the option

	for ip in value.split(","):
		errors.extend(_validate_ip(ip.strip(), field_name))

	return errors


def _validate_inputs(
	gateway: str,
	dns: str,
	ntp: str,
	pool_start: str,
	pool_end: str,
	lease_hours: int,
) -> list[str]:
	"""
	Run all field validations and return a combined list of error messages.
	An empty list means all inputs are valid and safe to save.
	"""
	errors = []

	if gateway.strip():
		errors.extend(_validate_ip(gateway, "Gateway"))

	errors.extend(_validate_ip_list(dns, "DNS servers"))
	errors.extend(_validate_ip_list(ntp, "NTP servers"))

	if pool_start.strip():
		errors.extend(_validate_ip(pool_start, "Pool start"))
	if pool_end.strip():
		errors.extend(_validate_ip(pool_end, "Pool end"))

	if not (MIN_LEASE_HOURS <= lease_hours <= MAX_LEASE_HOURS):
		errors.append(f"Lease length must be between {MIN_LEASE_HOURS} and {MAX_LEASE_HOURS} hours")

	return errors


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------


def render_settings(config: dict | None) -> None:
	"""
	Render the Settings tab.

	Shows current subnet DHCP parameters and allows editing them.
	Subnet CIDR is display-only because changing it requires a Kea restart.
	"""
	if config is None:
		st.error("Could not load Kea config — check connection to Kea Control Agent.")
		return

	subnet = _get_subnet(config)
	if not subnet:
		st.error("No subnet4 found in Kea config.")
		return

	# --- Read current values from config ----------------------------------

	current_subnet_cidr = subnet.get("subnet", "unknown")

	# Kea uses min-valid-lifetime / max-valid-lifetime at the subnet level.
	# Fall back to valid-lft for older Kea versions, then to 24h as a safe default.
	current_valid_lft = (
		subnet.get("min-valid-lifetime") or subnet.get("valid-lft") or SECONDS_PER_HOUR * 24
	)
	current_lease_hours = current_valid_lft // SECONDS_PER_HOUR

	current_gateway = _get_option_value(subnet, OPTION_NAME_GATEWAY)
	current_dns = _get_option_value(subnet, OPTION_NAME_DNS)
	current_domain = _get_option_value(subnet, OPTION_NAME_DOMAIN)
	current_ntp = _get_option_value(subnet, OPTION_NAME_NTP)
	current_pool_start, current_pool_end = _get_pool_range(subnet)

	# --- Layout -----------------------------------------------------------

	st.caption("Subnet-level DHCP settings. Changes apply immediately to new leases.")

	# Subnet CIDR is read-only — warn the user why they can't edit it
	st.text_input(
		"Subnet (read-only)",
		value=current_subnet_cidr,
		disabled=True,
		help="Changing the subnet CIDR requires a Kea service restart and cannot be applied via the API alone.",
	)

	st.divider()

	# Pool range — two columns side by side
	st.markdown("**Address Pool Range**")
	pool_col1, pool_col2 = st.columns(2)
	with pool_col1:
		new_pool_start = st.text_input(
			"Pool start",
			value=current_pool_start,
			placeholder="172.16.17.100",
		)
	with pool_col2:
		new_pool_end = st.text_input(
			"Pool end",
			value=current_pool_end,
			placeholder="172.16.17.200",
		)

	st.divider()

	# Lease length — stored in seconds, shown in hours
	new_lease_hours = st.number_input(
		"Lease length (hours)",
		min_value=MIN_LEASE_HOURS,
		max_value=MAX_LEASE_HOURS,
		value=int(current_lease_hours),
		step=1,
		help="How long a client holds an IP before it must renew. Existing leases keep their original expiry.",
	)

	st.divider()

	# DHCP options
	st.markdown("**DHCP Options delivered to clients**")

	new_gateway = st.text_input(
		"Default gateway (option 3)",
		value=current_gateway,
		placeholder="172.16.17.1",
	)

	new_dns = st.text_input(
		"DNS servers (option 6)",
		value=current_dns,
		placeholder="1.1.1.1, 8.8.8.8",
		help="Comma-separated list of DNS server IPs.",
	)

	new_domain = st.text_input(
		"Domain name (option 15)",
		value=current_domain,
		placeholder="cyberwraith.net",
	)

	new_ntp = st.text_input(
		"NTP servers (option 42)",
		value=current_ntp,
		placeholder="172.16.17.1",
		help="Comma-separated list of NTP server IPs.",
	)

	st.divider()

	# --- Save button ------------------------------------------------------

	if st.button("Save Settings", type="primary"):
		errors = _validate_inputs(
			gateway=new_gateway,
			dns=new_dns,
			ntp=new_ntp,
			pool_start=new_pool_start,
			pool_end=new_pool_end,
			lease_hours=int(new_lease_hours),
		)

		if errors:
			for error in errors:
				st.error(error)
			return

		# Kea uses min-valid-lifetime and max-valid-lifetime at subnet level.
		# Setting both to the same value gives a fixed lease time.
		# renew-timer (50%) and rebind-timer (87.5%) are derived per RFC 2131.
		lease_seconds = int(new_lease_hours) * SECONDS_PER_HOUR
		subnet["min-valid-lifetime"] = lease_seconds
		subnet["max-valid-lifetime"] = lease_seconds
		subnet["renew-timer"] = int(lease_seconds * 0.5)
		subnet["rebind-timer"] = int(lease_seconds * 0.875)

		# Remove valid-lft if it somehow ended up in the config — Kea rejects it
		subnet.pop("valid-lft", None)

		_set_pool_range(subnet, new_pool_start, new_pool_end)
		_set_option_value(subnet, OPTION_NAME_GATEWAY, new_gateway)
		_set_option_value(subnet, OPTION_NAME_DNS, new_dns)
		_set_option_value(subnet, OPTION_NAME_DOMAIN, new_domain)
		_set_option_value(subnet, OPTION_NAME_NTP, new_ntp)

		try:
			_save_config(config)
			st.toast("Settings saved successfully!", icon="✅")
			st.rerun()
		except KeaError as error:
			st.error(f"Failed to save: {error}")
