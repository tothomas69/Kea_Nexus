"""
ui_users.py - Users panel for KeaNexus (Settings tab, admins only).

Lists every login account and lets an admin add users, change a role, set
a new password, and disable or re-enable an account. Every rule that
protects the system (password length, never zero active admins, no
disabling yourself) lives in auth.py, not here, so it is unit-tested and
enforced no matter which screen calls it. This module only collects input
and shows auth.UserManagementError messages, which are written to be safe
to display.

app.py only calls render_users() for admins. Each dialog checks is_admin()
again anyway, because a dialog stays open across reruns and the user's role
could change in another session while it is open.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

import db
from auth import (
	MIN_PASSWORD_LENGTH,
	UserManagementError,
	change_role,
	create_user,
	current_user,
	is_admin,
	reset_password,
	set_disabled,
)

_TH = (
	"font-family:monospace;font-size:11px;font-weight:700;color:#1f2328;"
	"text-transform:uppercase;letter-spacing:.05em;overflow:hidden"
)
_TD = "font-family:monospace;font-size:13px;color:#1f2328"
_TD_MUTED = "font-family:monospace;font-size:13px;color:#6e7681;font-style:italic"

_COL_WIDTHS = [2.2, 1.2, 1.2, 1.8, 0.9]
_COL_HEADERS = ["Username", "Role", "Status", "Last login", ""]

# Same display zone as the Quarantine tab. Storage stays UTC; this is only
# how a timestamp is shown.
_LOCAL_TZ = ZoneInfo("America/New_York")

# Shown after a successful change. st.rerun() wipes anything drawn in the
# current run, so the message is stashed and shown on the next run instead.
_PENDING_NOTICE_KEY = "_users_pending_notice"


# --- Helpers ------------------------------------------------------------------


def _format_local_time(iso_utc: str) -> str:
	"""Show a stored UTC timestamp in local time, or "never" if it is blank."""
	if not iso_utc:
		return "never"
	try:
		moment = datetime.fromisoformat(iso_utc)
	except ValueError:
		# One bad cell should not take down the whole table.
		return iso_utc
	return moment.astimezone(_LOCAL_TZ).strftime("%m/%d %H:%M %Z")


def _role_chip(role: str) -> str:
	"""Coloured chip for a role, so admins stand out in the list."""
	if role == "admin":
		colours = "background:#ddf4ff;color:#0550ae"
	else:
		colours = "background:#f6f8fa;color:#57606a"
	return (
		f'<span style="{colours};font-size:11px;font-weight:600;padding:2px 9px;'
		f'border-radius:10px;display:inline-block;white-space:nowrap">{role}</span>'
	)


def _status_cell(is_disabled: bool) -> str:
	if is_disabled:
		return f'<span style="{_TD_MUTED}">disabled</span>'
	return f'<span style="{_TD}">active</span>'


def _new_password_problem(password: str, confirm: str) -> str:
	"""Return why a typed password can't be used, or "" if it is fine.

	Checked before any change is saved, so a dialog that changes the role and
	the password together never saves one and then refuses the other.
	"""
	if password != confirm:
		return "The two passwords don't match."
	if len(password) < MIN_PASSWORD_LENGTH:
		return f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters."
	return ""


def _finish(notice: str) -> None:
	"""Stash a success message and rerun so the table shows the change."""
	st.session_state[_PENDING_NOTICE_KEY] = notice
	st.rerun()


def _render_pending_notice() -> None:
	notice = st.session_state.pop(_PENDING_NOTICE_KEY, "")
	if notice:
		st.success(notice)


# --- Dialogs ------------------------------------------------------------------


@st.dialog("Add User")
def _add_user_dialog() -> None:
	"""Create a new login account."""
	if not is_admin():
		st.error("Only admins can add users.")
		return
	username = st.text_input(
		"Username *", help="Letters, numbers, dot, underscore or hyphen. Not case-sensitive."
	)
	role = st.selectbox("Role", db.USER_ROLES, index=db.USER_ROLES.index("viewer"))
	password = st.text_input(
		"Password *", type="password", help=f"At least {MIN_PASSWORD_LENGTH} characters."
	)
	confirm = st.text_input("Confirm password *", type="password")
	problem = ""
	save_col, cancel_col = st.columns(2)
	with save_col:
		if st.button("Save", type="primary", key="users_add_save"):
			problem = _add_user(username, password, confirm, role)
	with cancel_col:
		if st.button("Cancel", key="users_add_cancel"):
			st.rerun()
	# Drawn below the buttons, not inside a column, so it gets the full width.
	if problem:
		st.error(problem)


def _add_user(username: str, password: str, confirm: str, role: str) -> str:
	"""Create the user and rerun on success. Returns the problem otherwise."""
	problem = _new_password_problem(password, confirm)
	if problem:
		return problem
	try:
		create_user(username, password, role)
	except UserManagementError as error:
		return str(error)
	_finish(f"Added {username.strip()} as {role}.")
	return ""


def _apply_edit(user: dict, new_role: str, password: str, confirm: str) -> str:
	"""Save a role change and/or a new password. Returns the problem, if any."""
	wants_password_change = bool(password or confirm)
	if wants_password_change:
		problem = _new_password_problem(password, confirm)
		if problem:
			return problem
	username = user["username"]
	try:
		if new_role != user["role"]:
			change_role(username, new_role)
		if wants_password_change:
			reset_password(username, password)
	except UserManagementError as error:
		return str(error)
	_finish(f"Saved changes to {username}.")
	return ""


def _toggle_disabled(user: dict, acting_username: str) -> str:
	"""Disable an active user, or re-enable a disabled one. Returns the problem, if any."""
	disable = not user["is_disabled"]
	try:
		set_disabled(user["username"], disable, acting_username=acting_username)
	except UserManagementError as error:
		return str(error)
	verb = "Disabled" if disable else "Re-enabled"
	_finish(f"{verb} {user['username']}.")
	return ""


@st.dialog("Edit User")
def _edit_user_dialog(user: dict) -> None:
	"""Change one user's role or password, or disable/enable the account."""
	acting_user = current_user()
	if acting_user is None or acting_user["role"] != "admin":
		st.error("Only admins can edit users.")
		return
	is_self = acting_user["username"].casefold() == user["username"].casefold()

	st.markdown(f"**{user['username']}**")
	if is_self:
		# Every role or password change ends the user's open sessions,
		# including this one, so warn before it happens rather than after.
		st.caption("This is your own account. Saving a change signs you out.")

	new_role = st.selectbox("Role", db.USER_ROLES, index=db.USER_ROLES.index(user["role"]))
	st.caption("Leave the password fields blank to keep the current password.")
	password = st.text_input("New password", type="password")
	confirm = st.text_input("Confirm new password", type="password")

	problem = ""
	save_col, toggle_col, cancel_col = st.columns(3)
	with save_col:
		if st.button("Save", type="primary", key="users_edit_save"):
			problem = _apply_edit(user, new_role, password, confirm)
	with toggle_col:
		toggle_label = "Enable" if user["is_disabled"] else "Disable"
		if st.button(toggle_label, key="users_edit_toggle"):
			problem = _toggle_disabled(user, acting_user["username"])
	with cancel_col:
		if st.button("Cancel", key="users_edit_cancel"):
			st.rerun()
	# Drawn below the buttons, not inside a column, so it gets the full width.
	if problem:
		st.error(problem)


