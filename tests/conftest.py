# conftest.py - shared pytest fixtures
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

import db


@pytest.fixture(autouse=True)
def no_bootstrap_admin_from_dotenv(monkeypatch):
	"""Remove the first-admin env vars before every test.

	kea.py and others call load_dotenv() on import, so a developer's .env puts
	KEANEXUS_USERNAME / KEANEXUS_PASSWORD into os.environ. The app would then
	create an extra admin at startup, and tests that count admins would fail on
	that machine only. A test that needs these vars sets them itself.
	"""
	monkeypatch.delenv("KEANEXUS_USERNAME", raising=False)
	monkeypatch.delenv("KEANEXUS_PASSWORD", raising=False)


@pytest.fixture
def temp_db(tmp_path):
	"""Redirect all db module operations to a temporary SQLite file."""
	db_path = tmp_path / "test.db"
	with patch.object(db, "_DB_PATH", db_path):
		db.init_db()
		yield db_path


@pytest.fixture
def http_mock():
	"""Patch kea.httpx.Client and yield the mock POST target."""
	with patch("kea.httpx.Client") as mock_cls:
		mock_instance = MagicMock()
		mock_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
		mock_cls.return_value.__exit__ = MagicMock(return_value=False)
		yield mock_instance


class StubKeaClient:
	"""Duck-typed stand-in for KeaClient for the pool sampler's tests: just the
	pool counters, so those tests needn't mock httpx."""

	def __init__(self, pool_stats: Optional[dict] = None):
		self._pool_stats = pool_stats or {}

	def get_pool_stats(self) -> dict:
		"""Mirror KeaClient.get_pool_stats, including its derived `available`.

		Tests pass only the counters they care about; anything omitted
		defaults to zero, and `available` is computed the same way the real
		client computes it rather than being supplied by hand.
		"""
		total = self._pool_stats.get("total", 0)
		assigned = self._pool_stats.get("assigned", 0)
		declined = self._pool_stats.get("declined", 0)
		return {
			"total": total,
			"assigned": assigned,
			"declined": declined,
			"available": max(total - assigned - declined, 0),
			"cumulative": self._pool_stats.get("cumulative", 0),
		}


@pytest.fixture
def stub_kea_client():
	"""Factory fixture returning the StubKeaClient class itself.

	Tests construct their own instance with the counters they need, e.g.
	stub_kea_client(pool_stats={...}). Exposed as a
	fixture rather than a plain class import because tests/ has __init__.py
	(making it a package), so pytest registers this module as
	tests.conftest — a bare 'from conftest import StubKeaClient' in other
	test files won't resolve, but fixtures always will.
	"""
	return StubKeaClient
