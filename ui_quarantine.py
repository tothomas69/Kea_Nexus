"""
ui_quarantine.py — Device registry tab for the quarantine feature.

This tab manages the device_registry table, the source of truth for "who is
this device" used by the keanexus-quarantine service. Identity here is
keyed on friendly_name, not MAC address, since MAC can be changed by the
device itself — see docs/quarantine-feature-design.md.

This tab is read-only with respect to enforcement: it cannot trigger a
quarantine or release action. It only manages registry entries and displays
the audit log that the quarantine service writes to.
"""

import streamlit as st

from db import (
	delete_device,
	get_device,
	get_devices,
	get_quarantine_log,
	upsert_device,
)

_CELL = "font-family:monospace;font-size:11px;color:#1f2328"
_HEADER_CELL = (
	"font-family:monospace;font-size:11px;font-weight:700;color:#1f2328;"
	"text-transform:uppercase;letter-spacing:.05em;overflow:hidden"
)

_DEVICE_COL_WIDTHS = [1.6, 1.6, 1.1, 1.6, 1.4, 1.6, 1.0]
_DEVICE_COL_HEADERS = [
	"Friendly Name",
	"Hostname",
	"Group",
	"Last MAC",
	"Last IP",
	"Last Quarantined",
	"",
]

_LOG_COL_WIDTHS = [1.8, 1.4, 1.4, 1.0, 1.0, 1.6, 2.4]
_LOG_COL_HEADERS = [
	"Friendly Name",
	"Action",
	"Step",
	"OK",
	"Attempts",
	"Occurred At",
	"Detail",
]


def _render_grid_header(widths: list[float], headers: list[str]) -> None:
	col_template = " ".join(f"{w}fr" for w in widths)
	cells = "".join(f'<div style="{_HEADER_CELL}">{h}</div>' for h in headers)
	st.markdown(
		f'<div style="display:grid;grid-template-columns:{col_template};gap:0.4rem;'
		f'border-bottom:2px solid #d0d7de;padding:6px 0 8px;margin-bottom:6px">'
		f"{cells}</div>",
		unsafe_allow_html=True,
	)


@st.dialog("Device Registry Entry")
def _edit_dialog(friendly_name: str = "") -> None:
	"""Add or edit a device_registry entry. Delete is offered when editing."""
	existing = get_device(friendly_name) if friendly_name else None
	is_new = existing is None

	st.caption("New registry entry" if is_new else f"Editing **{friendly_name}**")

	name_input = st.text_input(
		"Friendly Name",
		value=friendly_name,
		disabled=not is_new,
		help="Stable identifier used to trigger quarantine actions, e.g. 'tommy_laptop'. "
		"Cannot be changed after creation — delete and re-add instead.",
	)
	hostname = st.text_input("Hostname", value=existing.get("hostname", "") if existing else "")
	group_tag = st.text_input(
		"Group Tag",
		value=existing.get("group_tag", "") if existing else "",
		help="Optional. Devices sharing a group tag can be quarantined together.",
	)
	notes = st.text_area("Notes", value=existing.get("notes", "") if existing else "", height=80)

	if existing:
		st.markdown(
			f'<div style="{_CELL};margin-top:4px">'
			f"OS fingerprint: {existing.get('os_fingerprint', '') or '—'}<br>"
			f"Last seen MAC: {existing.get('last_seen_mac_address', '') or '—'}<br>"
			f"Last seen IP: {existing.get('last_seen_ip_address', '') or '—'}<br>"
			f"Last quarantined: {existing.get('last_quarantined_at', '') or '—'}"
			f"</div>",
			unsafe_allow_html=True,
		)
		st.caption(
			"Fingerprint and last-seen fields are written by the quarantine "
			"service and cannot be edited here."
		)

	c1, c2, c3 = st.columns(3)
	with c1:
		if st.button("Save", type="primary", use_container_width=True):
			if not name_input.strip() or not hostname.strip():
				st.error("Friendly Name and Hostname are required.")
			else:
				upsert_device(
					name_input.strip(),
					hostname.strip(),
					group_tag=group_tag.strip(),
					os_fingerprint=existing.get("os_fingerprint", "") if existing else "",
					last_seen_mac_address=existing.get("last_seen_mac_address", "")
					if existing
					else "",
					last_seen_ip_address=existing.get("last_seen_ip_address", "")
					if existing
					else "",
					last_quarantined_at=existing.get("last_quarantined_at", "") if existing else "",
					notes=notes.strip(),
				)
				st.rerun()
	with c2:
		if existing and st.button("Delete", use_container_width=True):
			delete_device(friendly_name)
			st.rerun()
	with c3:
		if st.button("Cancel", use_container_width=True):
			st.rerun()


