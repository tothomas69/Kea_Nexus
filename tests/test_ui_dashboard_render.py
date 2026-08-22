"""
test_ui_dashboard_render.py — Smoke test that the Dashboard tab actually renders.

`ui_*.py` is excluded from *coverage* because it needs a Streamlit runtime, but
Streamlit ships `AppTest`, which runs a script headlessly and reports any
exception it raised. That is enough to catch the failures that matter for a
render-only module — a bad Altair encoding, a removed Streamlit parameter, a
column/layout call that throws — none of which the pure-data tests can see.

This is a smoke test, not a visual one: it proves the page executes and that
every section reached the output, not that it looks right.
"""

from datetime import datetime, timedelta, timezone

import pytest
from streamlit.testing.v1 import AppTest

import db


def _dashboard_script() -> None:
	"""Runs inside AppTest — must be self-contained, no closure over the test."""
	from collections import namedtuple
	from datetime import datetime, timezone

	from ui_dashboard import render_dashboard

	service_status = namedtuple("_ServiceStatus", "name up version")
	now_epoch = datetime.now(timezone.utc).timestamp()
	leases = [
		{
			"cltt": now_epoch,
			"valid-lft": 1800,
			"state": 0,
			"ip-address": "172.16.17.50",
			"hw-address": "aa:bb:cc:dd:ee:ff",
		},
		{
			"cltt": now_epoch,
			"valid-lft": 20 * 3600,
			"state": 0,
			"ip-address": "172.16.17.51",
			"hw-address": "aa:bb:cc:dd:ee:aa",
		},
	]
	config = {
		"subnet4": [
			{"reservations": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]}
		]
	}
	stats = {"total": 200, "assigned": 44, "declined": 1, "available": 155, "cumulative": 1400}
	status = {
		"dhcp4": service_status("Kea DHCPv4", True, "3.0.1"),
		"ca": service_status("Control Agent", True, "n/a"),
	}
	render_dashboard(stats, config, status, leases)


def _no_stats_script() -> None:
	from ui_dashboard import render_dashboard

	render_dashboard(None, None, {}, [])


@pytest.fixture
def seeded_db(temp_db):
	"""A registry with one quarantined device and 48h of samples with a gap."""
	db.upsert_device("kids_ipad", hostname="kids-ipad", is_quarantined=True)
	db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")

	base = datetime.now(timezone.utc) - timedelta(hours=48)
	with db._connect() as conn:
		for minutes in range(0, 48 * 60, 5):
			# A deliberate overnight hole, so the gap-breaking path renders too.
			if 10 * 60 < minutes < 19 * 60:
				continue
			assigned = 40 + minutes // 120
			conn.execute(
				"""
				INSERT INTO pool_samples (
					sampled_at, total, assigned, declined, available, cumulative
				)
				VALUES (?, ?, ?, ?, ?, ?)
				""",
				(
					(base + timedelta(minutes=minutes)).isoformat(),
					200,
					assigned,
					1,
					200 - assigned - 1,
					900 + minutes // 5,
				),
			)
		conn.commit()
	return temp_db


class TestDashboardRenders:
	def test_renders_without_raising(self, seeded_db):
		app = AppTest.from_function(_dashboard_script, default_timeout=60)
		app.run()
		assert not app.exception, [e.value for e in app.exception]

	@pytest.mark.parametrize(
		"heading",
		[
			"Active Leases",
			"Quarantined",
			"Issued / 24h",
			"Pool Utilisation",
			"Expiring Within",
			"Lease Composition",
			"Service Health",
		],
	)
	def test_every_section_reaches_the_page(self, seeded_db, heading):
		app = AppTest.from_function(_dashboard_script, default_timeout=60)
		app.run()
		rendered = "".join(block.value for block in app.markdown)
		assert heading in rendered

	def test_every_chart_has_a_table_view(self, seeded_db):
		"""Three charts, three "Show the numbers" expanders — no value is
		reachable only by reading a mark or hovering a tooltip."""
		app = AppTest.from_function(_dashboard_script, default_timeout=60)
		app.run()
		assert len(app.expander) == 3

	def test_missing_stats_shows_an_error_not_a_crash(self, temp_db):
		app = AppTest.from_function(_no_stats_script, default_timeout=60)
		app.run()
		assert not app.exception
		assert app.error, "expected a visible error when pool stats are unavailable"

	def test_renders_with_no_history_at_all(self, temp_db):
		"""A fresh deploy has an empty pool_samples table — the trend chart
		must degrade to a caption rather than throw."""
		db.upsert_device("kids_ipad", hostname="kids-ipad")
		app = AppTest.from_function(_dashboard_script, default_timeout=60)
		app.run()
		assert not app.exception, [e.value for e in app.exception]
		assert any("Not enough history" in c.value for c in app.caption)
