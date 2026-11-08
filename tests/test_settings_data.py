"""test_settings_data.py — Tests for web/settings_data.py."""

import pytest

from web import settings_data as sd


def _config(**subnet):
	base = {
		"subnet": "172.16.17.0/24",
		"pools": [{"pool": "172.16.17.100-172.16.17.199"}],
		"valid-lifetime": 86400,
		"option-data": [
			{"name": "routers", "data": "172.16.17.1"},
			{"name": "domain-name-servers", "data": "172.16.17.212"},
			{"name": "time-offset", "data": "0"},
		],
		"reservations": [{"hw-address": "aa:aa:aa:aa:aa:01", "client-classes": ["DROP"]}],
	}
	base.update(subnet)
	return {"subnet4": [base]}


def _values(**changes):
	values = sd.settings_values(_config())
	values.update(changes)
	return values


class TestRead:
	def test_values(self):
		assert sd.settings_values(_config()) == {
			"subnet": "172.16.17.0/24",
			"pool_start": "172.16.17.100",
			"pool_end": "172.16.17.199",
			"lease_hours": "24",
			"gateway": "172.16.17.1",
			"dns": "172.16.17.212",
			"domain": "",
			"ntp": "",
		}

	def test_lifetime_fallbacks(self):
		assert (
			sd.settings_values(_config(**{"valid-lifetime": None, "min-valid-lifetime": 7200}))[
				"lease_hours"
			]
			== "2"
		)
		assert sd.settings_values(None)["lease_hours"] == "24"


class TestValidate:
	def test_valid(self):
		assert sd.validate_settings(_values(ntp="1.1.1.1, 8.8.8.8")) == []

	@pytest.mark.parametrize(
		("changes", "fragment"),
		[
			({"pool_start": "nope"}, "Pool start"),
			({"pool_end": "10.0.0.5"}, "outside the subnet"),
			({"pool_start": "172.16.17.200"}, "start at or before"),
			({"lease_hours": "0"}, "Lease length must be"),
			({"lease_hours": "soon"}, "whole number"),
			({"gateway": "10.0.0.1"}, "Gateway"),
			({"dns": "1.1.1.1, nope"}, "DNS servers"),
			({"ntp": "x"}, "NTP servers"),
		],
	)
	def test_rejects(self, changes, fragment):
		assert any(fragment in error for error in sd.validate_settings(_values(**changes)))

	def test_no_subnet_skips_range_checks(self):
		assert (
			sd.validate_settings(_values(subnet="", pool_end="10.0.0.5", pool_start="10.0.0.1"))
			== []
		)


class TestApply:
	def test_applies_and_keeps_everything_else(self):
		original = _config()
		updated = sd.with_settings_applied(
			original,
			_values(
				pool_start="172.16.17.110", lease_hours="12", dns=" 1.1.1.1 ,8.8.8.8, ", gateway=""
			),
		)
		subnet = updated["subnet4"][0]
		assert subnet["pools"][0]["pool"] == "172.16.17.110-172.16.17.199"
		assert (
			subnet["valid-lifetime"]
			== subnet["min-valid-lifetime"]
			== subnet["max-valid-lifetime"]
			== 43200
		)
		assert subnet["renew-timer"] == 21600 and subnet["rebind-timer"] == 37800
		assert {"name": "time-offset", "data": "0"} in subnet["option-data"]
		assert {"name": "domain-name-servers", "data": "1.1.1.1, 8.8.8.8"} in subnet["option-data"]
		assert all(option["name"] != "routers" for option in subnet["option-data"])
		assert subnet["reservations"] == [
			{"hw-address": "aa:aa:aa:aa:aa:01", "client-classes": ["DROP"]}
		]
		assert original["subnet4"][0]["valid-lifetime"] == 86400

	def test_no_pools_or_options_yet(self):
		config = {"subnet4": [{"subnet": "172.16.17.0/24", "pools": [], "valid-lft": 5}]}
		subnet = sd.with_settings_applied(config, _values())["subnet4"][0]
		assert subnet["pools"] == [{"pool": "172.16.17.100-172.16.17.199"}]
		assert "valid-lft" not in subnet
