"""
test_auth.py — Tests for auth.py: login, sessions, the signed cookie, the
first-admin bootstrap, and the user-management rules.

Every test runs against a temporary database (temp_db) with a plain dict
standing in for st.session_state.
"""

import os
from unittest.mock import patch

import pytest

import auth
import db

_GOOD_PASSWORD = "correct-horse-battery"  # pragma: allowlist secret
_OTHER_PASSWORD = "different-horse-battery"  # pragma: allowlist secret
_TEST_SECRET = "test-cookie-secret"  # pragma: allowlist secret
_OTHER_SECRET = "a-different-secret"  # pragma: allowlist secret
# Shorter than MIN_PASSWORD_LENGTH on purpose: bootstrap must accept it anyway.
_SHORT_BOOTSTRAP_PASSWORD = "short"  # pragma: allowlist secret
_UNUSED_BOOTSTRAP_PASSWORD = "whatever"  # pragma: allowlist secret


@pytest.fixture
def session():
	"""Provide a plain dict as st.session_state for the duration of the test."""
	state = {}
	with patch.object(auth.st, "session_state", state):
		yield state


@pytest.fixture
def secret():
	with patch.dict(os.environ, {"KEANEXUS_SECRET": _TEST_SECRET}):
		yield _TEST_SECRET


def _add_user(username="tom", password=_GOOD_PASSWORD, role="admin"):
	auth.create_user(username, password, role)


class TestBootstrapAdmin:
	def test_creates_admin_from_env_when_table_empty(self, temp_db):
		env = {"KEANEXUS_USERNAME": "admin", "KEANEXUS_PASSWORD": _SHORT_BOOTSTRAP_PASSWORD}
		with patch.dict(os.environ, env):
			assert auth.bootstrap_admin_from_env() is True
		user = db.get_user("admin")
		assert user["role"] == "admin"

	def test_does_nothing_once_a_user_exists(self, temp_db):
		_add_user()
		env = {"KEANEXUS_USERNAME": "admin", "KEANEXUS_PASSWORD": _UNUSED_BOOTSTRAP_PASSWORD}
		with patch.dict(os.environ, env):
			assert auth.bootstrap_admin_from_env() is False
		assert db.get_user("admin") is None

	def test_does_nothing_without_env_vars(self, temp_db):
		with patch.dict(os.environ, {}, clear=True):
			assert auth.bootstrap_admin_from_env() is False
		assert db.count_users() == 0


class TestLogin:
	def test_correct_credentials_start_a_session(self, temp_db, session):
		_add_user()
		assert auth.attempt_login("tom", _GOOD_PASSWORD) is True
		assert auth.current_user() == {"username": "tom", "role": "admin"}
		assert db.get_user("tom")["last_login_at"]

	def test_username_is_case_insensitive(self, temp_db, session):
		_add_user()
		assert auth.attempt_login("TOM", _GOOD_PASSWORD) is True

	def test_wrong_password_fails(self, temp_db, session):
		_add_user()
		assert auth.attempt_login("tom", _OTHER_PASSWORD) is False
		assert auth.is_authenticated() is False

	def test_unknown_user_fails(self, temp_db, session):
		assert auth.attempt_login("nobody", _GOOD_PASSWORD) is False

	def test_empty_username_fails(self, temp_db, session):
		assert auth.attempt_login("", _GOOD_PASSWORD) is False

	def test_disabled_user_fails(self, temp_db, session):
		_add_user()
		db.set_user_disabled("tom", True)
		assert auth.attempt_login("tom", _GOOD_PASSWORD) is False

	def test_corrupt_stored_hash_fails_rather_than_raising(self, temp_db, session):
		db.insert_user("tom", "not-an-argon2-hash", "admin")
		assert auth.attempt_login("tom", _GOOD_PASSWORD) is False


class TestCurrentUserAndRoles:
	def test_nobody_signed_in(self, temp_db, session):
		assert auth.current_user() is None
		assert auth.is_authenticated() is False
		assert auth.is_admin() is False

	def test_viewer_is_not_admin(self, temp_db, session):
		_add_user(role="viewer")
		auth.attempt_login("tom", _GOOD_PASSWORD)
		assert auth.is_authenticated() is True
		assert auth.is_admin() is False

	def test_password_change_ends_open_session(self, temp_db, session):
		_add_user()
		auth.attempt_login("tom", _GOOD_PASSWORD)
		auth.reset_password("tom", _OTHER_PASSWORD)
		assert auth.current_user() is None

	def test_disable_ends_open_session(self, temp_db, session):
		_add_user()
		_add_user("amy")
		auth.attempt_login("amy", _GOOD_PASSWORD)
		auth.set_disabled("amy", True, acting_username="tom")
		assert auth.current_user() is None

	def test_deleted_from_database_ends_open_session(self, temp_db, session):
		session["auth_user"] = {"username": "ghost", "session_version": 1}
		assert auth.current_user() is None
		assert "auth_user" not in session

	def test_logout_clears_session(self, temp_db, session):
		_add_user()
		auth.attempt_login("tom", _GOOD_PASSWORD)
		auth.logout()
		assert auth.is_authenticated() is False

	def test_logout_when_not_signed_in_is_safe(self, session):
		auth.logout()
		assert session == {}


