"""
ui_leases.py - Leases tab for KeaNexus.
"""

import time as _time
from typing import Optional

import streamlit as st

from helpers import (
	build_hostname_override_sets,
	build_reservation_type_sets,
	fmt_ttl,
	get_client,
	html_safe_mac,
	lease_type,
	load_leases,
	load_pool_stats,
	real_hostname,
)
from kea import KeaClient, KeaError
from quarantine_client import QuarantineServiceError, trigger_liveness_sweep

LOOKUP_COLS = ["IP", "Hostname", "MAC", "Expires", "Status"]

# Session-state keys for the liveness sweep. Kept as module constants because
# both the button handler and the renderer below read them, and a typo in one
# of two string literals would silently render an always-empty result.
_LIVE_IPS_KEY = "leases_live_ips"
_LIVE_CHECKED_AT_KEY = "leases_live_checked_at"


# --- HTML table rendering ----------------------------------------------------

_TH = (
	"padding:8px 12px;text-align:left;font-size:11px;font-weight:700;"
	"color:#1f2328;text-transform:uppercase;letter-spacing:.05em;"
	"border-bottom:2px solid #d0d7de;white-space:nowrap;font-family:monospace"
)
_TD = (
	"padding:8px 12px;font-family:monospace;font-size:13px;"
	"color:#1f2328;border-bottom:1px solid #f0f2f5;white-space:nowrap"
)

# Same green the row-state chip already uses for an active lease, so "online"
# reads as one colour across the tab rather than introducing a second green.
_LIVE_TEXT_COLOR = "#1a7f37"
_TD_LIVE = _TD.replace("color:#1f2328", f"color:{_LIVE_TEXT_COLOR};font-weight:600")

_TYPE_STYLE: dict[str, tuple[str, str]] = {
	"fixed": ("#ddf4ff", "#0550ae"),
	"reserved": ("#fff8c5", "#7d4e00"),
	"name-only": ("#f6f8fa", "#57606a"),
	"dynamic": ("#f6f8fa", "#57606a"),
}


def _row_chip(num: int, state: int) -> str:
	bg = "#dafbe1" if state == 0 else "#ffebe9"
	color = "#1a7f37" if state == 0 else "#cf222e"
	return (
		f'<span style="background:{bg};color:{color};font-size:11px;font-weight:700;'
		f'padding:2px 9px;border-radius:10px;display:inline-block">{num}</span>'
	)


def _type_chip(label: str) -> str:
	bg, fg = _TYPE_STYLE.get(label, _TYPE_STYLE["dynamic"])
	return (
		f'<span style="background:{bg};color:{fg};font-size:13px;font-weight:600;'
		f'padding:2px 9px;border-radius:10px;display:inline-block">{label}</span>'
	)


def _lease_table(
	leases: list[dict],
	fixed_ips: set,
	reserved_macs: set,
	name_hosts: set,
	override_ips: set,
	override_macs: set,
	live_ips: Optional[set[str]] = None,
) -> str:
	"""Render the lease table. Rows whose IP is in `live_ips` are drawn in
	green — a device that answered the most recent ARP sweep.

	`live_ips` of None means no sweep has been run this session, which is
	deliberately distinct from an empty set (a sweep ran and nothing
	answered): the first says nothing about liveness, the second says every
	lease looked dead.
	"""
	now = int(_time.time())
	live_ips = live_ips or set()
	cols = ["#", "IP", "Hostname", "MAC", "Expires", "Type"]
	header = "".join(f'<th style="{_TH}">{c}</th>' for c in cols)

	rows = []
	for i, lease in enumerate(leases, 1):
		state = lease.get("state", 0)
		ip = lease.get("ip-address", "")
		mac = lease.get("hw-address", "") or "—"
		ttl = lease.get("cltt", 0) + lease.get("valid-lft", 86400) - now
		ltype = lease_type(lease, fixed_ips, reserved_macs, name_hosts)
		hn = real_hostname(lease, override_ips, override_macs) or "—"
		td = _TD_LIVE if ip in live_ips else _TD

		rows.append(
			f"<tr>"
			f'<td style="{td}">{_row_chip(i, state)}</td>'
			f'<td style="{td}">{ip}</td>'
			f'<td style="{td}">{hn}</td>'
			f'<td style="{td}">{html_safe_mac(mac)}</td>'
			f'<td style="{td}">{fmt_ttl(ttl)}</td>'
			f'<td style="{td}">{_type_chip(ltype)}</td>'
			f"</tr>"
		)

	return (
		f'<div style="overflow-x:auto">'
		f'<table style="width:100%;border-collapse:collapse">'
		f"<thead><tr>{header}</tr></thead>"
		f"<tbody>{''.join(rows)}</tbody>"
		f"</table>"
		f"</div>"
	)


