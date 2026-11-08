"""
test_web_maintenance.py — The web UI's Maintenance screen. Kea is a stub that
records every write, so each test checks exactly what would reach Kea.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from kea import KeaError
from tests.test_web_overview import _HTMX, _LEASES, _SECRET, StubKea, _sign_in
from web import cache
from web.deps import kea_client
from web.main import create_app
from web.routes.maintenance import MAINTENANCE_CHANGED_EVENT

_DECLINED = {
	"ip-address": "172.16.17.233",
	"hw-address": "de:ad:be:ef:00:01",
	"state": 1,
	"cltt": 0,
	"valid-lft": 1,
}


class MaintenanceKea(StubKea):
	def __init__(self):
		super().__init__()
		self.dhcp_enabled = True
		self.status_refused = False
		self.refuse_writes = False
		self.writes = []
		self.leases = [*_LEASES, _DECLINED]

	def get_leases(self):
		self._check()
		return self.leases

	def get_dhcp_enabled(self):
		if self.status_refused:
			raise KeaError("down")
		return self.dhcp_enabled

	def _write(self, *record):
		if self.refuse_writes:
			raise KeaError("refused")
		self.writes.append(record)

	def disable_dhcp(self, max_period=0):
		self._write("disable", max_period)

	def enable_dhcp(self):
		self._write("enable")

	def delete_lease(self, ip):
		self._write("delete", ip)

	def wipe_leases(self, subnet_id=1):
		self._write("wipe", subnet_id)
		return 3


@pytest.fixture(autouse=True)
def fresh_cache():
	cache.clear()
	yield
	cache.clear()


@pytest.fixture
def kea():
	return MaintenanceKea()


@pytest.fixture
def client(temp_db, kea):
	with patch.dict(os.environ, {"KEANEXUS_SECRET": _SECRET}):
		app = create_app(start_background_work=False)
		app.dependency_overrides[kea_client] = lambda: kea
		with TestClient(app) as test_client:
			yield test_client


class TestPage:
	def test_shows_state_declined_and_lease_count(self, client):
		_sign_in(client)
		page = client.get("/maintenance").text
		assert "On: handing out" in page
		assert "172.16.17.233" in page
		assert "Deletes all 3 leases" in page
		assert 'href="/maintenance"' in page

	@pytest.mark.parametrize(
		("enabled", "refused", "text"),
		[
			(False, False, "Off: no device"),
			(None, False, "doesn&#39;t report"),
			(True, True, "isn&#39;t answering"),
		],
	)
	def test_states(self, client, kea, enabled, refused, text):
		_sign_in(client)
		kea.dhcp_enabled, kea.status_refused = enabled, refused
		page = client.get("/fragments/maintenance", headers=_HTMX).text
		assert text in page or text.replace("&#39;", "'") in page

	def test_clean_pool_and_kea_down(self, client, kea):
		_sign_in(client)
		kea.leases = _LEASES
		assert "The pool is clean" in client.get("/maintenance").text
		kea.reachable = False
		cache.clear()
		assert "Kea can" in client.get("/fragments/maintenance", headers=_HTMX).text


class TestDhcp:
	def test_disable_with_timer(self, client, kea):
		headers = _sign_in(client)
		assert "Turn DHCP off" in client.get("/maintenance/dhcp/disable", headers=_HTMX).text
		response = client.post(
			"/maintenance/dhcp/disable", data={"seconds": "600"}, headers=headers
		)
		assert kea.writes == [("disable", 600)]
		assert MAINTENANCE_CHANGED_EVENT in response.headers["HX-Trigger"]

	def test_disable_bad_timer_not_sent(self, client, kea):
		headers = _sign_in(client)
		response = client.post(
			"/maintenance/dhcp/disable", data={"seconds": "99999"}, headers=headers
		)
		assert "between" in response.text
		assert kea.writes == []

	def test_disable_refused(self, client, kea):
		headers = _sign_in(client)
		kea.refuse_writes = True
		assert (
			"Kea refused"
			in client.post("/maintenance/dhcp/disable", data={"seconds": "0"}, headers=headers).text
		)

	@pytest.mark.parametrize(
		("refuse", "text"), [(False, "DHCP is on again"), (True, "Kea refused")]
	)
	def test_enable(self, client, kea, refuse, text):
		headers = _sign_in(client)
		kea.refuse_writes = refuse
		assert text in client.post("/maintenance/dhcp/enable", headers=headers).text


class TestDeclined:
	def test_clear_all(self, client, kea):
		headers = _sign_in(client)
		assert (
			"Cleared 1 declined lease."
			in client.post("/maintenance/declined/clear", headers=headers).text
		)
		assert kea.writes == [("delete", "172.16.17.233")]

	def test_clear_one(self, client, kea):
		headers = _sign_in(client)
		client.post("/maintenance/declined/172.16.17.233/clear", headers=headers)
		assert kea.writes == [("delete", "172.16.17.233")]

	def test_clear_one_refuses_a_live_lease(self, client, kea):
		headers = _sign_in(client)
		assert (
			client.post("/maintenance/declined/172.16.17.164/clear", headers=headers).status_code
			== 404
		)
		assert kea.writes == []

	def test_clear_failure_reported(self, client, kea):
		headers = _sign_in(client)
		kea.refuse_writes = True
		assert (
			"weren&#39;t cleared"
			in client.post("/maintenance/declined/clear", headers=headers).text
		)


class TestWipe:
	def test_needs_the_word(self, client, kea):
		headers = _sign_in(client)
		assert "Wipe all 3 leases" in client.get("/maintenance/wipe", headers=_HTMX).text
		response = client.post("/maintenance/wipe", data={"confirm": "wipe"}, headers=headers)
		assert "Nothing was wiped" in response.text
		assert kea.writes == []

	def test_wipes_the_configured_subnet(self, client, kea):
		headers = _sign_in(client)
		response = client.post("/maintenance/wipe", data={"confirm": "WIPE"}, headers=headers)
		assert kea.writes == [("wipe", 1)]
		assert "leases-changed" in response.headers["HX-Trigger"]

	def test_refused(self, client, kea):
		headers = _sign_in(client)
		kea.refuse_writes = True
		assert (
			"Kea refused"
			in client.post("/maintenance/wipe", data={"confirm": "WIPE"}, headers=headers).text
		)


class TestAccess:
	@pytest.mark.parametrize(
		("method", "path"),
		[
			("get", "/maintenance/dhcp/disable"),
			("post", "/maintenance/dhcp/disable"),
			("post", "/maintenance/dhcp/enable"),
			("post", "/maintenance/declined/clear"),
			("post", "/maintenance/declined/172.16.17.233/clear"),
			("get", "/maintenance/wipe"),
			("post", "/maintenance/wipe"),
		],
	)
	def test_viewer_refused(self, client, kea, method, path):
		headers = _sign_in(client, role="viewer")
		response = getattr(client, method)(
			path,
			headers=headers,
			**({"data": {"confirm": "WIPE", "seconds": "0"}} if method == "post" else {}),
		)
		assert response.status_code == 403
		assert kea.writes == []

	def test_csrf(self, client, kea):
		_sign_in(client)
		assert (
			client.post("/maintenance/wipe", data={"confirm": "WIPE"}, headers=_HTMX).status_code
			== 403
		)
		assert kea.writes == []

	def test_viewer_sees_state(self, client):
		_sign_in(client, role="viewer")
		assert "can't make changes" in client.get("/maintenance").text
