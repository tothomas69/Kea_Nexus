"""
test_db.py — Tests for db.py SQLite persistence layer.
All tests use the temp_db fixture (conftest.py) to avoid touching /app/data/.
"""

import pytest

import db


class TestInitDb:
	def test_creates_empty_table(self, temp_db):
		assert db.get_static_entries() == []

	def test_is_idempotent(self, temp_db):
		db.init_db()
		db.init_db()
		assert db.get_static_entries() == []


class TestUpsertAndGet:
	def test_insert_retrieves_all_fields(self, temp_db):
		db.upsert_static_entry(
			"192.168.1.1",
			hostname="router",
			mac_address="aa:bb:cc:dd:ee:ff",
			description="Main gateway",
			notes="Do not reassign",
		)
		entry = db.get_static_entry("192.168.1.1")
		assert entry is not None
		assert entry["ip_address"] == "192.168.1.1"
		assert entry["hostname"] == "router"
		assert entry["mac_address"] == "aa:bb:cc:dd:ee:ff"
		assert entry["description"] == "Main gateway"
		assert entry["notes"] == "Do not reassign"

	def test_update_overwrites_existing_entry(self, temp_db):
		db.upsert_static_entry("192.168.1.1", hostname="old-name")
		db.upsert_static_entry("192.168.1.1", hostname="new-name")
		entry = db.get_static_entry("192.168.1.1")
		assert entry["hostname"] == "new-name"

	def test_defaults_fill_empty_fields(self, temp_db):
		db.upsert_static_entry("10.0.0.1")
		entry = db.get_static_entry("10.0.0.1")
		assert entry["hostname"] == ""
		assert entry["mac_address"] == ""
		assert entry["description"] == ""
		assert entry["notes"] == ""

	def test_multiple_entries_stored_independently(self, temp_db):
		db.upsert_static_entry("10.0.0.1", hostname="host-a")
		db.upsert_static_entry("10.0.0.2", hostname="host-b")
		assert db.get_static_entry("10.0.0.1")["hostname"] == "host-a"
		assert db.get_static_entry("10.0.0.2")["hostname"] == "host-b"


class TestGetStaticEntry:
	def test_returns_none_for_unknown_ip(self, temp_db):
		assert db.get_static_entry("1.2.3.4") is None

	def test_returns_dict_for_known_ip(self, temp_db):
		db.upsert_static_entry("10.0.0.5")
		entry = db.get_static_entry("10.0.0.5")
		assert isinstance(entry, dict)


class TestGetStaticEntries:
	def test_returns_empty_list_on_fresh_db(self, temp_db):
		assert db.get_static_entries() == []

	def test_returns_all_entries(self, temp_db):
		db.upsert_static_entry("10.0.0.1")
		db.upsert_static_entry("10.0.0.2")
		entries = db.get_static_entries()
		assert len(entries) == 2

	def test_sorted_lexicographically_by_ip(self, temp_db):
		# Insert out of order; SQLite sorts TEXT lexicographically
		for ip in ["10.0.0.3", "10.0.0.1", "10.0.0.2"]:
			db.upsert_static_entry(ip)
		ips = [e["ip_address"] for e in db.get_static_entries()]
		assert ips == sorted(ips)


class TestDeleteStaticEntry:
	def test_removes_existing_entry(self, temp_db):
		db.upsert_static_entry("10.0.0.1")
		db.delete_static_entry("10.0.0.1")
		assert db.get_static_entry("10.0.0.1") is None

	def test_does_not_raise_for_unknown_ip(self, temp_db):
		db.delete_static_entry("1.2.3.4")  # must not raise

	def test_does_not_remove_other_entries(self, temp_db):
		db.upsert_static_entry("10.0.0.1")
		db.upsert_static_entry("10.0.0.2")
		db.delete_static_entry("10.0.0.1")
		assert db.get_static_entry("10.0.0.2") is not None


