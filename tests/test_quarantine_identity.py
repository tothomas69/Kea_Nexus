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

	@pytest.mark.parametrize(
		"target",
		[
			"tommy_laptop",  # exactly as registered
			"Tommy_laptop",  # iOS keyboard capitalizes the first letter
			"TOMMY_LAPTOP",
			"Tommy_laptop.",  # dictation appends a trailing period
			"Tommy_laptop ",  # autocorrect leaves a trailing space
			" tommy_laptop",
			"Tommy Laptop",  # nobody says "underscore" out loud
			"Tommy Laptop.",
			"tommy-laptop",
		],
	)
	def test_matches_however_ios_mangles_the_name(self, temp_db, stub_kea_client, target):
		"""Every spelling a Siri Shortcut can plausibly hand this service for
		a device registered as "tommy_laptop" must resolve to that device."""
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		kea = stub_kea_client(
			{"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]}
		)
		resolved = resolve_target(kea, target, is_group=False)
		assert len(resolved) == 1
		assert resolved[0].friendly_name == "tommy_laptop"

	def test_exact_match_wins_over_a_normalized_one(self, temp_db, stub_kea_client):
		"""Two names that normalize identically must not shadow each other —
		an exactly-registered name always resolves to itself."""
		db.upsert_device("Tommy Laptop", hostname="other-host")
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		kea = stub_kea_client(
			{
				"tommy-kubuntu": [
					{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}
				],
				"other-host": [{"hw-address": "bb:bb:bb:bb:bb:bb", "ip-address": "172.16.17.51"}],
			}
		)
		assert resolve_target(kea, "tommy_laptop", is_group=False)[0].hostname == "tommy-kubuntu"
		assert resolve_target(kea, "Tommy Laptop", is_group=False)[0].hostname == "other-host"

	def test_target_with_nothing_left_after_normalizing_is_rejected(self, temp_db, stub_kea_client):
		"""Stripping punctuation must not turn junk into a wildcard that
		matches the first registered device."""
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		kea = stub_kea_client({})
		with pytest.raises(DeviceNotRegisteredError):
			resolve_target(kea, "...", is_group=False)


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

	@pytest.mark.parametrize("target", ["kids", "Kids", "KIDS", "Kids.", "Kids ", " kids"])
	def test_matches_group_tag_however_ios_mangles_it(self, temp_db, stub_kea_client, target):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", group_tag="kids")
		kea = stub_kea_client(
			{"tommy-kubuntu": [{"hw-address": "aa:aa:aa:aa:aa:aa", "ip-address": "172.16.17.50"}]}
		)
		resolved = resolve_target(kea, target, is_group=True)
		assert len(resolved) == 1
		assert resolved[0].friendly_name == "tommy_laptop"

	def test_punctuation_only_group_target_does_not_match_ungrouped_devices(
		self, temp_db, stub_kea_client
	):
		"""An unnamed group_tag normalizes to the empty string, so without a
		guard a target of "." would sweep up every ungrouped device."""
		db.upsert_device("ungrouped_pc", hostname="pc")
		kea = stub_kea_client({})
		with pytest.raises(DeviceNotRegisteredError):
			resolve_target(kea, ".", is_group=True)

	def test_one_device_missing_lease_is_skipped_not_fatal(self, temp_db, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", group_tag="kids")
		db.upsert_device("lilly_laptop", hostname="lilly-kubuntu", group_tag="kids")
		kea = stub_kea_client(
			{"tommy-kubuntu": [{"hw-address": "aa:aa:aa:aa:aa:aa", "ip-address": "172.16.17.50"}]}
			# lilly-kubuntu has no lease
		)
		resolved = resolve_target(kea, "kids", is_group=True)
		assert len(resolved) == 1
		assert resolved[0].friendly_name == "tommy_laptop"

	def test_all_devices_missing_lease_raises(self, temp_db, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", group_tag="kids")
		db.upsert_device("lilly_laptop", hostname="lilly-kubuntu", group_tag="kids")
		kea = stub_kea_client({})  # neither has a lease
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
