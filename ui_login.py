"""
ui_login.py — Login page for KeaNexus.
"""

from pathlib import Path

import extra_streamlit_components as stx
import streamlit as st

from auth import SESSION_COOKIE_NAME
from streamlit_adapter import attempt_login, session_token

# Rolling expiry for the session cookie — the CookieManager component always
# attaches an expiry (no true zero-expiry "clears on browser close" option),
# so this is the closest practical approximation. See auth.py's docstring.
_SESSION_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24

# The cookie is written on the run after login, not during it. CookieManager
# only writes a cookie when its component actually renders in the browser,
# and the st.rerun() that follows a successful login discards that run's
# output first, so a cookie set right before the rerun never reached the
# browser and every reload logged the user out.
_PENDING_COOKIE_KEY = "_session_cookie_pending"


def write_pending_session_cookie(cookie_manager: stx.CookieManager) -> None:
	"""Write the session cookie once, on the first full page run after login.

	Called from app.py's main() after the authentication gate. An empty token
	means KEANEXUS_SECRET is unset: the login still works for this session, it
	just won't survive a page reload.
	"""
	if not st.session_state.pop(_PENDING_COOKIE_KEY, False):
		return
	token = session_token()
	if not token:
		return
	cookie_manager.set(SESSION_COOKIE_NAME, token, max_age=_SESSION_COOKIE_MAX_AGE_SECONDS)


def render_login(cookie_manager: stx.CookieManager) -> None:
	"""Render the login page. Calls st.rerun() on successful authentication."""
	_, col, _ = st.columns([1, 1.2, 1])

	with col:
		logo_path = Path(__file__).parent / "static" / "keanexus_logo.png"
		if logo_path.exists():
			st.image(str(logo_path), width="stretch")

		st.markdown(
			'<div style="margin:18px 0 10px;border-top:1px solid #d0d7de"></div>',
			unsafe_allow_html=True,
		)

		username = st.text_input("Username", autocomplete="username")
		password = st.text_input("Password", type="password", autocomplete="current-password")

		if st.button("Sign in", type="primary", width="stretch"):
			if attempt_login(username.strip(), password):
				st.session_state[_PENDING_COOKIE_KEY] = True
				st.rerun()
			else:
				st.error("Invalid username or password.")
