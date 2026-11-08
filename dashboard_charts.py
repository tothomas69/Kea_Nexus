"""
dashboard_charts.py — The Overview's pool and lease charts, as Altair specs.

The Overview embeds each chart's to_dict() and draws it in the browser with
the vendored vega-embed, so Vega-Lite's version must match Altair's schema
(see web/static/vendor/VERSIONS.md). Every builder
returns the chart plus its table-view twin, so no value is reachable only by
reading a mark or hovering. Pure: plain data in, specs out, no UI framework.
"""

from datetime import datetime
from typing import Optional

import altair as alt

from dashboard_data import lease_composition, lease_expiry_buckets, utilisation_points

# --- Palette ----------------------------------------------------------------------------
# Every value below was run through the data-viz palette validator against this
# app's white card surface (#ffffff), not eyeballed.
#
#   SERIES_1      the app's own accent, reused so charts belong to the app.
#                 Passes lightness band, chroma floor and 3:1 contrast.
#   EXPIRY_RAMP   a single-hue ordinal ramp for the expiry buckets, which are an
#                 *ordered* scale, not identities, so they take a ramp rather
#                 than categorical hues. Assigned darkest-first so the eye lands
#                 on the soonest-expiring bucket. Passes monotone lightness,
#                 adjacent-step separation, single-hue spread, and the 2:1
#                 light-end floor (2.50:1).
SERIES_1 = "#0969da"
EXPIRY_RAMP = ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec"]

CHART_FONT = "monospace"
_GRID_COLOR = "#f0f2f5"
_AXIS_COLOR = "#d0d7de"
_LABEL_COLOR = "#6e7781"
_TITLE_COLOR = "#57606a"
_AXIS_FONT_SIZE = 11
_TITLE_FONT_SIZE = 12

# A gap wider than this between consecutive samples breaks the utilisation line
# rather than being drawn through: see dashboard_data.utilisation_points. Three
# times the default five-minute sample interval, so an ordinary missed reading
# doesn't fragment the line but a real outage shows as the hole it was.
SAMPLE_GAP_SECONDS = 900
HISTORY_WINDOW_HOURS = 48

_TREND_HEIGHT = 200
_BAR_CHART_HEIGHT = 190
_HEADROOM_FACTOR = 1.25
_RECENT_SAMPLES_IN_TABLE = 12
_PERCENT_TICKS = [0, 25, 50, 75, 100]
_MAX_COUNT_TICKS = 6


def themed(chart: alt.TopLevelMixin) -> alt.TopLevelMixin:
	"""The app's typography and a recessive grid.

	Solid hairline gridlines one shade off the surface — never dashed, which
	reads as a threshold or projection when it is just a grid.
	"""
	return (
		chart.configure_view(strokeWidth=0)
		.configure_axis(
			labelFont=CHART_FONT,
			titleFont=CHART_FONT,
			labelColor=_LABEL_COLOR,
			titleColor=_TITLE_COLOR,
			labelFontSize=_AXIS_FONT_SIZE,
			titleFontSize=_AXIS_FONT_SIZE,
			gridColor=_GRID_COLOR,
			gridWidth=1,
			domainColor=_AXIS_COLOR,
			tickColor=_AXIS_COLOR,
		)
		.configure_title(
			font=CHART_FONT, fontSize=_TITLE_FONT_SIZE, color=_TITLE_COLOR, anchor="start"
		)
	)


def count_headroom(rows: list[dict]) -> int:
	"""Upper bound for a count axis: the largest value plus room for its label.

	Always at least 1, so an all-zero chart still gets a sensible axis instead
	of a zero-width domain. Integer, so the axis ticks stay whole — a "0.5
	leases" gridline is meaningless.
	"""
	largest = max((row["count"] for row in rows), default=0)
	return max(int(largest * _HEADROOM_FACTOR) + 1, 1)


def _bar_value_labels(
	base: alt.Chart, dx: int = 0, dy: int = 0, align: str = "center"
) -> alt.Chart:
	"""Value labels just past each bar's end, never inside it.

	A short bar cannot fit a label, and a label clipped by its own mark is worse
	than no label. Labels wear a text colour rather than the bar's, since the
	bar beside them already carries the identity.
	"""
	return base.mark_text(
		align=align,
		baseline="middle",
		dx=dx,
		dy=dy,
		font=CHART_FONT,
		fontSize=_AXIS_FONT_SIZE,
		color=_TITLE_COLOR,
	).encode(text=alt.Text("count:Q", format="d"))