class TestSessionCookie:
	def test_no_token_without_secret(self, temp_db, session):
		_add_user()
		auth.attempt_login("tom", _GOOD_PASSWORD)
		with patch.dict(os.environ, {}, clear=True):
			assert auth.session_token() == ""

	def test_no_token_when_signed_out(self, temp_db, session, secret):
		assert auth.session_token() == ""

	def test_token_round_trips_into_a_fresh_session(self, temp_db, session, secret):
		_add_user()
		auth.attempt_login("tom", _GOOD_PASSWORD)
		token = auth.session_token()
		session.clear()
		assert auth.restore_session_from_cookie(token) is True
		assert auth.current_user()["username"] == "tom"

	def test_cookie_from_before_password_change_is_rejected(self, temp_db, session, secret):
		_add_user()
		auth.attempt_login("tom", _GOOD_PASSWORD)
		old_token = auth.session_token()
		auth.reset_password("tom", _OTHER_PASSWORD)
		session.clear()
		assert auth.restore_session_from_cookie(old_token) is False

	def test_tampered_username_is_rejected(self, temp_db, session, secret):
		_add_user()
		_add_user("amy", role="viewer")
		auth.attempt_login("amy", _GOOD_PASSWORD)
		_, version, signature = auth.session_token().split("|")
		session.clear()
		assert auth.restore_session_from_cookie(f"tom|{version}|{signature}") is False

	def test_cookie_signed_with_another_secret_is_rejected(self, temp_db, session, secret):
		_add_user()
		auth.attempt_login("tom", _GOOD_PASSWORD)
		token = auth.session_token()
		session.clear()
		with patch.dict(os.environ, {"KEANEXUS_SECRET": _OTHER_SECRET}):
			assert auth.restore_session_from_cookie(token) is False

	@pytest.mark.parametrize("cookie", [None, "", "tom", "tom|1", "a|b|c|d"])
	def test_malformed_cookies_are_rejected(self, temp_db, session, secret, cookie):
		assert auth.restore_session_from_cookie(cookie) is False

	def test_non_numeric_version_is_rejected_even_if_signed(self, temp_db, session, secret):
		_add_user()
		signature = auth._cookie_signature(_TEST_SECRET, "tom|x")
		assert auth.restore_session_from_cookie(f"tom|x|{signature}") is False

	def test_no_restore_without_secret(self, temp_db, session):
		with patch.dict(os.environ, {}, clear=True):
			assert auth.restore_session_from_cookie("tom|1|abc") is False


class TestUserManagement:
	def test_create_user_stores_a_hash_not_the_password(self, temp_db):
		_add_user()
		stored_hash = db.get_user("tom")["password_hash"]
		assert stored_hash.startswith("$argon2")
		assert _GOOD_PASSWORD not in stored_hash

	def test_create_rejects_short_password(self, temp_db):
		with pytest.raises(auth.UserManagementError, match="at least"):
			auth.create_user("tom", "short", "admin")

	@pytest.mark.parametrize("username", ["t", "tom|admin", "has space", "x" * 33])
	def test_create_rejects_bad_usernames(self, temp_db, username):
		with pytest.raises(auth.UserManagementError, match="Usernames"):
			auth.create_user(username, _GOOD_PASSWORD, "viewer")

	def test_create_rejects_unknown_role(self, temp_db):
		with pytest.raises(auth.UserManagementError, match="Unknown role"):
			auth.create_user("tom", _GOOD_PASSWORD, "superuser")

	def test_create_rejects_duplicate_in_any_case(self, temp_db):
		_add_user()
		with pytest.raises(auth.UserManagementError, match="already exists"):
			auth.create_user("TOM", _GOOD_PASSWORD, "viewer")

	def test_reset_password_for_unknown_user(self, temp_db):
		with pytest.raises(auth.UserManagementError, match="No user"):
			auth.reset_password("nobody", _GOOD_PASSWORD)

	def test_reset_password_rejects_short_password(self, temp_db):
		_add_user()
		with pytest.raises(auth.UserManagementError, match="at least"):
			auth.reset_password("tom", "short")

	def test_change_role(self, temp_db):
		_add_user()
		_add_user("amy")
		auth.change_role("amy", "viewer")
		assert db.get_user("amy")["role"] == "viewer"

	def test_cannot_demote_last_active_admin(self, temp_db):
		_add_user()
		with pytest.raises(auth.UserManagementError, match="last active admin"):
			auth.change_role("tom", "viewer")

	def test_disabled_admin_does_not_count_toward_the_last_admin_rule(self, temp_db):
		_add_user()
		_add_user("amy")
		auth.set_disabled("amy", True, acting_username="tom")
		with pytest.raises(auth.UserManagementError, match="last active admin"):
			auth.change_role("tom", "viewer")

	def test_change_role_rejects_unknown_role(self, temp_db):
		_add_user()
		with pytest.raises(auth.UserManagementError, match="Unknown role"):
			auth.change_role("tom", "superuser")

	def test_cannot_disable_yourself(self, temp_db):
		_add_user()
		_add_user("amy")
		with pytest.raises(auth.UserManagementError, match="your own"):
			auth.set_disabled("tom", True, acting_username="TOM")

	def test_cannot_disable_last_active_admin(self, temp_db):
		_add_user()
		_add_user("zoe", role="viewer")
		with pytest.raises(auth.UserManagementError, match="last active admin"):
			auth.set_disabled("tom", True, acting_username="zoe")

	def test_disable_and_reenable(self, temp_db):
		_add_user()
		_add_user("zoe", role="viewer")
		auth.set_disabled("zoe", True, acting_username="tom")
		assert db.get_user("zoe")["is_disabled"] == 1
		auth.set_disabled("zoe", False, acting_username="tom")
		assert db.get_user("zoe")["is_disabled"] == 0