def _render_device_table(devices: list[dict]) -> None:
	_render_grid_header(_DEVICE_COL_WIDTHS, _DEVICE_COL_HEADERS)
	for device in devices:
		cols = st.columns(_DEVICE_COL_WIDTHS)
		cols[0].markdown(
			f'<span style="{_CELL};font-weight:600">{device["friendly_name"]}</span>',
			unsafe_allow_html=True,
		)
		cols[1].markdown(
			f'<span style="{_CELL}">{device["hostname"]}</span>', unsafe_allow_html=True
		)
		cols[2].markdown(
			f'<span style="{_CELL}">{device["group_tag"] or "—"}</span>', unsafe_allow_html=True
		)
		cols[3].markdown(
			f'<span style="{_CELL}">{device["last_seen_mac_address"] or "—"}</span>',
			unsafe_allow_html=True,
		)
		cols[4].markdown(
			f'<span style="{_CELL}">{device["last_seen_ip_address"] or "—"}</span>',
			unsafe_allow_html=True,
		)
		cols[5].markdown(
			f'<span style="{_CELL}">{device["last_quarantined_at"] or "—"}</span>',
			unsafe_allow_html=True,
		)
		if cols[6].button(
			"Edit", key=f"quarantine_edit_{device['friendly_name']}", use_container_width=True
		):
			_edit_dialog(device["friendly_name"])


def _render_log_table(entries: list[dict]) -> None:
	_render_grid_header(_LOG_COL_WIDTHS, _LOG_COL_HEADERS)
	for entry in entries:
		cols = st.columns(_LOG_COL_WIDTHS)
		cols[0].markdown(
			f'<span style="{_CELL}">{entry["friendly_name"]}</span>', unsafe_allow_html=True
		)
		cols[1].markdown(f'<span style="{_CELL}">{entry["action"]}</span>', unsafe_allow_html=True)
		cols[2].markdown(f'<span style="{_CELL}">{entry["step"]}</span>', unsafe_allow_html=True)
		ok_label = "✓" if entry["succeeded"] else "✗"
		cols[3].markdown(f'<span style="{_CELL}">{ok_label}</span>', unsafe_allow_html=True)
		cols[4].markdown(
			f'<span style="{_CELL}">{entry["attempt_count"]}</span>', unsafe_allow_html=True
		)
		cols[5].markdown(
			f'<span style="{_CELL}">{entry["occurred_at"]}</span>', unsafe_allow_html=True
		)
		cols[6].markdown(
			f'<span style="{_CELL}">{entry["detail"] or "—"}</span>', unsafe_allow_html=True
		)


def render_quarantine() -> None:
	"""Render the Quarantine tab: device registry CRUD + read-only audit log."""
	st.caption(
		"Identity registry for the network quarantine feature. Devices are "
		"identified by hostname, not MAC address, since MAC can be changed. "
		"This tab does not trigger quarantine or release actions directly — "
		"see docs/quarantine-feature-design.md."
	)

	if st.button("Add Device"):
		_edit_dialog()

	devices = get_devices()
	if devices:
		_render_device_table(devices)
	else:
		st.info("No devices registered yet.")

	st.markdown("---")
	st.markdown("**Recent Quarantine Activity**")

	log_entries = get_quarantine_log(limit=50)
	if log_entries:
		_render_log_table(log_entries)
	else:
		st.info("No quarantine actions have been logged yet.")
