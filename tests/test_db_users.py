"""
test_db_users.py — Tests for the users table in db.py.
Hashes here are placeholder strings: db.py stores hashes, it never computes them.
"""

import sqlite3

import pytest

import db


class TestInsertAndGet:
	def test_starts_empty(self, temp_db):
		assert db.count_users() == 0
		assert db.list_users() == []

	def test_insert_then_get_returns_all_fields(self, temp_db):
		db.insert_user("tom", "hash-1", "admin")
		user = db.get_user("tom")
		assert user["username"] == "tom"
		assert user["password_hash"] == "hash-1"  # pragma: allowlist secret
		assert user["role"] == "admin"
		assert user["session_version"] == 1
		assert user["is_disabled"] == 0
		assert user["created_at"]
		assert user["last_login_at"] == ""

	def test_get_unknown_user_returns_none(self, temp_db):
		assert db.get_user("nobody") is None

	def test_username_lookup_ignores_case(self, temp_db):
		db.insert_user("Tom", "hash-1", "admin")
		assert db.get_user("tom")["username"] == "Tom"

	def test_duplicate_username_in_any_case_is_rejected(self, temp_db):
		db.insert_user("tom", "hash-1", "admin")
		with pytest.raises(sqlite3.IntegrityError):
			db.insert_user("TOM", "hash-2", "viewer")

	def test_unknown_role_is_rejected(self, temp_db):
		with pytest.raises(AssertionError):
			db.insert_user("tom", "hash-1", "superuser")


class TestListAndCount:
	def test_list_is_sorted_and_omits_password_hash(self, temp_db):
		db.insert_user("zoe", "hash-z", "viewer")
		db.insert_user("Amy", "hash-a", "admin")
		users = db.list_users()
		assert [user["username"] for user in users] == ["Amy", "zoe"]
		assert all("password_hash" not in user for user in users)

	def test_count_active_admins_ignores_viewers_and_disabled(self, temp_db):
		db.insert_user("tom", "hash-1", "admin")
		db.insert_user("amy", "hash-2", "admin")
		db.insert_user("zoe", "hash-3", "viewer")
		db.set_user_disabled("amy", True)
		assert db.count_users() == 3
		assert db.count_active_admins() == 1


class TestChangesEndSessions:
	def test_password_change_bumps_session_version(self, temp_db):
		db.insert_user("tom", "hash-1", "admin")
		assert db.update_password_hash("tom", "hash-2") is True
		user = db.get_user("tom")
		assert user["password_hash"] == "hash-2"  # pragma: allowlist secret
		assert user["session_version"] == 2

	def test_role_change_bumps_session_version(self, temp_db):
		db.insert_user("tom", "hash-1", "admin")
		assert db.set_user_role("tom", "viewer") is True
		user = db.get_user("tom")
		assert user["role"] == "viewer"
		assert user["session_version"] == 2

	def test_disable_bumps_session_version(self, temp_db):
		db.insert_user("tom", "hash-1", "admin")
		assert db.set_user_disabled("tom", True) is True
		user = db.get_user("tom")
		assert user["is_disabled"] == 1
		assert user["session_version"] == 2

	def test_changes_to_unknown_user_return_false(self, temp_db):
		assert db.update_password_hash("nobody", "hash") is False
		assert db.set_user_role("nobody", "viewer") is False
		assert db.set_user_disabled("nobody", True) is False

	def test_set_unknown_role_is_rejected(self, temp_db):
		db.insert_user("tom", "hash-1", "admin")
		with pytest.raises(AssertionError):
			db.set_user_role("tom", "superuser")


class TestRecordLogin:
	def test_sets_last_login_without_ending_sessions(self, temp_db):
		db.insert_user("tom", "hash-1", "admin")
		db.record_login("tom")
		user = db.get_user("tom")
		assert user["last_login_at"]
		assert user["session_version"] == 1


class TestCompactHeader:
	def test_off_by_default_and_saved_per_user(self, temp_db):
		db.init_db()
		db.insert_user("tom", "hash", "admin")
		db.insert_user("ann", "hash", "viewer")
		assert db.get_user("tom")["compact_header"] == 0
		assert db.set_compact_header("tom", True) is True
		assert db.get_user("tom")["compact_header"] == 1
		assert db.get_user("ann")["compact_header"] == 0

	def test_changing_it_does_not_sign_anyone_out(self, temp_db):
		db.init_db()
		db.insert_user("tom", "hash", "admin")
		version = db.get_user("tom")["session_version"]
		db.set_compact_header("tom", True)
		assert db.get_user("tom")["session_version"] == version

	def test_unknown_user(self, temp_db):
		db.init_db()
		assert db.set_compact_header("nobody", True) is False

	def test_added_to_an_existing_users_table(self, temp_db):
		db.init_db()
		with db._connect() as conn:
			conn.execute("ALTER TABLE users DROP COLUMN compact_header")
		db.init_db()
		db.insert_user("tom", "hash", "admin")
		assert db.get_user("tom")["compact_header"] == 0
