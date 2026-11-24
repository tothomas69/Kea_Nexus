"""
test_devices_data.py — Shaping and rules for the Devices screen (web/devices_data.py).
"""

import pytest

from web import devices_data as dd

_LEASES = [
	{"ip-address": "172.16.17.164", "hw-address": "f0:18:98:3a:77:10", "hostname": "kids-ipad"},
	{"ip-address": "172.16.17.177", "hw-address": "a0:ce:c8:8f:42:e5", "hostname": "macbook-pro"},
	# A docked laptop's second lease under the same hostname.
	{"ip-address": "172.16.17.180", "hw-address": "a0:ce:c8:8f:42:e6", "hostname": "macbook-pro"},
]


def _device(name, hostname="kids-ipad", group="", notes=""):
	return {"friendly_name": name, "hostname": hostname, "group_tag": group, "notes": notes}


class TestDeviceRows:
	def test_alphabetical_ignoring_case(self):
		rows = dd.device_rows([_device("zulu"), _device("Alpha"), _device("bravo")], [], None)
		assert [row["name"] for row in rows] == ["Alpha", "bravo", "zulu"]

	def test_ip_and_mac_come_from_the_live_lease(self):
		row = dd.device_rows([_device("kids_ipad", group="kids", notes="n")], _LEASES, None)[0]
		assert row == {
			"name": "kids_ipad",
			"hostname": "kids-ipad",
			"group": "kids",
			"ip": "172.16.17.164",
			"mac": "f0:18:98:3a:77:10",
			"online": None,
			"notes": "n",
		}

	def test_first_lease_wins_for_a_shared_hostname(self):
		"""Same rule as the Address Map, so the two screens agree."""
		row = dd.device_rows([_device("tom_mac", "macbook-pro")], _LEASES, None)[0]
		assert row["ip"] == "172.16.17.177"

	def test_no_lease_means_blank_and_never_checked(self):
		row = dd.device_rows([_device("gone", "nobody")], _LEASES, {"172.16.17.164": ""})[0]
		assert (row["ip"], row["mac"], row["online"]) == ("", "", None)

	@pytest.mark.parametrize(
		("seen", "online"),
		[(None, None), ({}, False), ({"172.16.17.164": "f0:18:98:3a:77:10"}, True)],
	)
	def test_online_from_the_latest_sweep(self, seen, online):
		"""None before any sweep: "not checked" must never read as "off"."""
		assert dd.device_rows([_device("kids_ipad")], _LEASES, seen)[0]["online"] is online


class TestGroupTags:
	def test_counts_devices_per_tag(self):
		devices = [
			_device("a", group="kids"),
			_device("b", group="kids"),
			_device("c", group="Iot"),
		]
		assert dd.group_tags(devices) == [
			{"tag": "Iot", "devices": 1},
			{"tag": "kids", "devices": 2},
		]

	def test_untagged_devices_make_no_group(self):
		assert dd.group_tags([_device("a")]) == []


class TestValidateDevice:
	def test_valid_new(self):
		assert dd.validate_device("tommy_laptop", "tommy-pc", True, False) is None

	def test_valid_edit_ignores_name_rules(self):
		assert dd.validate_device("old name", "host", False, True) is None

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
		assert fragment in dd.validate_device(name, "host", True, False)

	def test_duplicate(self):
		assert "already registered" in dd.validate_device("taken", "host", True, True)

	def test_hostname_required(self):
		assert "hostname" in dd.validate_device("ok", "", True, False)
		assert "hostname" in dd.validate_device("ok", "", False, True)
