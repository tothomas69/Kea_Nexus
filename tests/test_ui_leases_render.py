"""
test_ui_leases_render.py — Smoke tests that the Leases tab renders, and that
the liveness sweep colours the rows it should.

Same rationale as tests/test_ui_quarantine_render.py: `ui_*.py` is excluded
from coverage because it needs a Streamlit runtime, but `AppTest` runs the
script headlessly and surfaces anything it raised.

The sweep's own behaviour is covered by test_quarantine_liveness.py; here
quarantine_client.trigger_liveness_sweep is patched, so no HTTP call and no
packet is involved — these tests check the wiring and the rendered markup.
"""

from unittest.mock import patch

from streamlit.testing.v1 import AppTest

_LIVE_IP = "172.16.17.50"
_DEAD_IP = "172.16.17.51"
# The cell style unique to a live row. Deliberately not the bare colour:
# _row_chip already paints the active-lease chip the same green in every
# row, so matching on "#1a7f37" alone would pass on any lease at all.
_LIVE_CELL_STYLE = "color:#1a7f37;font-weight:600"


def _leases_script() -> None:
	"""Runs inside AppTest — must be self-contained, no closure over the test."""
	import time

	from ui_leases import render_leases

	now_epoch = int(time.time())
	leases = [
		{
			"cltt": now_epoch,
			"valid-lft": 3600,
			"state": 0,
			"ip-address": "172.16.17.50",
			"hw-address": "aa:bb:cc:dd:ee:ff",
			"hostname": "kids-ipad",
		},
		{
			"cltt": now_epoch,
			"valid-lft": 3600,
			"state": 0,
			"ip-address": "172.16.17.51",
			"hw-address": "11:22:33:44:55:66",
			"hostname": "old-printer",
		},
	]
	render_leases(leases, {"subnet4": [{"reservations": []}]})


def _markdown_text(app) -> str:
	return "\n".join(element.value for element in app.markdown)


def _run_with_sweep(sweep_result, click: bool = True):
	"""Run the tab, optionally clicking the sweep button with a patched sweep."""
	app = AppTest.from_function(_leases_script, default_timeout=60)
	with patch("ui_leases.trigger_liveness_sweep", return_value=sweep_result) as mock_sweep:
		app.run()
		if click:
			app.button(key="leases_liveness_chipblue").click().run()
	return app, mock_sweep


class TestLeasesTabRenders:
	def test_renders_without_a_sweep(self):
		app, _ = _run_with_sweep([], click=False)
		assert not app.exception, [e.value for e in app.exception]

	def test_sweep_button_is_present(self):
		app, _ = _run_with_sweep([], click=False)
		assert "Check who's online" in {button.label for button in app.button}

	def test_no_row_is_green_before_a_sweep(self):
		"""Never-checked must not look like checked-and-dead, or the reverse."""
		app, _ = _run_with_sweep([], click=False)
		assert _LIVE_CELL_STYLE not in _markdown_text(app)


class TestLivenessSweep:
	def test_clicking_sweeps_every_lease_address(self):
		_app, mock_sweep = _run_with_sweep([_LIVE_IP])
		mock_sweep.assert_called_once_with([_LIVE_IP, _DEAD_IP])

	def test_responding_row_turns_green(self):
		app, _ = _run_with_sweep([_LIVE_IP])
		assert not app.exception, [e.value for e in app.exception]
		live_row = [line for line in _markdown_text(app).split("<tr>") if _LIVE_IP in line]
		assert live_row and _LIVE_CELL_STYLE in live_row[0]

	def test_non_responding_row_stays_default_coloured(self):
		app, _ = _run_with_sweep([_LIVE_IP])
		dead_row = [line for line in _markdown_text(app).split("<tr>") if _DEAD_IP in line]
		assert dead_row and _LIVE_CELL_STYLE not in dead_row[0]

	def test_caption_reports_how_many_answered(self):
		app, _ = _run_with_sweep([_LIVE_IP])
		captions = "\n".join(element.value for element in app.caption)
		assert "1 of 2" in captions

	def test_service_error_is_shown_not_raised(self):
		"""keanexus-quarantine is an optional profile — an unreachable one
		must not take the whole Leases tab down."""
		from quarantine_client import QuarantineServiceError

		app = AppTest.from_function(_leases_script, default_timeout=60)
		with patch(
			"ui_leases.trigger_liveness_sweep",
			side_effect=QuarantineServiceError("Cannot reach keanexus-quarantine"),
		):
			app.run()
			app.button(key="leases_liveness_chipblue").click().run()

		assert not app.exception, [e.value for e in app.exception]
		assert any("Cannot reach" in element.value for element in app.error)
		assert _LIVE_CELL_STYLE not in _markdown_text(app)
