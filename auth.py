"""
auth.py — Authentication for KeaNexus.

Credentials are read from environment variables. Session state tracks
whether the current browser session has authenticated.
"""
import hmac
import os

import streamlit as st

_USERNAME_ENV = "KEANEXUS_USERNAME"
_PASSWORD_ENV = "KEANEXUS_PASSWORD"
_SESSION_KEY  = "authenticated"


def is_authenticated() -> bool:
	"""Return True if the current browser session has logged in."""
	return st.session_state.get(_SESSION_KEY, False)


def attempt_login(username: str, password: str) -> bool:
	"""Validate credentials and set session state on success.

	Uses constant-time comparison to prevent timing-based username enumeration.
	Returns True if login succeeded.
	"""
	expected_user = os.environ.get(_USERNAME_ENV, "")
	expected_pass = os.environ.get(_PASSWORD_ENV, "")

	if not expected_user or not expected_pass:
		return False

	user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
	pass_ok = hmac.compare_digest(password.encode(), expected_pass.encode())

	if user_ok and pass_ok:
		st.session_state[_SESSION_KEY] = True
		return True

	return False


def logout() -> None:
	"""Clear the authenticated session state."""
	st.session_state[_SESSION_KEY] = False
