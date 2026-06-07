"""
test_db.py — Tests for db.py SQLite persistence layer.
All tests use the temp_db fixture (conftest.py) to avoid touching /app/data/.
"""

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
