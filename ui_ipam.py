"""
ui_ipam.py — IP Address Management tab for KeaNexus.

Displays all 256 addresses in the /24 subnet, classifying each as:
network, broadcast, gateway, leased, declined, reserved, static, scope (free
in pool), or free (outside pool, no record). Allows adding and editing static
records for out-of-scope addresses.
"""

import html as _html
import time as _time
from dataclasses import dataclass
from typing import Optional

import streamlit as st

from db import (
	delete_static_entry,
	get_reservation_labels,
	get_static_entries,
	get_static_entry,
	upsert_static_entry,
)
from helpers import build_hostname_override_sets, fmt_ttl, get_client, html_safe_mac, real_hostname
from ui_access import read_only, read_only_notice, refuse_if_viewer

# --- Status constants ---------------------------------------------------------

_NET = "network"
_BCAST = "broadcast"
_GATEWAY = "gateway"
_LEASED = "leased"
_DECLINED = "declined"
_RESERVED = "reserved"
_STATIC = "static"
_SCOPE = "scope"  # in DHCP pool, currently unassigned
_FREE = "free"  # outside pool, no record

_CHIP_STYLE: dict[str, tuple[str, str]] = {
	_NET: ("#f6f8fa", "#57606a"),
	_BCAST: ("#f6f8fa", "#57606a"),
	_GATEWAY: ("#ddf4ff", "#0550ae"),
	_LEASED: ("#dafbe1", "#1a7f37"),
	_DECLINED: ("#ffebe9", "#cf222e"),
	_RESERVED: ("#fff8c5", "#7d4e00"),
	_STATIC: ("#fbefff", "#8250df"),
	_SCOPE: ("#f0f6ff", "#0969da"),
	_FREE: ("#f6f8fa", "#57606a"),
}

# Column order: IP | Status | Label | Real Hostname | MAC | Expires | Edit
_COL_WIDTHS = [1.8, 1.2, 2.0, 2.0, 2.1, 1.2, 0.9]
_COL_HEADERS = ["IP Address", "Status", "Label", "Real Hostname", "MAC", "Expires", ""]
_STATUS_COL = 1  # header for this column is centered to match its chip
_CELL = "font-family:monospace;font-size:11px;color:#1f2328"
_HEADER_CENTER = ";text-align:center"


# --- IP row data model --------------------------------------------------------


@dataclass
class _IpRow:
	"""Fully resolved state for a single IP address in the subnet."""

	ip: str
	last_octet: int
	status: str
	label: str = ""  # admin-assigned name: reservation label, static hostname, or "gateway"
	hostname: str = ""  # ONLY the device's real DHCP-negotiated hostname, from a live lease
	mac: str = ""
	info: str = ""  # time-to-expiry for leases, "offline" for reservations with no lease
	notes: str = ""  # tooltip on status chip (static entries: description + notes)
	editable: bool = False  # True when this address accepts a static record


# --- Classification -----------------------------------------------------------


def _classify_all(
	prefix: str,
	pool_start_octet: int,
	pool_end_octet: int,
	gateway_ip: str,
	leases: list[dict],
	reservations: list[dict],
	static_entries: list[dict],
	labels: dict[str, str],
	override_ips: set,
	override_macs: set,
) -> list[_IpRow]:
	"""Return one _IpRow for every address .0–.255 in the subnet.

	`labels` maps lowercase MAC -> reservation label. The override sets come
	from build_hostname_override_sets() so a legacy Kea-side reservation
	"hostname" never masquerades as the device's real hostname.
	"""
	now = int(_time.time())
	lease_map = {lease["ip-address"]: lease for lease in leases if "ip-address" in lease}
	res_map = {r["ip-address"]: r for r in reservations if "ip-address" in r}
	static_map = {s["ip_address"]: s for s in static_entries}

	rows = []
	for octet in range(256):
		ip = f"{prefix}.{octet}"
		row = _IpRow(ip=ip, last_octet=octet, status=_FREE)

		if octet == 0:
			row.status = _NET
		elif octet == 255:
			row.status = _BCAST
		elif ip == gateway_ip:
			row.status = _GATEWAY
			row.label = "gateway"
			row.editable = True
		elif ip in lease_map:
			lease = lease_map[ip]
			if lease.get("state", 0) == 1:
				row.status = _DECLINED
			elif ip in res_map:
				# Active lease for a fixed reservation — show the permanent status
				row.status = _RESERVED
			else:
				row.status = _LEASED
			# real_hostname() returns "" when Kea is echoing a reservation override
			row.hostname = real_hostname(lease, override_ips, override_macs) or ""
			row.mac = lease.get("hw-address", "") or ""
			if ip in res_map:
				# Same fallback as the Reservations tab: SQLite label first, then
				# any legacy Kea "hostname" on a not-yet-resaved reservation
				res = res_map[ip]
				res_mac = (res.get("hw-address", "") or row.mac).lower()
				row.label = labels.get(res_mac, "") or res.get("hostname", "") or ""
			ttl = lease.get("cltt", 0) + lease.get("valid-lft", 86400) - now
			row.info = fmt_ttl(ttl)
		elif ip in res_map:
			res = res_map[ip]
			row.status = _RESERVED
			row.mac = res.get("hw-address", "") or ""
			row.label = labels.get(row.mac.lower(), "") or res.get("hostname", "") or ""
			# No lease means no real hostname to show; "offline" replaces the old
			# redundant "reserved" text (the status chip already says that)
			row.info = "offline"
		elif ip in static_map:
			s = static_map[ip]
			row.status = _STATIC
			# Static "hostname" is admin-typed, not device-reported -> it's a label.
			# Real Hostname stays empty since there's no lease to report one.
			row.label = s.get("hostname", "") or ""
			row.mac = s.get("mac_address", "") or ""
			# Description no longer has a column; surface it in the hover tooltip
			row.notes = " — ".join(
				part for part in (s.get("description", ""), s.get("notes", "")) if part
			)
			row.editable = True
		elif pool_start_octet <= octet <= pool_end_octet:
			row.status = _SCOPE
		else:
			row.status = _FREE
			row.editable = True

		rows.append(row)
	return rows


