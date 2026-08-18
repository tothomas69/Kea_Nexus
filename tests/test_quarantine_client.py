"""
test_quarantine_client.py — Tests for quarantine_client.py.

The client this module talks to (keanexus-quarantine) is a real HTTP
service, so — same reasoning as test_kea.py and test_pihole.py — httpx is
mocked via the quarantine_client_http_mock fixture (conftest.py) rather
than hitting a real service. Responses are built with MagicMock, not real
httpx.Response objects, since a standalone httpx.Response has no attached
request and raise_for_status() requires one.
"""

from typing import Optional
from unittest.mock import MagicMock

import httpx
import pytest

from quarantine_client import QuarantineServiceError, trigger_quarantine, trigger_release


def _make_response(json_data: Optional[dict] = None, content: bytes = b"{}") -> MagicMock:
	resp = MagicMock()
	resp.json.return_value = json_data if json_data is not None else {}
	resp.content = content
	resp.raise_for_status = MagicMock()
	return resp


class TestTriggerQuarantine:
	def test_posts_to_quarantine_endpoint_with_auth_header(
		self, quarantine_client_http_mock, monkeypatch
	):
		monkeypatch.setenv("QUARANTINE_SERVICE_URL", "http://172.16.17.215:8600")
		monkeypatch.setenv("QUARANTINE_SERVICE_TOKEN", "test-token")
		quarantine_client_http_mock.post.return_value = _make_response(
			{"target": "tommy_laptop", "action": "quarantine", "step_results": []}
		)

		trigger_quarantine("tommy_laptop")

		args, kwargs = quarantine_client_http_mock.post.call_args
		assert args[0] == "http://172.16.17.215:8600/quarantine"
		assert kwargs["json"] == {"target": "tommy_laptop", "is_group": False}
		assert kwargs["headers"]["Authorization"] == "Bearer test-token"

	def test_is_group_flag_passed_through(self, quarantine_client_http_mock, monkeypatch):
		monkeypatch.setenv("QUARANTINE_SERVICE_TOKEN", "test-token")
		quarantine_client_http_mock.post.return_value = _make_response({})

		trigger_quarantine("kids", is_group=True)

		_, kwargs = quarantine_client_http_mock.post.call_args
		assert kwargs["json"] == {"target": "kids", "is_group": True}

	def test_returns_parsed_json_body(self, quarantine_client_http_mock, monkeypatch):
		monkeypatch.setenv("QUARANTINE_SERVICE_TOKEN", "test-token")
		quarantine_client_http_mock.post.return_value = _make_response(
			{"action": "quarantine", "step_results": [{"friendly_name": "tommy_laptop"}]}
		)

		result = trigger_quarantine("tommy_laptop")

		assert result["action"] == "quarantine"
		assert result["step_results"][0]["friendly_name"] == "tommy_laptop"

	def test_raises_when_token_not_configured(self, monkeypatch):
		monkeypatch.delenv("QUARANTINE_SERVICE_TOKEN", raising=False)
		with pytest.raises(QuarantineServiceError, match="not configured"):
			trigger_quarantine("tommy_laptop")

	def test_connect_error_raises_quarantine_service_error(
		self, quarantine_client_http_mock, monkeypatch
	):
		monkeypatch.setenv("QUARANTINE_SERVICE_TOKEN", "test-token")
		quarantine_client_http_mock.post.side_effect = httpx.ConnectError("connection refused")

		with pytest.raises(QuarantineServiceError, match="Cannot reach"):
			trigger_quarantine("tommy_laptop")

	def test_http_error_includes_response_detail(self, quarantine_client_http_mock, monkeypatch):
		monkeypatch.setenv("QUARANTINE_SERVICE_TOKEN", "test-token")
		error_response = MagicMock()
		error_response.status_code = 404
		error_response.content = b'{"detail": "No device_registry entry for nobody"}'
		error_response.json.return_value = {"detail": "No device_registry entry for nobody"}

		error_resp = MagicMock()
		error_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
			"404", request=MagicMock(), response=error_response
		)
		quarantine_client_http_mock.post.return_value = error_resp

		with pytest.raises(QuarantineServiceError, match="No device_registry entry"):
			trigger_quarantine("nobody")


class TestTriggerRelease:
	def test_posts_to_release_endpoint(self, quarantine_client_http_mock, monkeypatch):
		monkeypatch.setenv("QUARANTINE_SERVICE_URL", "http://172.16.17.215:8600")
		monkeypatch.setenv("QUARANTINE_SERVICE_TOKEN", "test-token")
		quarantine_client_http_mock.post.return_value = _make_response({"action": "release"})

		trigger_release("tommy_laptop")

		args, _ = quarantine_client_http_mock.post.call_args
		assert args[0] == "http://172.16.17.215:8600/release"
