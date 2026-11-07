"""
test_web_overview.py — The web UI's Overview routes, the Kea read cache and
the header health checks.

Kea is a stub that subclasses KeaClient (so get_pool_range and friends are
real) with canned reads; the quarantine service is patched where called.
"""

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

import auth
import db
from kea import KeaClient, KeaError, ServiceStatus
from quarantine_client import QuarantineServiceError
from web import cache
from web.deps import kea_client
from web.health import service_health
from web.main import create_app
from web.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

_PASSWORD = "correct-horse-battery"  # pragma: allowlist secret
_SECRET = "test-cookie-secret"  # pragma: allowlist secret
_HTMX = {"HX-Request": "true"}

_CONFIG = {
	"subnet4": [
		{
			"subnet": "172.16.17.0/24",
			"pools": [{"pool": "172.16.17.100-172.16.17.199"}],
			"option-data": [{"name": "routers", "data": "172.16.17.1"}],
			"reservations": [{"hw-address": "a0:ce:c8:8f:42:e5", "ip-address": "172.16.17.177"}],
		}
	]
}
_LEASES = [
	{
		"ip-address": "172.16.17.164",
		"hw-address": "f0:18:98:3a:77:10",
		"hostname": "kids-ipad",
		"state": 0,
		"cltt": 0,
		"valid-lft": 1,
	},
	{
		"ip-address": "172.16.17.177",
		"hw-address": "a0:ce:c8:8f:42:e5",
		"hostname": "macbook-pro",
		"state": 0,
		"cltt": 0,
		"valid-lft": 1,
	},
]
_STATS = {"total": 100, "assigned": 60, "declined": 2, "available": 38, "cumulative": 500}


class StubKea(KeaClient):
	def __init__(self, reachable=True):
		super().__init__()
		self.reachable = reachable

	def _check(self):
		if not self.reachable:
			raise KeaError("Cannot reach Kea")

	def get_leases(self):
		self._check()
		return _LEASES

	def get_config(self):
		self._check()
		return _CONFIG

	def get_pool_stats(self):
		self._check()
		return _STATS

	def get_status(self):
		return {"dhcp4": ServiceStatus(up=self.reachable, version="3.0.3", name="DHCP"), "ca": None}


@pytest.fixture(autouse=True)
def fresh_cache():
	cache.clear()
	yield
	cache.clear()


@pytest.fixture
def stub_kea():
	return StubKea()


@pytest.fixture
def client(temp_db, stub_kea):
	with patch.dict(os.environ, {"KEANEXUS_SECRET": _SECRET}):
		app = create_app(start_background_work=False)
		app.dependency_overrides[kea_client] = lambda: stub_kea
		with TestClient(app) as test_client:
			yield test_client


def _sign_in(client, role="admin"):
	auth.create_user("tom", _PASSWORD, role)
	client.get("/login")
	headers = {CSRF_HEADER_NAME: client.cookies[CSRF_COOKIE_NAME], **_HTMX}
	client.post("/login", data={"username": "tom", "password": _PASSWORD}, headers=headers)
	return headers


def _register_kids_ipad():
	db.upsert_device("kids_ipad", "kids-ipad", group_tag="kids")


class TestOverviewPage:
	def test_needs_sign_in(self, client):
		assert client.get("/", follow_redirects=False).status_code == 303

	def test_renders_every_address_and_the_key_figures(self, client):
		_sign_in(client)
		page = client.get("/").text
		assert page.count('class="cell cell-') == 256
		assert "62%" in page
		assert "172.16.17.0/24" in page
		assert 'id="address-cells"' in page

	def test_hover_data_names_the_registered_device(self, client):
		_sign_in(client)
		_register_kids_ipad()
		page = client.get("/").text
		assert '"device": "kids_ipad"' in page

	def test_quarantine_card_lists_devices(self, client):
		_sign_in(client)
		_register_kids_ipad()
		assert "Quarantine kids_ipad" in client.get("/").text

	def test_viewer_sees_disabled_switches(self, client):
		_sign_in(client, role="viewer")
		_register_kids_ipad()
		page = client.get("/").text
		assert "Viewers can&#39;t make changes" in page or "Viewers can't make changes" in page

	def test_kea_down_still_renders_with_a_banner(self, client, stub_kea):
		_sign_in(client)
		stub_kea.reachable = False
		response = client.get("/")
		assert response.status_code == 200
		assert (
			"Kea can&#39;t be reached" in response.text or "Kea can't be reached" in response.text
		)

	def test_charts_ship_their_specs(self, client):
		_sign_in(client)
		page = client.get("/").text
		assert 'id="chart-expiry"' in page
		assert 'id="chart-composition"' in page
		assert "Show the numbers" in page

	def test_trend_waits_for_history(self, client):
		_sign_in(client)
		page = client.get("/").text
		assert 'id="chart-trend"' not in page
		assert "Not enough history yet" in page

	def test_trend_drawn_once_samples_exist(self, client):
		_sign_in(client)
		for _ in range(2):
			db.insert_pool_sample(total=100, assigned=40, declined=0, available=60, cumulative=0)
		assert 'id="chart-trend"' in client.get("/").text

	def test_fragments(self, client):
		_sign_in(client)
		assert 'id="address-map"' in client.get("/fragments/address-map", headers=_HTMX).text
		assert 'id="quarantine-card"' in client.get("/fragments/quarantine", headers=_HTMX).text
		assert 'id="activity"' in client.get("/fragments/activity", headers=_HTMX).text

	def test_health_fragment(self, client):
		_sign_in(client)
		with patch.dict(os.environ, {}, clear=False):
			os.environ.pop("QUARANTINE_SERVICE_URL", None)
			os.environ.pop("PIHOLE_API_URL", None)
			os.environ.pop("PIHOLE_SECONDARY_API_URL", None)
			text = client.get("/fragments/health", headers=_HTMX).text
		assert "Kea 3.0.3" in text


