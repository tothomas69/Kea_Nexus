"""
test_dashboard_data.py — Tests for dashboard_data.py's pure shaping functions.

Everything here is plain data in, plain data out — no Streamlit, no Kea, no
database — which is the whole reason this logic lives outside ui_dashboard.py.
Time is always passed in explicitly so no test races the clock.
"""

from datetime import datetime, timedelta, timezone

import pytest

import dashboard_data as dd

_NOW_EPOCH = 1_700_000_000
_BASE_TIME = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _lease(seconds_remaining: int, state: int = 0, **extra) -> dict:
	"""A lease expiring seconds_remaining from _NOW_EPOCH."""
	lease = {"cltt": _NOW_EPOCH, "valid-lft": seconds_remaining, "state": state}
	lease.update(extra)
	return lease


def _sample(minutes_after_base: int, cumulative: int = 0, **counters) -> dict:
	sample = {
		"sampled_at": (_BASE_TIME + timedelta(minutes=minutes_after_base)).isoformat(),
		"total": 100,
		"assigned": 10,
		"declined": 0,
		"available": 90,
		"cumulative": cumulative,
	}
	sample.update(counters)
	return sample


class TestLeaseExpiryBuckets:
	def test_every_bucket_present_even_when_empty(self):
		buckets = dd.lease_expiry_buckets([], _NOW_EPOCH)
		assert [b["bucket"] for b in buckets] == ["< 1h", "1–6h", "6–24h", "> 24h"]
		assert all(b["count"] == 0 for b in buckets)

	@pytest.mark.parametrize(
		"seconds_remaining,expected_bucket",
		[
			(60, "< 1h"),
			(3599, "< 1h"),
			(3600, "1–6h"),
			(5 * 3600, "1–6h"),
			(6 * 3600, "6–24h"),
			(23 * 3600, "6–24h"),
			(24 * 3600, "> 24h"),
			(72 * 3600, "> 24h"),
		],
	)
	def test_boundaries_land_in_the_right_bucket(self, seconds_remaining, expected_bucket):
		buckets = dd.lease_expiry_buckets([_lease(seconds_remaining)], _NOW_EPOCH)
		counted = [b["bucket"] for b in buckets if b["count"] == 1]
		assert counted == [expected_bucket]

	def test_already_expired_lease_counts_as_soonest(self):
		"""An expired lease Kea hasn't reclaimed yet is exactly what's worth
		seeing — dropping it would hide it."""
		buckets = dd.lease_expiry_buckets([_lease(-5000)], _NOW_EPOCH)
		assert buckets[0] == {"bucket": "< 1h", "count": 1}

	def test_declined_leases_excluded(self):
		leases = [_lease(60), _lease(60, state=1)]
		assert dd.lease_expiry_buckets(leases, _NOW_EPOCH)[0]["count"] == 1

	def test_missing_valid_lft_falls_back_to_a_day(self):
		lease = {"cltt": _NOW_EPOCH, "state": 0}
		buckets = dd.lease_expiry_buckets([lease], _NOW_EPOCH)
		assert [b["bucket"] for b in buckets if b["count"]] == ["> 24h"]


class TestLeaseComposition:
	def test_all_types_present_in_fixed_order(self):
		composition = dd.lease_composition([], None)
		assert [c["type"] for c in composition] == ["dynamic", "reserved", "fixed", "name-only"]

	def test_classifies_against_reservations(self):
		config = {
			"subnet4": [
				{
					"reservations": [
						{"hw-address": "aa:aa:aa:aa:aa:aa", "ip-address": "172.16.17.50"},
						{"hw-address": "bb:bb:bb:bb:bb:bb"},
					]
				}
			]
		}
		leases = [
			_lease(60, **{"ip-address": "172.16.17.50", "hw-address": "aa:aa:aa:aa:aa:aa"}),
			_lease(60, **{"ip-address": "172.16.17.51", "hw-address": "bb:bb:bb:bb:bb:bb"}),
			_lease(60, **{"ip-address": "172.16.17.52", "hw-address": "cc:cc:cc:cc:cc:cc"}),
		]
		counts = {c["type"]: c["count"] for c in dd.lease_composition(leases, config)}
		assert counts == {"fixed": 1, "reserved": 1, "dynamic": 1, "name-only": 0}

	def test_declined_leases_excluded(self):
		leases = [_lease(60), _lease(60, state=1)]
		counts = {c["type"]: c["count"] for c in dd.lease_composition(leases, None)}
		assert counts["dynamic"] == 1


