"""
test_quarantine_auth.py — Tests for quarantine_service/auth.py.
"""

import pytest
from fastapi import HTTPException

from quarantine_service.auth import require_bearer_token


class TestRequireBearerToken:
	def test_raises_500_when_token_not_configured(self, monkeypatch):
		monkeypatch.delenv("QUARANTINE_API_TOKEN", raising=False)
		with pytest.raises(HTTPException) as exc_info:
			require_bearer_token("Bearer anything")
		assert exc_info.value.status_code == 500

	def test_raises_401_when_header_missing(self, monkeypatch):
		monkeypatch.setenv("QUARANTINE_API_TOKEN", "secret-token")
		with pytest.raises(HTTPException) as exc_info:
			require_bearer_token("")
		assert exc_info.value.status_code == 401

	def test_raises_401_when_header_malformed(self, monkeypatch):
		monkeypatch.setenv("QUARANTINE_API_TOKEN", "secret-token")
		with pytest.raises(HTTPException) as exc_info:
			require_bearer_token("secret-token")  # missing "Bearer " prefix
		assert exc_info.value.status_code == 401

	def test_raises_401_when_token_incorrect(self, monkeypatch):
		monkeypatch.setenv("QUARANTINE_API_TOKEN", "secret-token")
		with pytest.raises(HTTPException) as exc_info:
			require_bearer_token("Bearer wrong-token")
		assert exc_info.value.status_code == 401

	def test_passes_when_token_correct(self, monkeypatch):
		monkeypatch.setenv("QUARANTINE_API_TOKEN", "secret-token")
		require_bearer_token("Bearer secret-token")  # must not raise