# --- Dialogs -----------------------------------------------------------------


@st.dialog("Add lease")
def add_lease_dialog() -> None:
	st.caption("Manually injects a lease. Useful for pre-registering a device.")
	ip = st.text_input("IP address *", placeholder="172.16.17.x")
	mac = st.text_input("MAC address *", placeholder="aa:bb:cc:dd:ee:ff")
	hostname = st.text_input("Hostname", placeholder="device-name")
	valid_lft = st.number_input(
		"Duration (seconds)", min_value=60, max_value=604800, value=86400, step=3600
	)
	c1, c2 = st.columns(2)
	with c1:
		if st.button("Add lease", type="primary", key="dialog_save"):
			if not ip.strip() or not mac.strip():
				st.error("IP and MAC are required.")
				return
			try:
				get_client().add_lease(ip.strip(), mac.strip(), hostname.strip(), int(valid_lft))
				load_leases.clear()
				load_pool_stats.clear()
				st.rerun()
			except KeaError as e:
				st.error(str(e))
	with c2:
		if st.button("Cancel", key="dialog_cancel"):
			st.rerun()


@st.dialog("Edit lease")
def edit_lease_dialog(lease: dict) -> None:
	ip = lease.get("ip-address", "")
	mac = st.text_input("MAC address", value=lease.get("hw-address", ""))
	hn = st.text_input("Hostname", value=lease.get("hostname", ""))
	vlt = st.number_input(
		"Duration (seconds)", min_value=60, max_value=604800, value=86400, step=3600
	)
	st.caption(f"Updating lease for {ip}. Sets a new expiry from now + duration.")
	c1, c2 = st.columns(2)
	with c1:
		if st.button("Save", type="primary", key="dialog_save"):
			try:
				get_client().update_lease(ip, mac.strip(), hn.strip(), int(vlt))
				load_leases.clear()
				st.rerun()
			except KeaError as e:
				st.error(str(e))
	with c2:
		if st.button("Cancel", key="dialog_cancel"):
			st.rerun()


# --- Liveness sweep ----------------------------------------------------------


def _run_liveness_sweep(ip_addresses: list[str]) -> None:
	"""ARP-sweep every lease address and stash the responders in session state.

	The sweep itself runs in keanexus-quarantine, which is on the LAN's L2
	segment and so can use ARP — see quarantine_client.trigger_liveness_sweep.
	Errors are shown rather than raised: an unreachable optional service is an
	ordinary condition here, and it must not take the whole Leases tab down.
	"""
	try:
		with st.spinner("ARP-sweeping the network..."):
			responding = trigger_liveness_sweep([ip for ip in ip_addresses if ip])
	except QuarantineServiceError as e:
		st.error(f"Liveness check failed: {e}")
		return

	st.session_state[_LIVE_IPS_KEY] = set(responding)
	st.session_state[_LIVE_CHECKED_AT_KEY] = _time.time()


def _age_label(seconds: float) -> str:
	if seconds < 60:
		return "just now"
	if seconds < 3600:
		return f"{int(seconds // 60)}m ago"
	return f"{int(seconds // 3600)}h ago"


def _render_liveness_caption(live_ips: Optional[set[str]], total_leases: int) -> None:
	"""Explain what the green rows mean, and how old that answer is.

	Without the age, a sweep from an hour ago looks exactly like one from a
	second ago — and a device that has since been switched off would still
	be showing green.
	"""
	if live_ips is None:
		st.caption(
			"Kea only knows which addresses are *leased*, not which devices are "
			"actually powered on — a lease outlives the device by hours. "
			"**Check who's online** ARP-sweeps every lease and turns the "
			"responding rows green."
		)
		return

	checked_at = st.session_state.get(_LIVE_CHECKED_AT_KEY, _time.time())
	age = _age_label(_time.time() - checked_at)
	st.caption(
		f"**{len(live_ips)} of {total_leases}** leases answered ARP ({age}) — "
		f"shown in green. The rest hold a lease but didn't respond, so the "
		f"device is most likely powered off or has left the network."
	)