class TestQuarantineActions:
	def test_viewer_is_refused(self, client):
		headers = _sign_in(client, role="viewer")
		_register_kids_ipad()
		assert client.post("/quarantine/kids_ipad", headers=headers).status_code == 403

	def test_unknown_device_is_not_found(self, client):
		headers = _sign_in(client)
		assert client.post("/quarantine/nobody", headers=headers).status_code == 404

	def test_quarantine_shows_the_service_summary_and_signals_a_redraw(self, client):
		headers = _sign_in(client)
		_register_kids_ipad()
		with patch(
			"web.routes.overview.trigger_quarantine",
			return_value={"summary": "Kids Ipad is off the network."},
		) as trigger:
			response = client.post("/quarantine/kids_ipad", headers=headers)
		trigger.assert_called_once_with("kids_ipad")
		assert "Kids Ipad is off the network." in response.text
		assert response.headers["HX-Trigger"] == "overview-changed"

	def test_release_calls_release(self, client):
		headers = _sign_in(client)
		_register_kids_ipad()
		with patch("web.routes.overview.trigger_release", return_value={}) as trigger:
			response = client.post("/quarantine/kids_ipad/release", headers=headers)
		trigger.assert_called_once_with("kids_ipad")
		assert "Done." in response.text

	def test_service_failure_is_shown_not_raised(self, client):
		headers = _sign_in(client)
		_register_kids_ipad()
		with patch(
			"web.routes.overview.trigger_quarantine",
			side_effect=QuarantineServiceError("unreachable"),
		):
			response = client.post("/quarantine/kids_ipad", headers=headers)
		assert response.status_code == 200
		assert "unreachable" in response.text

	def test_needs_csrf(self, client):
		_sign_in(client)
		_register_kids_ipad()
		assert client.post("/quarantine/kids_ipad", headers=_HTMX).status_code == 403


class TestStaticEntries:
	def test_form_prefills_an_existing_entry(self, client):
		_sign_in(client)
		db.upsert_static_entry("172.16.17.10", "nas", "aa:bb:cc:dd:ee:ff", "TerraMaster", "")
		page = client.get("/address/172.16.17.10/static", headers=_HTMX).text
		assert 'value="nas"' in page
		assert "Delete" in page

	def test_form_for_a_free_address(self, client):
		_sign_in(client)
		page = client.get("/address/172.16.17.50/static", headers=_HTMX).text
		assert "New static entry" in page

	@pytest.mark.parametrize("ip", ["172.16.17.150", "172.16.17.0", "10.0.0.5"])
	def test_pool_and_outside_addresses_are_refused(self, client, ip):
		_sign_in(client)
		assert client.get(f"/address/{ip}/static", headers=_HTMX).status_code == 404

	def test_save_writes_and_closes_the_dialog(self, client):
		headers = _sign_in(client)
		response = client.post(
			"/address/172.16.17.20/static",
			data={
				"hostname": " printer ",
				"mac_address": "AA:BB:CC:DD:EE:01",
				"description": "",
				"notes": "",
			},
			headers=headers,
		)
		assert response.headers["HX-Trigger"] == "overview-changed, close-dialog"
		stored = db.get_static_entry("172.16.17.20")
		assert stored["hostname"] == "printer"
		assert stored["mac_address"] == "aa:bb:cc:dd:ee:01"

	def test_bad_mac_re_renders_the_form_without_writing(self, client):
		headers = _sign_in(client)
		response = client.post(
			"/address/172.16.17.20/static", data={"mac_address": "nope"}, headers=headers
		)
		assert "MAC addresses look like" in response.text
		assert db.get_static_entry("172.16.17.20") is None

	def test_delete(self, client):
		headers = _sign_in(client)
		db.upsert_static_entry("172.16.17.10", "nas", "", "", "")
		response = client.delete("/address/172.16.17.10/static", headers=headers)
		assert "close-dialog" in response.headers["HX-Trigger"]
		assert db.get_static_entry("172.16.17.10") is None

	def test_viewer_is_refused(self, client):
		headers = _sign_in(client, role="viewer")
		assert (
			client.post("/address/172.16.17.20/static", data={}, headers=headers).status_code == 403
		)
		assert client.get("/address/172.16.17.20/static", headers=_HTMX).status_code == 403


