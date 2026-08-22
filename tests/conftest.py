# conftest.py - shared pytest fixtures
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

import db


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
	"""Duck-typed stand-in for KeaClient for quarantine_service tests.

	Implements what quarantine_service and the pool sampler actually call —
	lease lookup by hostname for identity resolution, config get/save for the
	Kea deny action, and the pool counters. Avoids mocking httpx for tests
	that don't care about the HTTP transport itself. saved_configs records every save_config call so tests
	can assert on what was actually pushed to Kea.
	"""

	def __init__(
		self,
		leases_by_hostname: Optional[dict[str, list[dict]]] = None,
		dhcp4_config: Optional[dict] = None,
		pool_stats: Optional[dict] = None,
	):
		self._leases_by_hostname = leases_by_hostname or {}
		self._dhcp4_config = (
			dhcp4_config if dhcp4_config is not None else {"subnet4": [{"reservations": []}]}
		)
		self._pool_stats = pool_stats or {}
		self.saved_configs: list[dict] = []

	def get_leases_by_hostname(self, hostname: str) -> list[dict]:
		return self._leases_by_hostname.get(hostname, [])

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

	def get_config(self) -> dict:
		return self._dhcp4_config

	def save_config(self, dhcp4_config: dict) -> None:
		self._dhcp4_config = dhcp4_config
		self.saved_configs.append(dhcp4_config)


@pytest.fixture
def stub_kea_client():
	"""Factory fixture returning the StubKeaClient class itself.

	Tests construct their own instance with whatever leases/config they
	need, e.g. stub_kea_client(leases_by_hostname={...}). Exposed as a
	fixture rather than a plain class import because tests/ has __init__.py
	(making it a package), so pytest registers this module as
	tests.conftest — a bare 'from conftest import StubKeaClient' in other
	test files won't resolve, but fixtures always will.
	"""
	return StubKeaClient


@pytest.fixture
def pihole_http_mock():
	"""Patch pihole.httpx.Client and yield the mock request target."""
	with patch("pihole.httpx.Client") as mock_cls:
		mock_instance = MagicMock()
		mock_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
		mock_cls.return_value.__exit__ = MagicMock(return_value=False)
		yield mock_instance


class StubPiholeClient:
	"""Duck-typed stand-in for PiholeClient for quarantine_service tests.

	Programmed with canned (method, path) -> response JSON via the
	responses dict; unmatched calls return {}. Records every call made
	(including its body) so tests can assert on the exact sequence sent to
	Pi-hole, same spirit as StubKeaClient.saved_configs.
	"""

	def __init__(self, responses: Optional[dict[tuple[str, str], dict]] = None):
		self._responses = responses or {}
		self.calls: list[tuple[str, str, Optional[dict]]] = []

	def request(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
		self.calls.append((method, path, json_body))
		return self._responses.get((method, path), {})


@pytest.fixture
def stub_pihole_client():
	"""Factory fixture returning the StubPiholeClient class itself. See
	stub_kea_client's docstring for why this is a fixture, not a plain import.
	"""
	return StubPiholeClient


@pytest.fixture
def quarantine_client_http_mock():
	"""Patch quarantine_client.httpx.Client and yield the mock POST target."""
	with patch("quarantine_client.httpx.Client") as mock_cls:
		mock_instance = MagicMock()
		mock_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
		mock_cls.return_value.__exit__ = MagicMock(return_value=False)
		yield mock_instance
