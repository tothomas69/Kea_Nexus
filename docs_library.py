"""
docs_library.py — The operator-facing articles the Docs sidebar entry offers.

Pure file lookup, no Streamlit, so the allowlist and the read path are
unit-testable. `ui_docs.py` renders what this returns.

Only the articles named in ARTICLES can be read. That is an allowlist, not a
convenience: the alternative — taking a filename from the UI and joining it
onto a directory — is a path-traversal hole in a page that is one login away
from the open internet, and there is no reason to accept arbitrary paths when
the set of articles is known at build time.
"""

from pathlib import Path

_DOCS_DIRECTORY = Path(__file__).resolve().parent / "docs"

# (display title, filename). Operator-facing only — the as-built guide and PRD
# are developer notes and stay out of the app.
ARTICLES: list[tuple[str, str]] = [
	("Siri Shortcut Setup", "siri-shortcut-setup.md"),
	("Quarantine Design", "quarantine-feature-design.md"),
]

_ALLOWED_FILENAMES = {filename for _, filename in ARTICLES}


def available_articles() -> list[tuple[str, str]]:
	"""ARTICLES filtered to those actually present on disk.

	The docs directory is bind-mounted into the container, so a deployment
	that missed the mount would otherwise offer articles that 404 on click.
	Listing only what can be read keeps the menu honest.
	"""
	return [
		(title, filename) for title, filename in ARTICLES if (_DOCS_DIRECTORY / filename).is_file()
	]


def read_article(filename: str) -> str:
	"""Return one allowlisted article's markdown source.

	Raises KeyError for anything not in ARTICLES, and FileNotFoundError if an
	allowlisted article is missing from disk.
	"""
	if filename not in _ALLOWED_FILENAMES:
		raise KeyError(f"{filename!r} is not an available article")
	return (_DOCS_DIRECTORY / filename).read_text(encoding="utf-8")


def article_title(filename: str) -> str:
	"""The display title for an allowlisted article's filename."""
	for title, name in ARTICLES:
		if name == filename:
			return title
	raise KeyError(f"{filename!r} is not an available article")