class TestReservationLabels:
	def test_insert_retrieves_label(self, temp_db):
		db.upsert_reservation_label("AA:BB:CC:DD:EE:FF", "kids-ipad")
		label = db.get_reservation_label("AA:BB:CC:DD:EE:FF")
		assert label is not None
		assert label["label"] == "kids-ipad"

	def test_mac_address_normalized_to_lowercase(self, temp_db):
		db.upsert_reservation_label("AA:BB:CC:DD:EE:FF", "kids-ipad")
		label = db.get_reservation_label("aa:bb:cc:dd:ee:ff")
		assert label["mac_address"] == "aa:bb:cc:dd:ee:ff"

	def test_update_overwrites_existing_label(self, temp_db):
		db.upsert_reservation_label("aa:bb:cc:dd:ee:ff", "old-label")
		db.upsert_reservation_label("aa:bb:cc:dd:ee:ff", "new-label")
		assert db.get_reservation_label("aa:bb:cc:dd:ee:ff")["label"] == "new-label"

	def test_returns_none_for_unknown_mac(self, temp_db):
		assert db.get_reservation_label("00:00:00:00:00:00") is None

	def test_empty_mac_address_raises(self, temp_db):
		with pytest.raises(AssertionError):
			db.upsert_reservation_label("", "some-label")

	def test_get_reservation_labels_returns_all_sorted(self, temp_db):
		db.upsert_reservation_label("bb:bb:bb:bb:bb:bb", "b-device")
		db.upsert_reservation_label("aa:aa:aa:aa:aa:aa", "a-device")
		macs = [row["mac_address"] for row in db.get_reservation_labels()]
		assert macs == sorted(macs)

	def test_delete_removes_existing_label(self, temp_db):
		db.upsert_reservation_label("aa:bb:cc:dd:ee:ff", "kids-ipad")
		db.delete_reservation_label("aa:bb:cc:dd:ee:ff")
		assert db.get_reservation_label("aa:bb:cc:dd:ee:ff") is None

	def test_delete_does_not_raise_for_unknown_mac(self, temp_db):
		db.delete_reservation_label("00:00:00:00:00:00")  # must not raise

	def test_delete_does_not_remove_other_labels(self, temp_db):
		db.upsert_reservation_label("aa:aa:aa:aa:aa:aa", "a-device")
		db.upsert_reservation_label("bb:bb:bb:bb:bb:bb", "b-device")
		db.delete_reservation_label("aa:aa:aa:aa:aa:aa")
		assert db.get_reservation_label("bb:bb:bb:bb:bb:bb") is not None


class TestUpsertAndGetDevice:
	def test_insert_retrieves_all_fields(self, temp_db):
		db.upsert_device(
			"tommy_laptop",
			hostname="tommy-kubuntu",
			group_tag="kids",
			os_fingerprint="Linux 6.x",
			last_seen_mac_address="aa:bb:cc:dd:ee:ff",
			last_seen_ip_address="172.16.17.50",
			last_quarantined_at="2026-08-16T00:00:00+00:00",
			notes="school laptop",
		)
		device = db.get_device("tommy_laptop")
		assert device is not None
		assert device["friendly_name"] == "tommy_laptop"
		assert device["hostname"] == "tommy-kubuntu"
		assert device["group_tag"] == "kids"
		assert device["os_fingerprint"] == "Linux 6.x"
		assert device["last_seen_mac_address"] == "aa:bb:cc:dd:ee:ff"
		assert device["last_seen_ip_address"] == "172.16.17.50"
		assert device["last_quarantined_at"] == "2026-08-16T00:00:00+00:00"
		assert device["notes"] == "school laptop"

	def test_update_overwrites_existing_entry(self, temp_db):
		db.upsert_device("tommy_laptop", hostname="old-host")
		db.upsert_device("tommy_laptop", hostname="new-host")
		device = db.get_device("tommy_laptop")
		assert device["hostname"] == "new-host"

	def test_defaults_fill_optional_fields(self, temp_db):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		device = db.get_device("tommy_laptop")
		assert device["group_tag"] == ""
		assert device["os_fingerprint"] == ""
		assert device["last_seen_mac_address"] == ""
		assert device["last_seen_ip_address"] == ""
		assert device["last_quarantined_at"] == ""
		assert device["notes"] == ""

	def test_multiple_devices_stored_independently(self, temp_db):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		db.upsert_device("lilly_laptop", hostname="lilly-kubuntu")
		assert db.get_device("tommy_laptop")["hostname"] == "tommy-kubuntu"
		assert db.get_device("lilly_laptop")["hostname"] == "lilly-kubuntu"

	def test_empty_friendly_name_raises(self, temp_db):
		with pytest.raises(AssertionError):
			db.upsert_device("", hostname="tommy-kubuntu")

	def test_empty_hostname_raises(self, temp_db):
		with pytest.raises(AssertionError):
			db.upsert_device("tommy_laptop", hostname="")


