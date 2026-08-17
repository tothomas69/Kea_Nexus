"""
retry.py — Shared retry-with-backoff wrapper for quarantine enforcement steps.

Every enforcement action (Kea deny, ARP disruption, Pi-hole block) retries
up to MAX_ATTEMPTS times on failure, then logs exactly one audit row
recording how many attempts it took and whether it ultimately succeeded —
not one row per attempt. See "Orchestration (per resolved device)" in
docs/quarantine-feature-design.md.
"""

import time
from typing import Callable

from db import insert_quarantine_log_entry

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0


def run_with_retries(
	step: str,
	friendly_name: str,
	action: str,
	attempt_fn: Callable[[], None],
	max_attempts: int = MAX_ATTEMPTS,
	backoff_seconds: float = BACKOFF_SECONDS,
) -> bool:
	"""Call attempt_fn up to max_attempts times, logging one final audit row.

	Returns True if attempt_fn eventually succeeded, False if every attempt
	failed. Never raises — failures are logged rather than propagated, so
	one failed step doesn't stop the other enforcement steps from running.
	"""
	assert max_attempts >= 1, "max_attempts must be at least 1"
	last_error = ""

	for attempt_number in range(1, max_attempts + 1):
		try:
			attempt_fn()
			insert_quarantine_log_entry(
				friendly_name, action, step, succeeded=True, attempt_count=attempt_number
			)
			return True
		# Broad except is deliberate: this wrapper is shared across Kea,
		# ARP, and Pi-hole actions, which fail in different ways
		# (KeaError, socket errors, HTTP errors) — all of them should
		# retry the same way rather than each step reimplementing this.
		except Exception as exc:
			last_error = str(exc)
			if attempt_number < max_attempts:
				time.sleep(backoff_seconds)

	insert_quarantine_log_entry(
		friendly_name,
		action,
		step,
		succeeded=False,
		attempt_count=max_attempts,
		detail=last_error,
	)
	return False
