"""
test_leases_data.py — Tests for web/leases_data.py, the Leases screen's
shaping and form rules.
"""

import pytest

from web import leases_data as ld

_NOW = 1_700_000_000
_CONFIG = {
	"subnet4": [
		{
			"subnet": "172.16.17.0/24",
			"reservations": [
				{
					"hw-address": "aa:aa:aa:aa:aa:01",
					"ip-address": "172.16.17.10",
					"hostname": "label",
				},
			],
		}
	]
}


def _lease(ip, mac="", hostname="", state=0, seconds_left=3600):
	return {
		"ip-address": ip,
		"hw-address": mac,
		"hostname": hostname,
		"state": state,
		"cltt": _NOW,
		"valid-lft": seconds_left,
	}


class TestLeaseRows:
	def test_sorted_numerically_by_address(self):
		rows = ld.lease_rows([_lease("172.16.17.100"), _lease("172.16.17.9")], None, None, _NOW)
		assert [row["ip"] for row in rows] == ["172.16.17.9", "172.16.17.100"]

	def test_fields(self):
		row = ld.lease_rows(
			[_lease("172.16.17.50", "bb:bb:bb:bb:bb:bb", "laptop")], None, None, _NOW
		)[0]
		assert row == {
			"ip": "172.16.17.50",
			"hostname": "laptop",
			"mac": "bb:bb:bb:bb:bb:bb",
			"expires": "1h 0m",
			"type": "dynamic",
			"declined": False,
			"expired": False,
			"online": None,
		}

	def test_masked_hostname_not_shown_and_type_from_reservation(self):
		row = ld.lease_rows(
			[_lease("172.16.17.10", "aa:aa:aa:aa:aa:01", "label")], _CONFIG, None, _NOW
		)[0]
		assert row["hostname"] == ""
		assert row["type"] == "fixed"

	def test_online_after_a_sweep(self):
		leases = [_lease("172.16.17.5"), _lease("172.16.17.6")]
		rows = ld.lease_rows(leases, None, frozenset({"172.16.17.5"}), _NOW)
		assert [row["online"] for row in rows] == [True, False]

	def test_declined(self):
		assert ld.lease_rows([_lease("172.16.17.5", state=1)], None, None, _NOW)[0]["declined"]

	def test_missing_lifetime_uses_kea_default(self):
		lease = {"ip-address": "172.16.17.5", "cltt": _NOW}
		assert ld.lease_rows([lease], None, None, _NOW)[0]["expires"] == "24h 0m"


class TestExpired:
	def test_row_marked_expired(self):
		rows = ld.lease_rows([_lease("172.16.17.5", seconds_left=0)], None, None, _NOW)
		assert rows[0]["expired"] and rows[0]["expires"] == "expired"

	def test_only_run_out_leases_sorted_by_address(self):
		leases = [
			_lease("172.16.17.100", seconds_left=-60),
			_lease("172.16.17.20", seconds_left=1),
			_lease("172.16.17.9", seconds_left=0),
			_lease("172.16.17.30", state=1, seconds_left=-1),
			{"hw-address": "no-address"},
		]
		assert ld.expired_lease_ips(leases, _NOW) == [
			"172.16.17.9",
			"172.16.17.30",
			"172.16.17.100",
		]

	def test_none_expired(self):
		assert ld.expired_lease_ips([_lease("172.16.17.5")], _NOW) == []


class TestFilterRows:
	ROWS = [
		{"ip": "172.16.17.5", "hostname": "Kids-iPad", "mac": "aa:bb:cc:00:00:01"},
		{"ip": "172.16.17.60", "hostname": "", "mac": "dd:ee:ff:00:00:02"},
	]

	@pytest.mark.parametrize(
		("query", "count"), [("", 2), ("  ", 2), ("ipad", 1), ("17.6", 1), ("DD:EE", 1), ("zzz", 0)]
	)
	def test_matches(self, query, count):
		assert len(ld.filter_rows(self.ROWS, query)) == count


class TestValidateLease:
	def test_valid(self):
		assert ld.validate_lease("172.16.17.40", "aa:bb:cc:dd:ee:ff", "3600", "172.16.17.0/24") == (
			None,
			3600,
		)

	def test_no_subnet_known(self):
		assert ld.validate_lease("10.0.0.1", "aa:bb:cc:dd:ee:ff", "3600", "")[0] is None

	@pytest.mark.parametrize(
		("ip", "mac", "duration", "fragment"),
		[
			("not-an-ip", "aa:bb:cc:dd:ee:ff", "3600", "IPv4"),
			("10.0.0.1", "aa:bb:cc:dd:ee:ff", "3600", "outside the subnet"),
			("172.16.17.40", "nope", "3600", "MAC"),
			("172.16.17.40", "aa:bb:cc:dd:ee:ff", "soon", "whole number"),
			("172.16.17.40", "aa:bb:cc:dd:ee:ff", "10", "between"),
			("172.16.17.40", "aa:bb:cc:dd:ee:ff", str(ld.MAX_LEASE_SECONDS + 1), "between"),
		],
	)
	def test_rejects(self, ip, mac, duration, fragment):
		error, seconds = ld.validate_lease(ip, mac, duration, "172.16.17.0/24")
		assert fragment in error
		assert seconds == 0

	def test_normalized_mac(self):
		assert ld.normalized_mac(" AA-BB-CC-DD-EE-FF ") == "aa:bb:cc:dd:ee:ff"
