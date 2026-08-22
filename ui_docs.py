"""
ui_docs.py — Docs entry in the sidebar, and the dialog that shows an article.

The sidebar is 210px wide, which is enough for a list of article titles and
nowhere near enough for the articles themselves — these have code blocks and
tables that would wrap to nonsense at that width. So the sidebar holds only
navigation, and the article opens in a wide modal over the page.

Rendering only; the allowlist and file read live in `docs_library.py`.
"""

import streamlit as st

from docs_library import article_title, available_articles, read_article

# Which article the reader picked, if any. Held in session state because the
# button that sets it lives in the sidebar and the dialog is opened from
# main() — the two cannot call each other directly.
_OPEN_ARTICLE_KEY = "docs_open_article"


@st.dialog("Documentation", width="large")
def _article_dialog(filename: str) -> None:
	st.markdown(f"### {article_title(filename)}")
	try:
		st.markdown(read_article(filename))
	except (KeyError, FileNotFoundError) as exc:
		# UI components surface their own errors rather than letting an
		# unreadable file take the whole page down.
		st.error(f"Could not open that article: {exc}")


def render_docs_nav() -> None:
	"""The sidebar's Docs section: one button per available article."""
	articles = available_articles()
	if not articles:
		return

	with st.sidebar.expander("Docs"):
		for title, filename in articles:
			if st.button(title, key=f"docs_open_{filename}", width="stretch"):
				st.session_state[_OPEN_ARTICLE_KEY] = filename
				st.rerun()


def render_open_article() -> None:
	"""Open the dialog for whichever article was picked, then clear it.

	Cleared on read so the dialog does not reopen on the next rerun — closing
	it is the reader dismissing the modal, and Streamlit gives no callback for
	that.
	"""
	filename = st.session_state.pop(_OPEN_ARTICLE_KEY, None)
	if filename:
		_article_dialog(filename)
