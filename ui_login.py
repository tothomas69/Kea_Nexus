"""
ui_login.py — Login page for KeaNexus.
"""

from pathlib import Path

import extra_streamlit_components as stx
import streamlit as st

from auth import SESSION_COOKIE_NAME, attempt_login, session_token

# Rolling expiry for the session cookie — the CookieManager component always
# attaches an expiry (no true zero-expiry "clears on browser close" option),
# so this is the closest practical approximation. See auth.py's docstring.
_SESSION_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24


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
				cookie_manager.set(
					SESSION_COOKIE_NAME, session_token(), max_age=_SESSION_COOKIE_MAX_AGE_SECONDS
				)
				st.rerun()
			else:
				st.error("Invalid username or password.")