class TestGetDevice:
	def test_returns_none_for_unknown_name(self, temp_db):
		assert db.get_device("nobody") is None

	def test_returns_dict_for_known_name(self, temp_db):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		assert isinstance(db.get_device("tommy_laptop"), dict)


class TestGetDevices:
	def test_returns_empty_list_on_fresh_db(self, temp_db):
		assert db.get_devices() == []

	def test_returns_all_devices(self, temp_db):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		db.upsert_device("lilly_laptop", hostname="lilly-kubuntu")
		assert len(db.get_devices()) == 2

	def test_sorted_alphabetically_by_friendly_name(self, temp_db):
		for name in ["charlie_pc", "alpha_pc", "bravo_pc"]:
			db.upsert_device(name, hostname=name)
		names = [d["friendly_name"] for d in db.get_devices()]
		assert names == sorted(names)


class TestDeleteDevice:
	def test_removes_existing_device(self, temp_db):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		db.delete_device("tommy_laptop")
		assert db.get_device("tommy_laptop") is None

	def test_does_not_raise_for_unknown_name(self, temp_db):
		db.delete_device("nobody")  # must not raise

	def test_does_not_remove_other_devices(self, temp_db):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		db.upsert_device("lilly_laptop", hostname="lilly-kubuntu")
		db.delete_device("tommy_laptop")
		assert db.get_device("lilly_laptop") is not None


class TestInsertQuarantineLogEntry:
	def test_insert_retrieves_all_fields(self, temp_db):
		db.insert_quarantine_log_entry(
			"tommy_laptop", "quarantine", "arp", succeeded=True, attempt_count=1, detail="ok"
		)
		entries = db.get_quarantine_log("tommy_laptop")
		assert len(entries) == 1
		entry = entries[0]
		assert entry["friendly_name"] == "tommy_laptop"
		assert entry["action"] == "quarantine"
		assert entry["step"] == "arp"
		assert entry["succeeded"] == 1
		assert entry["attempt_count"] == 1
		assert entry["detail"] == "ok"
		assert entry["occurred_at"] != ""

	def test_succeeded_false_stores_zero(self, temp_db):
		db.insert_quarantine_log_entry(
			"tommy_laptop", "quarantine", "pihole", succeeded=False, attempt_count=3
		)
		assert db.get_quarantine_log("tommy_laptop")[0]["succeeded"] == 0

	def test_invalid_action_raises(self, temp_db):
		with pytest.raises(AssertionError):
			db.insert_quarantine_log_entry(
				"tommy_laptop", "not_an_action", "arp", succeeded=True, attempt_count=1
			)

	def test_zero_attempt_count_raises(self, temp_db):
		with pytest.raises(AssertionError):
			db.insert_quarantine_log_entry(
				"tommy_laptop", "quarantine", "arp", succeeded=True, attempt_count=0
			)


class TestGetQuarantineLog:
	def test_returns_empty_list_on_fresh_db(self, temp_db):
		assert db.get_quarantine_log() == []

	def test_filters_by_friendly_name(self, temp_db):
		db.insert_quarantine_log_entry(
			"tommy_laptop", "quarantine", "arp", succeeded=True, attempt_count=1
		)
		db.insert_quarantine_log_entry(
			"lilly_laptop", "quarantine", "arp", succeeded=True, attempt_count=1
		)
		entries = db.get_quarantine_log("tommy_laptop")
		assert len(entries) == 1
		assert entries[0]["friendly_name"] == "tommy_laptop"

	def test_returns_most_recent_first(self, temp_db):
		db.insert_quarantine_log_entry(
			"tommy_laptop", "quarantine", "arp", succeeded=True, attempt_count=1
		)
		db.insert_quarantine_log_entry(
			"tommy_laptop", "quarantine", "pihole", succeeded=True, attempt_count=1
		)
		entries = db.get_quarantine_log("tommy_laptop")
		assert entries[0]["step"] == "pihole"
		assert entries[1]["step"] == "arp"

	def test_respects_limit(self, temp_db):
		for step in ["arp", "pihole", "kea"]:
			db.insert_quarantine_log_entry(
				"tommy_laptop", "quarantine", step, succeeded=True, attempt_count=1
			)
		assert len(db.get_quarantine_log("tommy_laptop", limit=2)) == 2

	def test_zero_limit_raises(self, temp_db):
		with pytest.raises(AssertionError):
			db.get_quarantine_log(limit=0)
