"""
test_web_liveness.py — Tests for web/liveness.py: the latest sweep, the
background loop and the age label. The quarantine service is always patched.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from quarantine_client import QuarantineServiceError
from web import liveness


@pytest.fixture(autouse=True)
def clean_state():
	liveness.stop_sweep_loop()
	liveness.reset()
	yield
	liveness.stop_sweep_loop()
	liveness.reset()


def _kea(addresses):
	kea = MagicMock()
	kea.get_leases.return_value = [{"ip-address": address} for address in addresses] + [
		{"hw-address": "x"}
	]
	return kea


class TestSweepNow:
	def test_keeps_the_latest(self):
		assert liveness.latest() is None
		with patch("web.liveness.trigger_liveness_sweep", return_value=["10.0.0.1"]):
			result = liveness.sweep_now(["10.0.0.1", "10.0.0.2", "10.0.0.2"])
		assert result.responding == frozenset({"10.0.0.1"})
		assert result.checked == 2
		assert liveness.latest() is result

	def test_failure_keeps_the_previous_result(self):
		with patch("web.liveness.trigger_liveness_sweep", return_value=["10.0.0.1"]):
			first = liveness.sweep_now(["10.0.0.1"])
		with patch(
			"web.liveness.trigger_liveness_sweep", side_effect=QuarantineServiceError("down")
		):
			with pytest.raises(QuarantineServiceError):
				liveness.sweep_now(["10.0.0.1"])
		assert liveness.latest() is first

	def test_sweep_leased_addresses_skips_leases_without_an_address(self):
		with patch("web.liveness.trigger_liveness_sweep", return_value=[]) as sweep:
			liveness.sweep_leased_addresses(_kea(["10.0.0.1"]))
		sweep.assert_called_once_with(["10.0.0.1"])


class TestLoop:
	def test_runs_survives_failures_and_stops(self):
		passes = threading.Event()
		calls = []

		def fake_sweep(addresses):
			calls.append(addresses)
			if len(calls) == 1:
				raise QuarantineServiceError("down")
			passes.set()
			return addresses

		with (
			patch("web.liveness.trigger_liveness_sweep", side_effect=fake_sweep),
			patch("web.liveness.sweep_interval_seconds", return_value=0),
		):
			liveness.start_sweep_loop(lambda: _kea(["10.0.0.1"]))
			liveness.start_sweep_loop(lambda: _kea(["10.0.0.9"]))
			assert passes.wait(timeout=5)
			assert liveness.is_sweeping()
			liveness.stop_sweep_loop()
		assert not liveness.is_sweeping()
		assert all(addresses == ["10.0.0.1"] for addresses in calls)
		assert liveness.latest().responding == frozenset({"10.0.0.1"})

	def test_stop_when_never_started(self):
		liveness.stop_sweep_loop()
		assert not liveness.is_sweeping()


class TestSettings:
	@pytest.mark.parametrize(
		("value", "expected"), [(None, 300), ("120", 120), ("5", 30), ("soon", 300)]
	)
	def test_interval(self, value, expected, monkeypatch):
		if value is None:
			monkeypatch.delenv("LIVENESS_SWEEP_INTERVAL_SECONDS", raising=False)
		else:
			monkeypatch.setenv("LIVENESS_SWEEP_INTERVAL_SECONDS", value)
		assert liveness.sweep_interval_seconds() == expected


class TestAgeLabel:
	@pytest.mark.parametrize(
		("seconds", "label"),
		[(-5, "just now"), (30, "just now"), (125, "2 min ago"), (3 * 3600 + 120, "3 h 2 min ago")],
	)
	def test_labels(self, seconds, label):
		result = liveness.SweepResult(frozenset(), 0, 1000.0)
		assert liveness.age_label(result, 1000.0 + seconds) == label


def test_age_uses_wall_clock():
	result = liveness.SweepResult(frozenset(), 0, time.time())
	assert liveness.age_label(result, time.time()) == "just now"
