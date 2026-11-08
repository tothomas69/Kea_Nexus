"""
test_web_reservations.py — The web UI's Reservations screen. Kea is a stub
whose config lives in memory and whose saves are recorded, so the tests can
check exactly what would reach kea-dhcp4.conf.
"""

import copy
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import db
from kea import KeaError
from tests.test_web_overview import _CONFIG, _HTMX, _SECRET, StubKea, _sign_in
from web import cache, liveness
from web.deps import kea_client
from web.main import create_app
from web.routes.reservations import RESERVATIONS_CHANGED_EVENT

_RESERVED_MAC = "a0:ce:c8:8f:42:e5"


class ConfigKea(StubKea):
	"""The Overview stub with a config that saves change."""

	def __init__(self):
		super().__init__()
		self.config = copy.deepcopy(_CONFIG)
		self.saves = []
		self.refuse_save = False
		self.refuse_read = False

	def get_config(self):
		self._check()
		if self.refuse_read:
			raise KeaError("timed out")
		return copy.deepcopy(self.config)

	def save_config(self, dhcp4_config):
		if self.refuse_save:
			raise KeaError("config-set: bad")
		self.saves.append(dhcp4_config)
		self.config = copy.deepcopy(dhcp4_config)

	def reservations(self):
		return self.config["subnet4"][0]["reservations"]


@pytest.fixture(autouse=True)
def fresh_cache():
	cache.clear()
	liveness.reset()
	yield
	cache.clear()
	liveness.reset()


@pytest.fixture
def kea():
	return ConfigKea()


@pytest.fixture
def client(temp_db, kea):
	with patch.dict(os.environ, {"KEANEXUS_SECRET": _SECRET}):
		app = create_app(start_background_work=False)
		app.dependency_overrides[kea_client] = lambda: kea
		with TestClient(app) as test_client:
			yield test_client


class TestTable:
	def test_lists_reservations_with_labels(self, client):
		_sign_in(client)
		db.upsert_reservation_label(_RESERVED_MAC, "Tom's MacBook")
		page = client.get("/reservations").text
		assert "Tom&#39;s MacBook" in page or "Tom's MacBook" in page
		assert "macbook-pro" in page
		assert 'href="/reservations"' in page

	def test_fragment_and_viewer(self, client):
		_sign_in(client, role="viewer")
		fragment = client.get("/fragments/reservations", headers=_HTMX).text
		assert "172.16.17.177" in fragment
		assert "Edit" not in fragment

	def test_online_column_before_and_after_a_sweep(self, client):
		_sign_in(client)
		page = client.get("/reservations").text
		assert "Not checked yet" in page
		assert "row-online" not in page
		# Only .responding and .checked_at_epoch are read, whatever else a sweep carries.
		sweep = SimpleNamespace(
			responding=frozenset({"172.16.17.177"}), checked_at_epoch=time.time()
		)
		with patch("web.liveness.latest", return_value=sweep):
			page = client.get("/reservations").text
		assert "1 answered" in page
		assert page.count("row-online") == 1

	def test_kea_down(self, client, kea):
		_sign_in(client)
		kea.reachable = False
		page = client.get("/reservations").text
		assert "can&#39;t be reached" in page or "can't be reached" in page


class TestForms:
	def test_new_and_edit(self, client):
		_sign_in(client)
		assert 'hx-post="/reservations"' in client.get("/reservations/new", headers=_HTMX).text
		form = client.get(f"/reservations/{_RESERVED_MAC.upper()}", headers=_HTMX).text
		assert 'value="172.16.17.177"' in form
		assert f'hx-put="/reservations/{_RESERVED_MAC}"' in form

	def test_edit_unknown(self, client):
		_sign_in(client)
		assert client.get("/reservations/aa:aa:aa:aa:aa:aa", headers=_HTMX).status_code == 404


