"""
test_quarantine_kea_deny.py — Tests for quarantine_service/kea_deny.py.
"""

from quarantine_service.kea_deny import (
	apply_drop_class,
	deny_via_kea,
	remove_drop_class,
	undo_deny_via_kea,
)


class TestApplyDropClass:
	def test_creates_reservation_when_none_exists(self):
		config = {"subnet4": [{"reservations": []}]}
		apply_drop_class(config, "aa:bb:cc:dd:ee:ff")
		reservations = config["subnet4"][0]["reservations"]
		assert len(reservations) == 1
		assert reservations[0]["hw-address"] == "aa:bb:cc:dd:ee:ff"
		assert reservations[0]["client-classes"] == ["DROP"]

	def test_adds_drop_to_existing_reservation_without_classes(self):
		config = {
			"subnet4": [
				{
					"reservations": [
						{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}
					]
				}
			]
		}
		apply_drop_class(config, "aa:bb:cc:dd:ee:ff")
		reservation = config["subnet4"][0]["reservations"][0]
		assert reservation["client-classes"] == ["DROP"]
		assert reservation["ip-address"] == "172.16.17.50"  # untouched

	def test_appends_drop_alongside_existing_classes(self):
		config = {
			"subnet4": [
				{
					"reservations": [
						{"hw-address": "aa:bb:cc:dd:ee:ff", "client-classes": ["SOME_OTHER"]}
					]
				}
			]
		}
		apply_drop_class(config, "aa:bb:cc:dd:ee:ff")
		classes = config["subnet4"][0]["reservations"][0]["client-classes"]
		assert set(classes) == {"SOME_OTHER", "DROP"}

	def test_is_idempotent(self):
		config = {"subnet4": [{"reservations": []}]}
		apply_drop_class(config, "aa:bb:cc:dd:ee:ff")
		apply_drop_class(config, "aa:bb:cc:dd:ee:ff")
		reservations = config["subnet4"][0]["reservations"]
		assert len(reservations) == 1
		assert reservations[0]["client-classes"] == ["DROP"]

	def test_matches_mac_case_insensitively(self):
		config = {"subnet4": [{"reservations": [{"hw-address": "AA:BB:CC:DD:EE:FF"}]}]}
		apply_drop_class(config, "aa:bb:cc:dd:ee:ff")
		reservations = config["subnet4"][0]["reservations"]
		assert len(reservations) == 1  # matched existing, didn't create a second
		assert reservations[0]["client-classes"] == ["DROP"]

	def test_does_not_affect_other_reservations(self):
		config = {
			"subnet4": [
				{
					"reservations": [
						{"hw-address": "11:11:11:11:11:11", "ip-address": "172.16.17.10"}
					]
				}
			]
		}
		apply_drop_class(config, "aa:bb:cc:dd:ee:ff")
		reservations = config["subnet4"][0]["reservations"]
		assert len(reservations) == 2
		untouched = next(r for r in reservations if r["hw-address"] == "11:11:11:11:11:11")
		assert "client-classes" not in untouched


class TestRemoveDropClass:
	def test_deletes_reservation_created_solely_for_drop(self):
		config = {
			"subnet4": [
				{"reservations": [{"hw-address": "aa:bb:cc:dd:ee:ff", "client-classes": ["DROP"]}]}
			]
		}
		remove_drop_class(config, "aa:bb:cc:dd:ee:ff")
		assert config["subnet4"][0]["reservations"] == []

	def test_keeps_reservation_with_other_fields_but_removes_drop(self):
		config = {
			"subnet4": [
				{
					"reservations": [
						{
							"hw-address": "aa:bb:cc:dd:ee:ff",
							"ip-address": "172.16.17.50",
							"client-classes": ["DROP"],
						}
					]
				}
			]
		}
		remove_drop_class(config, "aa:bb:cc:dd:ee:ff")
		reservations = config["subnet4"][0]["reservations"]
		assert len(reservations) == 1
		assert reservations[0]["ip-address"] == "172.16.17.50"
		assert "DROP" not in reservations[0].get("client-classes", [])

	def test_keeps_reservation_with_other_classes(self):
		config = {
			"subnet4": [
				{
					"reservations": [
						{
							"hw-address": "aa:bb:cc:dd:ee:ff",
							"client-classes": ["DROP", "SOME_OTHER"],
						}
					]
				}
			]
		}
		remove_drop_class(config, "aa:bb:cc:dd:ee:ff")
		reservations = config["subnet4"][0]["reservations"]
		assert len(reservations) == 1
		assert reservations[0]["client-classes"] == ["SOME_OTHER"]

	def test_no_op_when_mac_has_no_reservation(self):
		config = {"subnet4": [{"reservations": []}]}
		remove_drop_class(config, "aa:bb:cc:dd:ee:ff")  # must not raise
		assert config["subnet4"][0]["reservations"] == []

	def test_no_op_when_mac_not_in_drop(self):
		config = {
			"subnet4": [
				{
					"reservations": [
						{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}
					]
				}
			]
		}
		remove_drop_class(config, "aa:bb:cc:dd:ee:ff")
		reservations = config["subnet4"][0]["reservations"]
		assert len(reservations) == 1
		assert reservations[0]["ip-address"] == "172.16.17.50"


class TestDenyViaKea:
	def test_fetches_mutates_and_saves_config(self, stub_kea_client):
		kea = stub_kea_client(dhcp4_config={"subnet4": [{"reservations": []}]})
		deny_via_kea(kea, "aa:bb:cc:dd:ee:ff")
		assert len(kea.saved_configs) == 1
		saved_reservations = kea.saved_configs[0]["subnet4"][0]["reservations"]
		assert saved_reservations[0]["hw-address"] == "aa:bb:cc:dd:ee:ff"
		assert saved_reservations[0]["client-classes"] == ["DROP"]


class TestUndoDenyViaKea:
	def test_fetches_mutates_and_saves_config(self, stub_kea_client):
		kea = stub_kea_client(
			dhcp4_config={
				"subnet4": [
					{
						"reservations": [
							{"hw-address": "aa:bb:cc:dd:ee:ff", "client-classes": ["DROP"]}
						]
					}
				]
			}
		)
		undo_deny_via_kea(kea, "aa:bb:cc:dd:ee:ff")
		assert len(kea.saved_configs) == 1
		assert kea.saved_configs[0]["subnet4"][0]["reservations"] == []
