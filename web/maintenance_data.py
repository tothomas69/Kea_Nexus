"""
maintenance_data.py — Pure shaping and input rules for the Maintenance screen.
"""

from typing import Optional

from helpers import fmt_ttl
from web.leases_data import DEFAULT_LEASE_SECONDS

# Kea marks a lease declined (state 1) when a client reports its address is
# already in use. The address sits out of the pool until the lease expires.
LEASE_STATE_DECLINED = 1
# dhcp-disable's own timer: Kea turns DHCP back on by itself after this long.
DEFAULT_AUTO_ENABLE_SECONDS = 300
MAX_AUTO_ENABLE_SECONDS = 3600
WIPE_CONFIRMATION_WORD = "WIPE"


def declined_rows(leases: list[dict], now_epoch: int) -> list[dict]:
	"""Declined leases, by address, with how long until Kea frees each one itself."""
	rows = [
		{
			"ip": lease.get("ip-address", ""),
			"frees_in": fmt_ttl(
				lease.get("cltt", 0) + lease.get("valid-lft", DEFAULT_LEASE_SECONDS) - now_epoch
			),
		}
		for lease in leases
		if lease.get("state") == LEASE_STATE_DECLINED and lease.get("ip-address")
	]
	return sorted(rows, key=lambda row: tuple(int(part) for part in row["ip"].split(".")))


def validate_auto_enable(text: str) -> tuple[Optional[str], int]:
	"""(message or None, seconds). 0 means stay off until someone turns DHCP back on."""
	try:
		seconds = int(text)
	except ValueError:
		return "Enter a whole number of seconds, or 0.", 0
	if not 0 <= seconds <= MAX_AUTO_ENABLE_SECONDS:
		return f"Pick between 0 and {MAX_AUTO_ENABLE_SECONDS} seconds.", 0
	return None, seconds


def wipe_confirmed(typed: str) -> bool:
	"""The exact word, so a stray click or autofill can't wipe the lease database."""
	return typed.strip() == WIPE_CONFIRMATION_WORD