class TestWrites:
	def test_add_writes_kea_then_label(self, client, kea):
		headers = _sign_in(client)
		response = client.post(
			"/reservations",
			data={"mac": "BB-BB-BB-BB-BB-02", "ip": "172.16.17.20", "label": "Printer"},
			headers=headers,
		)
		assert RESERVATIONS_CHANGED_EVENT in response.headers["HX-Trigger"]
		assert {
			"hw-address": "bb:bb:bb:bb:bb:02",
			"ip-address": "172.16.17.20",
		} in kea.reservations()
		assert db.get_reservation_label("bb:bb:bb:bb:bb:02")["label"] == "Printer"

	def test_invalid_not_sent(self, client, kea):
		headers = _sign_in(client)
		response = client.post(
			"/reservations", data={"mac": _RESERVED_MAC, "label": "Dup"}, headers=headers
		)
		assert "already has a reservation" in response.text
		assert kea.saves == []

	def test_kea_refusal_leaves_the_label_alone(self, client, kea):
		headers = _sign_in(client)
		kea.refuse_save = True
		response = client.post(
			"/reservations", data={"mac": "bb:bb:bb:bb:bb:02", "label": "X"}, headers=headers
		)
		assert "Kea refused the change" in response.text
		assert db.get_reservation_label("bb:bb:bb:bb:bb:02") is None

	def test_kea_unreachable_on_save(self, client, kea):
		headers = _sign_in(client)
		kea.refuse_read = True
		response = client.post(
			"/reservations", data={"mac": "bb:bb:bb:bb:bb:02", "label": "X"}, headers=headers
		)
		assert "nothing was saved" in response.text

	def test_edit_reads_fresh_config_and_keeps_drop(self, client, kea):
		"""A DROP class added after the page loaded must survive an edit."""
		headers = _sign_in(client)
		client.get("/reservations")
		kea.reservations()[0]["client-classes"] = ["DROP"]
		client.put(
			f"/reservations/{_RESERVED_MAC}",
			data={"mac": "cc:cc:cc:cc:cc:03", "ip": "", "label": "Moved"},
			headers=headers,
		)
		assert kea.reservations()[0] == {
			"hw-address": "cc:cc:cc:cc:cc:03",
			"client-classes": ["DROP"],
		}
		assert db.get_reservation_label(_RESERVED_MAC) is None
		assert db.get_reservation_label("cc:cc:cc:cc:cc:03")["label"] == "Moved"

	def test_edit_vanished_reservation(self, client, kea):
		headers = _sign_in(client)
		response = client.put(
			"/reservations/aa:aa:aa:aa:aa:aa",
			data={"mac": "aa:aa:aa:aa:aa:aa", "label": "X"},
			headers=headers,
		)
		assert response.status_code == 404

	def test_delete(self, client, kea):
		headers = _sign_in(client)
		db.upsert_reservation_label(_RESERVED_MAC, "Gone")
		response = client.delete(f"/reservations/{_RESERVED_MAC}", headers=headers)
		assert "close-dialog" in response.headers["HX-Trigger"]
		assert kea.reservations() == []
		assert db.get_reservation_label(_RESERVED_MAC) is None

	def test_delete_refuses_a_quarantined_device(self, client, kea):
		headers = _sign_in(client)
		kea.reservations()[0]["client-classes"] = ["DROP"]
		response = client.delete(f"/reservations/{_RESERVED_MAC}", headers=headers)
		assert "Release it on the Quarantine screen first" in response.text
		assert kea.saves == []

	@pytest.mark.parametrize(
		("refuse_read", "message"),
		[(True, "nothing was deleted"), (False, "Kea refused the change")],
	)
	def test_delete_failures(self, client, kea, refuse_read, message):
		headers = _sign_in(client)
		kea.refuse_read = refuse_read
		kea.refuse_save = True
		assert message in client.delete(f"/reservations/{_RESERVED_MAC}", headers=headers).text

	def test_delete_unknown(self, client):
		headers = _sign_in(client)
		assert client.delete("/reservations/aa:aa:aa:aa:aa:aa", headers=headers).status_code == 404

	def test_viewer_refused_everywhere(self, client, kea):
		headers = _sign_in(client, role="viewer")
		assert client.get("/reservations/new", headers=_HTMX).status_code == 403
		assert (
			client.post(
				"/reservations", data={"mac": "bb:bb:bb:bb:bb:02", "label": "X"}, headers=headers
			).status_code
			== 403
		)
		assert client.delete(f"/reservations/{_RESERVED_MAC}", headers=headers).status_code == 403
		assert kea.saves == []

	def test_csrf_required(self, client, kea):
		_sign_in(client)
		assert client.delete(f"/reservations/{_RESERVED_MAC}", headers=_HTMX).status_code == 403
		assert kea.saves == []


class TestActivityRecorded:
	def test_add_and_delete_logged(self, client, kea):
		headers = _sign_in(client)
		client.post(
			"/reservations", data={"mac": "bb:bb:bb:bb:bb:02", "label": "Printer"}, headers=headers
		)
		client.delete("/reservations/bb:bb:bb:bb:bb:02", headers=headers)
		assert [row["summary"] for row in db.get_activity()] == [
			"deleted the reservation for bb:bb:bb:bb:bb:02",
			"added a reservation for Printer (bb:bb:bb:bb:bb:02, no fixed address)",
		]
