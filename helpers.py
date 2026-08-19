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


# --- Real vs. reservation-label hostnames ---------------------------------------
#
# A Kea reservation's "hostname" field, when set, is admin-typed free text
# that Kea echoes back on the live lease, discarding the device's actual
# DHCP-negotiated name. ui_reservations.py no longer writes that field to
# Kea — an admin label for a reservation lives in db.reservation_labels
# instead. But a reservation created before that change (or added to
# kea-dhcp4.conf by hand) can still carry a "hostname" override, so real vs.
# label has to be decided per-reservation, by checking whether *that specific*
# reservation still has the field — not by the lease's fixed/reserved/
# dynamic type, which says nothing about whether an override is present.


def build_hostname_override_sets(config: Optional[dict]) -> tuple[set, set]:
	"""
	Return the ip-address/hw-address values of reservations that still carry
	an explicit "hostname" override in Kea's own config.
	"""
	res = (config or {}).get("subnet4", [{}])[0].get("reservations", [])
	override_ips = {r["ip-address"] for r in res if "ip-address" in r and "hostname" in r}
	override_macs = {r["hw-address"].lower() for r in res if "hw-address" in r and "hostname" in r}
	return override_ips, override_macs


def real_hostname(lease: dict, override_ips: set, override_macs: set) -> str:
	"""The lease's hostname, but only when it reflects the device's own
	DHCP-negotiated name rather than a reservation's hostname override."""
	if lease.get("ip-address") in override_ips:
		return ""
	if lease.get("hw-address", "").lower() in override_macs:
		return ""
	return lease.get("hostname", "")


def distinct_real_hostnames(leases: list[dict], config: Optional[dict]) -> list[str]:
	"""Sorted, de-duplicated real hostnames currently observed across leases.

	Used to populate the quarantine Add Device hostname picker so users
	select from hostnames Kea actually saw a device report, rather than
	risk typing a reservation label that will never match a live lease.
	"""
	override_ips, override_macs = build_hostname_override_sets(config)
	hostnames = {real_hostname(lease, override_ips, override_macs) for lease in leases}
	hostnames.discard("")
	return sorted(hostnames)


def lease_for_reservation(reservation: dict, leases: list[dict]) -> Optional[dict]:
	"""The live lease matching a reservation, by MAC (falling back to IP if
	the reservation has no hw-address), or None if the device isn't
	currently leased.

	A missing lease (device offline) and a present lease whose hostname is
	masked by a reservation override (see real_hostname) are different
	situations — callers should distinguish "no active lease" from "hidden
	by an unmigrated override" rather than collapsing both to a blank.
	"""
	mac = reservation.get("hw-address", "").lower()
	if mac:
		for lease in leases:
			if lease.get("hw-address", "").lower() == mac:
				return lease
		return None
	ip = reservation.get("ip-address")
	if ip:
		for lease in leases:
			if lease.get("ip-address") == ip:
				return lease
	return None
