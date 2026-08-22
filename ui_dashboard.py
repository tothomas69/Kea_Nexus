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
nothing the existing convention was protecting.
"""

from datetime import timedelta
from typing import Optional

import altair as alt
import pandas as pd
import streamlit as st

import db
from dashboard_data import (
	lease_composition,
	lease_expiry_buckets,
	leases_issued_since,
	quarantined_count,
	utc_now,
	utilisation_points,
)

# --- Chart palette ------------------------------------------------------------
# Every value below was run through the data-viz palette validator against this
# app's white card surface (#ffffff), not eyeballed.
#
#   _SERIES_1        the app's own accent, reused so charts belong to the app.
#                    Passes lightness band, chroma floor and 3:1 contrast.
#   _EXPIRY_RAMP     a single-hue ordinal ramp for the expiry buckets, which
#                    are an *ordered* scale, not identities — so they take a
#                    ramp rather than categorical hues. Assigned darkest-first
#                    so the eye lands on the soonest-expiring bucket. Passes
#                    monotone lightness, adjacent-step separation, single-hue
#                    spread, and the 2:1 light-end floor (2.50:1).
_SERIES_1 = "#0969da"
_EXPIRY_RAMP = ["#104281", "#256abf", "#3987e5", "#6da7ec"]

_CHART_FONT = "monospace"
_GRID_COLOR = "#f0f2f5"
_AXIS_COLOR = "#d0d7de"
_LABEL_COLOR = "#6e7781"
_TITLE_COLOR = "#57606a"

_GOOD = "#1a7f37"
_WARN = "#b45309"
_BAD = "#cf222e"
_INK = "#1f2328"

# A gap wider than this between consecutive samples breaks the utilisation
# line rather than being drawn through — see dashboard_data.utilisation_points.
# Three times the default five-minute sample interval, so an ordinary missed
# reading doesn't fragment the line but a real outage shows as the hole it was.
_SAMPLE_GAP_SECONDS = 900

_ISSUED_WINDOW_HOURS = 24
_HISTORY_WINDOW_HOURS = 48


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


def _themed(chart: alt.Chart) -> alt.Chart:
	"""Apply the app's typography and a recessive grid to a chart.

	Solid hairline gridlines one shade off the surface — never dashed, which
	reads as a threshold or projection when it is just a grid.
	"""
	return (
		chart.configure_view(strokeWidth=0)
		.configure_axis(
			labelFont=_CHART_FONT,
			titleFont=_CHART_FONT,
			labelColor=_LABEL_COLOR,
			titleColor=_TITLE_COLOR,
			labelFontSize=11,
			titleFontSize=11,
			gridColor=_GRID_COLOR,
			gridWidth=1,
			domainColor=_AXIS_COLOR,
			tickColor=_AXIS_COLOR,
		)
		.configure_title(font=_CHART_FONT, fontSize=12, color=_TITLE_COLOR, anchor="start")
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
	"""Utilisation over time — one series, so no legend; the title names it."""
	st.markdown(
		f'<div style="font-family:monospace;font-size:15px;font-weight:700;color:#1f2328;'
		f'text-transform:uppercase;letter-spacing:.05em;margin:6px 0 2px">'
		f"Pool Utilisation — Last {_HISTORY_WINDOW_HOURS}h</div>",
		unsafe_allow_html=True,
	)

	if len(samples) < 2:
		st.caption(
			"Not enough history yet. KeaNexus samples Kea's pool counters every "
			"few minutes while it is running; this chart fills in as those "
			"readings accumulate."
		)
		return

	points = utilisation_points(samples, gap_seconds=_SAMPLE_GAP_SECONDS)
	frame = pd.DataFrame(points)
	# Stored UTC, shown local — the axis is read by a person in a timezone.
	frame["sampled_at"] = pd.to_datetime(frame["sampled_at"]).dt.tz_convert(None)

	base = alt.Chart(frame).encode(
		x=alt.X(
			"sampled_at:T",
			title=None,
			# "%H:%M" alone repeats every label across a multi-day window —
			# 17:29 appears on both days with nothing to tell them apart.
			axis=alt.Axis(format="%a %H:%M", tickCount=6),
		)
	)

	# No interpolation curve: readings are five minutes apart and a smoothed
	# line would draw values between them that were never measured.
	line = base.mark_line(color=_SERIES_1, strokeWidth=2).encode(
		y=alt.Y(
			"utilisation_pct:Q",
			title="% in use",
			scale=alt.Scale(domain=[0, 100]),
			axis=alt.Axis(values=[0, 25, 50, 75, 100]),
		)
	)

	# Hover layer: a 2px line is far too small a target to require landing on,
	# so an invisible full-height band per sample catches the pointer and
	# drives both the crosshair and the tooltip.
	hover = alt.selection_point(
		nearest=True, on="pointerover", fields=["sampled_at"], empty=False, clear="pointerout"
	)
	hover_targets = (
		base.mark_rule(opacity=0)
		.encode(
			tooltip=[
				alt.Tooltip("sampled_at:T", title="Time", format="%b %d %H:%M"),
				alt.Tooltip("utilisation_pct:Q", title="In use %", format=".1f"),
			]
		)
		.add_params(hover)
	)
	crosshair = base.mark_rule(color=_AXIS_COLOR, strokeWidth=1).encode(
		opacity=alt.condition(hover, alt.value(1), alt.value(0))
	)
	marker = base.mark_point(
		color=_SERIES_1, filled=True, size=60, stroke="#ffffff", strokeWidth=2
	).encode(
		y=alt.Y("utilisation_pct:Q"),
		opacity=alt.condition(hover, alt.value(1), alt.value(0)),
	)

	chart = alt.layer(line, crosshair, marker, hover_targets).properties(height=200)
	st.altair_chart(_themed(chart), width="stretch")

	with st.expander("Show the numbers"):
		recent = [p for p in points if p["utilisation_pct"] is not None][-12:]
		st.markdown(
			_numbers_table(
				[
					(
						p["sampled_at"].astimezone().strftime("%m/%d %H:%M"),
						f"{p['utilisation_pct']}%",
					)
					for p in reversed(recent)
				],
				"Sampled",
				"In use",
			),
			unsafe_allow_html=True,
		)


def _bar_value_labels(
	base: alt.Chart, dx: int = 0, dy: int = 0, align: str = "center"
) -> alt.Chart:
	"""Value labels sitting just past each bar's end.

	Outside the bar, never inside it: a short bar cannot fit a label, and a
	label clipped by its own mark is worse than no label. Labels wear a text
	token rather than the bar's colour — the bar beside them already carries
	the identity, and colouring the number too would make the text read as a
	third encoding.
	"""
	return base.mark_text(
		align=align,
		baseline="middle",
		dx=dx,
		dy=dy,
		font=_CHART_FONT,
		fontSize=11,
		color=_TITLE_COLOR,
	).encode(text=alt.Text("count:Q", format="d"))


def _render_expiry_chart(leases: list[dict]) -> None:
	"""Lease expiry distribution — ordered buckets, so a single-hue ordinal
	ramp rather than categorical hues; the x-axis labels carry identity, so
	nothing here is encoded by colour alone."""
	buckets = lease_expiry_buckets(leases, utc_now().timestamp())
	frame = pd.DataFrame(buckets)
	order = [b["bucket"] for b in buckets]

	base = alt.Chart(frame).encode(
		x=alt.X("bucket:N", title=None, sort=order, axis=alt.Axis(labelAngle=0)),
		y=alt.Y("count:Q", title="Leases"),
	)
	bars = base.mark_bar(cornerRadiusEnd=4, size=34).encode(
		color=alt.Color(
			"bucket:N",
			sort=order,
			scale=alt.Scale(domain=order, range=_EXPIRY_RAMP),
			legend=None,
		),
		tooltip=[
			alt.Tooltip("bucket:N", title="Expires in"),
			alt.Tooltip("count:Q", title="Leases"),
		],
	)
	chart = alt.layer(bars, _bar_value_labels(base, dy=-7)).properties(height=190)
	st.markdown(_chart_heading("Expiring Within"), unsafe_allow_html=True)
	st.altair_chart(_themed(chart), width="stretch")
	with st.expander("Show the numbers"):
		st.markdown(
			_numbers_table(
				[(b["bucket"], str(b["count"])) for b in buckets], "Expires in", "Leases"
			),
			unsafe_allow_html=True,
		)


def _render_composition_chart(leases: list[dict], config: Optional[dict]) -> None:
	"""Lease composition — nominal categories, so every bar takes the same
	slot-1 hue rather than spending the identity channel re-encoding bar
	length. Horizontal, because the type names are long."""
	composition = lease_composition(leases, config)
	frame = pd.DataFrame(composition)
	order = [c["type"] for c in composition]

	base = alt.Chart(frame).encode(
		y=alt.Y("type:N", title=None, sort=order),
		# Headroom on the axis so a label past the longest bar isn't clipped
		# by the plot edge.
		x=alt.X("count:Q", title="Leases", scale=alt.Scale(nice=True, padding=18)),
	)
	bars = base.mark_bar(color=_SERIES_1, cornerRadiusEnd=4, size=20).encode(
		tooltip=[
			alt.Tooltip("type:N", title="Type"),
			alt.Tooltip("count:Q", title="Leases"),
		]
	)
	chart = alt.layer(bars, _bar_value_labels(base, dx=7, align="left")).properties(height=190)
	st.markdown(_chart_heading("Lease Composition"), unsafe_allow_html=True)
	st.altair_chart(_themed(chart), width="stretch")
	with st.expander("Show the numbers"):
		st.markdown(
			_numbers_table([(c["type"], str(c["count"])) for c in composition], "Type", "Leases"),
			unsafe_allow_html=True,
		)


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
