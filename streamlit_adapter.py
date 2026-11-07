"""
streamlit_adapter.py — The Streamlit-only glue for the current UI.

auth.py and helpers.py hold no UI state and import no UI framework, so the
new web app (see docs/fastapi-htmx-migration.md) can share them. Everything
that needs st.session_state or Streamlit's caching lives here instead:

- the signed-in user, kept in st.session_state and restored from the signed
  cookie after a page reload (a reload starts a fresh Streamlit session)
- the per-session Kea client (admins get KeaClient, viewers ReadOnlyKeaClient)
- the cached Kea loaders

This file is deleted at cutover (phase 7), along with app.py and ui_*.py.
"""

from typing import Optional

import streamlit as st

import auth
from helpers import pool_stats_with_fallback
from kea import KeaClient, ReadOnlyKeaClient

_SESSION_USER_KEY = "auth_user"

# How long each cached Kea read may be reused. Lease and pool data move
# quickly; the config changes only when someone saves it.
_LEASES_CACHE_SECONDS = 30
_POOL_STATS_CACHE_SECONDS = 30
_CONFIG_CACHE_SECONDS = 120
_STATUS_CACHE_SECONDS = 20


# --- Session ------------------------------------------------------------------------


def _start_session(stored_user: dict) -> None:
	st.session_state[_SESSION_USER_KEY] = {
		"username": stored_user["username"],
		"session_version": stored_user["session_version"],
	}


def attempt_login(username: str, password: str) -> bool:
	"""Check credentials and start a session on success."""
	stored_user = auth.check_credentials(username, password)
	if stored_user is None:
		return False
	_start_session(stored_user)
	return True


def current_user() -> Optional[dict]:
	"""Return {"username", "role"} for the signed-in user, or None.

	Re-checked against the database on every call, so the session ends on the
	spot if the user has since been disabled or had a password or role change.
	"""
	session_user = st.session_state.get(_SESSION_USER_KEY)
	if not session_user:
		return None
	stored_user = auth.session_user_if_valid(
		session_user["username"], session_user["session_version"]
	)
	if stored_user is None:
		logout()
		return None
	return auth.public_user(stored_user)


def is_authenticated() -> bool:
	return current_user() is not None


def is_admin() -> bool:
	user = current_user()
	return user is not None and user["role"] == auth.ROLE_ADMIN


def logout() -> None:
	st.session_state.pop(_SESSION_USER_KEY, None)


def session_token() -> str:
	"""The signed cookie value for the current session, or "" if none can be made.

	Returns "" when nobody is signed in or KEANEXUS_SECRET is unset; callers
	must not set a cookie in that case.
	"""
	session_user = st.session_state.get(_SESSION_USER_KEY)
	if not session_user:
		return ""
	return auth.sign_session(session_user["username"], session_user["session_version"])


def restore_session_from_cookie(cookie_value: Optional[str]) -> bool:
	"""Re-authenticate from the session cookie after a page reload. True on success."""
	stored_user = auth.user_from_session_token(cookie_value)
	if stored_user is None:
		return False
	_start_session(stored_user)
	return True


# --- Kea client and cached reads ----------------------------------------------------


@st.cache_resource
def _admin_client() -> KeaClient:
	return KeaClient()


@st.cache_resource
def _viewer_client() -> ReadOnlyKeaClient:
	return ReadOnlyKeaClient()


def get_client() -> KeaClient:
	"""The Kea client for whoever is signed in to this session.

	Viewers get a ReadOnlyKeaClient, which refuses every command that changes
	Kea. This is the real enforcement of read-only access for Kea: disabled
	buttons only stop honest clicks, while this stops any write that reaches
	the server, from any tab, including ones added later. Not cached itself,
	because the answer depends on the session; the two clients are.
	"""
	return _admin_client() if is_admin() else _viewer_client()


@st.cache_data(ttl=_LEASES_CACHE_SECONDS)
def load_leases(_client: KeaClient) -> list[dict]:
	return _client.get_leases()


@st.cache_data(ttl=_POOL_STATS_CACHE_SECONDS)
def load_pool_stats(_client: KeaClient) -> Optional[dict]:
	return pool_stats_with_fallback(_client)


@st.cache_data(ttl=_CONFIG_CACHE_SECONDS)
def load_config(_client: KeaClient) -> Optional[dict]:
	try:
		return _client.get_config()
	except Exception:
		return None


@st.cache_data(ttl=_STATUS_CACHE_SECONDS)
def load_status(_client: KeaClient) -> dict:
	return _client.get_status()
