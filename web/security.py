"""
security.py — The web UI's session cookie and CSRF protection.

The session cookie holds auth.sign_session()'s token, nothing else. It is
checked against the database on every request (see deps.current_user), so a
password, role or disable change ends a session at its next click.

CSRF uses the double-submit pattern: a random token lives in its own cookie,
and every state-changing request must echo it back in the X-CSRF-Token header.
base.html puts the header on every HTMX request through hx-headers on <body>.
A cross-site page can make the browser send the cookie, but it cannot read it
to copy into the header, so it cannot forge a write. SameSite=Lax alone is not
enough here, because the app is reachable from the internet through a reverse
proxy and older or unusual browsers do not all enforce it.
"""

import hmac
import secrets

from fastapi import HTTPException, Request, Response

SESSION_COOKIE_NAME = "keanexus_web_session"
CSRF_COOKIE_NAME = "keanexus_web_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"

SESSION_MAX_AGE_SECONDS = 24 * 60 * 60
CSRF_TOKEN_BYTES = 32

CSRF_REFUSED_MESSAGE = "This page is out of date. Reload it and try again."

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_HTTP_FORBIDDEN = 403


def _is_https(request: Request) -> bool:
	"""True when the browser reached us over HTTPS.

	Behind Nginx Proxy Manager this relies on uvicorn's --proxy-headers, which
	turns X-Forwarded-Proto into the request scheme. Plain-HTTP access on the
	LAN (for side-by-side testing) gets cookies without the Secure flag, or
	the browser would never send them back.
	"""
	return request.url.scheme == "https"


def set_session_cookie(response: Response, request: Request, token: str) -> None:
	assert token, "set_session_cookie needs a signed token; never set an empty session cookie"
	response.set_cookie(
		SESSION_COOKIE_NAME,
		token,
		max_age=SESSION_MAX_AGE_SECONDS,
		httponly=True,
		secure=_is_https(request),
		samesite="lax",
		path="/",
	)


def clear_session_cookie(response: Response) -> None:
	response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def csrf_token_for(request: Request) -> tuple[str, bool]:
	"""The request's CSRF token, and whether it is new and needs setting as a cookie."""
	existing_token = request.cookies.get(CSRF_COOKIE_NAME)
	if existing_token:
		return existing_token, False
	return secrets.token_urlsafe(CSRF_TOKEN_BYTES), True


def set_csrf_cookie(response: Response, request: Request, token: str) -> None:
	# HttpOnly is fine: the server writes the token into the page itself, so
	# page scripts never need to read this cookie.
	response.set_cookie(
		CSRF_COOKIE_NAME,
		token,
		httponly=True,
		secure=_is_https(request),
		samesite="lax",
		path="/",
	)


def require_csrf(request: Request) -> None:
	"""App-wide dependency: refuse any state-changing request without a matching token."""
	if request.method in _SAFE_METHODS:
		return
	cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
	header_token = request.headers.get(CSRF_HEADER_NAME, "")
	if not cookie_token or not header_token:
		raise HTTPException(status_code=_HTTP_FORBIDDEN, detail=CSRF_REFUSED_MESSAGE)
	if not hmac.compare_digest(cookie_token, header_token):
		raise HTTPException(status_code=_HTTP_FORBIDDEN, detail=CSRF_REFUSED_MESSAGE)