# --- HTML rendering -----------------------------------------------------------


def _chip(label: str, status: str, tooltip: str = "") -> str:
	bg, fg = _CHIP_STYLE.get(status, _CHIP_STYLE[_FREE])
	tooltip_attr = f' data-tooltip="{_html.escape(tooltip, quote=True)}"' if tooltip else ""
	return (
		f'<span{tooltip_attr} style="background:{bg};color:{fg};font-size:11px;font-weight:600;'
		f'padding:2px 9px;border-radius:10px;display:inline-block;white-space:nowrap">{label}</span>'
	)


@st.dialog("Edit Static Entry")
def _edit_dialog(
	ip: str,
	prefix: str,
	pool_start_octet: int,
	pool_end_octet: int,
	known_mac: str = "",
	known_hostname: str = "",
) -> None:
	"""Add, edit, or delete the static record for a given IP."""
	try:
		octet = int(ip.split(".")[-1])
	except ValueError:
		st.error("Invalid IP address.")
		return

	if pool_start_octet <= octet <= pool_end_octet:
		st.warning(
			f"**{ip}** is within the DHCP scope "
			f"({prefix}.{pool_start_octet}–{prefix}.{pool_end_octet}). "
			f"Static records are only for out-of-scope addresses."
		)
		if st.button("Close", key="ipam_close_chipblue"):
			st.rerun()
		return

	existing = get_static_entry(ip)
	st.caption(f"{'Editing' if existing else 'New record for'} **{ip}**")

	hostname = st.text_input(
		"Hostname",
		value=existing.get("hostname", "") if existing else known_hostname,
	)
	mac = st.text_input(
		"MAC Address",
		value=existing.get("mac_address", "") if existing else known_mac,
		placeholder="aa:bb:cc:dd:ee:ff",
	)
	desc = st.text_input("Description", value=existing.get("description", "") if existing else "")
	notes = st.text_area("Notes", value=existing.get("notes", "") if existing else "", height=80)

	c1, c2, c3 = st.columns(3)
	with c1:
		if st.button("Save", type="primary", key="dialog_save"):
			if refuse_if_viewer():
				return
			upsert_static_entry(ip, hostname.strip(), mac.strip(), desc.strip(), notes.strip())
			st.rerun()
	with c2:
		if existing and st.button("Delete", key="dialog_delete"):
			if refuse_if_viewer():
				return
			delete_static_entry(ip)
			st.rerun()
	with c3:
		if st.button("Cancel", key="dialog_cancel"):
			st.rerun()


_HEADER_CELL = (
	"font-family:monospace;font-size:11px;font-weight:700;color:#1f2328;"
	"text-transform:uppercase;letter-spacing:.05em;overflow:hidden"
)


