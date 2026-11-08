"""
ui_dashboard.py - Dashboard tab for KeaNexus: KPI tiles, service health, and
pool/lease charts.

Rendering only. Every number on this page is shaped by `dashboard_data.py`,
which is plain-data-in/plain-data-out and unit-tested — this module has no
logic worth testing and, like every ui_*.py, is excluded from coverage.

Two rendering paths on purpose. Stat tiles, the utilisation gauge and the
health rows stay hand-written HTML, matching the rest of the app (see the
"HTML tables (not st.dataframe)" and "CSS conic-gradient gauge" decisions in
the as-built guide). Charts — anything with an axis and a scale — are Altair,
themed to match, because hand-rolling axis ticks, binning and scales buys
nothing the existing convention was protecting. The chart specs themselves
live in dashboard_charts.py, shared with the web UI's Overview.
"""

from datetime import timedelta
from typing import Optional

import streamlit as st

import db
from dashboard_charts import (
	HISTORY_WINDOW_HOURS,
	composition_chart,
	expiry_chart,
	utilisation_chart,
)
from dashboard_data import leases_issued_since, quarantined_count, utc_now

_GOOD = "#1a7f37"
_WARN = "#b45309"
_BAD = "#cf222e"
_INK = "#1f2328"

_ISSUED_WINDOW_HOURS = 24
_HISTORY_WINDOW_HOURS = HISTORY_WINDOW_HOURS


def _card(title: str, body: str) -> str:
	return (
		f'<div style="background:#fff;border:1px solid #d0d7de;border-radius:8px;'
		f'padding:18px 20px;height:100%">'
		f'<div style="font-family:monospace;font-size:15px;font-weight:700;color:#1f2328;'
		f'text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px">{title}</div>'
		f"{body}"
		f"</div>"
	)


def _stat_tile(label: str, value: str, color: str = _INK, note: str = "") -> str:
	"""One KPI tile: a label, a large value, and an optional small note.

	Proportional figures, not tabular-nums — equal-width digits make a large
	standalone number read loose.
	"""
	note_html = (
		f'<div style="font-family:monospace;font-size:10px;color:#8c959f;'
		f'margin-top:4px">{note}</div>'
		if note
		else ""
	)
	return (
		f'<div style="background:#fff;border:1px solid #d0d7de;border-radius:8px;'
		f'padding:14px 16px;height:100%">'
		f'<div style="font-family:monospace;font-size:10px;font-weight:700;color:#6e7781;'
		f'text-transform:uppercase;letter-spacing:.06em">{label}</div>'
		f'<div style="font-family:monospace;font-size:30px;font-weight:700;color:{color};'
		f'line-height:1.2;margin-top:6px">{value}</div>'
		f"{note_html}"
		f"</div>"
	)


def _gauge(pct: int, used: int, total: int, avail: int) -> str:
	"""CSS conic-gradient donut ring showing pool utilization percentage."""
	fill_deg = round(pct * 3.6, 1)  # 1% = 3.6°
	fill_color = _BAD if pct >= 90 else (_WARN if pct >= 75 else _GOOD)
	avail_color = _BAD if avail == 0 else (_WARN if avail < 10 else _GOOD)

	gradient = (
		f"conic-gradient(from -90deg, "
		f"{fill_color} 0deg {fill_deg}deg, "
		f"#e5e7eb {fill_deg}deg 360deg)"
	)

	return (
		f'<div style="display:flex;justify-content:center;padding:8px 0">'
		f'<div style="position:relative;width:160px;height:160px">'
		f'<div style="position:absolute;inset:0;border-radius:50%;background:{gradient}"></div>'
		f'<div style="position:absolute;inset:22px;border-radius:50%;background:#fff"></div>'
		f'<div style="position:absolute;inset:0;display:flex;flex-direction:column;'
		f'align-items:center;justify-content:center;font-family:monospace">'
		f'<span style="font-size:26px;font-weight:700;color:{fill_color};line-height:1">{pct}%</span>'
		f'<span style="font-size:11px;color:#6e7781;margin-top:4px">{used} / {total}</span>'
		f'<span style="font-size:11px;color:#6e7781;margin-top:2px">'
		f'Available: <span style="font-weight:700;color:{avail_color}">{avail}</span>'
		f"</span>"
		f"</div>"
		f"</div>"
		f"</div>"
	)


