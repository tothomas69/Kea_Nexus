"""
test_auth.py — Tests for auth.py: credential checks, session validity, the
signed session token, the first-admin bootstrap, and the user-management rules.

Every test runs against a temporary database (temp_db).
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


class TestCheckCredentials:
	def test_correct_credentials_return_the_user_and_record_the_login(self, temp_db):
		_add_user()
		user = auth.check_credentials("tom", _GOOD_PASSWORD)
		assert user["username"] == "tom"
		assert db.get_user("tom")["last_login_at"]

	def test_username_is_case_insensitive(self, temp_db):
		_add_user()
		assert auth.check_credentials("TOM", _GOOD_PASSWORD) is not None

	@pytest.mark.parametrize("username", ["nobody", ""])
	def test_unknown_or_empty_username_returns_none(self, temp_db, username):
		assert auth.check_credentials(username, _GOOD_PASSWORD) is None

	def test_wrong_password_returns_none(self, temp_db):
		_add_user()
		assert auth.check_credentials("tom", _OTHER_PASSWORD) is None

	def test_disabled_user_returns_none(self, temp_db):
		_add_user()
		db.set_user_disabled("tom", True)
		assert auth.check_credentials("tom", _GOOD_PASSWORD) is None


class TestSessionUserIfValid:
	def test_current_version_is_valid(self, temp_db):
		_add_user()
		version = db.get_user("tom")["session_version"]
		assert auth.session_user_if_valid("tom", version)["username"] == "tom"

	def test_old_version_is_rejected(self, temp_db):
		_add_user()
		version = db.get_user("tom")["session_version"]
		auth.reset_password("tom", _OTHER_PASSWORD)
		assert auth.session_user_if_valid("tom", version) is None

	def test_disabled_user_is_rejected(self, temp_db):
		_add_user()
		_add_user("amy")
		version = db.get_user("amy")["session_version"]
		db.set_user_disabled("amy", True)
		# Disabling bumps session_version too, so check against the new value:
		# the disabled flag alone must still end the session.
		assert auth.session_user_if_valid("amy", db.get_user("amy")["session_version"]) is None
		assert auth.session_user_if_valid("amy", version) is None

	def test_unknown_user_is_rejected(self, temp_db):
		assert auth.session_user_if_valid("ghost", 1) is None


class TestSessionToken:
	def test_round_trip(self, temp_db, secret):
		_add_user()
		user = db.get_user("tom")
		token = auth.sign_session("tom", user["session_version"])
		assert auth.user_from_session_token(token)["username"] == "tom"

	def test_no_token_without_secret(self, temp_db):
		with patch.dict(os.environ, {}, clear=True):
			assert auth.sign_session("tom", 1) == ""

	def test_token_from_before_password_change_is_rejected(self, temp_db, secret):
		_add_user()
		token = auth.sign_session("tom", db.get_user("tom")["session_version"])
		auth.reset_password("tom", _OTHER_PASSWORD)
		assert auth.user_from_session_token(token) is None

	def test_tampered_username_is_rejected(self, temp_db, secret):
		_add_user()
		_add_user("amy", role="viewer")
		_, version, signature = auth.sign_session("amy", 1).split("|")
		assert auth.user_from_session_token(f"tom|{version}|{signature}") is None

	@pytest.mark.parametrize("token", [None, "", "tom", "tom|1", "a|b|c|d"])
	def test_malformed_tokens_are_rejected(self, temp_db, secret, token):
		assert auth.user_from_session_token(token) is None

	def test_non_numeric_version_is_rejected_even_if_signed(self, temp_db, secret):
		_add_user()
		signature = auth._cookie_signature(_TEST_SECRET, "tom|x")
		assert auth.user_from_session_token(f"tom|x|{signature}") is None

	def test_public_user_never_includes_the_hash(self, temp_db):
		_add_user()
		assert auth.public_user(db.get_user("tom")) == {"username": "tom", "role": "admin"}


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
