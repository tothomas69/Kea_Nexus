"""
auth_routes.py — Sign in and sign out.

The session lives only in a signed cookie (see web/security.py), so unlike
the Streamlit UI there is no session state to restore and no cookie component
to wait for: the cookie is set on the login response and checked on every
request after it.
"""

from fastapi import APIRouter, Form, Request, Response

import auth
from web.deps import NotSignedIn, current_user
from web.security import clear_session_cookie, set_session_cookie
from web.templating import redirect, render

router = APIRouter()

WRONG_CREDENTIALS_MESSAGE = "Wrong username or password."
MISSING_SECRET_MESSAGE = (
	"Sign-in is unavailable: KEANEXUS_SECRET is not set, so sessions can't be signed."
	" Set it in .env and restart the container."
)


def _already_signed_in(request: Request) -> bool:
	try:
		current_user(request)
	except NotSignedIn:
		return False
	return True


@router.get("/login")
def login_page(request: Request) -> Response:
	if _already_signed_in(request):
		return redirect(request, "/")
	return render(request, "login.html", {"error": None, "username": ""})


@router.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form("")) -> Response:
	"""Check credentials; on success set the session cookie and go to the Overview.

	A failed attempt re-renders just the form with status 200, because HTMX
	does not swap the body of a 4xx response by default.
	"""
	username = username.strip()
	stored_user = auth.check_credentials(username, password)
	if stored_user is None:
		return render(
			request, "_login_form.html", {"error": WRONG_CREDENTIALS_MESSAGE, "username": username}
		)
	token = auth.sign_session(stored_user["username"], stored_user["session_version"])
	if not token:
		return render(
			request, "_login_form.html", {"error": MISSING_SECRET_MESSAGE, "username": username}
		)
	response = redirect(request, "/")
	set_session_cookie(response, request, token)
	return response


@router.post("/logout")
def logout(request: Request) -> Response:
	response = redirect(request, "/login")
	clear_session_cookie(response)
	return response
