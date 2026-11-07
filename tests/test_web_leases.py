"""
test_web_leases.py — The web UI's Leases screen: the table and filter, the
sweep (open to viewers), and adding or editing a lease (admins only).

Kea is the Overview tests' stub with write methods recorded; the quarantine
service's sweep is patched where web/liveness.py calls it.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from kea import KeaError
from quarantine_client import QuarantineServiceError
from tests.test_web_overview import _HTMX, _SECRET, StubKea, _sign_in
from web import cache, liveness
from web.deps import kea_client
from web.main import create_app, liveness_sweep_enabled
from web.routes.leases import LEASES_CHANGED_EVENT

_SWEEP = "web.liveness.trigger_liveness_sweep"


class RecordingKea(StubKea):
	"""The Overview stub plus recorded lease writes, optionally refused by Kea."""

	def __init__(self, refuse=False):
		super().__init__()
		self.refuse = refuse
		self.writes = []

	def add_lease(self, ip, mac, hostname="", valid_lft=86400, subnet_id=1):
		self._write("add", ip, mac, hostname, valid_lft)

	def update_lease(self, ip, mac, hostname="", valid_lft=86400, subnet_id=1):
		self._write("update", ip, mac, hostname, valid_lft)

	def _write(self, *record):
		if self.refuse:
			raise KeaError("address in use")
		self.writes.append(record)


@pytest.fixture(autouse=True)
def fresh_state():
	cache.clear()
	liveness.reset()
	yield
	cache.clear()
	liveness.reset()


@pytest.fixture
def kea():
	return RecordingKea()


@pytest.fixture
def client(temp_db, kea):
	with patch.dict(os.environ, {"KEANEXUS_SECRET": _SECRET}):
		app = create_app(start_background_work=False)
		app.dependency_overrides[kea_client] = lambda: kea
		with TestClient(app) as test_client:
			yield test_client


class TestTable:
	def test_needs_sign_in(self, client):
		assert client.get("/leases", follow_redirects=False).status_code == 303

	def test_lists_leases(self, client):
		_sign_in(client)
		page = client.get("/leases").text
		assert "172.16.17.164" in page and "kids-ipad" in page
		assert "2 leases" in page
		assert "Not checked yet" in page
		assert 'href="/leases"' in page

	def test_filter(self, client):
		_sign_in(client)
		fragment = client.get("/fragments/leases?q=macbook", headers=_HTMX).text
		assert "172.16.17.177" in fragment
		assert "172.16.17.164" not in fragment
		assert "1 of 2 leases match" in fragment

	def test_filter_with_no_match(self, client):
		_sign_in(client)
		assert "No lease matches" in client.get("/fragments/leases?q=zzz", headers=_HTMX).text

	def test_kea_down(self, client, kea):
		_sign_in(client)
		kea.reachable = False
		page = client.get("/leases").text
		assert "Kea can" in page
		assert "Kea has no leases right now" in page

	def test_viewer_has_no_edit_buttons(self, client):
		_sign_in(client, role="viewer")
		page = client.get("/leases").text
		assert "Add lease" not in page
		assert 'hx-get="/leases/172.16.17.164"' not in page
		assert "Check who" in page


class TestSweep:
	def test_viewer_may_sweep_every_lease(self, client):
		headers = _sign_in(client, role="viewer")
		with patch(_SWEEP, return_value=["172.16.17.164"]) as sweep:
			response = client.post("/leases/sweep", data={"q": "macbook"}, headers=headers)
		assert sorted(sweep.call_args.args[0]) == ["172.16.17.164", "172.16.17.177"]
		assert "1 answered" in response.text
		assert "row-online" not in response.text

	def test_online_rows_marked(self, client):
		headers = _sign_in(client)
		with patch(_SWEEP, return_value=["172.16.17.164"]):
			response = client.post("/leases/sweep", headers=headers)
		assert response.text.count("row-online") == 1
		assert "no answer" in response.text

	def test_service_down_is_a_notice(self, client):
		headers = _sign_in(client)
		with patch(_SWEEP, side_effect=QuarantineServiceError("unreachable")):
			response = client.post("/leases/sweep", headers=headers)
		assert response.status_code == 200
		assert "unreachable" in response.text
		assert "Not checked yet" in response.text

	def test_csrf_required(self, client):
		_sign_in(client)
		with patch(_SWEEP) as sweep:
			assert client.post("/leases/sweep", headers=_HTMX).status_code == 403
		sweep.assert_not_called()

	def test_overview_shows_the_online_count(self, client):
		headers = _sign_in(client)
		assert "Online now: not checked yet" in client.get("/").text
		with patch(_SWEEP, return_value=["172.16.17.164"]):
			client.post("/leases/sweep", headers=headers)
		assert "1 online" in client.get("/").text


class TestForms:
	def test_new_form(self, client):
		_sign_in(client)
		form = client.get("/leases/new", headers=_HTMX).text
		assert 'name="ip"' in form and 'value="86400"' in form

	def test_edit_form_prefilled(self, client):
		_sign_in(client)
		form = client.get("/leases/172.16.17.164", headers=_HTMX).text
		assert 'value="f0:18:98:3a:77:10"' in form
		assert 'name="ip"' not in form

	def test_edit_unknown(self, client):
		_sign_in(client)
		assert client.get("/leases/172.16.17.9", headers=_HTMX).status_code == 404

	def test_forms_admin_only(self, client):
		_sign_in(client, role="viewer")
		assert client.get("/leases/new", headers=_HTMX).status_code == 403
		assert client.get("/leases/172.16.17.164", headers=_HTMX).status_code == 403


class TestWrites:
	def test_add(self, client, kea):
		headers = _sign_in(client)
		response = client.post(
			"/leases",
			data={
				"ip": " 172.16.17.40 ",
				"mac": "AA-BB-CC-DD-EE-FF",
				"hostname": "printer",
				"duration": "3600",
			},
			headers=headers,
		)
		assert kea.writes == [("add", "172.16.17.40", "aa:bb:cc:dd:ee:ff", "printer", 3600)]
		assert LEASES_CHANGED_EVENT in response.headers["HX-Trigger"]
		assert "close-dialog" in response.headers["HX-Trigger"]

	def test_add_invalid_is_not_sent(self, client, kea):
		headers = _sign_in(client)
		response = client.post(
			"/leases", data={"ip": "10.0.0.1", "mac": "aa:bb:cc:dd:ee:ff"}, headers=headers
		)
		assert "outside the subnet" in response.text
		assert kea.writes == []

	def test_kea_refusal_shown(self, client):
		headers = _sign_in(client)
		client.app.dependency_overrides[kea_client] = lambda: RecordingKea(refuse=True)
		response = client.post(
			"/leases", data={"ip": "172.16.17.40", "mac": "aa:bb:cc:dd:ee:ff"}, headers=headers
		)
		assert "Kea refused the lease: address in use" in response.text

	def test_update(self, client, kea):
		headers = _sign_in(client)
		client.put(
			"/leases/172.16.17.164",
			data={"mac": "f0:18:98:3a:77:10", "hostname": "kids-ipad", "duration": "7200"},
			headers=headers,
		)
		assert kea.writes == [("update", "172.16.17.164", "f0:18:98:3a:77:10", "kids-ipad", 7200)]

	def test_update_unknown(self, client, kea):
		headers = _sign_in(client)
		response = client.put(
			"/leases/172.16.17.9", data={"mac": "aa:bb:cc:dd:ee:ff"}, headers=headers
		)
		assert response.status_code == 404
		assert kea.writes == []

	def test_viewer_refused(self, client, kea):
		headers = _sign_in(client, role="viewer")
		response = client.post(
			"/leases", data={"ip": "172.16.17.40", "mac": "aa:bb:cc:dd:ee:ff"}, headers=headers
		)
		assert response.status_code == 403
		assert kea.writes == []


class TestLivenessSwitch:
	@pytest.mark.parametrize(
		("switch", "url", "enabled"),
		[
			("on", "http://10.0.0.2:8600", True),
			("off", "http://10.0.0.2:8600", False),
			("on", "", False),
		],
	)
	def test_needs_the_switch_and_a_service(self, switch, url, enabled):
		with patch.dict(
			os.environ, {"KEANEXUS_WEB_LIVENESS_SWEEP": switch, "QUARANTINE_SERVICE_URL": url}
		):
			assert liveness_sweep_enabled() is enabled

	def test_lifespan_starts_the_loop(self, temp_db):
		with (
			patch.dict(os.environ, {"KEANEXUS_SECRET": _SECRET}),
			patch("web.main.start_sweep_loop") as start,
		):
			with TestClient(create_app(start_background_work=False, start_liveness_sweep=True)):
				pass
		start.assert_called_once()
