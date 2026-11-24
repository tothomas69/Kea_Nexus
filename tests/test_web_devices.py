"""
test_web_devices.py — The web UI's Devices screen: the registry table and its
add, edit and delete dialog. Kea is the Overview tests' stub.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import db
from tests.test_web_overview import _HTMX, _SECRET, StubKea, _sign_in
from web import cache, liveness
from web.deps import kea_client
from web.main import create_app


@pytest.fixture(autouse=True)
def fresh_state():
	cache.clear()
	liveness.reset()
	yield
	cache.clear()
	liveness.reset()


@pytest.fixture
def client(temp_db):
	with patch.dict(os.environ, {"KEANEXUS_SECRET": _SECRET}):
		app = create_app(start_background_work=False)
		app.dependency_overrides[kea_client] = lambda: StubKea()
		with TestClient(app) as test_client:
			yield test_client


def _register(name="kids_ipad", hostname="kids-ipad", **fields):
	db.upsert_device(name, hostname, **fields)


class TestPage:
	def test_needs_sign_in(self, client):
		assert client.get("/devices", follow_redirects=False).status_code == 303

	def test_lists_devices_with_their_live_lease(self, client):
		_sign_in(client)
		_register(group_tag="kids")
		page = client.get("/devices").text
		assert "kids_ipad" in page
		assert "172.16.17.164" in page  # from the stub's lease for kids-ipad
		assert "f0:18:98:3a:77:10" in page
		assert '<h3 class="subhead">Groups</h3>' in page

	def test_online_column_follows_the_sweep(self, client):
		_sign_in(client)
		_register()
		with patch("web.liveness.arp_sweep", return_value={"172.16.17.164": "f0:18:98:3a:77:10"}):
			liveness.sweep_now(["172.16.17.164"])
		assert 'class="row-online"' in client.get("/devices").text

	def test_nav_says_devices_not_quarantine(self, client):
		_sign_in(client)
		page = client.get("/devices").text
		assert 'href="/devices"' in page
		assert "quarantine" not in page.lower()

	def test_old_quarantine_urls_are_gone(self, client):
		headers = _sign_in(client)
		_register()
		assert client.get("/quarantine").status_code == 404
		assert client.post("/devices/kids_ipad/quarantine", headers=headers).status_code in (
			404,
			405,
		)
		assert client.post("/quarantine/kids_ipad", headers=headers).status_code == 404

	def test_viewer_sees_but_cannot_edit(self, client):
		_sign_in(client, role="viewer")
		_register()
		page = client.get("/devices").text
		assert "Add device" not in page
		assert "disabled" in page

	def test_fragment_polls_and_listens(self, client):
		_sign_in(client)
		text = client.get("/fragments/devices", headers=_HTMX).text
		assert 'id="registry"' in text
		assert "every 30s, devices-changed from:body" in text


class TestDialog:
	def test_new_form_offers_live_hostnames(self, client):
		_sign_in(client)
		text = client.get("/devices/new", headers=_HTMX).text
		assert '<option value="kids-ipad"' in text

	def test_create(self, client):
		headers = _sign_in(client)
		response = client.post(
			"/devices",
			data={"friendly_name": "kids_ipad", "hostname": "kids-ipad", "group_tag": "kids"},
			headers=headers,
		)
		assert response.headers["HX-Trigger"] == "devices-changed, close-dialog"
		assert db.get_device("kids_ipad")["group_tag"] == "kids"
		assert "registered kids_ipad" in [row["summary"] for row in db.get_activity()]

	def test_create_validation_re_renders_the_form(self, client):
		headers = _sign_in(client)
		response = client.post(
			"/devices", data={"friendly_name": "bad name", "hostname": "kids-ipad"}, headers=headers
		)
		assert "letters, digits" in response.text
		assert db.get_devices() == []

	def test_edit(self, client):
		headers = _sign_in(client)
		_register()
		client.put(
			"/devices/kids_ipad",
			data={"hostname": "kids-ipad", "group_tag": "iot", "notes": "hall"},
			headers=headers,
		)
		device = db.get_device("kids_ipad")
		assert (device["group_tag"], device["notes"]) == ("iot", "hall")

	def test_delete(self, client):
		headers = _sign_in(client)
		_register()
		response = client.delete("/devices/kids_ipad", headers=headers)
		assert "close-dialog" in response.headers["HX-Trigger"]
		assert db.get_device("kids_ipad") is None

	def test_unknown_device_is_not_found(self, client):
		headers = _sign_in(client)
		assert client.get("/devices/nobody", headers=_HTMX).status_code == 404
		assert client.delete("/devices/nobody", headers=headers).status_code == 404

	@pytest.mark.parametrize(
		("method", "path"),
		[("post", "/devices"), ("put", "/devices/kids_ipad"), ("delete", "/devices/kids_ipad")],
	)
	def test_viewer_cannot_write(self, client, method, path):
		headers = _sign_in(client, role="viewer")
		_register()
		data = {"friendly_name": "x", "hostname": "kids-ipad"} if method != "delete" else None
		kwargs = {"data": data} if data else {}
		assert getattr(client, method)(path, headers=headers, **kwargs).status_code == 403
		assert [device["friendly_name"] for device in db.get_devices()] == ["kids_ipad"]