# --- Table rendering ----------------------------------------------------------


def _render_table(users: list[dict]) -> None:
	"""CSS-grid header plus one st.columns row per user, matching Reservations."""
	col_template = " ".join(f"{width}fr" for width in _COL_WIDTHS)
	header_cells = "".join(f'<div style="{_TH}">{header}</div>' for header in _COL_HEADERS)
	st.markdown(
		f'<div style="display:grid;grid-template-columns:{col_template};gap:0.4rem;'
		f'border-bottom:2px solid #d0d7de;padding:6px 0 8px;margin-top:12px;margin-bottom:6px">'
		f"{header_cells}</div>",
		unsafe_allow_html=True,
	)
	for row_number, user in enumerate(users, 1):
		cols = st.columns(_COL_WIDTHS)
		cols[0].markdown(f'<span style="{_TD}">{user["username"]}</span>', unsafe_allow_html=True)
		cols[1].markdown(_role_chip(user["role"]), unsafe_allow_html=True)
		cols[2].markdown(_status_cell(bool(user["is_disabled"])), unsafe_allow_html=True)
		cols[3].markdown(
			f'<span style="{_TD}">{_format_local_time(user["last_login_at"])}</span>',
			unsafe_allow_html=True,
		)
		if cols[4].button("Edit", key=f"users_edit_{row_number}_chipblue"):
			_edit_user_dialog(user)


# --- Main render --------------------------------------------------------------


def render_users() -> None:
	"""Render the Users panel: count, + Add button, and the user table."""
	if not is_admin():
		st.error("Only admins can manage users.")
		return

	_render_pending_notice()
	users = db.list_users()

	header_col, button_col = st.columns([5, 1])
	with header_col:
		st.caption(
			f"{len(users)} users. Admins can change everything; viewers can look but not change."
		)
	with button_col:
		if st.button("+ Add", key="users_addtrigger_chipblue"):
			_add_user_dialog()

	_render_table(users)
