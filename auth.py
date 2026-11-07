"""
auth.py — Authentication and user management for KeaNexus.

Users live in the `users` table (see db.py), each with an admin or viewer role.
Passwords are stored only as argon2 hashes; this module is the only place a
plaintext password is ever handled.

This module holds no session state of its own and does not import any UI
framework. A session is a signed token carrying the username and the user's
session_version, signed with KEANEXUS_SECRET, so it needs no server-side
session store, and any change that bumps session_version (password, role,
disable) makes old tokens stop working. Where the token lives is the caller's
business: streamlit_adapter.py keeps it in st.session_state plus a cookie for
the current UI, and the web app keeps it in a cookie only.

Callers re-check the user against the database on every request with
session_user_if_valid() or user_from_session_token(), so a password change,
role change or disable takes effect on open sessions at their next
interaction, not only at next login.
"""

import hashlib
import hmac
import os
import re
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

import db

# The env-var login from before multi-user support. Used once, to create the
# first admin when the users table is empty, so an existing deployment keeps
# working on upgrade without anyone touching the database by hand.
_BOOTSTRAP_USERNAME_ENV = "KEANEXUS_USERNAME"
_BOOTSTRAP_PASSWORD_ENV = "KEANEXUS_PASSWORD"

# Signs the session cookie. Without it, logins still work but do not survive a
# page reload, because an unsigned cookie could be forged by anyone.
_SECRET_ENV = "KEANEXUS_SECRET"  # pragma: allowlist secret

SESSION_COOKIE_NAME = "keanexus_session"
_COOKIE_FIELD_SEPARATOR = "|"
_COOKIE_FIELD_COUNT = 3

ROLE_ADMIN = "admin"
ROLE_VIEWER = "viewer"

MIN_PASSWORD_LENGTH = 12
MIN_USERNAME_LENGTH = 2
MAX_USERNAME_LENGTH = 32
# Letters, digits, dot, underscore and hyphen only. Keeping the cookie field
# separator out of usernames is what lets the cookie be split unambiguously.
_USERNAME_PATTERN = re.compile(rf"^[A-Za-z0-9._-]{{{MIN_USERNAME_LENGTH},{MAX_USERNAME_LENGTH}}}$")

_hasher = PasswordHasher()

# Verifying against a real hash for unknown usernames makes a failed login
# take the same time whether or not the username exists, so login timing
# can't be used to discover valid usernames. Built lazily because hashing
# is deliberately slow and most imports (tests, the quarantine service)
# never log anyone in.
_timing_decoy_hash: Optional[str] = None


class UserManagementError(ValueError):
	"""A requested user change was refused. The message is safe to show in the UI."""


# --- Passwords --------------------------------------------------------------------


def hash_password(password: str) -> str:
	"""Hash a new password after checking it meets the minimum length."""
	_validate_password(password)
	return _hasher.hash(password)


def _password_matches(stored_hash: str, password: str) -> bool:
	try:
		return _hasher.verify(stored_hash, password)
	except (VerificationError, InvalidHashError):
		return False


def _spend_equal_time_on_unknown_user(password: str) -> None:
	global _timing_decoy_hash
	if _timing_decoy_hash is None:
		_timing_decoy_hash = _hasher.hash(
			"timing-decoy-not-a-real-password"
		)  # pragma: allowlist secret
	_password_matches(_timing_decoy_hash, password)


# --- Login and session --------------------------------------------------------------


def bootstrap_admin_from_env() -> bool:
	"""Create the first admin from KEANEXUS_USERNAME / KEANEXUS_PASSWORD.

	Does nothing once any user exists, so editing those env vars later has no
	effect. The minimum password length is not enforced here: the env-var
	password predates the rule, and refusing it would lock the owner out of
	their own upgrade. Returns True if an admin was created.
	"""
	if db.count_users() > 0:
		return False
	username = os.environ.get(_BOOTSTRAP_USERNAME_ENV, "").strip()
	password = os.environ.get(_BOOTSTRAP_PASSWORD_ENV, "")
	if not username or not password:
		return False
	db.insert_user(username, _hasher.hash(password), ROLE_ADMIN)
	return True


def check_credentials(username: str, password: str) -> Optional[dict]:
	"""Return the stored user row if the username and password match, else None.

	Disabled and unknown users get the same None, after the same amount of
	hashing work, as a wrong password. Records the login on success.
	"""
	user = db.get_user(username) if username else None
	if user is None or user["is_disabled"]:
		_spend_equal_time_on_unknown_user(password)
		return None
	if not _password_matches(user["password_hash"], password):
		return None
	db.record_login(user["username"])
	return user


