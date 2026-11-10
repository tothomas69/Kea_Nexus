"""
test_web_leases.py — The web UI's Leases screen: the table and filter, the
sweep (open to viewers), and adding, editing or removing expired leases
(admins only).

Kea is the Overview tests' stub with write methods recorded; the quarantine
service's sweep is patched where web/liveness.py calls it.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import db
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

	def __init__(self, refuse=False, reachable=True):
		super().__init__(reachable=reachable)
		self.refuse = refuse
		self.refuse_deletes = set()
		self.writes = []

	def add_lease(self, ip, mac, hostname="", valid_lft=86400, subnet_id=1):
		self._write("add", ip, mac, hostname, valid_lft)

	def update_lease(self, ip, mac, hostname="", valid_lft=86400, subnet_id=1):
		self._write("update", ip, mac, hostname, valid_lft)

	def delete_lease(self, ip):
		if ip in self.refuse_deletes:
			raise KeaError("lease locked")
		self.writes.append(("delete", ip))

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
	def test_viewer_may_sweep_the_whole_subnet(self, client):
		headers = _sign_in(client, role="viewer")
		with patch(_SWEEP, return_value={"172.16.17.164": "aa:bb:cc:00:00:a4"}) as sweep:
			response = client.post("/leases/sweep", data={"q": "macbook"}, headers=headers)
		swept = sweep.call_args.args[0]
		assert len(swept) == 254
		assert {"172.16.17.1", "172.16.17.164", "172.16.17.177", "172.16.17.254"} <= set(swept)
		assert "1 answered" in response.text
		assert "row-online" not in response.text

	def test_online_rows_marked(self, client):
		headers = _sign_in(client)
		with patch(_SWEEP, return_value={"172.16.17.164": "aa:bb:cc:00:00:a4"}):
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

	def test_kea_down_is_a_notice(self, client, kea):
		headers = _sign_in(client)
		kea.reachable = False
		with patch(_SWEEP) as sweep:
			response = client.post("/leases/sweep", headers=headers)
		assert response.status_code == 200
		assert "read Kea to know what to sweep" in response.text
		sweep.assert_not_called()

	def test_csrf_required(self, client):
		_sign_in(client)
		with patch(_SWEEP) as sweep:
			assert client.post("/leases/sweep", headers=_HTMX).status_code == 403
		sweep.assert_not_called()

	def test_overview_shows_the_online_count(self, client):
		headers = _sign_in(client)
		assert "Online now: not checked yet" in client.get("/").text
		with patch(_SWEEP, return_value={"172.16.17.164": "aa:bb:cc:00:00:a4"}):
			client.post("/leases/sweep", headers=headers)
		assert "1 on the network" in client.get("/").text

	def test_address_map_carries_the_mac_that_answered(self, client):
		headers = _sign_in(client)
		with patch(_SWEEP, return_value={"172.16.17.30": "de:ad:be:ef:00:1e"}):
			client.post("/leases/sweep", headers=headers)
		cells = client.get("/fragments/address-map", headers=_HTMX).text
		assert "de:ad:be:ef:00:1e" in cells
		assert '"seen_age": "just now"' in cells


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


class OneLiveLease(RecordingKea):
	"""Kea with one lease still running and one that ran out."""

	def get_leases(self):
		return [
			{
				"ip-address": "172.16.17.20",
				"hw-address": "aa:bb:cc:00:00:20",
				"cltt": 0,
				"valid-lft": 1,
			},
			{
				"ip-address": "172.16.17.21",
				"hw-address": "aa:bb:cc:00:00:21",
				"cltt": 4_000_000_000,
				"valid-lft": 3600,
			},
		]


class TestRemoveExpired:
	def test_button_shows_the_count_to_admins(self, client):
		_sign_in(client)
		page = client.get("/leases").text
		assert 'hx-post="/leases/remove-expired"' in page
		assert "Remove 2 expired" in page

	def test_no_button_for_viewers(self, client):
		_sign_in(client, role="viewer")
		assert "/leases/remove-expired" not in client.get("/leases").text

	def test_no_button_when_nothing_expired(self, client):
		_sign_in(client)
		client.app.dependency_overrides[kea_client] = lambda: _NothingExpired()
		assert "/leases/remove-expired" not in client.get("/leases").text

	def test_removes_only_expired_and_logs_it(self, client):
		headers = _sign_in(client)
		kea = OneLiveLease()
		client.app.dependency_overrides[kea_client] = lambda: kea
		response = client.post("/leases/remove-expired", headers=headers)
		assert kea.writes == [("delete", "172.16.17.20")]
		assert "Removed 1 expired lease." in response.text
		assert 'id="leases-table"' in response.text
		assert db.get_activity()[0]["summary"] == "removed 1 expired lease: 172.16.17.20"

	def test_removes_all_from_the_stub(self, client, kea):
		headers = _sign_in(client)
		response = client.post("/leases/remove-expired", headers=headers)
		assert kea.writes == [("delete", "172.16.17.164"), ("delete", "172.16.17.177")]
		assert "Removed 2 expired leases." in response.text
		assert "172.16.17.164, 172.16.17.177" in db.get_activity()[0]["summary"]

	def test_one_refusal_does_not_stop_the_rest(self, client, kea):
		headers = _sign_in(client)
		kea.refuse_deletes = {"172.16.17.164"}
		response = client.post("/leases/remove-expired", headers=headers)
		assert kea.writes == [("delete", "172.16.17.177")]
		assert (
			"Removed 1 expired lease. Kea refused 1: 172.16.17.164 (lease locked)" in response.text
		)
		assert "notice-error" in response.text

	def test_nothing_expired(self, client):
		headers = _sign_in(client)
		kea = _NothingExpired()
		client.app.dependency_overrides[kea_client] = lambda: kea
		response = client.post("/leases/remove-expired", headers=headers)
		assert kea.writes == []
		assert "No lease has expired" in response.text
		assert db.get_activity() == []

	def test_kea_unreachable(self, client):
		headers = _sign_in(client)
		client.app.dependency_overrides[kea_client] = lambda: RecordingKea(reachable=False)
		response = client.post("/leases/remove-expired", headers=headers)
		assert "read the leases from Kea: Cannot reach Kea" in response.text

	def test_reads_kea_not_the_cache(self, client, kea):
		headers = _sign_in(client)
		cache.cached("leases", 30, lambda: [])
		client.post("/leases/remove-expired", headers=headers)
		assert ("delete", "172.16.17.164") in kea.writes

	def test_viewer_refused(self, client, kea):
		headers = _sign_in(client, role="viewer")
		assert client.post("/leases/remove-expired", headers=headers).status_code == 403
		assert kea.writes == []

	def test_long_list_shortened_in_the_log(self, client):
		headers = _sign_in(client)
		kea = _ManyExpired()
		client.app.dependency_overrides[kea_client] = lambda: kea
		client.post("/leases/remove-expired", headers=headers)
		summary = db.get_activity()[0]["summary"]
		assert summary.startswith("removed 12 expired leases: 172.16.17.1, ")
		assert summary.endswith("172.16.17.10 and 2 more")


class _NothingExpired(RecordingKea):
	def get_leases(self):
		return [{"ip-address": "172.16.17.21", "cltt": 4_000_000_000, "valid-lft": 3600}]


class _ManyExpired(RecordingKea):
	def get_leases(self):
		return [
			{"ip-address": f"172.16.17.{octet}", "cltt": 0, "valid-lft": 1}
			for octet in range(1, 13)
		]


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


class TestActivityRecorded:
	def test_add_logged(self, client, kea):
		headers = _sign_in(client)
		client.post(
			"/leases", data={"ip": "172.16.17.40", "mac": "aa:bb:cc:dd:ee:ff"}, headers=headers
		)
		assert (
			db.get_activity()[0]["summary"] == "added a lease for 172.16.17.40 (aa:bb:cc:dd:ee:ff)"
		)
