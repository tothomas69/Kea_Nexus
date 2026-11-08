"""
test_address_map.py — Tests for address_map.py, the classifier behind both the
IPAM tab and the web UI's Address Map.

The scenario: a 172.16.17.0/24 with the pool at .100–.199 and the gateway at .1.
"""

import pytest

from address_map import (
	STATUS_BROADCAST,
	STATUS_DECLINED,
	STATUS_FREE,
	STATUS_GATEWAY,
	STATUS_LEASED,
	STATUS_NETWORK,
	STATUS_RESERVED,
	STATUS_SCOPE,
	STATUS_STATIC,
	classify_addresses,
	gateway_ip,
	last_octet,
	subnet_prefix,
	subnet_reservations,
)

_NOW = 1_700_000_000
_HOUR_SECONDS = 3600
_POOL_START = 100
_POOL_END = 199


def _classify(
	leases=(),
	reservations=(),
	static_entries=(),
	labels=None,
	override_ips=None,
	override_macs=None,
):
	rows = classify_addresses(
		"172.16.17",
		_POOL_START,
		_POOL_END,
		"172.16.17.1",
		list(leases),
		list(reservations),
		list(static_entries),
		labels or {},
		override_ips or set(),
		override_macs or set(),
		now_epoch=_NOW,
	)
	return {row.last_octet: row for row in rows}


def _lease(
	octet, mac="aa:bb:cc:00:00:01", hostname="laptop", state=0, expires_in=2 * _HOUR_SECONDS
):
	return {
		"ip-address": f"172.16.17.{octet}",
		"hw-address": mac,
		"hostname": hostname,
		"state": state,
		"cltt": _NOW,
		"valid-lft": expires_in,
	}


class TestConfigHelpers:
	_CONFIG = {
		"subnet4": [
			{
				"subnet": "10.20.30.0/24",
				"option-data": [
					{"name": "domain-name-servers", "data": "10.20.30.2"},
					{"name": "routers", "data": "10.20.30.1"},
				],
				"reservations": [{"hw-address": "aa:bb:cc:dd:ee:ff"}],
			}
		]
	}

	def test_subnet_prefix(self):
		assert subnet_prefix(self._CONFIG) == "10.20.30"

	def test_subnet_prefix_defaults_without_config(self):
		assert subnet_prefix(None) == "172.16.17"

	def test_gateway_ip(self):
		assert gateway_ip(self._CONFIG) == "10.20.30.1"

	def test_gateway_ip_empty_when_no_routers_option(self):
		assert gateway_ip({"subnet4": [{"option-data": []}]}) == ""

	def test_subnet_reservations(self):
		assert subnet_reservations(self._CONFIG) == [{"hw-address": "aa:bb:cc:dd:ee:ff"}]
		assert subnet_reservations(None) == []

	def test_last_octet(self):
		assert last_octet("172.16.17.42") == 42


class TestClassifyAddresses:
	def test_returns_every_address_in_the_subnet(self):
		rows = _classify()
		assert len(rows) == 256
		assert rows[0].ip == "172.16.17.0"

	def test_network_broadcast_and_gateway(self):
		rows = _classify()
		assert rows[0].status == STATUS_NETWORK
		assert rows[255].status == STATUS_BROADCAST
		assert rows[1].status == STATUS_GATEWAY
		assert rows[1].label == "gateway"
		assert rows[1].editable is True

	def test_untouched_addresses_split_on_the_pool(self):
		rows = _classify()
		assert rows[150].status == STATUS_SCOPE
		assert rows[150].editable is False
		assert rows[50].status == STATUS_FREE
		assert rows[50].editable is True

	def test_dynamic_lease_carries_hostname_mac_and_time_left(self):
		row = _classify(leases=[_lease(120)])[120]
		assert row.status == STATUS_LEASED
		assert row.hostname == "laptop"
		assert row.mac == "aa:bb:cc:00:00:01"
		assert row.info == "2h 0m"
		assert row.label == ""

	def test_declined_lease_wins_over_its_reservation(self):
		reservation = {"ip-address": "172.16.17.120", "hw-address": "aa:bb:cc:00:00:01"}
		row = _classify(leases=[_lease(120, state=1)], reservations=[reservation])[120]
		assert row.status == STATUS_DECLINED

	def test_leased_reservation_shows_as_reserved_with_its_stored_label(self):
		reservation = {"ip-address": "172.16.17.120", "hw-address": "AA:BB:CC:00:00:01"}
		labels = {"aa:bb:cc:00:00:01": "Tom's MacBook"}
		row = _classify(leases=[_lease(120)], reservations=[reservation], labels=labels)[120]
		assert row.status == STATUS_RESERVED
		assert row.label == "Tom's MacBook"
		assert row.hostname == "laptop"

	def test_legacy_kea_hostname_is_the_label_fallback(self):
		reservation = {
			"ip-address": "172.16.17.130",
			"hw-address": "aa:bb:cc:00:00:02",
			"hostname": "old-label",
		}
		row = _classify(reservations=[reservation])[130]
		assert row.label == "old-label"

	def test_reservation_without_a_lease_is_offline(self):
		reservation = {"ip-address": "172.16.17.130", "hw-address": "aa:bb:cc:00:00:02"}
		row = _classify(reservations=[reservation], labels={"aa:bb:cc:00:00:02": "Printer"})[130]
		assert row.status == STATUS_RESERVED
		assert row.info == "offline"
		assert row.label == "Printer"
		assert row.hostname == ""

	def test_masked_hostname_is_never_shown_as_the_real_one(self):
		reservation = {
			"ip-address": "172.16.17.120",
			"hw-address": "aa:bb:cc:00:00:01",
			"hostname": "typed-by-admin",
		}
		lease = _lease(120, hostname="typed-by-admin")
		row = _classify(leases=[lease], reservations=[reservation], override_ips={"172.16.17.120"})[
			120
		]
		assert row.hostname == ""
		assert row.label == "typed-by-admin"

	def test_static_entry_is_an_editable_label_with_notes(self):
		entry = {
			"ip_address": "172.16.17.10",
			"hostname": "nas",
			"mac_address": "11:22:33:44:55:66",
			"description": "TerraMaster",
			"notes": "rack 1",
		}
		row = _classify(static_entries=[entry])[10]
		assert row.status == STATUS_STATIC
		assert row.label == "nas"
		assert row.hostname == ""
		assert row.notes == "TerraMaster — rack 1"
		assert row.editable is True

	def test_lease_without_valid_lifetime_uses_keas_default(self):
		lease = _lease(120)
		del lease["valid-lft"]
		assert _classify(leases=[lease])[120].info == "24h 0m"

	def test_rejects_a_malformed_prefix(self):
		with pytest.raises(AssertionError):
			classify_addresses("172.16", 1, 2, "", [], [], [], {}, set(), set(), now_epoch=_NOW)
