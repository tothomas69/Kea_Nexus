"""
test_pool_history.py — Tests for pool_history.py's sampler.

KeaClient is always stubbed (via the shared stub_kea_client fixture) so no
test touches a real Kea or a socket. Tests that start the real background
thread are cleaned up by an autouse fixture — a leaked sampler would keep
writing to the next test's temp database.
"""

import threading

import pytest

import db
import pool_history
from kea import KeaError


@pytest.fixture(autouse=True)
def _no_leaked_sampler():
	"""Guarantee no sampler survives a test, even one that fails mid-way."""
	yield
	pool_history.stop_pool_sampler()


class _UnreachableKeaClient:
	def get_pool_stats(self) -> dict:
		raise KeaError("connection refused")


class TestTakeSampleNow:
	def test_writes_a_row_from_kea_stats(self, temp_db, stub_kea_client):
		kea = stub_kea_client(
			pool_stats={"total": 200, "assigned": 40, "declined": 2, "cumulative": 900}
		)
		assert pool_history.take_sample_now(kea) is True

		samples = db.get_pool_samples()
		assert len(samples) == 1
		assert samples[0]["total"] == 200
		assert samples[0]["assigned"] == 40
		assert samples[0]["declined"] == 2
		assert samples[0]["cumulative"] == 900

	def test_records_available_from_the_client(self, temp_db, stub_kea_client):
		kea = stub_kea_client(pool_stats={"total": 200, "assigned": 40, "declined": 2})
		pool_history.take_sample_now(kea)
		assert db.get_pool_samples()[0]["available"] == 158

	def test_unreachable_kea_writes_nothing_and_does_not_raise(self, temp_db):
		assert pool_history.take_sample_now(_UnreachableKeaClient()) is False
		assert db.get_pool_samples() == []


class TestSamplerLifecycle:
	def test_start_runs_the_loop_and_records(self, temp_db, monkeypatch, stub_kea_client):
		monkeypatch.setenv("POOL_SAMPLE_INTERVAL_SECONDS", "0.01")
		kea = stub_kea_client(pool_stats={"total": 10, "assigned": 1, "declined": 0})
		monkeypatch.setattr(pool_history, "KeaClient", lambda: kea)

		recorded = threading.Event()
		real_insert = db.insert_pool_sample

		def signalling_insert(*args, **kwargs):
			real_insert(*args, **kwargs)
			recorded.set()

		monkeypatch.setattr(pool_history, "insert_pool_sample", signalling_insert)

		pool_history.start_pool_sampler()
		assert recorded.wait(timeout=5.0), "sampler never wrote a sample"
		assert pool_history.is_sampling() is True

	def test_start_is_idempotent(self, temp_db, monkeypatch, stub_kea_client):
		"""A second start must not create a second loop — two loops would
		double every sample in the series."""
		monkeypatch.setenv("POOL_SAMPLE_INTERVAL_SECONDS", "60")
		monkeypatch.setattr(pool_history, "KeaClient", lambda: stub_kea_client())

		pool_history.start_pool_sampler()
		first_thread = pool_history._sampler_thread
		pool_history.start_pool_sampler()
		assert pool_history._sampler_thread is first_thread

	def test_stop_ends_the_loop(self, temp_db, monkeypatch, stub_kea_client):
		monkeypatch.setenv("POOL_SAMPLE_INTERVAL_SECONDS", "0.01")
		monkeypatch.setattr(pool_history, "KeaClient", lambda: stub_kea_client())

		pool_history.start_pool_sampler()
		pool_history.stop_pool_sampler()
		assert pool_history.is_sampling() is False

	def test_stop_is_safe_when_never_started(self):
		pool_history.stop_pool_sampler()  # must not raise

	def test_a_failing_pass_does_not_kill_the_loop(self, temp_db, monkeypatch, stub_kea_client):
		"""A sampler that dies on one bad pass stops producing history
		silently — nothing would notice until the chart looked frozen."""
		monkeypatch.setenv("POOL_SAMPLE_INTERVAL_SECONDS", "0.01")
		monkeypatch.setattr(pool_history, "KeaClient", lambda: stub_kea_client())

		attempts = []
		survived = threading.Event()

		def sometimes_raises(*args, **kwargs):
			attempts.append(1)
			if len(attempts) == 1:
				raise RuntimeError("disk full")
			survived.set()
			return True

		monkeypatch.setattr(pool_history, "take_sample_now", sometimes_raises)

		pool_history.start_pool_sampler()
		assert survived.wait(timeout=5.0), "loop died on the first failing pass"


class TestConfiguration:
	def test_interval_defaults_when_unset(self, monkeypatch):
		monkeypatch.delenv("POOL_SAMPLE_INTERVAL_SECONDS", raising=False)
		assert (
			pool_history._sample_interval_seconds() == pool_history.DEFAULT_SAMPLE_INTERVAL_SECONDS
		)

	def test_interval_read_from_environment(self, monkeypatch):
		monkeypatch.setenv("POOL_SAMPLE_INTERVAL_SECONDS", "42")
		assert pool_history._sample_interval_seconds() == 42.0

	def test_interval_floors_at_one_second(self, monkeypatch):
		"""A zero or negative interval would spin the loop at full tilt."""
		monkeypatch.setenv("POOL_SAMPLE_INTERVAL_SECONDS", "0")
		assert pool_history._sample_interval_seconds() == 1.0

	def test_retention_defaults_when_unset(self, monkeypatch):
		monkeypatch.delenv("POOL_SAMPLE_RETENTION_DAYS", raising=False)
		assert pool_history._retention_days() == pool_history.DEFAULT_RETENTION_DAYS

	def test_retention_read_from_environment(self, monkeypatch):
		monkeypatch.setenv("POOL_SAMPLE_RETENTION_DAYS", "7")
		assert pool_history._retention_days() == 7
