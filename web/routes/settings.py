"""
settings.py — The Settings screen: the subnet's DHCP settings, and (admins
only) the Users panel.

DHCP settings are saved onto a config read fresh from Kea, for the same
reason as Reservations: the quarantine service edits the same config.
User rules all live in auth.py; this module only collects input and shows
the UserManagementError message auth.py raises.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

import auth
import db
from kea import KeaClient, KeaError
from web import cache
from web.deps import current_user, kea_client, require_admin
from web.kea_reads import read_kea
from web.overview_data import local_time
from web.settings_data import (
	MAX_LEASE_HOURS,
	MIN_LEASE_HOURS,
	settings_values,
	validate_settings,
	with_settings_applied,
)
from web.templating import redirect, render

router = APIRouter()

USERS_CHANGED_EVENT = "users-changed"
_HTTP_NOT_FOUND = 404
_FORM_FIELDS = ("pool_start", "pool_end", "lease_hours", "gateway", "dns", "domain", "ntp")


def _is_admin(user: dict) -> bool:
	return user.get("role") == auth.ROLE_ADMIN


# --- DHCP settings ------------------------------------------------------------------------


def _dhcp_form(
	request: Request, user: dict, values: dict, errors: list[str], saved: bool = False
) -> Response:
	context = {
		"user": user,
		"is_admin": _is_admin(user),
		"values": values,
		"errors": errors,
		"saved": saved,
		"min_hours": MIN_LEASE_HOURS,
		"max_hours": MAX_LEASE_HOURS,
	}
	return render(request, "settings/_dhcp_form.html", context)


@router.get("/settings")
def settings_page(
	request: Request, user: dict = Depends(current_user), kea: KeaClient = Depends(kea_client)
) -> Response:
	config = read_kea(kea)["config"]
	context = {
		"user": user,
		"is_admin": _is_admin(user),
		"values": settings_values(config),
		"errors": [],
		"saved": False,
		"kea_error": config is None,
		"min_hours": MIN_LEASE_HOURS,
		"max_hours": MAX_LEASE_HOURS,
		"section": "dhcp",
	}
	return render(request, "settings.html", context)


@router.put("/settings/dhcp")
async def save_dhcp_settings(
	request: Request, user: dict = Depends(require_admin), kea: KeaClient = Depends(kea_client)
) -> Response:
	form = await request.form()
	submitted = {field: str(form.get(field, "")).strip() for field in _FORM_FIELDS}
	try:
		config = kea.get_config()
	except KeaError as exc:
		return _dhcp_form(
			request,
			user,
			{**settings_values(None), **submitted},
			[f"Kea can't be reached, so nothing was saved: {exc}"],
		)
	values = {**settings_values(config), **submitted}
	errors = validate_settings(values)
	if errors:
		return _dhcp_form(request, user, values, errors)
	try:
		kea.save_config(with_settings_applied(config, values))
	except KeaError as exc:
		return _dhcp_form(request, user, values, [f"Kea refused the change: {exc}"])
	cache.forget("config", "pool_stats")
	return _dhcp_form(
		request, user, settings_values(with_settings_applied(config, values)), [], saved=True
	)


# --- Users ----------------------------------------------------------------------------------


def _user_rows() -> list[dict]:
	return [
		{
			"username": row["username"],
			"role": row["role"],
			"disabled": bool(row["is_disabled"]),
			"last_login": local_time(row.get("last_login_at")) or "never",
		}
		for row in db.list_users()
	]


@router.get("/settings/users")
def users_page(request: Request, user: dict = Depends(require_admin)) -> Response:
	context = {"user": user, "is_admin": True, "users": _user_rows(), "section": "users"}
	return render(request, "settings.html", context)


@router.get("/fragments/users")
def users_fragment(request: Request, user: dict = Depends(require_admin)) -> Response:
	return render(request, "settings/_users.html", {"user": user, "users": _user_rows()})


def _user_form(
	request: Request, user: dict, target: Optional[dict], values: dict, error: Optional[str] = None
) -> Response:
	context = {
		"target": target,
		"values": values,
		"error": error,
		"roles": db.USER_ROLES,
		"min_password": auth.MIN_PASSWORD_LENGTH,
		"is_self": target is not None and target["username"].lower() == user["username"].lower(),
	}
	return render(request, "settings/_user_form.html", context)


def _stored_user(username: str) -> dict:
	stored = db.get_user(username)
	if stored is None:
		raise HTTPException(status_code=_HTTP_NOT_FOUND, detail="No such user.")
	return auth.public_user(stored) | {"is_disabled": bool(stored["is_disabled"])}


def _users_saved(request: Request, user: dict, ended_own_session: bool) -> Response:
	# A change to your own role, password or status ends your own session, so
	# send the browser to sign in again instead of drawing a page it can't use.
	if ended_own_session:
		return redirect(request, "/login")
	return Response(headers={"HX-Trigger": f"{USERS_CHANGED_EVENT}, close-dialog"})


def _password_problem(password: str, confirm: str) -> Optional[str]:
	"""Checked before anything is saved, so a role change can't land without its password."""
	if password != confirm:
		return "The two passwords don't match."
	if password and len(password) < auth.MIN_PASSWORD_LENGTH:
		return f"Passwords need at least {auth.MIN_PASSWORD_LENGTH} characters."
	return None


