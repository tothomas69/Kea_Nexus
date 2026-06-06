"""
ui_ipam.py — IP Address Management tab for KeaNexus.

Displays all 256 addresses in the /24 subnet, classifying each as:
network, broadcast, gateway, leased, declined, reserved, static, scope (free
in pool), or free (outside pool, no record). Allows adding and editing static
records for out-of-scope addresses.
"""
import time as _time
from dataclasses import dataclass
from typing import Optional

import streamlit as st

from helpers import get_client, fmt_ttl
from db import get_static_entries, get_static_entry, upsert_static_entry, delete_static_entry


# --- Status constants ---------------------------------------------------------

_NET       = "network"
_BCAST     = "broadcast"
_GATEWAY   = "gateway"
_LEASED    = "leased"
_DECLINED  = "declined"
_RESERVED  = "reserved"
_STATIC    = "static"
_SCOPE     = "scope"    # in DHCP pool, currently unassigned
_FREE      = "free"     # outside pool, no record

_CHIP_STYLE: dict[str, tuple[str, str]] = {
	_NET:      ("#f6f8fa", "#57606a"),
	_BCAST:    ("#f6f8fa", "#57606a"),
	_GATEWAY:  ("#ddf4ff", "#0550ae"),
	_LEASED:   ("#dafbe1", "#1a7f37"),
	_DECLINED: ("#ffebe9", "#cf222e"),
	_RESERVED: ("#fff8c5", "#7d4e00"),
	_STATIC:   ("#fbefff", "#8250df"),
	_SCOPE:    ("#f0f6ff", "#0969da"),
	_FREE:     ("#f6f8fa", "#57606a"),
}

_COL_WIDTHS  = [0.8, 2.0, 2.0, 2.3, 1.3, 1.8, 1.1]
_COL_HEADERS = ["#", "IP Address", "Hostname", "MAC", "Status", "Info", ""]
_CELL        = "font-family:monospace;font-size:13px;color:#1f2328"


# --- IP row data model --------------------------------------------------------

@dataclass
class _IpRow:
	"""Fully resolved state for a single IP address in the subnet."""
	ip: str
	last_octet: int
	status: str
	hostname: str = ""
	mac: str = ""
	info: str = ""     # expiry for leases, notes for static, "reserved" for reservations
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
) -> list[_IpRow]:
	"""Return one _IpRow for every address .0–.255 in the subnet."""
	now        = int(_time.time())
	lease_map  = {l["ip-address"]: l for l in leases  if "ip-address" in l}
	res_map    = {r["ip-address"]: r for r in reservations if "ip-address" in r}
	static_map = {s["ip_address"]: s for s in static_entries}

	rows = []
	for octet in range(256):
		ip  = f"{prefix}.{octet}"
		row = _IpRow(ip=ip, last_octet=octet, status=_FREE)

		if octet == 0:
			row.status = _NET
		elif octet == 255:
			row.status = _BCAST
		elif ip == gateway_ip:
			row.status   = _GATEWAY
			row.hostname = "gateway"
			row.editable = True
		elif ip in lease_map:
			lease        = lease_map[ip]
			row.status   = _DECLINED if lease.get("state", 0) == 1 else _LEASED
			row.hostname = lease.get("hostname", "") or ""
			row.mac      = lease.get("hw-address", "") or ""
			ttl          = lease.get("cltt", 0) + lease.get("valid-lft", 86400) - now
			row.info     = fmt_ttl(ttl)
		elif ip in res_map:
			res          = res_map[ip]
			row.status   = _RESERVED
			row.hostname = res.get("hostname", "") or ""
			row.mac      = res.get("hw-address", "") or ""
			row.info     = "reserved"
		elif ip in static_map:
			s            = static_map[ip]
			row.status   = _STATIC
			row.hostname = s.get("hostname", "") or ""
			row.mac      = s.get("mac_address", "") or ""
			row.info     = s.get("notes", "") or s.get("description", "") or ""
			row.editable = True
		elif pool_start_octet <= octet <= pool_end_octet:
			row.status = _SCOPE
		else:
			row.status   = _FREE
			row.editable = True

		rows.append(row)
	return rows


# --- HTML rendering -----------------------------------------------------------

def _chip(label: str, status: str) -> str:
	bg, fg = _CHIP_STYLE.get(status, _CHIP_STYLE[_FREE])
	return (
		f'<span style="background:{bg};color:{fg};font-size:11px;font-weight:600;'
		f'padding:2px 9px;border-radius:10px;display:inline-block;white-space:nowrap">{label}</span>'
	)


def _row_num_chip(octet: int, status: str) -> str:
	bg, color = _CHIP_STYLE.get(status, _CHIP_STYLE[_FREE])
	return (
		f'<span style="background:{bg};color:{color};font-size:11px;font-weight:700;'
		f'padding:2px 9px;border-radius:10px;display:inline-block;white-space:nowrap">{octet}</span>'
	)


