"""
helpers.py - Shared utilities, cache loaders, and data transformers for KeaNexus.
"""

import time
from typing import Optional

import pandas as pd
import streamlit as st

from kea import KeaClient

# --- Client -------------------------------------------------------------------


@st.cache_resource
def get_client() -> KeaClient:
	return KeaClient()


# --- Cached data loaders ------------------------------------------------------


@st.cache_data(ttl=30)
def load_leases(_client: KeaClient) -> list[dict]:
	return _client.get_leases()


@st.cache_data(ttl=30)
def load_pool_stats(_client: KeaClient) -> Optional[dict]:
	# Try stat-lease4-summary first (requires stat_cmds hook).
	# Fall back to computing from the lease list if it fails for any reason.
	try:
		return _client.get_pool_stats()
	except Exception:
		pass
	try:
		leases = _client.get_leases()
		cfg = _client.get_config()
		_, _, pool_size = _client.get_pool_range(cfg)
		assigned = sum(1 for lease in leases if lease.get("state", 0) == 0)
		declined = sum(1 for lease in leases if lease.get("state", 0) == 1)
		return {
			"total": pool_size,
			"assigned": assigned,
			"declined": declined,
			"available": max(pool_size - assigned - declined, 0),
			"cumulative": 0,
		}
	except Exception:
		return None


@st.cache_data(ttl=120)
def load_config(_client: KeaClient) -> Optional[dict]:
	try:
		return _client.get_config()
	except Exception:
		return None


@st.cache_data(ttl=20)
def load_status(_client: KeaClient) -> dict:
	return _client.get_status()


# --- Data helpers -------------------------------------------------------------


def fmt_ttl(seconds: int) -> str:
	if seconds <= 0:
		return "expired"
	return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def leases_to_df(leases: list[dict]) -> pd.DataFrame:
	"""Build display DataFrame. Column named 'IP' (no space) to avoid Streamlit width issues."""
	now = int(time.time())
	rows = []
	for lease in leases:
		ttl = lease.get("cltt", 0) + lease.get("valid-lft", 86400) - now
		state = lease.get("state", 0)
		rows.append(
			{
				"IP": str(lease.get("ip-address", "")),
				"Hostname": str(lease.get("hostname", "") or "-"),
				"MAC": str(lease.get("hw-address", "") or "-"),
				"Expires": fmt_ttl(ttl),
				"Status": "[!!] declined" if state == 1 else "[OK] active",
				"_state": state,
			}
		)
	rows.sort(key=lambda r: KeaClient.ip_to_int(r["IP"]) if r["IP"] else 0)
	return pd.DataFrame(rows)


def chip(label: str, cls: str) -> str:
	return f'<span class="chip {cls}">{label}</span>'


# --- Lease/reservation classification ------------------------------------------
#
# A Kea reservation's "hostname" field is admin-typed free text (see
# ui_reservations.py's mandatory "Hostname *" input), not the device's actual
# DHCP-negotiated hostname — Kea reports that same typed string back on the
# live lease, overriding whatever the client itself sent. So for a lease
# matched by IP or MAC to a reservation (types "fixed"/"reserved"), the
# lease's hostname is really just that label. Only "dynamic" leases (no
# reservation match) and "name-only" reservations (matched purely by the
# client's self-reported hostname, since there's no MAC/IP to key on instead)
# carry the device's real hostname.


def build_reservation_type_sets(config: Optional[dict]) -> tuple[set, set, set]:
	"""
	Return three sets for cross-referencing a lease against reservations:
	  fixed_ips     — ip-address values from reservations that pin a specific IP
	  reserved_macs — hw-address values from reservations without a pinned IP
	  name_hosts    — hostname values from reservations with no MAC or IP
	"""
	res = (config or {}).get("subnet4", [{}])[0].get("reservations", [])
	fixed_ips = {r["ip-address"] for r in res if "ip-address" in r}
	reserved_macs = {
		r["hw-address"].lower() for r in res if "hw-address" in r and "ip-address" not in r
	}
	name_hosts = {
		r["hostname"].lower()
		for r in res
		if "hostname" in r and "hw-address" not in r and "ip-address" not in r
	}
	return fixed_ips, reserved_macs, name_hosts


def lease_type(lease: dict, fixed_ips: set, reserved_macs: set, name_hosts: set) -> str:
	if lease.get("ip-address") in fixed_ips:
		return "fixed"
	if lease.get("hw-address", "").lower() in reserved_macs:
		return "reserved"
	if lease.get("hostname", "").lower() in name_hosts:
		return "name-only"
	return "dynamic"


def real_hostname(lease: dict, ltype: str) -> str:
	"""The lease's hostname, but only when it reflects the device's own
	DHCP-negotiated name rather than an admin-typed reservation label."""
	if ltype in ("fixed", "reserved"):
		return ""
	return lease.get("hostname", "")


def distinct_real_hostnames(leases: list[dict], config: Optional[dict]) -> list[str]:
	"""Sorted, de-duplicated real hostnames currently observed across leases.

	Used to populate the quarantine Add Device hostname picker so users
	select from hostnames Kea actually saw a device report, rather than
	risk typing a reservation label that will never match a live lease.
	"""
	fixed_ips, reserved_macs, name_hosts = build_reservation_type_sets(config)
	hostnames = {
		real_hostname(lease, lease_type(lease, fixed_ips, reserved_macs, name_hosts))
		for lease in leases
	}
	hostnames.discard("")
	return sorted(hostnames)
