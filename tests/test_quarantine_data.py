"""
test_quarantine_data.py — Tests for web/quarantine_data.py, the Quarantine
screen's pure shaping and rules.
"""

import pytest

from web import quarantine_data as qd


def _device(name: str, **fields) -> dict:
	device = {
		"friendly_name": name,
		"hostname": f"{name}-host",
		"group_tag": "",
		"last_seen_mac_address": "",
		"last_seen_ip_address": "",
		"last_seen_at": "",
		"last_quarantined_at": "",
		"is_quarantined": False,
		"notes": "",
	}
	device.update(fields)
	return device


def _step(name: str, kea=True, arp=True, pihole=True, fingerprint=True, skipped=False) -> dict:
	return {
		"friendly_name": name,
		"kea_step_succeeded": kea,
		"arp_step_succeeded": arp,
		"pihole_step_succeeded": pihole,
		"fingerprint_refreshed": fingerprint,
		"skipped_due_to_identity_drift": skipped,
	}


class TestDeviceRows:
	def test_alphabetical_ignoring_case(self):
		rows = qd.device_rows([_device("zed"), _device("Alpha"), _device("beta")])
		assert [row["name"] for row in rows] == ["Alpha", "beta", "zed"]

	def test_fields_and_local_times(self):
		row = qd.device_rows(
			[
				_device(
					"kids_ipad",
					group_tag="kids",
					last_seen_mac_address="aa:bb:cc:dd:ee:ff",
					last_seen_ip_address="10.0.0.5",
					last_seen_at="2026-08-22T12:00:00+00:00",
					is_quarantined=1,
				)
			]
		)[0]
		assert row["group"] == "kids"
		assert row["mac"] == "aa:bb:cc:dd:ee:ff"
		assert row["quarantined"] is True
		assert row["last_seen"]
		assert row["last_quarantined"] == ""

	def test_missing_values_are_blank(self):
		row = qd.device_rows([{"friendly_name": "bare", "hostname": None}])[0]
		assert row["hostname"] == "" and row["ip"] == "" and row["quarantined"] is False


class TestGroupTags:
	def test_counts_devices_and_quarantined(self):
		devices = [
			_device("a", group_tag="kids", is_quarantined=True),
			_device("b", group_tag="kids"),
			_device("c", group_tag="Garage"),
			_device("d"),
		]
		assert qd.group_tags(devices) == [
			{"tag": "Garage", "devices": 1, "quarantined": 0},
			{"tag": "kids", "devices": 2, "quarantined": 1},
		]

	def test_no_groups(self):
		assert qd.group_tags([_device("a")]) == []


class TestLogRows:
	def test_shape(self):
		entry = {
			"friendly_name": "kids_ipad",
			"action": "quarantine",
			"step": "kea",
			"succeeded": 0,
			"attempt_count": 3,
			"occurred_at": "2026-08-22T12:00:00+00:00",
			"detail": None,
		}
		row = qd.log_rows([entry])[0]
		assert row["ok"] is False
		assert row["attempts"] == 3
		assert row["detail"] == ""
		assert row["when"]

	def test_unreadable_time_shown_raw(self):
		entry = {
			"friendly_name": "x",
			"action": "release",
			"step": "arp",
			"succeeded": 1,
			"attempt_count": 1,
			"occurred_at": "not-a-time",
		}
		assert qd.log_rows([entry])[0]["when"] == "not-a-time"


class TestStepOutcomes:
	def test_per_step_results(self):
		outcome = qd.step_outcomes({"step_results": [_step("a", arp=False)]})[0]
		assert outcome["skipped"] is False
		assert dict(outcome["steps"]) == {
			"Kea": True,
			"ARP": False,
			"Pi-hole": True,
			"Fingerprint": True,
		}

	def test_skipped_device_has_no_steps(self):
		outcome = qd.step_outcomes({"step_results": [_step("a", skipped=True)]})[0]
		assert outcome == {"name": "a", "skipped": True, "steps": []}

	def test_empty_result(self):
		assert qd.step_outcomes({}) == []


class TestEnforcementSucceeded:
	def test_all_layers(self):
		assert qd.enforcement_succeeded({"step_results": [_step("a"), _step("b")]})

	def test_failed_fingerprint_does_not_count(self):
		assert qd.enforcement_succeeded({"step_results": [_step("a", fingerprint=False)]})

	@pytest.mark.parametrize(
		"failure", [{"kea": False}, {"arp": False}, {"pihole": False}, {"skipped": True}]
	)
	def test_any_enforcement_failure(self, failure):
		assert not qd.enforcement_succeeded({"step_results": [_step("a"), _step("b", **failure)]})

	def test_nothing_resolved(self):
		assert not qd.enforcement_succeeded({"step_results": []})


class TestValidateDevice:
	def test_valid_new(self):
		assert qd.validate_device("tommy_laptop", "tommy-pc", True, False) is None

	def test_valid_edit_ignores_name_rules(self):
		assert qd.validate_device("old name", "host", False, True) is None

	@pytest.mark.parametrize(
		("name", "fragment"),
		[
			("", "required"),
			("x" * 65, "64 characters"),
			("has space", "letters, digits"),
			("_leading", "letters, digits"),
			("a/b", "letters, digits"),
			("New", "reserved"),
		],
	)
	def test_bad_names(self, name, fragment):
		assert fragment in qd.validate_device(name, "host", True, False)

	def test_duplicate(self):
		assert "already registered" in qd.validate_device("taken", "host", True, True)

	def test_hostname_required(self):
		assert "hostname" in qd.validate_device("ok", "", True, False)
		assert "hostname" in qd.validate_device("ok", "", False, True)


class TestReleaseAttempts:
	def test_counts_and_force_threshold(self):
		attempts = qd.ReleaseAttempts()
		assert attempts.count("a") == 0
		for expected in range(1, qd.MAX_RELEASE_ATTEMPTS_BEFORE_FORCE + 1):
			assert not attempts.may_force("a")
			assert attempts.record_failure("a") == expected
		assert attempts.may_force("a")
		assert not attempts.may_force("b")

	def test_clear(self):
		attempts = qd.ReleaseAttempts()
		attempts.record_failure("a")
		attempts.clear("a")
		attempts.clear("never-seen")
		assert attempts.count("a") == 0
