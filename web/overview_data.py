"""
overview_data.py — Shapes everything the Overview page shows.

Pure: Kea data, address rows and database rows in, plain dicts out. No
FastAPI, no Kea or database calls, so every number and every map cell is
unit-tested. The route in routes/overview.py only fetches and renders.
"""

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from address_map import (
	STATUS_BROADCAST,
	STATUS_DECLINED,
	STATUS_FREE,
	STATUS_GATEWAY,
	STATUS_LEASED,
	STATUS_NETWORK,
	STATUS_RESERVED,
	STATUS_SCOPE,
	STATUS_STATIC,
	AddressRow,
)
from dashboard_data import leases_issued_since

# The household's own zone; every time on screen is shown in it.
LOCAL_TIMEZONE = ZoneInfo("America/New_York")

# Two display-only statuses layered over address_map's classification.
DISPLAY_QUARANTINED = "quarantined"
DISPLAY_UNKNOWN = "unknown"

# Legend order, and the text shown for each status on the map and hover card.
STATUS_LABELS: dict[str, str] = {
	STATUS_LEASED: "Leased",
	STATUS_RESERVED: "Reserved",
	STATUS_STATIC: "Static",
	DISPLAY_QUARANTINED: "Quarantined",
	DISPLAY_UNKNOWN: "Unknown device",
	STATUS_DECLINED: "Declined",
	STATUS_SCOPE: "Free in pool",
	STATUS_FREE: "Unused",
	STATUS_GATEWAY: "Gateway",
	STATUS_NETWORK: "Network",
	STATUS_BROADCAST: "Broadcast",
}
LEGEND_STATUSES = [
	STATUS_LEASED,
	STATUS_RESERVED,
	STATUS_STATIC,
	DISPLAY_QUARANTINED,
	DISPLAY_UNKNOWN,
	STATUS_DECLINED,
	STATUS_SCOPE,
	STATUS_FREE,
]

# Pool bar colour bands, matching the Streamlit dashboard's gauge.
POOL_WARN_PERCENT = 75
POOL_FULL_PERCENT = 90
_PERCENT = 100

ISSUED_WINDOW_HOURS = 24

# Steps that actually take a device off the network. nmap_refresh is identity
# upkeep that runs both ways, so, as in quarantine_service._enforcement_succeeded,
# it never counts towards "did it work".
ENFORCEMENT_STEPS = frozenset({"kea", "arp", "pihole_primary", "pihole_secondary"})
_SKIPPED_STEP = "identity_resolution"
# Rows for one Quarantine/Release land within a few minutes of each other
# (each step retries up to 3 times, and nmap can take ~45s an attempt).
ACTION_GROUPING_SECONDS = 5 * 60


def local_time(utc_iso: Optional[str], time_format: str = "%m/%d %H:%M") -> str:
	"""A stored UTC ISO-8601 timestamp in local time, or "" if missing or unreadable."""
	if not utc_iso:
		return ""
	try:
		moment = datetime.fromisoformat(utc_iso)
	except ValueError:
		return ""
	return moment.astimezone(LOCAL_TIMEZONE).strftime(time_format)


# --- Key figures ---------------------------------------------------------------------


def pool_figures(stats: Optional[dict]) -> Optional[dict]:
	"""Pool use for the KPI tile and its bar, or None when Kea gave no stats."""
	if not stats:
		return None
	total = stats.get("total", 0)
	used = stats.get("assigned", 0) + stats.get("declined", 0)
	percent = round(used / total * _PERCENT) if total else 0
	if percent >= POOL_FULL_PERCENT:
		level = "full"
	elif percent >= POOL_WARN_PERCENT:
		level = "warn"
	else:
		level = "ok"
	return {
		"percent": percent,
		"used": used,
		"total": total,
		"available": stats.get("available", 0),
		"assigned": stats.get("assigned", 0),
		"declined": stats.get("declined", 0),
		"level": level,
		"warn_percent": POOL_WARN_PERCENT,
	}


def issued_recently(samples: list[dict], now: datetime) -> Optional[int]:
	"""New leases handed out in the last day, or None without enough history."""
	return leases_issued_since(samples, ISSUED_WINDOW_HOURS, now)


# --- Address map ---------------------------------------------------------------------


def devices_by_ip(devices: list[dict], rows: list[AddressRow]) -> dict[str, dict]:
	"""Which registered device sits at each address.

	Matched by the hostname the device reports on its live lease first, since
	that is how the quarantine service itself finds a device. The last IP it
	was seen at is the fallback, for a device that is quarantined and so holds
	no lease right now.
	"""
	ip_by_hostname = {row.hostname: row.ip for row in rows if row.hostname}
	matched: dict[str, dict] = {}
	for device in devices:
		ip = ip_by_hostname.get(device.get("hostname") or "") or device.get("last_seen_ip_address")
		if ip and ip not in matched:
			matched[ip] = device
	return matched


