# conftest.py - shared pytest fixtures
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