# --- Main render -------------------------------------------------------------


def render_leases(leases: list[dict], config: Optional[dict] = None) -> None:
	fixed_ips, reserved_macs, name_hosts = build_reservation_type_sets(config)
	override_ips, override_macs = build_hostname_override_sets(config)

	# Sort by IP ascending
	sorted_leases = sorted(
		leases,
		key=lambda lease: KeaClient.ip_to_int(lease.get("ip-address", "0.0.0.0")),
	)

	# Captured before the filter narrows the list: a sweep covers every lease
	# regardless of what's on screen, so its result stays valid as the user
	# types in the filter box rather than going stale on each keystroke.
	all_lease_ips = [lease.get("ip-address", "") for lease in sorted_leases]

	fc, bc, cc = st.columns([4, 1.6, 1])
	with fc:
		q = st.text_input(
			"Filter", placeholder="IP, hostname or MAC...", label_visibility="collapsed"
		)
	with bc:
		if st.button("Check who's online", key="leases_liveness_chipblue"):
			_run_liveness_sweep(all_lease_ips)
	with cc:
		st.markdown(
			f"<div style='text-align:right;padding-top:8px;color:#6e7681;"
			f"font-size:12px'>{len(sorted_leases)} leases</div>",
			unsafe_allow_html=True,
		)

	if q:
		q_lower = q.lower()
		sorted_leases = [
			lease
			for lease in sorted_leases
			if q_lower in lease.get("ip-address", "").lower()
			or q_lower in (lease.get("hostname") or "").lower()
			or q_lower in (lease.get("hw-address") or "").lower()
		]

	live_ips = st.session_state.get(_LIVE_IPS_KEY)

	st.markdown(
		_lease_table(
			sorted_leases,
			fixed_ips,
			reserved_macs,
			name_hosts,
			override_ips,
			override_macs,
			live_ips,
		),
		unsafe_allow_html=True,
	)
	_render_liveness_caption(live_ips, len(all_lease_ips))

	st.divider()

	with st.expander("Advanced lease operations"):
		lc1, lc2, lc3 = st.columns([2, 3, 1])
		with lc1:
			ltype = st.selectbox(
				"Lookup by", ["IP Address", "MAC Address", "Hostname"], label_visibility="collapsed"
			)
		with lc2:
			lval = st.text_input(
				"Value",
				placeholder="Enter value...",
				label_visibility="collapsed",
				key="leases_lookup_val",
			)
		with lc3:
			do_lookup = st.button("Search", key="leases_search_chipblue")

		if do_lookup and lval.strip():
			kea = get_client()
			try:
				if ltype == "IP Address":
					r = kea.get_lease_by_ip(lval.strip())
					found = [r] if r else []
				elif ltype == "MAC Address":
					found = kea.get_leases_by_mac(lval.strip())
				else:
					found = kea.get_leases_by_hostname(lval.strip())

				if not found:
					st.info("No leases found.")
				else:
					st.markdown(
						_lease_table(
							found,
							fixed_ips,
							reserved_macs,
							name_hosts,
							override_ips,
							override_macs,
							st.session_state.get(_LIVE_IPS_KEY),
						),
						unsafe_allow_html=True,
					)
					if ltype == "IP Address" and found:
						if st.button("Edit this lease", key="leases_editresult_chipblue"):
							edit_lease_dialog(found[0])
			except KeaError as e:
				st.error(str(e))

		st.divider()
		ac1, ac2 = st.columns(2)
		with ac1:
			if st.button("+ Add lease manually", key="leases_addtrigger_chipblue"):
				add_lease_dialog()
		with ac2:
			eip = st.text_input(
				"Edit lease by IP", placeholder="172.16.17.x", label_visibility="collapsed"
			)
			if st.button("Edit ->", key="leases_editbyip_chipblue") and eip.strip():
				lease = get_client().get_lease_by_ip(eip.strip())
				if lease:
					edit_lease_dialog(lease)
				else:
					st.warning(f"No lease found for {eip.strip()}")
