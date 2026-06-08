"""
ui_reservations.py - Reservations tab for KeaNexus.

Renders all DHCP reservations as a styled HTML-column table with
per-row Edit dialogs. Each save writes directly to kea-dhcp4.conf
via config-set + config-write.
"""

import streamlit as st

from helpers import get_client, load_config
from kea import KeaClient, KeaError

_TH = (
	"font-family:monospace;font-size:11px;font-weight:700;color:#1f2328;"
	"text-transform:uppercase;letter-spacing:.05em;overflow:hidden"
)
_TD = "font-family:monospace;font-size:13px;color:#1f2328"

_COL_WIDTHS = [0.5, 2.0, 2.5, 2.5, 1.5, 1.0]
_COL_HEADERS = ["#", "IP Address", "Hostname", "MAC", "Type", ""]


# --- Helpers ------------------------------------------------------------------


def _type_chip(ip: str) -> str:
	"""Chip showing 'fixed' (has IP) or 'name-only'."""
	if ip:
		return (
			'<span style="background:#ddf4ff;color:#0550ae;font-size:11px;font-weight:600;'
			'padding:2px 9px;border-radius:10px;display:inline-block;white-space:nowrap">'
			"fixed</span>"
		)
	return (
		'<span style="background:#f6f8fa;color:#57606a;font-size:11px;font-weight:600;'
		'padding:2px 9px;border-radius:10px;display:inline-block;white-space:nowrap">'
		"name-only</span>"
	)


def _sorted_reservations(reservations: list[dict]) -> list[dict]:
	"""Fixed-IP reservations first (sorted by IP), then name-only."""
	return sorted(
		reservations,
		key=lambda r: (
			0 if r.get("ip-address") else 1,
			KeaClient.ip_to_int(r.get("ip-address", "")) if r.get("ip-address") else 0,
		),
	)


def _save_config(config: dict) -> None:
	"""Write config to Kea and clear the cache."""
	get_client().save_config(config)
	load_config.clear()


# --- Dialogs ------------------------------------------------------------------


@st.dialog("Add Reservation")
def add_reservation_dialog(config: dict) -> None:
	"""Add a new DHCP reservation to the Kea config."""
	st.caption("Saves to kea-dhcp4.conf via config-set + config-write.")
	ip = st.text_input("IP address", placeholder="172.16.17.x  (blank = name-only)")
	hn = st.text_input("Hostname *")
	mac = st.text_input("MAC address *", placeholder="aa:bb:cc:dd:ee:ff")
	c1, c2 = st.columns(2)
	with c1:
		if st.button("Save", type="primary", use_container_width=True):
			if not mac.strip() or not hn.strip():
				st.error("MAC and hostname are required.")
				return
			entry: dict = {"hw-address": mac.strip(), "hostname": hn.strip()}
			if ip.strip():
				entry["ip-address"] = ip.strip()
			config["subnet4"][0]["reservations"].append(entry)
			try:
				_save_config(config)
				st.rerun()
			except KeaError as e:
				config["subnet4"][0]["reservations"].pop()
				st.error(str(e))
	with c2:
		if st.button("Cancel", use_container_width=True):
			st.rerun()


@st.dialog("Edit Reservation")
def _edit_dialog(reservation: dict, config: dict) -> None:
	"""Edit or delete an existing DHCP reservation."""
	ip = st.text_input(
		"IP address",
		value=reservation.get("ip-address", ""),
		placeholder="172.16.17.x  (blank = name-only)",
	)
	hn = st.text_input("Hostname *", value=reservation.get("hostname", ""))
	mac = st.text_input(
		"MAC address *", value=reservation.get("hw-address", ""), placeholder="aa:bb:cc:dd:ee:ff"
	)
	c1, c2, c3 = st.columns(3)
	with c1:
		if st.button("Save", type="primary", use_container_width=True):
			if not mac.strip() or not hn.strip():
				st.error("MAC and hostname are required.")
				return
			updated: dict = {"hw-address": mac.strip(), "hostname": hn.strip()}
			if ip.strip():
				updated["ip-address"] = ip.strip()
			res_list = config["subnet4"][0]["reservations"]
			# reservation is the same dict object from the config list — identity is safe
			for idx, r in enumerate(res_list):
				if r is reservation:
					res_list[idx] = updated
					break
			try:
				_save_config(config)
				st.rerun()
			except KeaError as e:
				st.error(str(e))
	with c2:
		if st.button("Delete", use_container_width=True):
			res_list = config["subnet4"][0]["reservations"]
			try:
				res_list.remove(reservation)
			except ValueError:
				pass
			try:
				_save_config(config)
				st.rerun()
			except KeaError as e:
				st.error(str(e))
	with c3:
		if st.button("Cancel", use_container_width=True):
			st.rerun()


# --- Table rendering ----------------------------------------------------------


def _render_table(reservations: list[dict], config: dict) -> None:
	"""Render reservations as CSS-grid header + st.columns data rows."""
	col_template = " ".join(f"{w}fr" for w in _COL_WIDTHS)
	header_cells = "".join(f'<div style="{_TH}">{h}</div>' for h in _COL_HEADERS)
	st.markdown(
		f'<div style="display:grid;grid-template-columns:{col_template};gap:0.4rem;'
		f'border-bottom:2px solid #d0d7de;padding:6px 0 8px;margin-top:12px;margin-bottom:6px">'
		f"{header_cells}</div>",
		unsafe_allow_html=True,
	)

	for i, res in enumerate(_sorted_reservations(reservations), 1):
		ip = res.get("ip-address", "")
		hn = res.get("hostname", "")
		mac = res.get("hw-address", "")

		cols = st.columns(_COL_WIDTHS)
		cols[0].markdown(f'<span style="{_TD};font-weight:600">{i}</span>', unsafe_allow_html=True)
		cols[1].markdown(f'<span style="{_TD}">{ip or "—"}</span>', unsafe_allow_html=True)
		cols[2].markdown(f'<span style="{_TD}">{hn or "—"}</span>', unsafe_allow_html=True)
		cols[3].markdown(f'<span style="{_TD}">{mac or "—"}</span>', unsafe_allow_html=True)
		cols[4].markdown(_type_chip(ip), unsafe_allow_html=True)
		if cols[5].button("Edit", key=f"res_edit_{i}", use_container_width=True):
			_edit_dialog(res, config)


# --- Main render --------------------------------------------------------------


def render_reservations(config) -> None:
	"""Render the Reservations tab: count, + Add button, and reservation table."""
	if config is None:
		st.error("Could not load Kea config. Check connection.")
		return

	reservations = config.get("subnet4", [{}])[0].get("reservations", [])

	header_col, btn_col = st.columns([5, 1])
	with header_col:
		st.caption(f"{len(reservations)} reservations")
	with btn_col:
		if st.button("+ Add", use_container_width=True):
			add_reservation_dialog(config)

	_render_table(reservations, config)
