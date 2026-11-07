"""
test_streamlit_adapter.py — Tests for the Streamlit glue: the signed-in user
kept in st.session_state, the session cookie round trip, and the per-session
Kea client.

Every session test runs against a temporary database (temp_db) with a plain
dict standing in for st.session_state.
"""

import os
from unittest.mock import patch

import pytest

import auth
import db
import streamlit_adapter as adapter
from kea import KeaClient, ReadOnlyKeaClient

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
	with patch.object(adapter.st, "session_state", state):
		yield state


@pytest.fixture
def secret():
	with patch.dict(os.environ, {"KEANEXUS_SECRET": _TEST_SECRET}):
		yield _TEST_SECRET


def _add_user(username="tom", password=_GOOD_PASSWORD, role="admin"):
	auth.create_user(username, password, role)


class TestLogin:
	def test_correct_credentials_start_a_session(self, temp_db, session):
		_add_user()
		assert adapter.attempt_login("tom", _GOOD_PASSWORD) is True
		assert adapter.current_user() == {"username": "tom", "role": "admin"}
		assert db.get_user("tom")["last_login_at"]

	def test_username_is_case_insensitive(self, temp_db, session):
		_add_user()
		assert adapter.attempt_login("TOM", _GOOD_PASSWORD) is True

	def test_wrong_password_fails(self, temp_db, session):
		_add_user()
		assert adapter.attempt_login("tom", _OTHER_PASSWORD) is False
		assert adapter.is_authenticated() is False

	def test_unknown_user_fails(self, temp_db, session):
		assert adapter.attempt_login("nobody", _GOOD_PASSWORD) is False

	def test_empty_username_fails(self, temp_db, session):
		assert adapter.attempt_login("", _GOOD_PASSWORD) is False

	def test_disabled_user_fails(self, temp_db, session):
		_add_user()
		db.set_user_disabled("tom", True)
		assert adapter.attempt_login("tom", _GOOD_PASSWORD) is False

	def test_corrupt_stored_hash_fails_rather_than_raising(self, temp_db, session):
		db.insert_user("tom", "not-an-argon2-hash", "admin")
		assert adapter.attempt_login("tom", _GOOD_PASSWORD) is False


class TestCurrentUserAndRoles:
	def test_nobody_signed_in(self, temp_db, session):
		assert adapter.current_user() is None
		assert adapter.is_authenticated() is False
		assert adapter.is_admin() is False

	def test_viewer_is_not_admin(self, temp_db, session):
		_add_user(role="viewer")
		adapter.attempt_login("tom", _GOOD_PASSWORD)
		assert adapter.is_authenticated() is True
		assert adapter.is_admin() is False

	def test_password_change_ends_open_session(self, temp_db, session):
		_add_user()
		adapter.attempt_login("tom", _GOOD_PASSWORD)
		auth.reset_password("tom", _OTHER_PASSWORD)
		assert adapter.current_user() is None

	def test_disable_ends_open_session(self, temp_db, session):
		_add_user()
		_add_user("amy")
		adapter.attempt_login("amy", _GOOD_PASSWORD)
		auth.set_disabled("amy", True, acting_username="tom")
		assert adapter.current_user() is None

	def test_deleted_from_database_ends_open_session(self, temp_db, session):
		session["auth_user"] = {"username": "ghost", "session_version": 1}
		assert adapter.current_user() is None
		assert "auth_user" not in session

	def test_logout_clears_session(self, temp_db, session):
		_add_user()
		adapter.attempt_login("tom", _GOOD_PASSWORD)
		adapter.logout()
		assert adapter.is_authenticated() is False

	def test_logout_when_not_signed_in_is_safe(self, session):
		adapter.logout()
		assert session == {}


class TestSessionCookie:
	def test_no_token_without_secret(self, temp_db, session):
		_add_user()
		adapter.attempt_login("tom", _GOOD_PASSWORD)
		with patch.dict(os.environ, {}, clear=True):
			assert adapter.session_token() == ""

	def test_no_token_when_signed_out(self, temp_db, session, secret):
		assert adapter.session_token() == ""

	def test_token_round_trips_into_a_fresh_session(self, temp_db, session, secret):
		_add_user()
		adapter.attempt_login("tom", _GOOD_PASSWORD)
		token = adapter.session_token()
		session.clear()
		assert adapter.restore_session_from_cookie(token) is True
		assert adapter.current_user()["username"] == "tom"

	def test_cookie_from_before_password_change_is_rejected(self, temp_db, session, secret):
		_add_user()
		adapter.attempt_login("tom", _GOOD_PASSWORD)
		old_token = adapter.session_token()
		auth.reset_password("tom", _OTHER_PASSWORD)
		session.clear()
		assert adapter.restore_session_from_cookie(old_token) is False

	def test_tampered_username_is_rejected(self, temp_db, session, secret):
		_add_user()
		_add_user("amy", role="viewer")
		adapter.attempt_login("amy", _GOOD_PASSWORD)
		_, version, signature = adapter.session_token().split("|")
		session.clear()
		assert adapter.restore_session_from_cookie(f"tom|{version}|{signature}") is False

	def test_cookie_signed_with_another_secret_is_rejected(self, temp_db, session, secret):
		_add_user()
		adapter.attempt_login("tom", _GOOD_PASSWORD)
		token = adapter.session_token()
		session.clear()
		with patch.dict(os.environ, {"KEANEXUS_SECRET": _OTHER_SECRET}):
			assert adapter.restore_session_from_cookie(token) is False

	@pytest.mark.parametrize("cookie", [None, "", "tom", "tom|1", "a|b|c|d"])
	def test_malformed_cookies_are_rejected(self, temp_db, session, secret, cookie):
		assert adapter.restore_session_from_cookie(cookie) is False

	def test_non_numeric_version_is_rejected_even_if_signed(self, temp_db, session, secret):
		_add_user()
		signature = auth._cookie_signature(_TEST_SECRET, "tom|x")
		assert adapter.restore_session_from_cookie(f"tom|x|{signature}") is False

	def test_no_restore_without_secret(self, temp_db, session):
		with patch.dict(os.environ, {}, clear=True):
			assert adapter.restore_session_from_cookie("tom|1|abc") is False


class TestGetClient:
	"""Viewers must get the read-only client; it is what blocks their Kea writes."""

	def test_admin_gets_full_client(self):
		with patch("streamlit_adapter.is_admin", return_value=True):
			client = adapter.get_client()
		assert isinstance(client, KeaClient)
		assert not isinstance(client, ReadOnlyKeaClient)

	def test_viewer_gets_read_only_client(self):
		with patch("streamlit_adapter.is_admin", return_value=False):
			assert isinstance(adapter.get_client(), ReadOnlyKeaClient)
