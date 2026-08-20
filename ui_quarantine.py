"""
ui_quarantine.py — Device registry tab for the quarantine feature.

This tab manages the device_registry table, the source of truth for "who is
this device" used by the keanexus-quarantine service. Identity here is
keyed on friendly_name, not MAC address, since MAC can be changed by the
device itself — see docs/quarantine-feature-design.md.

Registry CRUD and enforcement are otherwise separate concerns, but this tab
also offers direct Quarantine/Release buttons per device as a testing and
manual-override convenience — they call keanexus-quarantine's own API via
quarantine_client.py, the same API a Siri Shortcut would call.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from db import (
	delete_device,
	get_device,
	get_devices,
	get_quarantine_log,
	upsert_device,
)
from helpers import distinct_real_hostnames, html_safe_mac
from quarantine_client import (
	QuarantineServiceError,
	trigger_presence_check,
	trigger_quarantine,
	trigger_release,
)

_CELL = "font-family:monospace;font-size:13px;color:#1f2328"
# MAC/IP columns are narrow enough that the default cell style can wrap them
# mid-address across two lines — nowrap keeps each address on one line.
_CELL_NOWRAP = _CELL + ";white-space:nowrap"
# Timestamps are long even after shortening ("08/18 16:11 EDT") — a smaller,
# muted style keeps them from dominating the row the way the raw ISO string
# (32+ characters) did.
_CELL_META = "font-family:monospace;font-size:11px;color:#6e7681"
_HEADER_CELL = (
	"font-family:monospace;font-size:12px;font-weight:700;color:#1f2328;"
	"text-transform:uppercase;letter-spacing:.05em;overflow:hidden"
)

_LOCAL_TZ = ZoneInfo("America/New_York")


def _format_local_time(iso_utc: str) -> str:
	"""Render a UTC ISO-8601 timestamp (db.py always stores UTC) as a short
	local-time string for display, e.g. "08/18 16:11 EDT".

	Storage stays UTC/ISO so log ordering and any other consumer of these
	columns keep working normally — this is purely a render-time concern.
	Uses zoneinfo (stdlib, no new dependency) so DST is handled correctly —
	a fixed UTC-5 offset would be wrong for roughly half the year. 24-hour
	clock, matching this project's "always UTC/military internally, convert
	for display" time handling convention.
	"""
	if not iso_utc:
		return "—"
	try:
		dt = datetime.fromisoformat(iso_utc)
	except ValueError:
		# Malformed/unexpected value — show it raw rather than crash the
		# whole table over one bad cell.
		return iso_utc
	return dt.astimezone(_LOCAL_TZ).strftime("%m/%d %H:%M %Z")


_DEVICE_COL_WIDTHS = [1.1, 0.95, 0.45, 1.5, 1.3, 1.0, 1.0, 1.2, 1.0, 0.8]
_DEVICE_COL_HEADERS = [
	"Friendly Name",
	"Hostname",
	"Group",
	"Last MAC",
	"Last IP",
	"Last Seen",
	"Last Quarantined",
	"Quarantine",
	"Release",
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

_ACTION_RESULT_KEY = "quarantine_last_action_result"


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
def _edit_dialog(
	friendly_name: str = "", leases: list[dict] | None = None, config: dict | None = None
) -> None:
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

	existing_hostname = existing.get("hostname", "") if existing else ""
	hostname_options = distinct_real_hostnames(leases or [], config)
	if existing_hostname and existing_hostname not in hostname_options:
		hostname_options = sorted({*hostname_options, existing_hostname})

	if hostname_options:
		hostname = st.selectbox(
			"Hostname",
			hostname_options,
			index=hostname_options.index(existing_hostname) if existing_hostname else 0,
			help="A device's real DHCP-negotiated hostname, not a reservation label — "
			"identity resolution matches leases by this value.",
		)
	else:
		st.caption(
			"No real hostnames currently observed in leases — a reservation's "
			"hostname field is just a label and won't match a live lease."
		)
		hostname = st.text_input("Hostname", value=existing_hostname)
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
			f"Last quarantined: {existing.get('last_quarantined_at', '') or '—'}"
			f"</div>",
			unsafe_allow_html=True,
		)
		st.caption(
			"Fingerprint and last-quarantined time are written by the quarantine "
			"service and cannot be edited here. MAC/IP/last-seen breadcrumbs are "
			"shown in the main table only."
		)

	c1, c2, c3 = st.columns(3)
	with c1:
		if st.button("Save", type="primary", key="dialog_save"):
			if not name_input.strip() or not hostname.strip():
				st.error("Friendly Name and Hostname are required.")
			else:
				name = name_input.strip()
				upsert_device(
					name,
					hostname.strip(),
					group_tag=group_tag.strip(),
					os_fingerprint=existing.get("os_fingerprint", "") if existing else "",
					last_seen_mac_address=existing.get("last_seen_mac_address", "")
					if existing
					else "",
					last_seen_ip_address=existing.get("last_seen_ip_address", "")
					if existing
					else "",
					last_seen_at=existing.get("last_seen_at", "") if existing else "",
					last_quarantined_at=existing.get("last_quarantined_at", "") if existing else "",
					notes=notes.strip(),
				)
				# Best-effort — an immediate ARP probe so Last MAC/Last IP/Last
				# Seen don't sit blank until the quarantine service's next
				# background pass (up to 5 minutes). A failure here (service
				# down, unreachable) shouldn't block the save that already
				# succeeded above.
				with st.spinner("Checking device presence…"):
					try:
						trigger_presence_check(name)
					except QuarantineServiceError:
						pass
				st.rerun()
	with c2:
		if existing and st.button("Delete", key="dialog_delete"):
			delete_device(friendly_name)
			st.rerun()
	with c3:
		if st.button("Cancel", key="dialog_cancel"):
			st.rerun()


_ENFORCEMENT_STEPS = [
	(
		"Kea",
		"Adds a DHCP deny rule for the device's MAC, so it can't renew or obtain a "
		"new IP lease. This alone doesn't drop an existing connection — a device "
		"keeps working until its current lease expires — which is why the other "
		"three steps run alongside it.",
	),
	(
		"ARP",
		"Starts a background loop that tells the device its gateway lives at a "
		"black-hole MAC address. This is what actually severs a live connection "
		"right away, since Kea's deny only blocks future lease renewals.",
	),
	(
		"Pi-hole",
		"Blocks all DNS resolution for the device's current IP, so even a "
		"connection that somehow survives ARP disruption can't resolve any "
		"hostname to reach it.",
	),
	(
		"Fingerprint",
		"Refreshes the device's OS fingerprint via an nmap scan, keeping identity "
		"data current. Runs on both Quarantine and Release — it's identity "
		"upkeep, not enforcement, so it isn't undone on release.",
	),
]


def _render_enforcement_step_legend() -> None:
	with st.expander("What do Kea / ARP / Pi-hole / Fingerprint mean?"):
		for name, explanation in _ENFORCEMENT_STEPS:
			st.markdown(f"**{name}** — {explanation}")


def _summarize_step_results(action_label: str, result: dict) -> tuple[bool, str]:
	"""Turn keanexus-quarantine's response into a short pass/fail summary.

	Returns (all_ok, message) — all_ok is False if any device was skipped
	for identity drift or any step failed, so the caller can decide
	between st.success and st.warning.
	"""
	all_ok = True
	lines = []
	for step in result.get("step_results", []):
		if step.get("skipped_due_to_identity_drift"):
			all_ok = False
			lines.append(f"{step['friendly_name']}: skipped — identity drifted, retry")
			continue

		checks = {
			"kea": step.get("kea_step_succeeded"),
			"arp": step.get("arp_step_succeeded"),
			"pihole": step.get("pihole_step_succeeded"),
			"fingerprint": step.get("fingerprint_refreshed"),
		}
		if not all(checks.values()):
			all_ok = False
		parts = [f"{name} {'✓' if ok else '✗'}" for name, ok in checks.items()]
		lines.append(f"{step['friendly_name']}: {', '.join(parts)}")

	summary = (
		f"{action_label} — " + " | ".join(lines)
		if lines
		else f"{action_label}: no devices resolved"
	)
	return all_ok, summary


def _trigger_action(target: str, action: str, is_group: bool = False) -> None:
	"""Call keanexus-quarantine and stash a result to show after rerun.

	target is a friendly_name when is_group is False, a group_tag when True
	— quarantine_client.py and the service already accept either, this is
	just the one function both the per-device buttons and the group-action
	buttons route through.

	Stashing in session_state (rather than displaying inline immediately)
	is necessary because st.rerun() wipes whatever was on screen this run
	— the stashed result gets displayed at the top of render_quarantine()
	on the next run, then cleared so it doesn't linger indefinitely.
	"""
	action_label = "Quarantine" if action == "quarantine" else "Release"
	trigger_fn = trigger_quarantine if action == "quarantine" else trigger_release
	display_name = f"group '{target}'" if is_group else target
	with st.spinner(f"{action_label} in progress for {display_name}\u2026 this can take a while."):
		try:
			result = trigger_fn(target, is_group=is_group)
			all_ok, message = _summarize_step_results(action_label, result)
			st.session_state[_ACTION_RESULT_KEY] = {"ok": all_ok, "message": message}
		except QuarantineServiceError as exc:
			st.session_state[_ACTION_RESULT_KEY] = {
				"ok": False,
				"message": f"{action_label} failed for {display_name}: {exc}",
			}
	st.rerun()


def _render_pending_action_result() -> None:
	pending = st.session_state.pop(_ACTION_RESULT_KEY, None)
	if pending is None:
		return
	if pending["ok"]:
		st.success(pending["message"])
	else:
		st.warning(pending["message"])


def _render_device_table(devices: list[dict], leases: list[dict], config: dict | None) -> None:
	_render_grid_header(_DEVICE_COL_WIDTHS, _DEVICE_COL_HEADERS)
	for device in devices:
		friendly_name = device["friendly_name"]
		cols = st.columns(_DEVICE_COL_WIDTHS)
		cols[0].markdown(
			f'<span style="{_CELL};font-weight:600">{friendly_name}</span>',
			unsafe_allow_html=True,
		)
		cols[1].markdown(
			f'<span style="{_CELL}">{device["hostname"]}</span>', unsafe_allow_html=True
		)
		cols[2].markdown(
			f'<span style="{_CELL}">{device["group_tag"] or "—"}</span>', unsafe_allow_html=True
		)
		cols[3].markdown(
			f'<span style="{_CELL_NOWRAP}">'
			f"{html_safe_mac(device['last_seen_mac_address']) or '—'}</span>",
			unsafe_allow_html=True,
		)
		cols[4].markdown(
			f'<span style="{_CELL_NOWRAP}">{device["last_seen_ip_address"] or "—"}</span>',
			unsafe_allow_html=True,
		)
		cols[5].markdown(
			f'<span style="{_CELL_META}">{_format_local_time(device["last_seen_at"])}</span>',
			unsafe_allow_html=True,
		)
		cols[6].markdown(
			f'<span style="{_CELL_META}">{_format_local_time(device["last_quarantined_at"])}</span>',
			unsafe_allow_html=True,
		)
		if cols[7].button(
			"Quarantine", key=f"quarantine_go_{friendly_name}", use_container_width=True
		):
			_trigger_action(friendly_name, "quarantine")
		if cols[8].button(
			"Release", key=f"quarantine_release_{friendly_name}", use_container_width=True
		):
			_trigger_action(friendly_name, "release")
		if cols[9].button("Edit", key=f"quarantine_edit_{friendly_name}", use_container_width=True):
			_edit_dialog(friendly_name, leases, config)


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
			f'<span style="{_CELL}">{_format_local_time(entry["occurred_at"])}</span>',
			unsafe_allow_html=True,
		)
		cols[6].markdown(
			f'<span style="{_CELL}">{entry["detail"] or "—"}</span>', unsafe_allow_html=True
		)


def _distinct_group_tags(devices: list[dict]) -> list[str]:
	"""Non-blank group_tag values currently assigned to any device, sorted.

	Derived from the registry rather than a separate lookup table — a group
	tag isn't its own entity, it's just a value devices happen to share.
	"""
	return sorted({device["group_tag"] for device in devices if device["group_tag"]})


def _render_group_actions(devices: list[dict]) -> None:
	"""Quarantine/Release an entire group_tag at once.

	The per-device Edit dialog already lets you assign a group_tag to a
	device (free text) — this is the other half that was missing: an
	actual control to act on the group. quarantine_client.py and
	keanexus-quarantine's /quarantine and /release both already accept
	is_group=True, this was purely a gap in the UI. Hidden entirely when no
	device has a group_tag set, since there'd be nothing to select.
	"""
	group_tags = _distinct_group_tags(devices)
	if not group_tags:
		return

	st.markdown("**Group Actions**")
	select_col, _ = st.columns([1, 3])
	with select_col:
		selected_group = st.selectbox(
			"Group Tag",
			group_tags,
			label_visibility="collapsed",
			key="quarantine_group_select",
		)
	gc1, gc2, _ = st.columns([1, 1, 5])
	with gc1:
		if st.button("Quarantine Group", key="quarantine_group_go"):
			_trigger_action(selected_group, "quarantine", is_group=True)
	with gc2:
		if st.button("Release Group", key="quarantine_group_release"):
			_trigger_action(selected_group, "release", is_group=True)


def render_quarantine(leases: list[dict], config: dict | None) -> None:
	"""Render the Quarantine tab: device registry CRUD, Quarantine/Release
	buttons, and a read-only audit log.

	leases/config are only used to populate the Hostname picker in the Add/Edit
	dialog with real, currently-observed hostnames — see helpers.distinct_real_hostnames.
	"""
	st.caption(
		"Identity registry for the network quarantine feature. Devices are "
		"identified by hostname, not MAC address, since MAC can be changed. "
		"Quarantine/Release call keanexus-quarantine's own API directly — "
		"see docs/quarantine-feature-design.md."
	)
	_render_enforcement_step_legend()

	_render_pending_action_result()

	if st.button("Add Device", key="quarantine_add_device"):
		_edit_dialog(leases=leases, config=config)

	devices = get_devices()
	if devices:
		_render_device_table(devices, leases, config)
		st.markdown("---")
		_render_group_actions(devices)
	else:
		st.info("No devices registered yet.")

	st.markdown("---")
	st.markdown("**Recent Quarantine Activity**")

	log_entries = get_quarantine_log(limit=50)
	if log_entries:
		_render_log_table(log_entries)
	else:
		st.info("No quarantine actions have been logged yet.")