@router.get("/settings/users/new")
def new_user_form(request: Request, user: dict = Depends(require_admin)) -> Response:
	return _user_form(request, user, None, {"username": "", "role": auth.ROLE_VIEWER})


@router.get("/settings/users/{username}")
def edit_user_form(
	username: str, request: Request, user: dict = Depends(require_admin)
) -> Response:
	target = _stored_user(username)
	return _user_form(
		request, user, target, {"username": target["username"], "role": target["role"]}
	)


@router.post("/settings/users")
def create_user(
	request: Request,
	username: str = Form(""),
	role: str = Form(auth.ROLE_VIEWER),
	password: str = Form(""),
	confirm: str = Form(""),
	user: dict = Depends(require_admin),
) -> Response:
	values = {"username": username.strip(), "role": role}
	problem = _password_problem(password, confirm) or (
		None if password else "Set a password for the new user."
	)
	if problem:
		return _user_form(request, user, None, values, problem)
	try:
		auth.create_user(values["username"], password, role)
	except auth.UserManagementError as exc:
		return _user_form(request, user, None, values, str(exc))
	return _users_saved(request, user, ended_own_session=False)


@router.put("/settings/users/{username}")
def update_user(
	username: str,
	request: Request,
	role: str = Form(""),
	password: str = Form(""),
	confirm: str = Form(""),
	user: dict = Depends(require_admin),
) -> Response:
	target = _stored_user(username)
	values = {"username": target["username"], "role": role or target["role"]}
	problem = _password_problem(password, confirm)
	if problem:
		return _user_form(request, user, target, values, problem)
	changed = False
	try:
		if values["role"] != target["role"]:
			auth.change_role(target["username"], values["role"])
			changed = True
		if password:
			auth.reset_password(target["username"], password)
			changed = True
	except auth.UserManagementError as exc:
		return _user_form(request, user, target, values, str(exc))
	is_self = target["username"].lower() == user["username"].lower()
	return _users_saved(request, user, ended_own_session=changed and is_self)


@router.post("/settings/users/{username}/disable")
def disable_user(username: str, request: Request, user: dict = Depends(require_admin)) -> Response:
	return _set_disabled(request, user, username, True)


@router.post("/settings/users/{username}/enable")
def enable_user(username: str, request: Request, user: dict = Depends(require_admin)) -> Response:
	return _set_disabled(request, user, username, False)


def _set_disabled(request: Request, user: dict, username: str, disabled: bool) -> Response:
	target = _stored_user(username)
	try:
		auth.set_disabled(target["username"], disabled, user["username"])
	except auth.UserManagementError as exc:
		return _user_form(
			request,
			user,
			target,
			{"username": target["username"], "role": target["role"]},
			str(exc),
		)
	return _users_saved(request, user, ended_own_session=False)
