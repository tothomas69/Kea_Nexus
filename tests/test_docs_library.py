"""
test_docs_library.py — Tests for docs_library.py's article allowlist and reads.

The allowlist is the security boundary here: `ui_docs.py` hands a filename
straight from a UI control into `read_article`, so anything that is not an
explicitly listed article must be refused rather than resolved against the
docs directory.
"""

import pytest

import docs_library


class TestArticleList:
	def test_every_listed_article_exists_on_disk(self):
		"""A title in the menu that cannot be opened is worse than no menu."""
		missing = [
			filename
			for _, filename in docs_library.ARTICLES
			if not (docs_library._DOCS_DIRECTORY / filename).is_file()
		]
		assert not missing, f"ARTICLES names files that do not exist: {missing}"

	def test_available_articles_matches_the_list_when_all_present(self):
		assert docs_library.available_articles() == docs_library.ARTICLES

	def test_available_articles_hides_a_missing_file(self, monkeypatch, tmp_path):
		"""A deployment that missed the docs bind-mount should offer nothing,
		not entries that fail on click."""
		monkeypatch.setattr(docs_library, "_DOCS_DIRECTORY", tmp_path)
		assert docs_library.available_articles() == []

	def test_developer_docs_are_not_exposed(self):
		"""The as-built guide and PRD are internal notes, deliberately absent."""
		listed = {filename for _, filename in docs_library.ARTICLES}
		assert "as-built-project-guide.md" not in listed
		assert "prd.md" not in listed


class TestReadArticle:
	@pytest.mark.parametrize("filename", [name for _, name in docs_library.ARTICLES])
	def test_reads_each_listed_article(self, filename):
		assert docs_library.read_article(filename).strip()

	@pytest.mark.parametrize(
		"filename",
		[
			"../.env",
			"../../etc/passwd",
			"/etc/passwd",
			"as-built-project-guide.md",
			"siri-shortcut-setup.md/../../.env",
			"",
		],
	)
	def test_rejects_anything_not_allowlisted(self, filename):
		"""Path traversal and unlisted files alike — the allowlist is checked
		before any path is built, so nothing outside ARTICLES is reachable."""
		with pytest.raises(KeyError):
			docs_library.read_article(filename)


class TestArticleTitle:
	def test_returns_the_display_title(self):
		assert docs_library.article_title("siri-shortcut-setup.md") == "Siri Shortcut Setup"

	def test_rejects_an_unlisted_filename(self):
		with pytest.raises(KeyError):
			docs_library.article_title("prd.md")