class TestCache:
	def test_reuses_a_fresh_value(self):
		load = MagicMock(return_value=1)
		assert cache.cached("k", 60, load) == 1
		assert cache.cached("k", 60, load) == 1
		load.assert_called_once()

	def test_reloads_after_the_ttl(self):
		load = MagicMock(side_effect=[1, 2])
		with patch("web.cache.time.monotonic", side_effect=[0, 0, 100, 100]):
			assert cache.cached("k", 60, load) == 1
			assert cache.cached("k", 60, load) == 2

	def test_failures_are_not_cached(self):
		load = MagicMock(side_effect=[KeaError("down"), 3])
		with pytest.raises(KeaError):
			cache.cached("k", 60, load)
		assert cache.cached("k", 60, load) == 3

	def test_forget(self):
		load = MagicMock(side_effect=[1, 2])
		cache.cached("k", 60, load)
		cache.forget("k", "missing")
		assert cache.cached("k", 60, load) == 2

	def test_ttl_must_be_positive(self):
		with pytest.raises(AssertionError):
			cache.cached("k", 0, lambda: 1)


class TestHealth:
	def test_kea_only_when_nothing_else_is_configured(self):
		with patch.dict(os.environ, {}, clear=True):
			assert service_health(StubKea()) == [{"name": "Kea 3.0.3", "up": True}]

	def test_kea_down(self):
		with patch.dict(os.environ, {}, clear=True):
			assert service_health(StubKea(reachable=False)) == [{"name": "Kea", "up": False}]

	def test_pihole_counts_any_non_5xx_answer_as_up(self):
		env = {"PIHOLE_API_URL": "http://pihole1", "PIHOLE_SECONDARY_API_URL": "http://pihole2/"}
		responses = {
			"http://pihole1/api/auth": httpx.Response(401),
			"http://pihole2/api/auth": httpx.Response(503),
		}
		with (
			patch.dict(os.environ, env, clear=True),
			patch("web.health.httpx.get", side_effect=lambda url, timeout: responses[url]),
		):
			entries = service_health(StubKea())
		assert entries[1:] == [
			{"name": "Pi-hole 1", "up": True},
			{"name": "Pi-hole 2", "up": False},
		]

	def test_quarantine_service_needs_a_2xx(self):
		env = {"QUARANTINE_SERVICE_URL": "http://q:8600/"}
		with (
			patch.dict(os.environ, env, clear=True),
			patch("web.health.httpx.get", return_value=httpx.Response(401)),
		):
			assert service_health(StubKea())[-1] == {"name": "Quarantine svc", "up": False}

	def test_unreachable_is_down(self):
		env = {"QUARANTINE_SERVICE_URL": "http://q:8600"}
		with (
			patch.dict(os.environ, env, clear=True),
			patch("web.health.httpx.get", side_effect=httpx.ConnectError("no route")),
		):
			assert service_health(StubKea())[-1] == {"name": "Quarantine svc", "up": False}


class TestActivityRecorded:
	def test_static_entry_logged_and_shown(self, client):
		headers = _sign_in(client)
		client.post("/address/172.16.17.10/static", data={"hostname": "nas"}, headers=headers)
		assert db.get_activity()[0]["summary"] == "saved the static entry for 172.16.17.10"
		assert (
			"tom saved the static entry for 172.16.17.10"
			in client.get("/fragments/activity", headers=_HTMX).text
		)
		client.delete("/address/172.16.17.10/static", headers=headers)
		assert db.get_activity()[0]["summary"] == "deleted the static entry for 172.16.17.10"


class TestSearch:
	def test_needs_sign_in(self, client):
		assert client.get("/search?q=kids", follow_redirects=False).status_code == 303

	def test_results(self, client):
		_sign_in(client)
		_register_kids_ipad()
		text = client.get("/search?q=kids", headers=_HTMX).text
		assert "kids_ipad" in text and "/leases?q=172.16.17.164" in text

	def test_no_match_and_too_short(self, client):
		_sign_in(client)
		assert "Nothing matches" in client.get("/search?q=zzzz", headers=_HTMX).text
		assert "search-results" not in client.get("/search?q=z", headers=_HTMX).text

	def test_box_is_live(self, client):
		_sign_in(client)
		page = client.get("/").text
		assert 'hx-get="/search"' in page
		assert "disabled" not in page.split('name="q"')[1].split(">")[0]
