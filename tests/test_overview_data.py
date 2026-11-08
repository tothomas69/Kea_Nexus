"""
test_overview_data.py — Tests for web/overview_data.py: the key figures, the map
cells and their display statuses, the Quarantine card rows and the Activity feed.
"""

from datetime import datetime, timedelta, timezone

from address_map import STATUS_FREE, STATUS_LEASED, STATUS_RESERVED, AddressRow
from web.overview_data import (
	ACTION_GROUPING_SECONDS,
	DISPLAY_QUARANTINED,
	DISPLAY_UNKNOWN,
	LEGEND_STATUSES,
	activity,
	devices_by_ip,
	issued_recently,
	legend,
	local_time,
	map_cells,
	pool_figures,
	quarantine_rows,
)

_START = datetime(2026, 11, 7, 17, 0, tzinfo=timezone.utc)


def _row(octet, status=STATUS_LEASED, hostname="", label=""):
	return AddressRow(
		ip=f"172.16.17.{octet}", last_octet=octet, status=status, hostname=hostname, label=label
	)


def _device(name, hostname="", last_ip=None, quarantined=False, group="", since=None):
	return {
		"friendly_name": name,
		"hostname": hostname,
		"last_seen_ip_address": last_ip,
		"is_quarantined": quarantined,
		"group_tag": group,
		"last_quarantined_at": since,
	}


def _log(name, action, step, succeeded=True, minutes_after_start=0):
	return {
		"friendly_name": name,
		"action": action,
		"step": step,
		"succeeded": succeeded,
		"occurred_at": (_START + timedelta(minutes=minutes_after_start)).isoformat(),
	}


def _action(name, action, minutes, failed_steps=()):
	"""quarantine_log rows for one action, newest first, as db.get_quarantine_log returns them."""
	steps = ["kea", "arp", "pihole_primary", "pihole_secondary", "nmap_refresh"]
	return [_log(name, action, step, step not in failed_steps, minutes) for step in reversed(steps)]


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
		device = _device("kids_ipad", hostname="kids-ipad", last_ip="172.16.17.170")
		assert devices_by_ip([device], rows) == {"172.16.17.164": device}

	def test_falls_back_to_last_seen_ip(self):
		rows = [_row(164, status=STATUS_FREE)]
		device = _device("kids_ipad", hostname="kids-ipad", last_ip="172.16.17.164")
		assert devices_by_ip([device], rows) == {"172.16.17.164": device}

	def test_device_with_no_address_is_not_placed(self):
		assert devices_by_ip([_device("ghost")], [_row(10)]) == {}

	def test_quarantined_device_shows_quarantined(self):
		rows = [_row(164, hostname="kids-ipad")]
		cell = map_cells(rows, [_device("kids_ipad", hostname="kids-ipad", quarantined=True)])[0]
		assert cell["status"] == DISPLAY_QUARANTINED
		assert cell["status_label"] == "Quarantined"
		assert cell["device"] == "kids_ipad"
		assert cell["quarantined"] is True

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
			"quarantined": False,
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


class TestQuarantineRows:
	def test_quarantined_devices_come_first(self):
		rows = quarantine_rows(
			[_device("alpha"), _device("zulu", quarantined=True), _device("bravo")]
		)
		assert [row["name"] for row in rows] == ["zulu", "alpha", "bravo"]

	def test_meta_shows_group_and_since_only_while_quarantined(self):
		since = "2026-11-07T00:30:00+00:00"
		quarantined, free = quarantine_rows(
			[
				_device("kids_ipad", group="kids", quarantined=True, since=since),
				_device("tv", group="kids", since=since),
			]
		)
		assert quarantined["meta"] == "Group: kids · since 11/06 19:30"
		assert free["meta"] == "Group: kids"


class TestActivity:
	def test_one_line_per_action_counting_only_enforcement_steps(self):
		lines = activity(_action("kids_ipad", "quarantine", 0), limit=5)
		assert [{key: line[key] for key in ("when", "text", "tone")} for line in lines] == [
			{"when": "12:00", "text": "kids_ipad quarantined — 4/4 steps ok", "tone": "quarantine"}
		]

	def test_failed_fingerprint_does_not_count_as_a_problem(self):
		lines = activity(_action("tv", "release", 0, failed_steps={"nmap_refresh"}), limit=5)
		assert lines[0]["text"] == "tv released — 4/4 steps ok"
		assert lines[0]["tone"] == "release"

	def test_failed_enforcement_step_is_flagged(self):
		lines = activity(_action("tv", "quarantine", 0, failed_steps={"arp"}), limit=5)
		assert lines[0]["text"] == "tv quarantined with problems — 3/4 steps ok"
		assert lines[0]["tone"] == "warn"

	def test_identity_drift_skip_is_its_own_message(self):
		lines = activity(
			[_log("tv", "quarantine", "identity_resolution", succeeded=False)], limit=5
		)
		assert lines[0]["text"] == "tv skipped: its address changed mid-request"

	def test_separate_actions_stay_separate(self):
		later = ACTION_GROUPING_SECONDS // 60 + 1
		entries = _action("tv", "release", later) + _action("tv", "quarantine", 0)
		assert [line["text"] for line in activity(entries, limit=5)] == [
			"tv released — 4/4 steps ok",
			"tv quarantined — 4/4 steps ok",
		]

	def test_limit_caps_the_number_of_lines(self):
		entries = (
			_action("a", "quarantine", 30)
			+ _action("b", "quarantine", 20)
			+ _action("c", "quarantine", 10)
		)
		assert len(activity(entries, limit=2)) == 2

	def test_empty_log(self):
		assert activity([], limit=5) == []


class TestActivityMerge:
	def test_admin_changes_interleave_by_time(self):
		admin = [
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
		lines = activity(_action("kids_ipad", "quarantine", 0), limit=5, admin_entries=admin)
		assert [line["text"] for line in lines] == [
			"tom turned DHCP on",
			"kids_ipad quarantined — 4/4 steps ok",
			"pat saved the DHCP settings",
		]
		assert lines[0]["tone"] == "admin"

	def test_limit_applies_across_both(self):
		admin = [
			{
				"occurred_at": f"2026-11-07T18:0{n}:00+00:00",
				"username": "tom",
				"summary": f"change {n}",
			}
			for n in range(3)
		]
		lines = activity(_action("kids_ipad", "quarantine", 0), limit=2, admin_entries=admin)
		assert [line["text"] for line in lines] == ["tom change 2", "tom change 1"]