def utilisation_chart(
	samples: list[dict],
) -> Optional[tuple[alt.TopLevelMixin, list[tuple[str, str]]]]:
	"""Pool utilisation over time, or None with fewer than two samples to draw.

	Timestamps go to Vega as ISO-8601 *with their UTC offset*, so each browser
	shows them in its own local time. The previous version stripped the zone
	with tz_convert(None), which leaves UTC wall-clock values that Vega then
	reads as local: in Michigan the axis ran five hours ahead.

	Inline values rather than a DataFrame, for the same reason as the bar
	charts below; it also takes pandas out of the path entirely.
	"""
	if len(samples) < 2:
		return None
	points = utilisation_points(samples, gap_seconds=SAMPLE_GAP_SECONDS)
	values = [
		{"sampled_at": point["sampled_at"].isoformat(), "utilisation_pct": point["utilisation_pct"]}
		for point in points
	]

	base = alt.Chart(alt.Data(values=values)).encode(
		x=alt.X(
			"sampled_at:T",
			title=None,
			# "%H:%M" alone repeats every label across a multi-day window.
			axis=alt.Axis(format="%a %H:%M", tickCount=6),
		)
	)
	# No interpolation curve: readings are minutes apart, and a smoothed line
	# would draw values between them that were never measured.
	line = base.mark_line(color=SERIES_1, strokeWidth=2).encode(
		y=alt.Y(
			"utilisation_pct:Q",
			title="% in use",
			scale=alt.Scale(domain=[0, 100]),
			axis=alt.Axis(values=_PERCENT_TICKS),
		)
	)
	# A 2px line is far too small a target, so an invisible full-height rule per
	# sample catches the pointer and drives both the crosshair and the tooltip.
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
		color=SERIES_1, filled=True, size=60, stroke="#ffffff", strokeWidth=2
	).encode(
		y=alt.Y("utilisation_pct:Q"),
		opacity=alt.condition(hover, alt.value(1), alt.value(0)),
	)
	chart = alt.layer(line, crosshair, marker, hover_targets).properties(height=_TREND_HEIGHT)

	recent = [point for point in points if point["utilisation_pct"] is not None]
	recent = recent[-_RECENT_SAMPLES_IN_TABLE:]
	table = [
		(point["sampled_at"].astimezone().strftime("%m/%d %H:%M"), f"{point['utilisation_pct']}%")
		for point in reversed(recent)
	]
	return themed(chart), table


def expiry_chart(
	leases: list[dict], now: datetime
) -> tuple[alt.TopLevelMixin, list[tuple[str, str]]]:
	"""Lease expiry distribution: ordered buckets, so a single-hue ordinal ramp.

	The x-axis labels carry identity, so nothing here is encoded by colour alone.
	alt.Data(values=...) rather than a DataFrame: Altair turns a DataFrame into
	a *named* dataset the spec only references, and Vega evaluates the spec
	before that name resolves — logging "Infinite extent for field count"
	against an empty array on first paint. Inline values have nothing to resolve.
	"""
	buckets = lease_expiry_buckets(leases, now.timestamp())
	order = [bucket["bucket"] for bucket in buckets]
	base = alt.Chart(alt.Data(values=buckets)).encode(
		x=alt.X("bucket:N", title=None, sort=order, axis=alt.Axis(labelAngle=0)),
		y=alt.Y(
			"count:Q",
			title="Leases",
			scale=alt.Scale(domain=[0, count_headroom(buckets)]),
			axis=alt.Axis(tickMinStep=1, format="d"),
		),
	)
	bars = base.mark_bar(cornerRadiusEnd=4, size=28).encode(
		color=alt.Color(
			"bucket:N", sort=order, scale=alt.Scale(domain=order, range=EXPIRY_RAMP), legend=None
		),
		tooltip=[
			alt.Tooltip("bucket:N", title="Expires in"),
			alt.Tooltip("count:Q", title="Leases"),
		],
	)
	chart = alt.layer(bars, _bar_value_labels(base, dy=-7)).properties(height=_BAR_CHART_HEIGHT)
	table = [(bucket["bucket"], str(bucket["count"])) for bucket in buckets]
	return themed(chart), table


def composition_chart(
	leases: list[dict], config: Optional[dict]
) -> tuple[alt.TopLevelMixin, list[tuple[str, str]]]:
	"""Lease composition: nominal categories, so every bar takes the same hue.

	Horizontal, because the type names are long. An explicit zero-anchored
	domain, NOT Scale(padding=...): Vega-Lite's scale padding expands the domain
	at both ends, so on a count axis it draws -1, -2 ticks for a quantity that
	cannot be negative. count_headroom leaves room for the value label instead.
	"""
	composition = lease_composition(leases, config)
	order = [row["type"] for row in composition]
	base = alt.Chart(alt.Data(values=composition)).encode(
		y=alt.Y("type:N", title=None, sort=order),
		x=alt.X(
			"count:Q",
			title="Leases",
			scale=alt.Scale(domain=[0, count_headroom(composition)]),
			# A wide card otherwise gets a tick every 2 leases, which is noise.
			axis=alt.Axis(tickMinStep=1, format="d", tickCount=_MAX_COUNT_TICKS),
		),
	)
	bars = base.mark_bar(color=SERIES_1, cornerRadiusEnd=4, size=20).encode(
		tooltip=[alt.Tooltip("type:N", title="Type"), alt.Tooltip("count:Q", title="Leases")]
	)
	chart = alt.layer(bars, _bar_value_labels(base, dx=7, align="left")).properties(
		height=_BAR_CHART_HEIGHT
	)
	table = [(row["type"], str(row["count"])) for row in composition]
	return themed(chart), table
