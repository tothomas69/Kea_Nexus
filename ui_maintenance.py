"""
ui_maintenance.py - Maintenance tab for KeaNexus.
"""

import streamlit as st

from helpers import (
	fmt_ttl,
	get_client,
	leases_to_df,
	load_config,
	load_leases,
	load_pool_stats,
	load_status,
)
from kea import KeaError

DISPLAY_COLS = ["IP", "Hostname", "MAC", "Expires", "Status"]


@st.dialog("Disable DHCP service?")
def confirm_disable_dialog() -> None:
	st.error(
		"Disabling DHCP immediately stops Kea from responding to all "
		"DISCOVER and REQUEST packets. Clients cannot renew or obtain leases."
	)
	st.warning(
		"Existing leases are NOT revoked. Devices keep their IPs until "
		"their leases expire, but no new addresses will be issued."
	)
	max_p = st.number_input(
		"Auto re-enable after (seconds, 0 = manual only)",
		min_value=0,
		max_value=3600,
		value=300,
		step=60,
	)
	c1, c2 = st.columns(2)
	with c1:
		if st.button("Confirm - Disable DHCP", type="primary", use_container_width=True):
			try:
				get_client().disable_dhcp(max_period=int(max_p))
				st.session_state.dhcp_enabled = False
				st.rerun()
			except KeaError as e:
				st.error(str(e))
	with c2:
		if st.button("Cancel", use_container_width=True):
			st.rerun()


@st.dialog("Wipe ALL leases?")
def confirm_wipe_dialog(lease_count: int) -> None:
	st.error(f"WARNING: This will permanently delete all {lease_count} leases from Kea's database.")
	st.warning(
		"Every device will lose its lease. On next renewal Kea issues new addresses "
		"which may differ. Fixed reservations in kea-dhcp4.conf are unaffected."
	)
	confirm = st.text_input("Type WIPE to confirm", placeholder="WIPE")
	can_wipe = confirm.strip() == "WIPE"
	c1, c2 = st.columns(2)
	with c1:
		if st.button(
			"Wipe all leases", type="primary", use_container_width=True, disabled=not can_wipe
		):
			try:
				count = get_client().wipe_leases(subnet_id=1)
				load_leases.clear()
				load_pool_stats.clear()
				st.session_state.wipe_result = count
				st.rerun()
			except KeaError as e:
				st.error(str(e))
	with c2:
		if st.button("Cancel", use_container_width=True):
			st.rerun()


def render_maintenance(leases: list[dict]) -> None:
	# --- Refresh --------------------------------------------------------------
	rc1, rc2 = st.columns([8, 1])
	with rc2:
		if st.button("Refresh", use_container_width=True):
			load_leases.clear()
			load_pool_stats.clear()
			load_config.clear()
			load_status.clear()
			st.rerun()

	# --- DHCP service control -------------------------------------------------
	st.subheader("DHCP service control")
	dhcp_on = st.session_state.get("dhcp_enabled", True)
	sc1, sc2 = st.columns([4, 1])
	with sc1:
		if dhcp_on:
			st.success("[OK] DHCP is enabled - serving leases normally")
		else:
			st.error("[!!] DHCP is DISABLED - clients cannot obtain or renew leases")
	with sc2:
		if dhcp_on:
			if st.button("Disable DHCP", use_container_width=True):
				confirm_disable_dialog()
		else:
			if st.button("Enable DHCP", type="primary", use_container_width=True):
				try:
					get_client().enable_dhcp()
					st.session_state.dhcp_enabled = True
					st.rerun()
				except KeaError as e:
					st.error(str(e))

	st.divider()

	# --- Declined leases ------------------------------------------------------
	st.subheader("Declined leases")
	dec = [lease for lease in leases if lease.get("state", 0) == 1]

	if not dec:
		st.success("No declined leases - pool is clean")
	else:
		st.warning(
			f"{len(dec)} declined address(es) occupying pool space. "
			"Clear them to reclaim the space immediately."
		)
		if st.button(f"Clear all {len(dec)} declined"):
			errors = []
			for lease in dec:
				try:
					get_client().delete_lease(lease["ip-address"])
				except KeaError as e:
					errors.append(str(e))
			load_leases.clear()
			load_pool_stats.clear()
			if errors:
				st.error("\n".join(errors))
			else:
				st.success(f"Cleared {len(dec)} declined leases.")
			st.rerun()

		import time as _time

		now = int(_time.time())
		for lease in dec:
			ttl = lease.get("cltt", 0) + lease.get("valid-lft", 86400) - now
			r1, r2, r3 = st.columns([3, 3, 1])
			r1.code(lease["ip-address"])
			r2.write(f"Quarantine: {fmt_ttl(ttl)}")
			with r3:
				if st.button("Clear", key=f"clr_{lease['ip-address']}"):
					try:
						get_client().delete_lease(lease["ip-address"])
						load_leases.clear()
						load_pool_stats.clear()
						st.rerun()
					except KeaError as e:
						st.error(str(e))

	st.divider()

	# --- Lease search ---------------------------------------------------------
	st.subheader("Lease lookup")
	ls1, ls2, ls3 = st.columns([2, 3, 1])
	with ls1:
		stype = st.selectbox("By", ["MAC Address", "Hostname"], label_visibility="collapsed")
	with ls2:
		sval = st.text_input(
			"Value",
			placeholder="Enter value...",
			label_visibility="collapsed",
			key="maint_search_val",
		)
	with ls3:
		do_search = st.button("Search", use_container_width=True, key="maint_search")

	if do_search and sval.strip():
		try:
			kea = get_client()
			found = (
				kea.get_leases_by_mac(sval.strip())
				if stype == "MAC Address"
				else kea.get_leases_by_hostname(sval.strip())
			)
			if not found:
				st.info("No leases found.")
			else:
				st.dataframe(
					leases_to_df(found)[DISPLAY_COLS], use_container_width=True, hide_index=True
				)
		except KeaError as e:
			st.error(str(e))

	st.divider()

	# --- Wipe all leases ------------------------------------------------------
	st.subheader("Lease database")
	if "wipe_result" in st.session_state:
		st.success(f"Wiped {st.session_state.pop('wipe_result')} leases.")

	wc1, wc2 = st.columns([4, 1])
	with wc1:
		st.caption(
			f"lease4-wipe: delete all {len(leases)} leases in the subnet. "
			"Fixed reservations in kea-dhcp4.conf are unaffected."
		)
	with wc2:
		if st.button("Wipe all leases", use_container_width=True):
			confirm_wipe_dialog(len(leases))
