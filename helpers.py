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
