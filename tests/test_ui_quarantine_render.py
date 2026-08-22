"""
test_ui_quarantine_render.py — Smoke test that the Quarantine tab renders.

Same rationale as tests/test_ui_dashboard_render.py: `ui_*.py` is excluded from
coverage because it needs a Streamlit runtime, but `AppTest` runs the script
headlessly and surfaces anything it raised. That is what catches a Streamlit
parameter being removed out from under this module — the failure mode that
prompted these tests, since every button here passes a width argument.

A smoke test, not a visual one: it proves the page executes, not that it looks
right.
"""

import pytest
from streamlit.testing.v1 import AppTest

import db


def _quarantine_script() -> None:
	"""Runs inside AppTest — must be self-contained, no closure over the test."""
	from datetime import datetime, timezone

	from ui_quarantine import render_quarantine

	now_epoch = datetime.now(timezone.utc).timestamp()
	leases = [
		{
			"cltt": now_epoch,
			"valid-lft": 3600,
			"state": 0,
			"ip-address": "172.16.17.50",
			"hw-address": "aa:bb:cc:dd:ee:ff",
			"hostname": "kids-ipad",
		}
	]
	render_quarantine(leases, {"subnet4": [{"reservations": []}]})


@pytest.fixture
def registry(temp_db):
	db.upsert_device("kids_ipad", hostname="kids-ipad", group_tag="kids", is_quarantined=True)
	db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", group_tag="kids")
	db.insert_quarantine_log_entry(
		"kids_ipad", "quarantine", "kea", succeeded=True, attempt_count=1
	)
	return temp_db


class TestQuarantineTabRenders:
	def test_renders_with_devices_registered(self, registry):
		app = AppTest.from_function(_quarantine_script, default_timeout=60)
		app.run()
		assert not app.exception, [e.value for e in app.exception]

	def test_renders_with_an_empty_registry(self, temp_db):
		"""A fresh deploy has no devices — the tab must still draw."""
		app = AppTest.from_function(_quarantine_script, default_timeout=60)
		app.run()
		assert not app.exception, [e.value for e in app.exception]

	def test_device_row_buttons_are_present(self, registry):
		app = AppTest.from_function(_quarantine_script, default_timeout=60)
		app.run()
		labels = {button.label for button in app.button}
		assert {"Quarantine", "Release", "Edit"} <= labels

	def test_quarantined_device_row_uses_the_alert_colour(self, registry):
		"""The whole row turning red is the only live "quarantined right now"
		signal — last_quarantined_at is never cleared on release."""
		app = AppTest.from_function(_quarantine_script, default_timeout=60)
		app.run()
		rendered = "".join(block.value for block in app.markdown)
		assert "#991b1b" in rendered

	def test_timestamp_render_survives_a_malformed_value(self, temp_db):
		"""_format_local_time falls back to the raw string rather than
		crashing the whole table over one bad cell."""
		db.upsert_device("odd_device", hostname="kids-ipad", last_seen_at="not-a-timestamp")
		app = AppTest.from_function(_quarantine_script, default_timeout=60)
		app.run()
		assert not app.exception, [e.value for e in app.exception]
