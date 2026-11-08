"""
test_dashboard_charts.py — Tests for the chart builders both UIs share.

The builders return Altair charts; the tests read the Vega-Lite dict the
browser will receive, since that is the real contract.
"""

from datetime import datetime, timedelta, timezone

import dashboard_charts as charts
from dashboard_data import EXPIRY_BUCKETS

_BASE_TIME = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
_NOW_EPOCH = int(_BASE_TIME.timestamp())


def _sample(minutes_after_base: int, assigned: int = 10) -> dict:
	return {
		"sampled_at": (_BASE_TIME + timedelta(minutes=minutes_after_base)).isoformat(),
		"total": 100,
		"assigned": assigned,
		"declined": 0,
		"available": 100 - assigned,
		"cumulative": 0,
	}


def _inline_values(spec: dict) -> list[dict]:
	"""The inline data rows of a layered spec."""
	return spec["data"]["values"]


class TestUtilisationChart:
	def test_needs_two_samples(self):
		assert charts.utilisation_chart([]) is None
		assert charts.utilisation_chart([_sample(0)]) is None

	def test_timestamps_keep_their_utc_offset(self):
		"""Regression: stripping the zone made Vega show UTC as local time."""
		chart, _ = charts.utilisation_chart([_sample(0), _sample(5)])
		stamps = [row["sampled_at"] for row in _inline_values(chart.to_dict())]
		assert stamps
		assert all(stamp.endswith("+00:00") for stamp in stamps)

	def test_gap_breaks_the_line(self):
		chart, _ = charts.utilisation_chart([_sample(0), _sample(120)])
		values = [row["utilisation_pct"] for row in _inline_values(chart.to_dict())]
		assert None in values

	def test_table_is_newest_first(self):
		_, table = charts.utilisation_chart([_sample(0, assigned=10), _sample(5, assigned=20)])
		assert [value for _, value in table] == ["20.0%", "10.0%"]


class TestExpiryChart:
	def test_every_bucket_in_table(self):
		lease = {"cltt": _NOW_EPOCH, "valid-lft": 1800, "state": 0}
		_, table = charts.expiry_chart([lease], _BASE_TIME)
		assert [label for label, _ in table] == [label for label, _ in EXPIRY_BUCKETS]
		assert table[0][1] == "1"

	def test_spec_is_plain_json(self):
		chart, _ = charts.expiry_chart([], _BASE_TIME)
		spec = chart.to_dict()
		assert "datasets" not in spec
		assert len(_inline_values(spec)) == len(EXPIRY_BUCKETS)


class TestCompositionChart:
	def test_counts_by_type(self):
		lease = {"ip-address": "10.0.0.50", "hw-address": "aa:bb:cc:dd:ee:ff", "state": 0}
		_, table = charts.composition_chart([lease], None)
		assert dict(table)["dynamic"] == "1"


class TestCountHeadroom:
	def test_never_zero(self):
		assert charts.count_headroom([]) == 1
		assert charts.count_headroom([{"count": 0}]) == 1

	def test_room_above_largest(self):
		assert charts.count_headroom([{"count": 8}]) > 8
