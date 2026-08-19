"""
auth.py — Authentication for KeaNexus.

Credentials are read from environment variables. Session state tracks
whether the current browser session has authenticated.

st.session_state alone only survives while the WebSocket connection stays
up — a full page reload starts a brand-new Streamlit session with a blank
session_state, which is why refreshing used to force a re-login every time.
SESSION_COOKIE_NAME/session_token() back a real browser session cookie (set
by app.py via a CookieManager component) that survives a reload but is
cleared when the browser itself fully closes, so app.py can restore
session_state from it on a fresh session instead of re-prompting.
"""

import hashlib
import hmac
import os
from typing import Optional

import streamlit as st

_USERNAME_ENV = "KEANEXUS_USERNAME"
_PASSWORD_ENV = "KEANEXUS_PASSWORD"
_SESSION_KEY = "authenticated"

SESSION_COOKIE_NAME = "keanexus_session"


def is_authenticated() -> bool:
	"""Return True if the current browser session has logged in."""
	return st.session_state.get(_SESSION_KEY, False)


def session_token() -> str:
	"""The value a valid session cookie must carry.

	Derived from KEANEXUS_PASSWORD via HMAC rather than a random value, so
	there's no server-side session store to maintain — restarting the app
	doesn't invalidate cookies already issued, and no separate secret needs
	configuring. Stateless by design: the token itself has no expiry — the
	browser's own session-cookie lifecycle is what limits how long it lasts.
	"""
	expected_pass = os.environ.get(_PASSWORD_ENV, "")
	return hmac.new(expected_pass.encode(), b"keanexus-session", hashlib.sha256).hexdigest()


def restore_session_from_cookie(cookie_value: Optional[str]) -> bool:
	"""Re-authenticate from a previously-set session cookie.

	Called on every fresh Streamlit session (e.g. after a page reload) before
	falling back to the login page. Returns True if the session was restored.
	"""
	if not cookie_value:
		return False
	if hmac.compare_digest(cookie_value, session_token()):
		st.session_state[_SESSION_KEY] = True
		return True
	return False


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
