"""
ui_access.py - Shared read-only handling for viewer sessions.

Two layers, on purpose:

1. Buttons that change things are drawn disabled for viewers
   (`disabled=read_only()`), and each tab shows read_only_notice(), so a
   viewer can see why nothing responds.
2. Each write path checks again before writing. Kea writes are refused by
   streamlit_adapter.get_client(), which gives viewers a ReadOnlyKeaClient. Writes
   that don't go through Kea (SQLite rows, the quarantine service) call
   refuse_if_viewer() first.

Layer 1 is only for the user. A disabled button is drawn by the browser, and
nothing stops a crafted request from claiming it was clicked, so layer 2 is
what actually protects the network.
"""

import streamlit as st

from kea import VIEWER_WRITE_REFUSED_MESSAGE
from streamlit_adapter import is_admin


def read_only() -> bool:
	"""True when this session may look but not change anything."""
	return not is_admin()


def read_only_notice() -> None:
	"""One line at the top of a tab, shown to viewers only."""
	if read_only():
		st.caption("Read-only: your account can view this tab but not change it.")


def refuse_if_viewer() -> bool:
	"""Call before any non-Kea write. Shows the refusal and returns True for viewers.

	Usage: `if refuse_if_viewer(): return`
	"""
	if not read_only():
		return False
	st.error(VIEWER_WRITE_REFUSED_MESSAGE)
	return True
