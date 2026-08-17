"""
test_pihole.py — Tests for pihole.py PiholeClient.
All network calls are intercepted via the pihole_http_mock fixture
(conftest.py). Responses are built with MagicMock, not real httpx.Response
objects — a standalone httpx.Response has no attached request, and
raise_for_status() requires one, same reasoning test_kea.py follows.
"""

import time
from typing import Optional
from unittest.mock import MagicMock

import httpx
import pytest

from pihole import PiholeClient, PiholeError


def _make_response(json_data: Optional[dict] = None, content: bytes = b"{}") -> MagicMock:
	resp = MagicMock()
	resp.json.return_value = json_data if json_data is not None else {}
	resp.content = content
	resp.raise_for_status = MagicMock()
	return resp


def _auth_response(sid: str = "sid-123", csrf: str = "csrf-456", validity: int = 300) -> MagicMock:
	return _make_response(
		{"session": {"valid": True, "sid": sid, "csrf": csrf, "validity": validity}}
	)


class TestAuthenticate:
	def test_caches_sid_and_csrf_after_first_call(self, pihole_http_mock, monkeypatch):
		monkeypatch.setenv("PIHOLE_API_PASSWORD", "test-password")
		pihole_http_mock.post.return_value = _auth_response()
		pihole_http_mock.request.return_value = _make_response({"groups": []})

		client = PiholeClient()
		client.request("GET", "/groups")

		assert client._sid == "sid-123"
		assert client._csrf == "csrf-456"
		assert pihole_http_mock.post.call_count == 1

	def test_reuses_session_within_validity_window(self, pihole_http_mock, monkeypatch):
		monkeypatch.setenv("PIHOLE_API_PASSWORD", "test-password")
		pihole_http_mock.post.return_value = _auth_response()
		pihole_http_mock.request.return_value = _make_response({"groups": []})

		client = PiholeClient()
		client.request("GET", "/groups")
		client.request("GET", "/groups")

		assert pihole_http_mock.post.call_count == 1  # only authenticated once

	def test_reauthenticates_after_expiry(self, pihole_http_mock, monkeypatch):
		monkeypatch.setenv("PIHOLE_API_PASSWORD", "test-password")
		pihole_http_mock.post.return_value = _auth_response()
		pihole_http_mock.request.return_value = _make_response({"groups": []})

		client = PiholeClient()
		client.request("GET", "/groups")
		client._sid_expires_at = time.time() - 1  # force expiry
		client.request("GET", "/groups")

		assert pihole_http_mock.post.call_count == 2

	def test_raises_on_invalid_credentials(self, pihole_http_mock, monkeypatch):
		monkeypatch.setenv("PIHOLE_API_PASSWORD", "wrong-password")
		pihole_http_mock.post.return_value = _make_response(
			{"session": {"valid": False, "message": "invalid password"}}
		)

		client = PiholeClient()
		with pytest.raises(PiholeError):
			client.request("GET", "/groups")

	def test_connect_error_raises_pihole_error(self, pihole_http_mock, monkeypatch):
		monkeypatch.setenv("PIHOLE_API_PASSWORD", "test-password")
		pihole_http_mock.post.side_effect = httpx.ConnectError("connection refused")

		client = PiholeClient()
		with pytest.raises(PiholeError, match="Cannot reach Pi-hole"):
			client.request("GET", "/groups")


class TestRequest:
	def test_get_does_not_include_csrf_header(self, pihole_http_mock, monkeypatch):
		monkeypatch.setenv("PIHOLE_API_PASSWORD", "test-password")
		pihole_http_mock.post.return_value = _auth_response()
		pihole_http_mock.request.return_value = _make_response({"groups": []})

		client = PiholeClient()
		client.request("GET", "/groups")

		_, kwargs = pihole_http_mock.request.call_args
		assert "X-FTL-SID" in kwargs["headers"]
		assert "X-FTL-CSRF" not in kwargs["headers"]

	def test_post_includes_csrf_header(self, pihole_http_mock, monkeypatch):
		monkeypatch.setenv("PIHOLE_API_PASSWORD", "test-password")
		pihole_http_mock.post.return_value = _auth_response()
		pihole_http_mock.request.return_value = _make_response({})

		client = PiholeClient()
		client.request("POST", "/groups", json_body={"name": "test"})

		_, kwargs = pihole_http_mock.request.call_args
		assert kwargs["headers"]["X-FTL-CSRF"] == "csrf-456"

	def test_returns_empty_dict_for_empty_response(self, pihole_http_mock, monkeypatch):
		monkeypatch.setenv("PIHOLE_API_PASSWORD", "test-password")
		pihole_http_mock.post.return_value = _auth_response()
		pihole_http_mock.request.return_value = _make_response(content=b"")

		client = PiholeClient()
		assert client.request("DELETE", "/clients/172.16.17.50") == {}

	def test_http_status_error_raises_pihole_error(self, pihole_http_mock, monkeypatch):
		monkeypatch.setenv("PIHOLE_API_PASSWORD", "test-password")
		pihole_http_mock.post.return_value = _auth_response()

		error_resp = MagicMock()
		error_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
			"500 Internal Server Error",
			request=MagicMock(),
			response=MagicMock(status_code=500, text="internal error"),
		)
		pihole_http_mock.request.return_value = error_resp

		client = PiholeClient()
		with pytest.raises(PiholeError, match="HTTP 500"):
			client.request("GET", "/groups")
