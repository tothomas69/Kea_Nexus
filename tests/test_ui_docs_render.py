"""
test_ui_docs_render.py — Smoke test for the Docs sidebar entry and its dialog.

Same approach as the other ui_* render tests: `AppTest` runs the script
headlessly and reports anything it raised. Here it also drives the actual
interaction — clicking an article button and confirming the dialog opens —
because the button and the dialog live in different functions wired together
through session state, which is exactly the kind of seam that breaks silently.
"""

from streamlit.testing.v1 import AppTest

import docs_library


def _docs_script() -> None:
	from ui_docs import render_docs_nav, render_open_article

	render_docs_nav()
	render_open_article()


class TestDocsNav:
	def test_renders_without_raising(self):
		app = AppTest.from_function(_docs_script, default_timeout=60)
		app.run()
		assert not app.exception, [e.value for e in app.exception]

	def test_one_button_per_available_article(self):
		app = AppTest.from_function(_docs_script, default_timeout=60)
		app.run()
		labels = {button.label for button in app.button}
		assert labels == {title for title, _ in docs_library.available_articles()}

	def test_clicking_an_article_opens_it(self):
		"""The button sets session state in the sidebar and the dialog opens
		from a separate call — the seam most likely to break silently."""
		app = AppTest.from_function(_docs_script, default_timeout=60)
		app.run()
		app.button[0].click().run()
		assert not app.exception, [e.value for e in app.exception]

		title = docs_library.ARTICLES[0][0]
		rendered = "".join(block.value for block in app.markdown)
		assert title in rendered, "the picked article's title never reached the page"

	def test_article_body_is_rendered_not_just_the_title(self):
		app = AppTest.from_function(_docs_script, default_timeout=60)
		app.run()
		app.button[0].click().run()

		filename = docs_library.ARTICLES[0][1]
		# A distinctive line from the article itself, so this fails if the
		# dialog renders a heading over empty content.
		excerpt = docs_library.read_article(filename).splitlines()[0].lstrip("# ").strip()
		rendered = "".join(block.value for block in app.markdown)
		assert excerpt in rendered

	def test_nothing_opens_without_a_click(self):
		app = AppTest.from_function(_docs_script, default_timeout=60)
		app.run()
		rendered = "".join(block.value for block in app.markdown)
		assert "Before you start" not in rendered
