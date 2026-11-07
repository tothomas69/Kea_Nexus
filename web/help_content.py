"""
help_content.py — Turns the allowlisted docs into the Help panel's table of
contents and into article HTML.

The table of contents comes from each article's own "## " headings, so it can
never drift from the docs. Anchors use the same slugify the markdown "toc"
extension uses to give headings their ids, so every link lands on its heading.
The articles are our own files from docs_library's allowlist, never user
input, so rendering their markdown as HTML is safe.
"""

import re

import markdown
from markdown.extensions.toc import slugify

from docs_library import article_title, available_articles, read_article

_SECTION_HEADING = re.compile(r"^## +(.+?)\s*$")
_CODE_FENCE = "```"
_ANCHOR_SEPARATOR = "-"
_MARKDOWN_EXTENSIONS = ["fenced_code", "tables", "toc"]


def section_headings(markdown_source: str) -> list[dict]:
	"""The "## " headings of an article as [{"text", "anchor"}], skipping code blocks.

	Code blocks are skipped because the docs contain shell snippets whose
	comment lines start with "#" and would otherwise read as headings.
	"""
	headings = []
	inside_code_block = False
	for line in markdown_source.splitlines():
		if line.strip().startswith(_CODE_FENCE):
			inside_code_block = not inside_code_block
			continue
		if inside_code_block:
			continue
		match = _SECTION_HEADING.match(line)
		if match:
			text = match.group(1)
			headings.append({"text": text, "anchor": slugify(text, _ANCHOR_SEPARATOR)})
	return headings


def table_of_contents() -> list[dict]:
	"""Every available article with its sections, for the Help panel."""
	return [
		{
			"title": title,
			"filename": filename,
			"sections": section_headings(read_article(filename)),
		}
		for title, filename in available_articles()
	]


def article_html(filename: str) -> tuple[str, str]:
	"""(title, HTML body) for one allowlisted article.

	Raises KeyError for anything not on the allowlist, before any file path
	is built (see docs_library.read_article).
	"""
	source = read_article(filename)
	body = markdown.markdown(source, extensions=_MARKDOWN_EXTENSIONS)
	return article_title(filename), body
