"""
deps.py — FastAPI dependencies for who is signed in and what they may do.

These replace streamlit_adapter's session functions and ui_access.py:
current_user() ends the session the moment the user is disabled or has a
password or role change, require_admin() guards every write route, and
kea_client() hands viewers a ReadOnlyKeaClient, which is the real guard on
Kea writes no matter which route issues them.
"""

from functools import cache

from fastapi import Depends, HTTPException, Request

import auth
from kea import VIEWER_WRITE_REFUSED_MESSAGE, KeaClient, ReadOnlyKeaClient
from web.security import SESSION_COOKIE_NAME

_HTTP_FORBIDDEN = 403


class NotSignedIn(Exception):
	"""Raised by current_user(); main.py turns it into a redirect to the login page."""


def current_user(request: Request) -> dict:
	"""{"username", "role"} for the signed-in user. Raises NotSignedIn otherwise."""
	stored_user = auth.user_from_session_token(request.cookies.get(SESSION_COOKIE_NAME))
	if stored_user is None:
		raise NotSignedIn()
	return auth.public_user(stored_user)


def require_admin(user: dict = Depends(current_user)) -> dict:
	"""Refuse viewers. Every route that changes anything depends on this."""
	if user["role"] != auth.ROLE_ADMIN:
		raise HTTPException(status_code=_HTTP_FORBIDDEN, detail=VIEWER_WRITE_REFUSED_MESSAGE)
	return user


@cache
def _admin_client() -> KeaClient:
	return KeaClient()


@cache
def _viewer_client() -> ReadOnlyKeaClient:
	return ReadOnlyKeaClient()


def kea_client(user: dict = Depends(current_user)) -> KeaClient:
	"""The Kea client for this user: read-only for viewers."""
	if user["role"] == auth.ROLE_ADMIN:
		return _admin_client()
	return _viewer_client()