def _display_status(row: AddressRow, device: Optional[dict]) -> str:
	if device and device.get("is_quarantined"):
		return DISPLAY_QUARANTINED
	if row.status == STATUS_LEASED and not row.hostname and not row.label and not device:
		return DISPLAY_UNKNOWN
	return row.status


def map_cells(rows: list[AddressRow], devices: list[dict]) -> list[dict]:
	"""One dict per address: what the map square and its hover card show."""
	matched = devices_by_ip(devices, rows)
	cells = []
	for row in rows:
		device = matched.get(row.ip)
		status = _display_status(row, device)
		cells.append(
			{
				"octet": row.last_octet,
				"ip": row.ip,
				"status": status,
				"status_label": STATUS_LABELS[status],
				"label": row.label,
				"hostname": row.hostname,
				"mac": row.mac,
				"info": row.info,
				"notes": row.notes,
				"editable": row.editable,
				"device": device["friendly_name"] if device else "",
				"quarantined": bool(device and device.get("is_quarantined")),
			}
		)
	return cells


def legend() -> list[dict]:
	return [{"status": status, "label": STATUS_LABELS[status]} for status in LEGEND_STATUSES]


# --- Quarantine card ------------------------------------------------------------------


def quarantine_rows(devices: list[dict]) -> list[dict]:
	"""Registered devices for the Quarantine card, quarantined ones first."""
	rows = []
	for device in devices:
		quarantined = bool(device.get("is_quarantined"))
		meta_parts = []
		if device.get("group_tag"):
			meta_parts.append(f"Group: {device['group_tag']}")
		since = local_time(device.get("last_quarantined_at"))
		if quarantined and since:
			meta_parts.append(f"since {since}")
		rows.append(
			{
				"name": device["friendly_name"],
				"quarantined": quarantined,
				"meta": " · ".join(meta_parts),
			}
		)
	rows.sort(key=lambda row: (not row["quarantined"], row["name"]))
	return rows


# --- Activity ---------------------------------------------------------------------------


def _same_action(group: dict, entry: dict) -> bool:
	if (group["name"], group["action"]) != (entry["friendly_name"], entry["action"]):
		return False
	gap_seconds = abs(
		(
			datetime.fromisoformat(group["first_at"]) - datetime.fromisoformat(entry["occurred_at"])
		).total_seconds()
	)
	return gap_seconds <= ACTION_GROUPING_SECONDS


def _summarize(group: dict) -> dict:
	verb = "quarantined" if group["action"] == "quarantine" else "released"
	if group["skipped"]:
		text = f"{group['name']} skipped: its address changed mid-request"
		tone = "warn"
	elif group["total"] and group["ok"] == group["total"]:
		text = f"{group['name']} {verb} — {group['ok']}/{group['total']} steps ok"
		tone = "quarantine" if group["action"] == "quarantine" else "release"
	else:
		text = f"{group['name']} {verb} with problems — {group['ok']}/{group['total']} steps ok"
		tone = "warn"
	return {
		"when": local_time(group["first_at"], "%H:%M"),
		"text": text,
		"tone": tone,
		"at": group["first_at"],
	}


def activity(
	log_entries: list[dict], limit: int, admin_entries: Optional[list[dict]] = None
) -> list[dict]:
	"""The newest lines for the Activity card, from both logs.

	quarantine_log rows (newest first) are folded into one line per
	Quarantine/Release, since that log has a row per enforcement step and a
	person wants one line per action. activity_log rows (every other change
	made in the web UI) are one line each already. ISO-8601 UTC strings sort
	in time order, so the two merge on their timestamps.
	"""
	lines = _quarantine_lines(log_entries, limit) + [
		_admin_line(entry) for entry in admin_entries or []
	]
	lines.sort(key=lambda line: line["at"], reverse=True)
	return lines[:limit]


def _admin_line(entry: dict) -> dict:
	return {
		"when": local_time(entry["occurred_at"], "%H:%M"),
		"text": f"{entry['username']} {entry['summary']}",
		"tone": "admin",
		"at": entry["occurred_at"],
	}


def _quarantine_lines(log_entries: list[dict], limit: int) -> list[dict]:
	groups: list[dict] = []
	for entry in log_entries:
		if not groups or not _same_action(groups[-1], entry):
			if len(groups) == limit:
				break
			groups.append(
				{
					"name": entry["friendly_name"],
					"action": entry["action"],
					"first_at": entry["occurred_at"],
					"ok": 0,
					"total": 0,
					"skipped": False,
				}
			)
		group = groups[-1]
		if entry["step"] == _SKIPPED_STEP and not entry["succeeded"]:
			group["skipped"] = True
		elif entry["step"] in ENFORCEMENT_STEPS:
			group["total"] += 1
			group["ok"] += 1 if entry["succeeded"] else 0
	return [_summarize(group) for group in groups]
