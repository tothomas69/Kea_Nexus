"""
ui_leases.py - Leases tab for KeaNexus.
"""

import time as _time
from typing import Optional

import streamlit as st

from helpers import (
	build_reservation_type_sets,
	fmt_ttl,
	get_client,
	lease_type,
	load_leases,
	load_pool_stats,
	real_hostname,
)
from kea import KeaClient, KeaError

LOOKUP_COLS = ["IP", "Hostname", "MAC", "Expires", "Status"]


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
		f'<span style="background:{bg};color:{fg};font-size:11px;font-weight:600;'
		f'padding:2px 9px;border-radius:10px;display:inline-block">{label}</span>'
	)


def _lease_table(
	leases: list[dict],
	fixed_ips: set,
	reserved_macs: set,
	name_hosts: set,
) -> str:
	now = int(_time.time())
	cols = ["#", "IP", "Hostname", "MAC", "Expires", "Type"]
	header = "".join(f'<th style="{_TH}">{c}</th>' for c in cols)

	rows = []
	for i, lease in enumerate(leases, 1):
		state = lease.get("state", 0)
		ip = lease.get("ip-address", "")
		mac = lease.get("hw-address", "") or "—"
		ttl = lease.get("cltt", 0) + lease.get("valid-lft", 86400) - now
		ltype = lease_type(lease, fixed_ips, reserved_macs, name_hosts)
		hn = real_hostname(lease, ltype) or "—"

		rows.append(
			f"<tr>"
			f'<td style="{_TD}">{_row_chip(i, state)}</td>'
			f'<td style="{_TD}">{ip}</td>'
			f'<td style="{_TD}">{hn}</td>'
			f'<td style="{_TD}">{mac}</td>'
			f'<td style="{_TD}">{fmt_ttl(ttl)}</td>'
			f'<td style="{_TD}">{_type_chip(ltype)}</td>'
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


# --- Main render -------------------------------------------------------------


def render_leases(leases: list[dict], config: Optional[dict] = None) -> None:
	fixed_ips, reserved_macs, name_hosts = build_reservation_type_sets(config)

	# Sort by IP ascending
	sorted_leases = sorted(
		leases,
		key=lambda lease: KeaClient.ip_to_int(lease.get("ip-address", "0.0.0.0")),
	)

	fc, cc = st.columns([5, 1])
	with fc:
		q = st.text_input(
			"Filter", placeholder="IP, hostname or MAC...", label_visibility="collapsed"
		)
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

	st.markdown(
		_lease_table(sorted_leases, fixed_ips, reserved_macs, name_hosts),
		unsafe_allow_html=True,
	)

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
			do_lookup = st.button("Search", use_container_width=True)

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
						_lease_table(found, fixed_ips, reserved_macs, name_hosts),
						unsafe_allow_html=True,
					)
					if ltype == "IP Address" and found:
						if st.button("Edit this lease"):
							edit_lease_dialog(found[0])
			except KeaError as e:
				st.error(str(e))

		st.divider()
		ac1, ac2 = st.columns(2)
		with ac1:
			if st.button("+ Add lease manually", use_container_width=True):
				add_lease_dialog()
		with ac2:
			eip = st.text_input(
				"Edit lease by IP", placeholder="172.16.17.x", label_visibility="collapsed"
			)
			if st.button("Edit ->", use_container_width=True) and eip.strip():
				lease = get_client().get_lease_by_ip(eip.strip())
				if lease:
					edit_lease_dialog(lease)
				else:
					st.warning(f"No lease found for {eip.strip()}")
