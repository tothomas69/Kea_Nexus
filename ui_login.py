"""
ui_login.py — Login page for KeaNexus.
"""
from pathlib import Path

import streamlit as st

from auth import attempt_login


def render_login() -> None:
	"""Render the login page. Calls st.rerun() on successful authentication."""
	_, col, _ = st.columns([1, 1.2, 1])

	with col:
		logo_path = Path(__file__).parent / "static" / "keanexus_logo.png"
		if logo_path.exists():
			st.image(str(logo_path), use_container_width=True)

		st.markdown(
			'<div style="margin:18px 0 10px;border-top:1px solid #d0d7de"></div>',
			unsafe_allow_html=True,
		)

		username = st.text_input("Username", autocomplete="username")
		password = st.text_input("Password", type="password", autocomplete="current-password")

		if st.button("Sign in", type="primary", use_container_width=True):
			if attempt_login(username.strip(), password):
				st.rerun()
			else:
				st.error("Invalid username or password.")