def _health_row(label: str, detail: str, up: bool, last: bool = False) -> str:
	dot = _GOOD if up else _BAD
	bg = "#dafbe1" if up else "#ffebe9"
	text = _GOOD if up else _BAD
	badge = "UP" if up else "DOWN"
	border = "" if last else "border-bottom:1px solid #f0f2f5;"
	return (
		f'<div style="display:flex;align-items:center;gap:8px;padding:8px 0;{border}'
		f'font-family:monospace;font-size:13px">'
		f'<div style="width:8px;height:8px;border-radius:50%;background:{dot};flex-shrink:0"></div>'
		f'<span style="color:#57606a;font-weight:400;flex:1">{label}</span>'
		f'<span style="color:#8c959f;font-size:10px">{detail}</span>'
		f'<span style="background:{bg};color:{text};font-size:10px;font-weight:700;'
		f'padding:2px 7px;border-radius:10px;margin-left:6px">{badge}</span>'
		f"</div>"
	)


def _metric_row(label: str, value, alert: bool = False, last: bool = False) -> str:
	val_color = _BAD if alert else _INK
	border = "" if last else "border-bottom:1px solid #f0f2f5;"
	return (
		f'<div style="display:flex;justify-content:space-between;align-items:center;'
		f'padding:8px 0;{border}font-family:monospace;font-size:13px">'
		f'<span style="color:#57606a">{label}</span>'
		f'<span style="font-weight:700;color:{val_color}">{value}</span>'
		f"</div>"
	)


def _numbers_table(rows: list[tuple[str, str]], label_heading: str, value_heading: str) -> str:
	"""The table-view twin every chart needs — the same values, reachable
	without reading a mark or hovering a tooltip."""
	body = "".join(
		f'<tr><td style="padding:3px 12px 3px 0;color:#57606a">{label}</td>'
		f'<td style="padding:3px 0;font-weight:700;color:#1f2328;'
		f'font-variant-numeric:tabular-nums">{value}</td></tr>'
		for label, value in rows
	)
	return (
		f'<table style="font-family:monospace;font-size:12px;border-collapse:collapse">'
		f'<tr><th style="text-align:left;padding:0 12px 4px 0;color:#8c959f;'
		f'font-weight:400">{label_heading}</th>'
		f'<th style="text-align:left;padding:0 0 4px;color:#8c959f;'
		f'font-weight:400">{value_heading}</th></tr>{body}</table>'
	)


def _render_utilisation_history(samples: list[dict]) -> None:
	"""Utilisation over time — one series, so no legend; the heading names it."""
	st.markdown(
		_chart_heading(f"Pool Utilisation — Last {_HISTORY_WINDOW_HOURS}h"), unsafe_allow_html=True
	)
	built = utilisation_chart(samples)
	if built is None:
		st.caption(
			"Not enough history yet. KeaNexus samples Kea's pool counters every "
			"few minutes while it is running; this chart fills in as those "
			"readings accumulate."
		)
		return
	chart, table = built
	st.altair_chart(chart, width="stretch")
	with st.expander("Show the numbers"):
		st.markdown(_numbers_table(table, "Sampled", "In use"), unsafe_allow_html=True)


def _render_expiry_chart(leases: list[dict]) -> None:
	chart, table = expiry_chart(leases, utc_now())
	st.markdown(_chart_heading("Expiring Within"), unsafe_allow_html=True)
	st.altair_chart(chart, width="stretch")
	with st.expander("Show the numbers"):
		st.markdown(_numbers_table(table, "Expires in", "Leases"), unsafe_allow_html=True)


def _render_composition_chart(leases: list[dict], config: Optional[dict]) -> None:
	chart, table = composition_chart(leases, config)
	st.markdown(_chart_heading("Lease Composition"), unsafe_allow_html=True)
	st.altair_chart(chart, width="stretch")
	with st.expander("Show the numbers"):
		st.markdown(_numbers_table(table, "Type", "Leases"), unsafe_allow_html=True)