def session_user_if_valid(username: str, session_version: int) -> Optional[dict]:
	"""Return the stored user row if a session for them is still good, else None.

	A session ends when the user is deleted or disabled, or when their
	session_version has moved on (password, role or disable change).
	"""
	stored_user = db.get_user(username)
	if stored_user is None or stored_user["is_disabled"]:
		return None
	if stored_user["session_version"] != session_version:
		return None
	return stored_user


# --- Session token -----------------------------------------------------------------


def _cookie_secret() -> str:
	return os.environ.get(_SECRET_ENV, "")


def _cookie_signature(secret: str, payload: str) -> str:
	return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def sign_session(username: str, session_version: int) -> str:
	"""The signed session token for a user, or "" when KEANEXUS_SECRET is unset.

	Callers must not set a cookie when this returns "", since an unsigned
	token could be forged by anyone.
	"""
	secret = _cookie_secret()
	if not secret:
		return ""
	payload = f"{username}{_COOKIE_FIELD_SEPARATOR}{session_version}"
	return f"{payload}{_COOKIE_FIELD_SEPARATOR}{_cookie_signature(secret, payload)}"


def user_from_session_token(token: Optional[str]) -> Optional[dict]:
	"""Return the stored user row a session token belongs to, or None.

	Rejects a token that is malformed, wrongly signed, or issued before the
	user's latest password, role or disable change.
	"""
	secret = _cookie_secret()
	if not token or not secret:
		return None
	fields = token.split(_COOKIE_FIELD_SEPARATOR)
	if len(fields) != _COOKIE_FIELD_COUNT:
		return None
	username, version_text, signature = fields
	payload = f"{username}{_COOKIE_FIELD_SEPARATOR}{version_text}"
	if not hmac.compare_digest(signature, _cookie_signature(secret, payload)):
		return None
	if not version_text.isdigit():
		return None
	return session_user_if_valid(username, int(version_text))


def public_user(stored_user: dict) -> dict:
	"""The fields callers may hold on to: never the password hash."""
	return {"username": stored_user["username"], "role": stored_user["role"]}


# --- User management (admin actions) ---------------------------------------------
# The UI checks the user's role before offering these. The rules that protect the
# system as a whole (never zero active admins, no disabling yourself) live
# here rather than in ui_settings.py so they are enforced and tested no
# matter which screen calls them.


def _validate_username(username: str) -> None:
	if not _USERNAME_PATTERN.match(username):
		raise UserManagementError(
			f"Usernames are {MIN_USERNAME_LENGTH} to {MAX_USERNAME_LENGTH} characters:"
			" letters, numbers, dot, underscore or hyphen."
		)


def _validate_password(password: str) -> None:
	if len(password) < MIN_PASSWORD_LENGTH:
		raise UserManagementError(f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters.")


def _validate_role(role: str) -> None:
	if role not in db.USER_ROLES:
		raise UserManagementError(f"Unknown role: {role}.")


def _require_user(username: str) -> dict:
	user = db.get_user(username)
	if user is None:
		raise UserManagementError(f"No user named {username}.")
	return user


def _is_last_active_admin(user: dict) -> bool:
	is_active_admin = user["role"] == ROLE_ADMIN and not user["is_disabled"]
	return is_active_admin and db.count_active_admins() <= 1


def create_user(username: str, password: str, role: str) -> None:
	username = username.strip()
	_validate_username(username)
	_validate_role(role)
	if db.get_user(username) is not None:
		raise UserManagementError(f"A user named {username} already exists.")
	db.insert_user(username, hash_password(password), role)


def reset_password(username: str, new_password: str) -> None:
	"""Set a new password. Signs the user out of every open session."""
	_require_user(username)
	db.update_password_hash(username, hash_password(new_password))


def change_role(username: str, role: str) -> None:
	"""Change a user's role. Refuses to demote the last active admin."""
	_validate_role(role)
	user = _require_user(username)
	if role != ROLE_ADMIN and _is_last_active_admin(user):
		raise UserManagementError("You can't remove the last active admin.")
	db.set_user_role(username, role)


def set_disabled(username: str, is_disabled: bool, acting_username: str) -> None:
	"""Disable or re-enable a user.

	Refuses to let an admin disable their own account, or to disable the last
	active admin, since either would leave nobody able to undo it.
	"""
	user = _require_user(username)
	if is_disabled and username.casefold() == acting_username.casefold():
		raise UserManagementError("You can't disable your own account.")
	if is_disabled and _is_last_active_admin(user):
		raise UserManagementError("You can't disable the last active admin.")
	db.set_user_disabled(username, is_disabled)