def _render_table(
	rows: list[_IpRow],
	prefix: str,
	pool_start_octet: int,
	pool_end_octet: int,
) -> None:
	"""Render IPAM rows as Streamlit columns so Edit buttons are functional.

	The header is pure HTML (CSS grid) so it is unaffected by column-row CSS.
	"""
	# Checked once here, not per row: it reads the users table, and the map has 256 rows.
	viewer = read_only()
	# Header as a single CSS grid element — matches st.columns fr proportions
	# Status column (index 1) is centered to match its chip content
	col_template = " ".join(f"{w}fr" for w in _COL_WIDTHS)
	header_styles = [
		_HEADER_CELL + (_HEADER_CENTER if i == _STATUS_COL else "")
		for i in range(len(_COL_HEADERS))
	]
	cells = "".join(
		f'<div style="{style}">{h}</div>' for style, h in zip(header_styles, _COL_HEADERS)
	)
	st.markdown(
		f'<div style="display:grid;grid-template-columns:{col_template};gap:0.4rem;'
		f'border-bottom:2px solid #d0d7de;padding:6px 0 8px;margin-bottom:6px">'
		f"{cells}</div>",
		unsafe_allow_html=True,
	)

	for row in rows:
		c = st.columns(_COL_WIDTHS)
		c[0].markdown(
			f'<span style="{_CELL};font-weight:600">{row.ip}</span>', unsafe_allow_html=True
		)
		c[1].markdown(
			f'<div style="text-align:center">{_chip(row.status, row.status, tooltip=row.notes)}</div>',
			unsafe_allow_html=True,
		)
		# Static entries get the purple chip on their (admin-typed) label
		label_html = (
			_chip(row.label, _STATIC)
			if row.status == _STATIC and row.label
			else f'<span style="{_CELL}">{row.label or "—"}</span>'
		)
		c[2].markdown(label_html, unsafe_allow_html=True)
		c[3].markdown(f'<span style="{_CELL}">{row.hostname or "—"}</span>', unsafe_allow_html=True)
		c[4].markdown(
			f'<span style="{_CELL}">{html_safe_mac(row.mac) or "—"}</span>', unsafe_allow_html=True
		)
		c[5].markdown(f'<span style="{_CELL}">{row.info or ""}</span>', unsafe_allow_html=True)
		# Network (.0) and broadcast (.255) have nothing to edit
		if row.last_octet not in (0, 255):
			if c[6].button("Edit", key=f"ipam_edit_{row.ip}_chipblue", disabled=viewer):
				_edit_dialog(
					row.ip,
					prefix,
					pool_start_octet,
					pool_end_octet,
					known_mac=row.mac,
					known_hostname=row.label or row.hostname,
				)


# --- Main render --------------------------------------------------------------


def render_ipam(leases: list[dict], config: Optional[dict]) -> None:
	"""Render the IPAM tab: full subnet map + static entry management."""
	if not config:
		st.error("Cannot render IPAM: Kea config is unavailable.")
		return
	read_only_notice()

	subnet_str = (config.get("subnet4") or [{}])[0].get("subnet", "172.16.17.0/24")
	# "172.16.17.0/24" → "172.16.17"
	prefix = ".".join(subnet_str.split("/")[0].split(".")[:3])

	kea = get_client()
	pool_start_ip, pool_end_ip, _ = kea.get_pool_range(config)
	pool_start_octet = int(pool_start_ip.split(".")[-1])
	pool_end_octet = int(pool_end_ip.split(".")[-1])

	gateway_ip = ""
	for opt in (config.get("subnet4") or [{}])[0].get("option-data", []):
		if opt.get("name") == "routers":
			gateway_ip = opt.get("data", "")

	reservations = (config.get("subnet4") or [{}])[0].get("reservations", [])
	static_entries = get_static_entries()
	labels = {r["mac_address"].lower(): r["label"] for r in get_reservation_labels()}
	override_ips, override_macs = build_hostname_override_sets(config)

	all_rows = _classify_all(
		prefix,
		pool_start_octet,
		pool_end_octet,
		gateway_ip,
		leases,
		reservations,
		static_entries,
		labels,
		override_ips,
		override_macs,
	)

	# Status summary counts
	counts: dict[str, int] = {}
	for row in all_rows:
		counts[row.status] = counts.get(row.status, 0) + 1

	named_static_count = sum(1 for r in all_rows if r.status == _STATIC and r.label)

	summary = "".join(
		_chip(f"{counts.get(s, 0)} {s}", s) + "&nbsp; "
		for s in [_LEASED, _DECLINED, _RESERVED, _STATIC, _SCOPE, _FREE]
		if counts.get(s, 0) > 0
	)
	if named_static_count > 0:
		summary += _chip(f"{named_static_count} labeled", _STATIC) + "&nbsp; "
	st.markdown(f'<div style="margin-bottom:12px">{summary}</div>', unsafe_allow_html=True)

	# Filter controls
	fc, sc = st.columns([4, 2])
	with fc:
		q = st.text_input(
			"Filter", placeholder="Search IP, label, hostname or MAC…", label_visibility="collapsed"
		)
	with sc:
		status_filter = st.selectbox(
			"Status",
			["All statuses", _LEASED, _DECLINED, _RESERVED, _STATIC, _SCOPE, _FREE, _GATEWAY],
			label_visibility="collapsed",
		)

	# Apply filters
	visible = all_rows
	if status_filter != "All statuses":
		visible = [r for r in visible if r.status == status_filter]
	if q:
		q_lower = q.lower()
		visible = [
			r
			for r in visible
			if q_lower in r.ip
			or q_lower in r.label.lower()
			or q_lower in r.hostname.lower()
			or q_lower in r.mac.lower()
		]

	_render_table(visible, prefix, pool_start_octet, pool_end_octet)
