"""
test_quarantine_identity.py — Tests for quarantine_service/identity.py.

Uses the temp_db fixture (conftest.py) for device_registry and the
stub_kea_client fixture (also conftest.py) rather than mocking httpx, since
resolve_target only calls get_leases_by_hostname.
"""

import pytest

import db
from quarantine_service.identity import (
	DeviceNotOnNetworkError,
	DeviceNotRegisteredError,
	ResolvedDevice,
	resolve_target,
	verify_identity_unchanged,
)


class TestResolveTargetSingleDevice:
	def test_resolves_registered_device_with_live_lease(self, temp_db, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		kea = stub_kea_client(
			{"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]}
		)
		resolved = resolve_target(kea, "tommy_laptop", is_group=False)
		assert len(resolved) == 1
		assert resolved[0].friendly_name == "tommy_laptop"
		assert resolved[0].hostname == "tommy-kubuntu"
		assert resolved[0].mac_address == "aa:bb:cc:dd:ee:ff"
		assert resolved[0].ip_address == "172.16.17.50"

	def test_refreshes_last_seen_fields_in_registry(self, temp_db, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		kea = stub_kea_client(
			{"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]}
		)
		resolve_target(kea, "tommy_laptop", is_group=False)
		device = db.get_device("tommy_laptop")
		assert device["last_seen_mac_address"] == "aa:bb:cc:dd:ee:ff"
		assert device["last_seen_ip_address"] == "172.16.17.50"

	def test_unregistered_target_raises(self, temp_db, stub_kea_client):
		kea = stub_kea_client({})
		with pytest.raises(DeviceNotRegisteredError):
			resolve_target(kea, "nobody", is_group=False)

	def test_registered_device_with_no_lease_raises(self, temp_db, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		kea = stub_kea_client({})  # no lease for tommy-kubuntu
		with pytest.raises(DeviceNotOnNetworkError):
			resolve_target(kea, "tommy_laptop", is_group=False)


class TestResolveTargetGroup:
	def test_resolves_all_devices_in_group(self, temp_db, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", group_tag="kids")
		db.upsert_device("lilly_laptop", hostname="lilly-kubuntu", group_tag="kids")
		kea = stub_kea_client(
			{
				"tommy-kubuntu": [
					{"hw-address": "aa:aa:aa:aa:aa:aa", "ip-address": "172.16.17.50"}
				],
				"lilly-kubuntu": [
					{"hw-address": "bb:bb:bb:bb:bb:bb", "ip-address": "172.16.17.51"}
				],
			}
		)
		resolved = resolve_target(kea, "kids", is_group=True)
		assert len(resolved) == 2
		assert {device.friendly_name for device in resolved} == {"tommy_laptop", "lilly_laptop"}

	def test_ignores_devices_outside_group(self, temp_db, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", group_tag="kids")
		db.upsert_device("test_pc", hostname="test-pc", group_tag="test_group_1")
		kea = stub_kea_client(
			{"tommy-kubuntu": [{"hw-address": "aa:aa:aa:aa:aa:aa", "ip-address": "172.16.17.50"}]}
		)
		resolved = resolve_target(kea, "kids", is_group=True)
		assert len(resolved) == 1
		assert resolved[0].friendly_name == "tommy_laptop"

	def test_unknown_group_raises(self, temp_db, stub_kea_client):
		kea = stub_kea_client({})
		with pytest.raises(DeviceNotRegisteredError):
			resolve_target(kea, "nonexistent_group", is_group=True)

	def test_one_device_missing_lease_raises_before_returning_partial_results(
		self, temp_db, stub_kea_client
	):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", group_tag="kids")
		db.upsert_device("lilly_laptop", hostname="lilly-kubuntu", group_tag="kids")
		kea = stub_kea_client(
			{"tommy-kubuntu": [{"hw-address": "aa:aa:aa:aa:aa:aa", "ip-address": "172.16.17.50"}]}
			# lilly-kubuntu has no lease
		)
		with pytest.raises(DeviceNotOnNetworkError):
			resolve_target(kea, "kids", is_group=True)


class TestVerifyIdentityUnchanged:
	def test_true_when_ip_and_mac_still_match(self, stub_kea_client):
		kea = stub_kea_client(
			{"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]}
		)
		device = ResolvedDevice(
			friendly_name="tommy_laptop",
			hostname="tommy-kubuntu",
			mac_address="aa:bb:cc:dd:ee:ff",
			ip_address="172.16.17.50",
		)
		assert verify_identity_unchanged(kea, device) is True

	def test_false_when_ip_has_changed(self, stub_kea_client):
		kea = stub_kea_client(
			{"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.99"}]}
		)
		device = ResolvedDevice(
			friendly_name="tommy_laptop",
			hostname="tommy-kubuntu",
			mac_address="aa:bb:cc:dd:ee:ff",
			ip_address="172.16.17.50",
		)
		assert verify_identity_unchanged(kea, device) is False

	def test_false_when_mac_has_changed(self, stub_kea_client):
		kea = stub_kea_client(
			{"tommy-kubuntu": [{"hw-address": "11:22:33:44:55:66", "ip-address": "172.16.17.50"}]}
		)
		device = ResolvedDevice(
			friendly_name="tommy_laptop",
			hostname="tommy-kubuntu",
			mac_address="aa:bb:cc:dd:ee:ff",
			ip_address="172.16.17.50",
		)
		assert verify_identity_unchanged(kea, device) is False

	def test_false_when_hostname_has_no_current_lease(self, stub_kea_client):
		kea = stub_kea_client({})  # hostname dropped off the network entirely
		device = ResolvedDevice(
			friendly_name="tommy_laptop",
			hostname="tommy-kubuntu",
			mac_address="aa:bb:cc:dd:ee:ff",
			ip_address="172.16.17.50",
		)
		assert verify_identity_unchanged(kea, device) is False
