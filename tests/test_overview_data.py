"""
test_overview_data.py — Tests for web/overview_data.py: the key figures, the map
cells and their display statuses, and the Activity feed.
"""

from datetime import datetime, timezone

from address_map import STATUS_FREE, STATUS_LEASED, STATUS_RESERVED, AddressRow
from web.overview_data import (
	DISPLAY_UNKNOWN,
	LEGEND_STATUSES,
	activity,
	devices_by_ip,
	issued_recently,
	legend,
	local_time,
	map_cells,
	pool_figures,
)

_START = datetime(2026, 11, 7, 17, 0, tzinfo=timezone.utc)


def _row(octet, status=STATUS_LEASED, hostname="", label=""):
	return AddressRow(
		ip=f"172.16.17.{octet}", last_octet=octet, status=status, hostname=hostname, label=label
	)


def _device(name, hostname=""):
	return {"friendly_name": name, "hostname": hostname}


class TestLocalTime:
	def test_converts_utc_to_eastern(self):
		assert local_time("2026-11-07T17:30:00+00:00") == "11/07 12:30"

	def test_custom_format(self):
		assert local_time("2026-11-07T17:30:00+00:00", "%H:%M") == "12:30"

	def test_missing_or_garbage_is_blank(self):
		assert local_time(None) == ""
		assert local_time("") == ""
		assert local_time("not a time") == ""


class TestPoolFigures:
	def test_none_without_stats(self):
		assert pool_figures(None) is None
		assert pool_figures({}) is None

	def test_counts_declined_as_used(self):
		figures = pool_figures({"total": 150, "assigned": 93, "declined": 1, "available": 56})
		assert figures["used"] == 94
		assert figures["percent"] == 63
		assert figures["level"] == "ok"

	def test_warn_and_full_levels(self):
		assert pool_figures({"total": 100, "assigned": 75})["level"] == "warn"
		assert pool_figures({"total": 100, "assigned": 90})["level"] == "full"

	def test_zero_sized_pool_does_not_divide_by_zero(self):
		assert pool_figures({"total": 0, "assigned": 0})["percent"] == 0

	def test_issued_needs_history(self):
		assert issued_recently([], _START) is None


class TestMapCells:
	def test_device_matches_by_live_hostname_first(self):
		rows = [_row(164, hostname="kids-ipad"), _row(170)]
		device = _device("kids_ipad", hostname="kids-ipad")
		assert devices_by_ip([device], rows) == {"172.16.17.164": device}

	def test_no_lease_means_no_square(self):
		"""No stale last-seen fallback: a device whose lease is gone isn't placed."""
		rows = [_row(164, status=STATUS_FREE)]
		assert devices_by_ip([_device("kids_ipad", hostname="kids-ipad")], rows) == {}

	def test_device_with_no_address_is_not_placed(self):
		assert devices_by_ip([_device("ghost")], [_row(10)]) == {}

	def test_registered_device_keeps_its_status_and_shows_its_name(self):
		rows = [_row(164, hostname="kids-ipad")]
		cell = map_cells(rows, [_device("kids_ipad", hostname="kids-ipad")])[0]
		assert cell["status"] == STATUS_LEASED
		assert cell["device"] == "kids_ipad"

	def test_nameless_dynamic_lease_is_unknown(self):
		cell = map_cells([_row(151)], [])[0]
		assert cell["status"] == DISPLAY_UNKNOWN

	def test_named_or_reserved_leases_are_not_unknown(self):
		cells = map_cells([_row(150, hostname="ps5"), _row(152, status=STATUS_RESERVED)], [])
		assert [cell["status"] for cell in cells] == [STATUS_LEASED, STATUS_RESERVED]

	def test_cell_carries_everything_the_hover_card_shows(self):
		row = AddressRow(
			ip="172.16.17.10",
			last_octet=10,
			status="static",
			label="nas",
			mac="aa:bb:cc:dd:ee:ff",
			notes="rack 1",
			editable=True,
		)
		cell = map_cells([row], [])[0]
		assert cell == {
			"octet": 10,
			"ip": "172.16.17.10",
			"status": "static",
			"status_label": "Static",
			"label": "nas",
			"hostname": "",
			"mac": "aa:bb:cc:dd:ee:ff",
			"info": "",
			"notes": "rack 1",
			"editable": True,
			"device": "",
			"seen": None,
			"seen_mac": "",
			"seen_age": "",
			"seen_mismatch": False,
		}

	def test_static_address_shows_the_mac_that_answered(self):
		row = AddressRow(ip="172.16.17.10", last_octet=10, status="static", mac="")
		cell = map_cells([row], [], {"172.16.17.10": "aa:bb:cc:00:00:0a"}, "2 min ago")[0]
		assert (cell["seen"], cell["seen_mac"], cell["seen_age"]) == (
			True,
			"aa:bb:cc:00:00:0a",
			"2 min ago",
		)
		assert cell["seen_mismatch"] is False

	def test_silent_address_after_a_sweep_is_no_answer_not_unchecked(self):
		cell = map_cells([_row(20)], [], {}, "just now")[0]
		assert cell["seen"] is False
		assert cell["seen_mac"] == ""

	def test_different_answering_mac_is_flagged(self):
		row = AddressRow(ip="172.16.17.10", last_octet=10, status="static", mac="AA:BB:CC:DD:EE:FF")
		seen = {"172.16.17.10": "11:22:33:44:55:66"}
		assert map_cells([row], [], seen, "just now")[0]["seen_mismatch"] is True

	def test_same_mac_in_another_case_is_not_a_mismatch(self):
		row = AddressRow(ip="172.16.17.10", last_octet=10, status="static", mac="AA:BB:CC:DD:EE:FF")
		seen = {"172.16.17.10": "aa:bb:cc:dd:ee:ff"}
		assert map_cells([row], [], seen, "just now")[0]["seen_mismatch"] is False

	def test_answer_without_a_mac_is_never_a_mismatch(self):
		row = AddressRow(ip="172.16.17.10", last_octet=10, status="static", mac="aa:bb:cc:dd:ee:ff")
		cell = map_cells([row], [], {"172.16.17.10": ""}, "just now")[0]
		assert (cell["seen"], cell["seen_mismatch"]) == (True, False)

	def test_legend_follows_the_declared_order(self):
		assert [item["status"] for item in legend()] == LEGEND_STATUSES


class TestActivity:
	_ADMIN = [
		{
			"occurred_at": "2026-11-07T17:30:00+00:00",
			"username": "tom",
			"summary": "turned DHCP on",
		},
		{
			"occurred_at": "2026-11-07T16:00:00+00:00",
			"username": "pat",
			"summary": "saved the DHCP settings",
		},
	]

	def test_one_line_per_change(self):
		assert activity(self._ADMIN, limit=5) == [
			{
				"when": "12:30",
				"text": "tom turned DHCP on",
				"tone": "admin",
				"at": "2026-11-07T17:30:00+00:00",
			},
			{
				"when": "11:00",
				"text": "pat saved the DHCP settings",
				"tone": "admin",
				"at": "2026-11-07T16:00:00+00:00",
			},
		]

	def test_limit(self):
		assert [line["text"] for line in activity(self._ADMIN, limit=1)] == ["tom turned DHCP on"]

	def test_empty(self):
		assert activity([], limit=5) == []