def _chart_heading(title: str) -> str:
	return (
		f'<div style="font-family:monospace;font-size:15px;font-weight:700;color:#1f2328;'
		f'text-transform:uppercase;letter-spacing:.05em;margin:6px 0 2px">{title}</div>'
	)


def _render_kpi_row(stats: dict, leases: list[dict], samples: list[dict]) -> None:
	assigned = stats["assigned"]
	declined = stats["declined"]
	avail = stats["available"]

	issued = leases_issued_since(samples, _ISSUED_WINDOW_HOURS, utc_now())
	quarantined = quarantined_count(db.get_devices())

	tiles = [
		_stat_tile("Active Leases", str(assigned)),
		_stat_tile(
			"Available", str(avail), _BAD if avail == 0 else (_WARN if avail < 10 else _INK)
		),
		_stat_tile("Declined", str(declined), _BAD if declined else _INK),
		_stat_tile(
			f"Issued / {_ISSUED_WINDOW_HOURS}h",
			"—" if issued is None else str(issued),
			_INK,
			# Blank rather than a wrong number: see leases_issued_since.
			note="needs more history" if issued is None else "new leases handed out",
		),
		_stat_tile("Quarantined", str(quarantined), _BAD if quarantined else _INK),
	]
	for column, tile in zip(st.columns(len(tiles)), tiles):
		column.markdown(tile, unsafe_allow_html=True)


def render_dashboard(
	stats: Optional[dict],
	config: Optional[dict],
	status: dict,
	leases: Optional[list[dict]] = None,
) -> None:
	if stats is None:
		st.error(
			"Could not load pool stats. Check that Kea is reachable and the stat_cmds hook is loaded."
		)
		return

	leases = leases or []
	samples = db.get_pool_samples(since=_history_cutoff())

	dhcp4 = status["dhcp4"]
	ca = status["ca"]
	dhcp_on = st.session_state.get("dhcp_enabled", True)

	assigned = stats["assigned"]
	declined = stats["declined"]
	avail = stats["available"]
	total = stats["total"]
	pct = round((assigned + declined) / total * 100) if total else 0

	res = (config or {}).get("subnet4", [{}])[0].get("reservations", [])
	fixed_ips = sum(1 for r in res if r.get("ip-address"))

	_render_kpi_row(stats, leases, samples)
	st.markdown('<div style="margin-top:14px"></div>', unsafe_allow_html=True)

	col_gauge, col_health, col_leases = st.columns(3)

	with col_gauge:
		st.markdown(
			_card("Pool Utilization", _gauge(pct, assigned + declined, total, avail)),
			unsafe_allow_html=True,
		)

	with col_health:

		def svc_detail(svc) -> str:
			return svc.version if svc.version not in ("-", "ok") else ""

		body = (
			_health_row(dhcp4.name, svc_detail(dhcp4), dhcp4.up)
			+ _health_row(ca.name, svc_detail(ca), ca.up)
			+ _health_row(
				"DHCP Leasing Service", "enabled" if dhcp_on else "disabled", dhcp_on, last=True
			)
		)
		st.markdown(_card("Service Health", body), unsafe_allow_html=True)

	with col_leases:
		body = (
			_metric_row("Active Leases", assigned)
			+ _metric_row("Available Leases", avail)
			+ _metric_row("Declined Leases", declined, alert=declined > 0)
			+ _metric_row("Reserved", len(res))
			+ _metric_row("Fixed IPs", fixed_ips, last=True)
		)
		st.markdown(_card("Lease Summary", body), unsafe_allow_html=True)

	_render_utilisation_history(samples)

	col_expiry, col_composition = st.columns(2)
	with col_expiry:
		_render_expiry_chart(leases)
	with col_composition:
		_render_composition_chart(leases, config)


def _history_cutoff() -> str:
	"""UTC ISO8601 timestamp _HISTORY_WINDOW_HOURS ago, for the samples query."""
	return (utc_now() - timedelta(hours=_HISTORY_WINDOW_HOURS)).isoformat()
