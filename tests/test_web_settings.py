"""
test_web_settings.py — The web UI's Settings screen: DHCP settings saved onto a
fresh config, and the admin-only Users panel driven by auth.py's rules.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import auth
import db
from tests.test_web_overview import _HTMX, _PASSWORD, _SECRET, _sign_in
from tests.test_web_reservations import ConfigKea
from web import cache
from web.deps import kea_client
from web.main import create_app
from web.routes.settings import USERS_CHANGED_EVENT
from web.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

_OTHER_PASSWORD = "another-long-password"  # pragma: allowlist secret
_TOO_SHORT_PASSWORD = "short"  # pragma: allowlist secret
_UNCONFIRMED_PASSWORD = "aaaaaaaaaaaaaa"  # pragma: allowlist secret
_SETTINGS = {
	"pool_start": "172.16.17.100",
	"pool_end": "172.16.17.150",
	"lease_hours": "12",
	"gateway": "172.16.17.1",
	"dns": "1.1.1.1",
	"domain": "home.arpa",
	"ntp": "",
}


@pytest.fixture(autouse=True)
def fresh_cache():
	cache.clear()
	yield
	cache.clear()


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


class TestDhcp:
	def test_page(self, client):
		_sign_in(client)
		page = client.get("/settings").text
		assert 'value="172.16.17.100"' in page and "172.16.17.0/24" in page
		assert 'href="/settings/users"' in page

	def test_viewer_read_only(self, client):
		_sign_in(client, role="viewer")
		page = client.get("/settings").text
		assert "<fieldset disabled>" in page
		assert "Save settings" not in page
		assert 'href="/settings/users"' not in page

	def test_save_onto_fresh_config(self, client, kea):
		headers = _sign_in(client)
		client.get("/settings")
		kea.reservations()[0]["client-classes"] = ["DROP"]
		response = client.put("/settings/dhcp", data=_SETTINGS, headers=headers)
		assert "Saved." in response.text
		subnet = kea.config["subnet4"][0]
		assert subnet["pools"][0]["pool"] == "172.16.17.100-172.16.17.150"
		assert subnet["valid-lifetime"] == 43200
		assert kea.reservations()[0]["client-classes"] == ["DROP"]

	def test_invalid_not_sent(self, client, kea):
		headers = _sign_in(client)
		response = client.put(
			"/settings/dhcp", data={**_SETTINGS, "pool_end": "10.0.0.1"}, headers=headers
		)
		assert "outside the subnet" in response.text
		assert kea.saves == []

	@pytest.mark.parametrize(
		("refuse_read", "message"), [(True, "nothing was saved"), (False, "Kea refused the change")]
	)
	def test_kea_failures(self, client, kea, refuse_read, message):
		headers = _sign_in(client)
		kea.refuse_read, kea.refuse_save = refuse_read, True
		assert message in client.put("/settings/dhcp", data=_SETTINGS, headers=headers).text

	def test_viewer_and_csrf_refused(self, client, kea):
		_sign_in(client)
		assert client.put("/settings/dhcp", data=_SETTINGS, headers=_HTMX).status_code == 403
		client.cookies.clear()
		headers = _sign_in_as("pat", client, role="viewer")
		assert client.put("/settings/dhcp", data=_SETTINGS, headers=headers).status_code == 403
		assert kea.saves == []

	def test_kea_down(self, client, kea):
		_sign_in(client)
		kea.reachable = False
		assert (
			"can&#39;t be reached" in client.get("/settings").text
			or "can't be reached" in client.get("/settings").text
		)


def _sign_in_as(username, client, role="admin"):
	auth.create_user(username, _PASSWORD, role)
	client.get("/login")
	headers = {CSRF_HEADER_NAME: client.cookies[CSRF_COOKIE_NAME], **_HTMX}
	client.post("/login", data={"username": username, "password": _PASSWORD}, headers=headers)
	return headers


class TestTabHighlight:
	def test_settings_tab_current_on_its_sub_pages(self, client):
		_sign_in(client)
		page = client.get("/settings/users").text
		assert 'href="/settings" class="tab tab-current" aria-current="page"' in page

	def test_overview_tab_not_current_on_other_screens(self, client):
		_sign_in(client)
		page = client.get("/settings").text
		assert 'href="/" class="tab tab-current"' not in page
		assert 'href="/settings" class="tab tab-current"' in page


class TestUsers:
	def test_list_and_forms(self, client):
		_sign_in(client)
		page = client.get("/settings/users").text
		assert "tom" in page and "(you)" in page
		assert 'name="username"' in client.get("/settings/users/new", headers=_HTMX).text
		assert "your own account" in client.get("/settings/users/tom", headers=_HTMX).text
		assert client.get("/settings/users/ghost", headers=_HTMX).status_code == 404
		assert 'id="users"' in client.get("/fragments/users", headers=_HTMX).text

	def test_viewer_refused(self, client):
		headers = _sign_in(client, role="viewer")
		assert client.get("/settings/users").status_code == 403
		response = client.post(
			"/settings/users",
			data={"username": "x1", "password": _OTHER_PASSWORD, "confirm": _OTHER_PASSWORD},
			headers=headers,
		)
		assert response.status_code == 403
		assert db.get_user("x1") is None

	def test_create(self, client):
		headers = _sign_in(client)
		response = client.post(
			"/settings/users",
			data={
				"username": "pat",
				"role": "viewer",
				"password": _OTHER_PASSWORD,
				"confirm": _OTHER_PASSWORD,
			},
			headers=headers,
		)
		assert USERS_CHANGED_EVENT in response.headers["HX-Trigger"]
		assert db.get_user("pat")["role"] == "viewer"

	@pytest.mark.parametrize(
		("data", "message"),
		[
			({"username": "pat", "password": "a", "confirm": "b"}, "match"),
			(
				{
					"username": "pat",
					"password": _TOO_SHORT_PASSWORD,
					"confirm": _TOO_SHORT_PASSWORD,
				},
				"at least",
			),
			({"username": "pat", "password": "", "confirm": ""}, "Set a password"),
			(
				{"username": "bad name", "password": _OTHER_PASSWORD, "confirm": _OTHER_PASSWORD},
				"sername",
			),
			(
				{"username": "TOM", "password": _OTHER_PASSWORD, "confirm": _OTHER_PASSWORD},
				"exists",
			),
		],
	)
	def test_create_refused(self, client, data, message):
		headers = _sign_in(client)
		response = client.post("/settings/users", data={"role": "viewer", **data}, headers=headers)
		assert "form-error" in response.text
		assert message.lower() in response.text.lower()

	def test_edit_other_user(self, client):
		headers = _sign_in(client)
		auth.create_user("pat", _OTHER_PASSWORD, "viewer")
		response = client.put(
			"/settings/users/pat",
			data={"role": "admin", "password": _PASSWORD, "confirm": _PASSWORD},
			headers=headers,
		)
		assert "close-dialog" in response.headers["HX-Trigger"]
		assert db.get_user("pat")["role"] == "admin"
		assert auth.check_credentials("pat", _PASSWORD) is not None

	def test_password_mismatch_saves_nothing(self, client):
		headers = _sign_in(client)
		auth.create_user("pat", _OTHER_PASSWORD, "viewer")
		client.put(
			"/settings/users/pat",
			data={"role": "admin", "password": _UNCONFIRMED_PASSWORD, "confirm": "b"},
			headers=headers,
		)
		assert db.get_user("pat")["role"] == "viewer"

	def test_last_admin_cannot_be_demoted(self, client):
		headers = _sign_in(client)
		response = client.put("/settings/users/tom", data={"role": "viewer"}, headers=headers)
		assert "form-error" in response.text
		assert db.get_user("tom")["role"] == "admin"

	def test_own_password_change_signs_you_out(self, client):
		headers = _sign_in(client)
		response = client.put(
			"/settings/users/tom",
			data={"role": "admin", "password": _OTHER_PASSWORD, "confirm": _OTHER_PASSWORD},
			headers=headers,
		)
		assert response.headers["HX-Redirect"] == "/login"
		assert client.get("/", follow_redirects=False).status_code == 303

	def test_own_save_without_changes_keeps_you_signed_in(self, client):
		headers = _sign_in(client)
		response = client.put("/settings/users/tom", data={"role": "admin"}, headers=headers)
		assert "close-dialog" in response.headers["HX-Trigger"]

	def test_disable_and_enable(self, client):
		headers = _sign_in(client)
		auth.create_user("pat", _OTHER_PASSWORD, "viewer")
		client.post("/settings/users/pat/disable", headers=headers)
		assert db.get_user("pat")["is_disabled"]
		assert "disabled" in client.get("/fragments/users", headers=_HTMX).text
		client.post("/settings/users/pat/enable", headers=headers)
		assert not db.get_user("pat")["is_disabled"]

	def test_cannot_disable_yourself(self, client):
		headers = _sign_in(client)
		response = client.post("/settings/users/tom/disable", headers=headers)
		assert "form-error" in response.text
		assert not db.get_user("tom")["is_disabled"]


class TestActivityRecorded:
	def test_settings_and_user_changes_are_logged(self, client):
		headers = _sign_in(client)
		client.put("/settings/dhcp", data=_SETTINGS, headers=headers)
		client.post(
			"/settings/users",
			data={
				"username": "pat",
				"role": "viewer",
				"password": _OTHER_PASSWORD,
				"confirm": _OTHER_PASSWORD,
			},
			headers=headers,
		)
		client.put(
			"/settings/users/pat",
			data={"role": "admin", "password": _PASSWORD, "confirm": _PASSWORD},
			headers=headers,
		)
		client.post("/settings/users/pat/disable", headers=headers)
		assert [row["summary"] for row in db.get_activity()] == [
			"disabled the login pat",
			"changed the role and password of pat",
			"added the viewer login pat",
			"saved the DHCP settings",
		]


class TestDisplay:
	def test_viewer_can_reach_display_and_sees_it_in_the_menu(self, client):
		_sign_in(client, role="viewer")
		page = client.get("/settings/display").text
		assert "Compact header" in page
		assert 'href="/settings/display"' in page
		assert 'href="/settings/users"' not in page

	def test_turning_compact_on_and_off(self, client):
		headers = _sign_in(client, role="viewer")
		assert "header-compact" not in client.get("/settings/display").text
		response = client.put("/settings/display", data={"compact_header": "on"}, headers=headers)
		assert response.headers["HX-Redirect"] == "/settings/display"
		page = client.get("/settings/display").text
		assert 'class="header-compact"' in page
		assert "checked" in page
		client.put("/settings/display", data={}, headers=headers)
		assert "header-compact" not in client.get("/").text

	def test_saving_keeps_the_session(self, client):
		headers = _sign_in(client)
		client.put("/settings/display", data={"compact_header": "on"}, headers=headers)
		assert client.get("/settings/display").status_code == 200

	def test_csrf_required(self, client):
		_sign_in(client)
		assert client.put("/settings/display", data={"compact_header": "on"}).status_code == 403