class TestUtilisationPoints:
	def test_counts_declined_as_used(self):
		points = dd.utilisation_points(
			[_sample(0, total=200, assigned=40, declined=10)], gap_seconds=900
		)
		assert points[0]["utilisation_pct"] == 25.0

	def test_zero_total_does_not_divide_by_zero(self):
		points = dd.utilisation_points([_sample(0, total=0, assigned=0)], gap_seconds=900)
		assert points[0]["utilisation_pct"] == 0.0

	def test_contiguous_samples_have_no_break(self):
		samples = [_sample(0), _sample(5), _sample(10)]
		points = dd.utilisation_points(samples, gap_seconds=900)
		assert len(points) == 3
		assert all(p["utilisation_pct"] is not None for p in points)

	def test_gap_inserts_a_null_point_so_the_line_breaks(self):
		"""The sampler leaves real holes; drawing through one would assert a
		measurement that was never taken."""
		samples = [_sample(0), _sample(5), _sample(600)]
		points = dd.utilisation_points(samples, gap_seconds=900)
		assert [p["utilisation_pct"] is None for p in points] == [False, False, True, False]

	def test_break_sits_at_the_start_of_the_gap(self):
		samples = [_sample(0), _sample(600)]
		points = dd.utilisation_points(samples, gap_seconds=900)
		assert points[1]["sampled_at"] == _BASE_TIME + timedelta(seconds=1)

	def test_empty_input_yields_no_points(self):
		assert dd.utilisation_points([], gap_seconds=900) == []

	def test_zero_gap_seconds_raises(self):
		with pytest.raises(AssertionError):
			dd.utilisation_points([], gap_seconds=0)


class TestLeasesIssuedSince:
	def test_counts_the_cumulative_delta(self):
		samples = [_sample(m, cumulative=100 + m) for m in range(0, 121, 5)]
		issued = dd.leases_issued_since(samples, 1, _BASE_TIME + timedelta(minutes=120))
		assert issued == 60

	def test_none_without_a_sample_old_enough(self):
		samples = [_sample(0, cumulative=100), _sample(5, cumulative=101)]
		assert dd.leases_issued_since(samples, 24, _BASE_TIME + timedelta(minutes=5)) is None

	def test_none_when_a_single_sample(self):
		assert dd.leases_issued_since([_sample(0)], 1, _BASE_TIME) is None

	def test_stale_baseline_is_rejected_rather_than_mislabelled(self):
		"""With a long gap, the newest sample at-or-before the cutoff can be
		hours older than the window asked for. Reporting ten hours of leases
		as "the last hour" would be worse than reporting nothing.
		"""
		samples = [
			_sample(0, cumulative=100),
			_sample(5, cumulative=101),
			_sample(600, cumulative=150),
			_sample(605, cumulative=152),
		]
		assert dd.leases_issued_since(samples, 1, _BASE_TIME + timedelta(minutes=605)) is None

	def test_baseline_within_tolerance_is_accepted(self):
		"""A missed sample or two must not blank the figure."""
		samples = [_sample(0, cumulative=100), _sample(70, cumulative=130)]
		issued = dd.leases_issued_since(
			samples, 1, _BASE_TIME + timedelta(minutes=70), baseline_tolerance_seconds=3600
		)
		assert issued == 30

	def test_counter_reset_reports_nothing_rather_than_a_negative(self):
		"""Kea restarting resets cumulative-assigned-addresses."""
		samples = [_sample(0, cumulative=5000), _sample(70, cumulative=12)]
		assert dd.leases_issued_since(samples, 1, _BASE_TIME + timedelta(minutes=70)) is None

	def test_zero_window_raises(self):
		with pytest.raises(AssertionError):
			dd.leases_issued_since([_sample(0), _sample(5)], 0, _BASE_TIME)


class TestQuarantinedCount:
	def test_counts_only_the_live_flag(self):
		"""last_quarantined_at is never cleared on release, so it cannot
		answer "quarantined right now"."""
		devices = [
			{"is_quarantined": 1, "last_quarantined_at": "2026-08-01T00:00:00+00:00"},
			{"is_quarantined": 0, "last_quarantined_at": "2026-08-01T00:00:00+00:00"},
			{"is_quarantined": 0, "last_quarantined_at": ""},
		]
		assert dd.quarantined_count(devices) == 1

	def test_empty_registry(self):
		assert dd.quarantined_count([]) == 0


class TestUtcNow:
	def test_returns_an_aware_utc_datetime(self):
		now = dd.utc_now()
		assert now.tzinfo is not None
		assert now.utcoffset() == timedelta(0)
