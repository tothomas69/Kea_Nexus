"""
test_reservations_data.py — Tests for web/reservations_data.py: the table
rows, the form rules, and the config edits that must keep fields KeaNexus
doesn't show (above all the quarantine service's DROP class).
"""

import pytest

from web import reservations_data as rd

_MAC_A = "aa:aa:aa:aa:aa:01"
_MAC_B = "bb:bb:bb:bb:bb:02"


def _config(*reservations):
	return {"subnet4": [{"subnet": "172.16.17.0/24", "reservations": list(reservations)}]}


def _lease(ip, mac, hostname=""):
	return {"ip-address": ip, "hw-address": mac, "hostname": hostname}


class TestRows:
	def test_order_fixed_by_address_then_name_only(self):
		config = _config(
			{"hw-address": "cc:cc:cc:cc:cc:03"},
			{"hw-address": _MAC_B, "ip-address": "172.16.17.100"},
			{"hw-address": _MAC_A, "ip-address": "172.16.17.9"},
		)
		assert [row["mac"] for row in rd.reservation_rows(config, [], {})] == [
			_MAC_A,
			_MAC_B,
			"cc:cc:cc:cc:cc:03",
		]

	def test_hostname_states(self):
		config = _config(
			{"hw-address": _MAC_A, "ip-address": "172.16.17.10"},
			{"hw-address": _MAC_B, "ip-address": "172.16.17.11", "hostname": "typed-label"},
			{"hw-address": "cc:cc:cc:cc:cc:03"},
			{"hw-address": "dd:dd:dd:dd:dd:04", "ip-address": "172.16.17.13"},
		)
		leases = [
			_lease("172.16.17.10", _MAC_A, "real-name"),
			_lease("172.16.17.11", _MAC_B, "typed-label"),
			_lease("172.16.17.120", "cc:cc:cc:cc:cc:03", ""),
		]
		states = {
			row["mac"]: (row["hostname_state"], row["hostname"])
			for row in rd.reservation_rows(config, leases, {})
		}
		assert states[_MAC_A] == ("", "real-name")
		assert states[_MAC_B] == (rd.HOSTNAME_MASKED, "")
		assert states["cc:cc:cc:cc:cc:03"] == (rd.HOSTNAME_NOT_REPORTED, "")
		assert states["dd:dd:dd:dd:dd:04"] == (rd.HOSTNAME_OFFLINE, "")

	def test_label_prefers_keanexus_then_legacy_kea_hostname(self):
		config = _config(
			{"hw-address": _MAC_A.upper(), "hostname": "legacy"},
			{"hw-address": _MAC_B, "hostname": "old"},
		)
		rows = {row["mac"]: row for row in rd.reservation_rows(config, [], {_MAC_B: "New label"})}
		assert rows[_MAC_A]["label"] == "legacy"
		assert rows[_MAC_B]["label"] == "New label"

	def test_quarantined_and_non_mac_reservations(self):
		config = _config(
			{"hw-address": _MAC_A, "client-classes": ["DROP"]},
			{"client-id": "01:02", "ip-address": "172.16.17.5"},
		)
		rows = rd.reservation_rows(config, [], {})
		assert rows[0]["editable"] is False and rows[0]["mac"] == ""
		assert rows[1]["quarantined"] is True

	def test_no_config(self):
		assert rd.reservation_rows(None, [], {}) == []


class TestValidate:
	CONFIG = _config({"hw-address": _MAC_A, "ip-address": "172.16.17.10"})

	def test_valid_add_and_name_only(self):
		assert rd.validate_reservation(_MAC_B, "172.16.17.11", "TV", self.CONFIG) is None
		assert rd.validate_reservation(_MAC_B, "", "TV", self.CONFIG) is None

	def test_editing_itself_is_not_a_duplicate(self):
		assert (
			rd.validate_reservation(_MAC_A, "172.16.17.10", "TV", self.CONFIG, original_mac=_MAC_A)
			is None
		)

	@pytest.mark.parametrize(
		("mac", "ip", "label", "fragment"),
		[
			("nope", "", "TV", "MAC"),
			(_MAC_B, "", "", "label"),
			(_MAC_B, "", "x" * 65, "64 characters"),
			(_MAC_A, "", "TV", "already has a reservation"),
			(_MAC_B, "not-an-ip", "TV", "IPv4"),
			(_MAC_B, "10.0.0.1", "TV", "outside the subnet"),
			(_MAC_B, "172.16.17.10", "TV", "already reserved"),
		],
	)
	def test_rejects(self, mac, ip, label, fragment):
		assert fragment in rd.validate_reservation(mac, ip, label, self.CONFIG)

	def test_no_config_means_no_subnet_check(self):
		assert rd.validate_reservation(_MAC_B, "10.0.0.1", "TV", None) is None


class TestConfigEdits:
	def test_add_leaves_the_original_untouched(self):
		original = _config()
		updated = rd.with_reservation_saved(original, "", _MAC_A, "172.16.17.10")
		assert updated["subnet4"][0]["reservations"] == [
			{"hw-address": _MAC_A, "ip-address": "172.16.17.10"}
		]
		assert original["subnet4"][0]["reservations"] == []

	def test_add_when_the_subnet_has_no_reservation_list(self):
		updated = rd.with_reservation_saved(
			{"subnet4": [{"subnet": "172.16.17.0/24"}]}, "", _MAC_A, ""
		)
		assert updated["subnet4"][0]["reservations"] == [{"hw-address": _MAC_A}]

	def test_edit_keeps_the_drop_class_and_other_fields(self):
		"""Regression: the Streamlit tab rebuilt the entry and dropped DROP."""
		original = _config(
			{
				"hw-address": _MAC_A,
				"ip-address": "172.16.17.10",
				"hostname": "label",
				"client-classes": ["DROP"],
				"option-data": [1],
			}
		)
		entry = rd.with_reservation_saved(original, _MAC_A, _MAC_B, "")["subnet4"][0][
			"reservations"
		][0]
		assert entry == {"hw-address": _MAC_B, "client-classes": ["DROP"], "option-data": [1]}

	def test_remove(self):
		original = _config({"hw-address": _MAC_A.upper()}, {"hw-address": _MAC_B})
		updated = rd.with_reservation_removed(original, _MAC_A)
		assert updated["subnet4"][0]["reservations"] == [{"hw-address": _MAC_B}]
		assert len(original["subnet4"][0]["reservations"]) == 2

	def test_find(self):
		config = _config({"hw-address": _MAC_A.upper()})
		assert rd.find_reservation(config, _MAC_A) is not None
		assert rd.find_reservation(config, _MAC_B) is None
