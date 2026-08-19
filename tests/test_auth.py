"""
test_auth.py — Tests for auth.py authentication logic.
"""

import os
from unittest.mock import patch

import pytest

import auth


@pytest.fixture
def session():
	"""Provide a plain dict as st.session_state for the duration of the test."""
	state = {}
	with patch.object(auth.st, "session_state", state):
		yield state


class TestIsAuthenticated:
	def test_returns_false_when_key_absent(self, session):
		assert auth.is_authenticated() is False

	def test_returns_true_when_session_key_set(self, session):
		session["authenticated"] = True
		assert auth.is_authenticated() is True

	def test_returns_false_when_session_key_explicitly_false(self, session):
		session["authenticated"] = False
		assert auth.is_authenticated() is False


class TestAttemptLogin:
	_VALID_USER = "admin"
	_VALID_PASS = "s3cr3t"
	_ENV = {"KEANEXUS_USERNAME": _VALID_USER, "KEANEXUS_PASSWORD": _VALID_PASS}

	def test_returns_false_when_env_vars_missing(self, session):
		with patch.dict(os.environ, {}, clear=True):
			os.environ.pop("KEANEXUS_USERNAME", None)
			os.environ.pop("KEANEXUS_PASSWORD", None)
			result = auth.attempt_login("admin", "pass")
		assert result is False

	def test_returns_false_when_only_username_set(self, session):
		with patch.dict(os.environ, {"KEANEXUS_USERNAME": self._VALID_USER}, clear=True):
			os.environ.pop("KEANEXUS_PASSWORD", None)
			result = auth.attempt_login(self._VALID_USER, "")
		assert result is False

	def test_returns_false_on_wrong_password(self, session):
		with patch.dict(os.environ, self._ENV):
			result = auth.attempt_login(self._VALID_USER, "wrong")
		assert result is False
		assert session.get("authenticated") is not True

	def test_returns_false_on_wrong_username(self, session):
		with patch.dict(os.environ, self._ENV):
			result = auth.attempt_login("intruder", self._VALID_PASS)
		assert result is False

	def test_returns_false_on_both_wrong(self, session):
		with patch.dict(os.environ, self._ENV):
			result = auth.attempt_login("bad", "bad")
		assert result is False

	def test_returns_true_and_sets_session_on_correct_credentials(self, session):
		with patch.dict(os.environ, self._ENV):
			result = auth.attempt_login(self._VALID_USER, self._VALID_PASS)
		assert result is True
		assert session.get("authenticated") is True


class TestLogout:
	def test_clears_authenticated_flag(self, session):
		session["authenticated"] = True
		auth.logout()
		assert session.get("authenticated") is False

	def test_logout_when_not_authenticated_is_safe(self, session):
		auth.logout()
		assert session.get("authenticated") is False


class TestSessionToken:
	_ENV = {"KEANEXUS_PASSWORD": "s3cr3t"}

	def test_same_password_produces_same_token(self, session):
		with patch.dict(os.environ, self._ENV):
			assert auth.session_token() == auth.session_token()

	def test_different_password_produces_different_token(self, session):
		with patch.dict(os.environ, {"KEANEXUS_PASSWORD": "s3cr3t"}):
			token_a = auth.session_token()
		with patch.dict(os.environ, {"KEANEXUS_PASSWORD": "different"}):
			token_b = auth.session_token()
		assert token_a != token_b

	def test_missing_password_still_returns_a_token(self, session):
		with patch.dict(os.environ, {}, clear=True):
			os.environ.pop("KEANEXUS_PASSWORD", None)
			assert auth.session_token() != ""


class TestRestoreSessionFromCookie:
	_ENV = {"KEANEXUS_PASSWORD": "s3cr3t"}

	def test_none_cookie_does_not_authenticate(self, session):
		assert auth.restore_session_from_cookie(None) is False
		assert session.get("authenticated") is not True

	def test_empty_cookie_does_not_authenticate(self, session):
		assert auth.restore_session_from_cookie("") is False
		assert session.get("authenticated") is not True

	def test_wrong_cookie_does_not_authenticate(self, session):
		with patch.dict(os.environ, self._ENV):
			assert auth.restore_session_from_cookie("bogus-value") is False
		assert session.get("authenticated") is not True

	def test_valid_cookie_authenticates(self, session):
		with patch.dict(os.environ, self._ENV):
			valid_token = auth.session_token()
			assert auth.restore_session_from_cookie(valid_token) is True
		assert session.get("authenticated") is True
