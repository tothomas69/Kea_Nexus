"""
test_quarantine_retry.py — Tests for quarantine_service/retry.py.

backoff_seconds=0 is passed throughout so failing-then-succeeding tests
don't actually sleep — the retry *logic* is what's under test, not timing.
"""

import pytest

import db
from quarantine_service.retry import run_with_retries


class TestRunWithRetries:
	def test_succeeds_on_first_attempt(self, temp_db):
		calls = []
		result = run_with_retries(
			"kea", "tommy_laptop", "quarantine", lambda: calls.append(1), backoff_seconds=0
		)
		assert result is True
		assert len(calls) == 1
		log = db.get_quarantine_log("tommy_laptop")
		assert len(log) == 1
		assert log[0]["succeeded"] == 1
		assert log[0]["attempt_count"] == 1
		assert log[0]["step"] == "kea"

	def test_succeeds_after_earlier_failures(self, temp_db):
		attempts = {"count": 0}

		def flaky():
			attempts["count"] += 1
			if attempts["count"] < 3:
				raise RuntimeError("transient failure")

		result = run_with_retries("kea", "tommy_laptop", "quarantine", flaky, backoff_seconds=0)
		assert result is True
		assert attempts["count"] == 3
		log = db.get_quarantine_log("tommy_laptop")
		assert len(log) == 1  # one row, not one per attempt
		assert log[0]["succeeded"] == 1
		assert log[0]["attempt_count"] == 3

	def test_fails_after_max_attempts(self, temp_db):
		def always_fails():
			raise RuntimeError("permanent failure")

		result = run_with_retries(
			"kea", "tommy_laptop", "quarantine", always_fails, backoff_seconds=0
		)
		assert result is False
		log = db.get_quarantine_log("tommy_laptop")
		assert len(log) == 1
		assert log[0]["succeeded"] == 0
		assert log[0]["attempt_count"] == 3
		assert "permanent failure" in log[0]["detail"]

	def test_respects_custom_max_attempts(self, temp_db):
		call_count = {"count": 0}

		def always_fails():
			call_count["count"] += 1
			raise RuntimeError("nope")

		run_with_retries(
			"kea",
			"tommy_laptop",
			"quarantine",
			always_fails,
			max_attempts=5,
			backoff_seconds=0,
		)
		assert call_count["count"] == 5

	def test_zero_max_attempts_raises(self, temp_db):
		with pytest.raises(AssertionError):
			run_with_retries("kea", "tommy_laptop", "quarantine", lambda: None, max_attempts=0)

	def test_logs_release_action_correctly(self, temp_db):
		run_with_retries("kea", "tommy_laptop", "release", lambda: None, backoff_seconds=0)
		log = db.get_quarantine_log("tommy_laptop")
		assert log[0]["action"] == "release"
