"""
overview_data.py — Shapes everything the Overview page shows.

Pure: Kea data, address rows and database rows in, plain dicts out. No
FastAPI, no Kea or database calls, so every number and every map cell is
unit-tested. The route in routes/overview.py only fetches and renders.
"""

from datetime import datetime
from typing import Mapping, Optional
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

# A display-only status layered over address_map's classification.
DISPLAY_UNKNOWN = "unknown"

# Legend order, and the text shown for each status on the map and hover card.
STATUS_LABELS: dict[str, str] = {
	STATUS_LEASED: "Leased",
	STATUS_RESERVED: "Reserved",
	STATUS_STATIC: "Static",
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

	Matched by the hostname the device reports on its live lease, the same way
	the Devices screen finds it. A device with no lease right now has no square.
	"""
	ip_by_hostname = {row.hostname: row.ip for row in rows if row.hostname}
	matched: dict[str, dict] = {}
	for device in devices:
		ip = ip_by_hostname.get(device.get("hostname") or "")
		if ip and ip not in matched:
			matched[ip] = device
	return matched


def _display_status(row: AddressRow, device: Optional[dict]) -> str:
	if row.status == STATUS_LEASED and not row.hostname and not row.label and not device:
		return DISPLAY_UNKNOWN
	return row.status


def _seen_fields(row: AddressRow, seen_macs: Optional[Mapping[str, str]], seen_age: str) -> dict:
	"""What the latest ARP sweep says about one address.

	`seen` is None before any sweep, so the card can tell "not checked" apart
	from "didn't answer". `seen_mismatch` flags an answering MAC that differs
	from the one Kea or the static record holds: a different device is using
	that address.
	"""
	if seen_macs is None:
		return {"seen": None, "seen_mac": "", "seen_age": "", "seen_mismatch": False}
	answered = row.ip in seen_macs
	seen_mac = seen_macs.get(row.ip, "")
	mismatch = bool(seen_mac and row.mac and seen_mac.lower() != row.mac.lower())
	return {"seen": answered, "seen_mac": seen_mac, "seen_age": seen_age, "seen_mismatch": mismatch}


def map_cells(
	rows: list[AddressRow],
	devices: list[dict],
	seen_macs: Optional[Mapping[str, str]] = None,
	seen_age: str = "",
) -> list[dict]:
	"""One dict per address: what the map square and its hover card show.

	`seen_macs` is the latest ARP sweep's {ip: mac}, or None before the first.
	"""
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
				**_seen_fields(row, seen_macs, seen_age),
			}
		)
	return cells


def legend() -> list[dict]:
	return [{"status": status, "label": STATUS_LABELS[status]} for status in LEGEND_STATUSES]


# --- Activity ---------------------------------------------------------------------------


def activity(admin_entries: list[dict], limit: int) -> list[dict]:
	"""The newest lines for the Activity card: every change made in the web UI."""
	return [_admin_line(entry) for entry in admin_entries[:limit]]


def _admin_line(entry: dict) -> dict:
	return {
		"when": local_time(entry["occurred_at"], "%H:%M"),
		"text": f"{entry['username']} {entry['summary']}",
		"tone": "admin",
		"at": entry["occurred_at"],
	}