@st.dialog("Edit Static Entry")
def _edit_dialog(ip: str, prefix: str, pool_start_octet: int, pool_end_octet: int) -> None:
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
		if st.button("Close"):
			st.rerun()
		return

	existing = get_static_entry(ip)
	st.caption(f"{'Editing' if existing else 'New record for'} **{ip}**")

	hostname = st.text_input("Hostname",    value=existing.get("hostname", "")    if existing else "")
	mac      = st.text_input("MAC Address", value=existing.get("mac_address", "") if existing else "",
	                          placeholder="aa:bb:cc:dd:ee:ff")
	desc     = st.text_input("Description", value=existing.get("description", "") if existing else "")
	notes    = st.text_area("Notes",        value=existing.get("notes", "")       if existing else "",
	                         height=80)

	c1, c2, c3 = st.columns(3)
	with c1:
		if st.button("Save", type="primary", use_container_width=True):
			upsert_static_entry(ip, hostname.strip(), mac.strip(), desc.strip(), notes.strip())
			st.rerun()
	with c2:
		if existing and st.button("Delete", use_container_width=True):
			delete_static_entry(ip)
			st.rerun()
	with c3:
		if st.button("Cancel", use_container_width=True):
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
	# Header as a single CSS grid element — matches st.columns fr proportions
	col_template = " ".join(f"{w}fr" for w in _COL_WIDTHS)
	cells = "".join(f'<div style="{_HEADER_CELL}">{h}</div>' for h in _COL_HEADERS)
	st.markdown(
		f'<div style="display:grid;grid-template-columns:{col_template};gap:0.4rem;'
		f'border-bottom:2px solid #d0d7de;padding:6px 0 8px;margin-bottom:6px">'
		f'{cells}</div>',
		unsafe_allow_html=True,
	)

	for row in rows:
		c = st.columns(_COL_WIDTHS)
		c[0].markdown(_row_num_chip(row.last_octet, row.status), unsafe_allow_html=True)
		c[1].markdown(f'<span style="{_CELL};font-weight:600">{row.ip}</span>', unsafe_allow_html=True)
		c[2].markdown(f'<span style="{_CELL}">{row.hostname or "—"}</span>', unsafe_allow_html=True)
		c[3].markdown(f'<span style="{_CELL}">{row.mac or "—"}</span>', unsafe_allow_html=True)
		c[4].markdown(_chip(row.status, row.status), unsafe_allow_html=True)
		c[5].markdown(f'<span style="{_CELL}">{row.info or ""}</span>', unsafe_allow_html=True)
		# Network (.0) and broadcast (.255) have nothing to edit
		if row.last_octet not in (0, 255):
			if c[6].button("Edit", key=f"ipam_edit_{row.ip}", use_container_width=True):
				_edit_dialog(row.ip, prefix, pool_start_octet, pool_end_octet)


# --- Main render --------------------------------------------------------------

def render_ipam(leases: list[dict], config: Optional[dict]) -> None:
	"""Render the IPAM tab: full subnet map + static entry management."""
	if not config:
		st.error("Cannot render IPAM: Kea config is unavailable.")
		return

	subnet_str = (config.get("subnet4") or [{}])[0].get("subnet", "172.16.17.0/24")
	# "172.16.17.0/24" → "172.16.17"
	prefix = ".".join(subnet_str.split("/")[0].split(".")[:3])

	kea = get_client()
	pool_start_ip, pool_end_ip, _ = kea.get_pool_range(config)
	pool_start_octet = int(pool_start_ip.split(".")[-1])
	pool_end_octet   = int(pool_end_ip.split(".")[-1])

	gateway_ip = ""
	for opt in (config.get("subnet4") or [{}])[0].get("option-data", []):
		if opt.get("name") == "routers":
			gateway_ip = opt.get("data", "")

	reservations   = (config.get("subnet4") or [{}])[0].get("reservations", [])
	static_entries = get_static_entries()

	all_rows = _classify_all(
		prefix, pool_start_octet, pool_end_octet,
		gateway_ip, leases, reservations, static_entries,
	)

	# Status summary counts
	counts: dict[str, int] = {}
	for row in all_rows:
		counts[row.status] = counts.get(row.status, 0) + 1

	summary = "".join(
		_chip(f"{counts.get(s, 0)} {s}", s) + "&nbsp; "
		for s in [_LEASED, _DECLINED, _RESERVED, _STATIC, _SCOPE, _FREE]
		if counts.get(s, 0) > 0
	)
	st.markdown(f'<div style="margin-bottom:12px">{summary}</div>', unsafe_allow_html=True)

	# Filter controls
	fc, sc = st.columns([4, 2])
	with fc:
		q = st.text_input("Filter", placeholder="Search IP, hostname or MAC…",
		                  label_visibility="collapsed")
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
			r for r in visible
			if q_lower in r.ip
			or q_lower in r.hostname.lower()
			or q_lower in r.mac.lower()
		]

	_render_table(visible, prefix, pool_start_octet, pool_end_octet)
