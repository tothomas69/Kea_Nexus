"""
test_web_quarantine.py — The web UI's Quarantine screen: the registry,
Quarantine/Release for devices and groups, registry CRUD and the
release-before-delete rule.

Kea is the Overview tests' stub; the quarantine service is patched where the
route module calls it, so nothing leaves the process.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import db
from quarantine_client import QuarantineServiceError
from tests.test_web_overview import _HTMX, _SECRET, StubKea, _sign_in
from web import cache
from web.deps import kea_client
from web.main import create_app
from web.quarantine_data import MAX_RELEASE_ATTEMPTS_BEFORE_FORCE
from web.routes import quarantine as routes

_ROUTES = "web.routes.quarantine"


def _result(name: str, ok: bool = True) -> dict:
	return {
		"summary": f"{name} done.",
		"step_results": [
			{
				"friendly_name": name,
				"kea_step_succeeded": ok,
				"arp_step_succeeded": ok,
				"pihole_step_succeeded": ok,
				"fingerprint_refreshed": True,
				"skipped_due_to_identity_drift": False,
			}
		],
	}


@pytest.fixture(autouse=True)
def fresh_state():
	cache.clear()
	routes.release_attempts.failures.clear()
	yield
	cache.clear()
	routes.release_attempts.failures.clear()


@pytest.fixture
def client(temp_db):
	with patch.dict(os.environ, {"KEANEXUS_SECRET": _SECRET}):
		app = create_app(start_background_work=False)
		app.dependency_overrides[kea_client] = lambda: StubKea()
		with TestClient(app) as test_client:
			yield test_client


@pytest.fixture
def no_presence_check():
	with patch(f"{_ROUTES}.trigger_presence_check") as presence:
		yield presence


def _register(name="kids_ipad", **fields):
	db.upsert_device(name, fields.pop("hostname", "kids-ipad"), **fields)


class TestPage:
	def test_needs_sign_in(self, client):
		assert client.get("/quarantine", follow_redirects=False).status_code == 303

	def test_lists_devices_groups_and_log(self, client):
		headers = _sign_in(client)
		_register(group_tag="kids", is_quarantined=True)
		db.insert_quarantine_log_entry("kids_ipad", "quarantine", "kea", True, 1, "ok")
		page = client.get(
			"/quarantine", headers={k: v for k, v in headers.items() if k != "HX-Request"}
		).text
		assert "kids_ipad" in page
		assert "row-quarantined" in page
		assert "Quarantine group" in page
		assert "Audit log" in page
		assert '<td class="mono">kea</td>' in page

	def test_nav_links_here(self, client):
		_sign_in(client)
		assert 'href="/quarantine"' in client.get("/").text

	def test_viewer_buttons_disabled(self, client):
		_sign_in(client, role="viewer")
		_register()
		page = client.get("/quarantine").text
		assert "Add device" not in page
		assert "can't make changes" in page or "can&#39;t make changes" in page

	def test_fragments(self, client):
		_sign_in(client)
		assert 'id="registry"' in client.get("/fragments/quarantine-registry", headers=_HTMX).text
		assert 'id="quarantine-log"' in client.get("/fragments/quarantine-log", headers=_HTMX).text

	def test_empty_registry(self, client):
		_sign_in(client)
		assert "No devices are registered yet" in client.get("/quarantine").text


class TestActions:
	def test_viewer_refused(self, client):
		headers = _sign_in(client, role="viewer")
		_register()
		with patch(f"{_ROUTES}.trigger_quarantine") as trigger:
			assert client.post("/devices/kids_ipad/quarantine", headers=headers).status_code == 403
		trigger.assert_not_called()

	def test_csrf_required(self, client):
		_sign_in(client)
		_register()
		with patch(f"{_ROUTES}.trigger_quarantine") as trigger:
			assert client.post("/devices/kids_ipad/quarantine", headers=_HTMX).status_code == 403
		trigger.assert_not_called()

	def test_unknown_device(self, client):
		headers = _sign_in(client)
		assert client.post("/devices/ghost/quarantine", headers=headers).status_code == 404

	def test_quarantine_shows_steps_and_refreshes_log(self, client):
		headers = _sign_in(client)
		_register()
		with patch(f"{_ROUTES}.trigger_quarantine", return_value=_result("kids_ipad")) as trigger:
			response = client.post("/devices/kids_ipad/quarantine", headers=headers)
		trigger.assert_called_once_with("kids_ipad", is_group=False)
		assert response.headers["HX-Trigger"] == routes.QUARANTINE_CHANGED_EVENT
		assert "notice-ok" in response.text
		assert "Pi-hole ✓" in response.text

	def test_partial_failure_is_an_error(self, client):
		headers = _sign_in(client)
		_register()
		with patch(f"{_ROUTES}.trigger_release", return_value=_result("kids_ipad", ok=False)):
			response = client.post("/devices/kids_ipad/release", headers=headers)
		assert "notice-error" in response.text
		assert "ARP ✗" in response.text

	def test_service_down(self, client):
		headers = _sign_in(client)
		_register()
		with patch(
			f"{_ROUTES}.trigger_quarantine", side_effect=QuarantineServiceError("unreachable")
		):
			response = client.post("/devices/kids_ipad/quarantine", headers=headers)
		assert response.status_code == 200
		assert "unreachable" in response.text

	def test_group_actions(self, client):
		headers = _sign_in(client)
		_register(group_tag="kids")
		with patch(f"{_ROUTES}.trigger_quarantine", return_value=_result("kids_ipad")) as trigger:
			assert client.post("/groups/kids/quarantine", headers=headers).status_code == 200
		trigger.assert_called_once_with("kids", is_group=True)
		with patch(f"{_ROUTES}.trigger_release", return_value=_result("kids_ipad")) as trigger:
			assert client.post("/groups/kids/release", headers=headers).status_code == 200
		trigger.assert_called_once_with("kids", is_group=True)

	def test_unknown_group(self, client):
		headers = _sign_in(client)
		_register()
		assert client.post("/groups/nobody/quarantine", headers=headers).status_code == 404


class TestRegistryForms:
	def test_new_form_offers_lease_hostnames(self, client):
		_sign_in(client)
		form = client.get("/devices/new", headers=_HTMX).text
		assert '<option value="kids-ipad"' in form
		assert 'name="friendly_name"' in form

	def test_edit_form_keeps_a_quiet_hostname(self, client):
		_sign_in(client)
		_register(hostname="old-laptop")
		form = client.get("/devices/kids_ipad", headers=_HTMX).text
		assert '<option value="old-laptop" selected' in form
		assert 'name="friendly_name"' not in form

	def test_free_text_when_kea_reports_no_hostnames(self, client):
		_sign_in(client)
		client.app.dependency_overrides[kea_client] = lambda: StubKea(reachable=False)
		form = client.get("/devices/new", headers=_HTMX).text
		assert '<input name="hostname"' in form

	def test_forms_are_admin_only(self, client):
		_sign_in(client, role="viewer")
		assert client.get("/devices/new", headers=_HTMX).status_code == 403

	def test_edit_unknown(self, client):
		_sign_in(client)
		assert client.get("/devices/ghost", headers=_HTMX).status_code == 404


class TestCreateUpdate:
	def test_create(self, client, no_presence_check):
		headers = _sign_in(client)
		response = client.post(
			"/devices",
			data={"friendly_name": " tommy_laptop ", "hostname": "kids-ipad", "group_tag": "kids"},
			headers=headers,
		)
		assert "close-dialog" in response.headers["HX-Trigger"]
		assert routes.REGISTRY_CHANGED_EVENT in response.headers["HX-Trigger"]
		assert db.get_device("tommy_laptop")["group_tag"] == "kids"
		no_presence_check.assert_called_once_with("tommy_laptop")

	def test_create_survives_presence_check_failure(self, client):
		headers = _sign_in(client)
		with patch(f"{_ROUTES}.trigger_presence_check", side_effect=QuarantineServiceError("down")):
			client.post("/devices", data={"friendly_name": "a", "hostname": "h"}, headers=headers)
		assert db.get_device("a") is not None

	def test_create_rejects_duplicate(self, client, no_presence_check):
		headers = _sign_in(client)
		_register()
		response = client.post(
			"/devices", data={"friendly_name": "kids_ipad", "hostname": "other"}, headers=headers
		)
		assert "already registered" in response.text
		assert db.get_device("kids_ipad")["hostname"] == "kids-ipad"

	def test_create_rejects_bad_name(self, client, no_presence_check):
		headers = _sign_in(client)
		response = client.post(
			"/devices", data={"friendly_name": "a b", "hostname": "h"}, headers=headers
		)
		assert "form-error" in response.text
		assert db.get_devices() == []

	def test_create_viewer_refused(self, client):
		headers = _sign_in(client, role="viewer")
		response = client.post(
			"/devices", data={"friendly_name": "a", "hostname": "h"}, headers=headers
		)
		assert response.status_code == 403
		assert db.get_devices() == []

	def test_update_keeps_service_fields(self, client, no_presence_check):
		headers = _sign_in(client)
		_register(
			os_fingerprint="iOS 18",
			last_seen_ip_address="172.16.17.164",
			last_quarantined_at="2026-08-22T12:00:00+00:00",
			is_quarantined=True,
		)
		response = client.put(
			"/devices/kids_ipad",
			data={"hostname": "kids-ipad", "group_tag": "kids", "notes": "Sam's"},
			headers=headers,
		)
		assert "close-dialog" in response.headers["HX-Trigger"]
		device = db.get_device("kids_ipad")
		assert device["group_tag"] == "kids" and device["notes"] == "Sam's"
		assert device["os_fingerprint"] == "iOS 18"
		assert device["last_seen_ip_address"] == "172.16.17.164"
		assert device["is_quarantined"]

	def test_update_needs_hostname(self, client, no_presence_check):
		headers = _sign_in(client)
		_register()
		response = client.put("/devices/kids_ipad", data={"hostname": " "}, headers=headers)
		assert "form-error" in response.text
		assert db.get_device("kids_ipad")["hostname"] == "kids-ipad"


class TestDelete:
	def test_plain_delete(self, client):
		headers = _sign_in(client)
		_register()
		with patch(f"{_ROUTES}.trigger_release") as release:
			response = client.delete("/devices/kids_ipad", headers=headers)
		release.assert_not_called()
		assert "close-dialog" in response.headers["HX-Trigger"]
		assert db.get_device("kids_ipad") is None

	def test_quarantined_device_released_first(self, client):
		headers = _sign_in(client)
		_register(is_quarantined=True)
		with patch(f"{_ROUTES}.trigger_release", return_value=_result("kids_ipad")) as release:
			client.delete("/devices/kids_ipad", headers=headers)
		release.assert_called_once_with("kids_ipad")
		assert db.get_device("kids_ipad") is None

	def test_failed_release_keeps_the_entry(self, client):
		headers = _sign_in(client)
		_register(is_quarantined=True)
		with patch(f"{_ROUTES}.trigger_release", return_value=_result("kids_ipad", ok=False)):
			response = client.delete("/devices/kids_ipad", headers=headers)
		assert "Not deleted" in response.text
		assert db.get_device("kids_ipad") is not None
		assert routes.release_attempts.count("kids_ipad") == 1

	def test_force_refused_until_threshold(self, client):
		headers = _sign_in(client)
		_register(is_quarantined=True)
		with patch(
			f"{_ROUTES}.trigger_release", side_effect=QuarantineServiceError("down")
		) as release:
			client.delete("/devices/kids_ipad?force=true", headers=headers)
		release.assert_called_once()
		assert db.get_device("kids_ipad") is not None

	def test_force_offered_and_honoured_after_threshold(self, client):
		headers = _sign_in(client)
		_register(is_quarantined=True)
		with patch(f"{_ROUTES}.trigger_release", side_effect=QuarantineServiceError("down")):
			for _ in range(MAX_RELEASE_ATTEMPTS_BEFORE_FORCE):
				response = client.delete("/devices/kids_ipad", headers=headers)
		assert "Force delete" in response.text
		with patch(f"{_ROUTES}.trigger_release") as release:
			client.delete("/devices/kids_ipad?force=true", headers=headers)
		release.assert_not_called()
		assert db.get_device("kids_ipad") is None
		assert routes.release_attempts.count("kids_ipad") == 0

	def test_viewer_refused(self, client):
		headers = _sign_in(client, role="viewer")
		_register()
		assert client.delete("/devices/kids_ipad", headers=headers).status_code == 403
		assert db.get_device("kids_ipad") is not None

	def test_unknown(self, client):
		headers = _sign_in(client)
		assert client.delete("/devices/ghost", headers=headers).status_code == 404


class TestActivityRecorded:
	def test_registry_changes_logged(self, client, no_presence_check):
		headers = _sign_in(client)
		client.post("/devices", data={"friendly_name": "pc", "hostname": "h"}, headers=headers)
		client.put("/devices/pc", data={"hostname": "h2"}, headers=headers)
		client.delete("/devices/pc", headers=headers)
		assert [row["summary"] for row in db.get_activity()] == [
			"deleted the registry entry for pc",
			"edited the registry entry for pc",
			"registered pc for quarantine",
		]

	def test_forced_delete_says_so(self, client):
		headers = _sign_in(client)
		_register(is_quarantined=True)
		with patch(f"{_ROUTES}.trigger_release", side_effect=QuarantineServiceError("down")):
			for _ in range(MAX_RELEASE_ATTEMPTS_BEFORE_FORCE):
				client.delete("/devices/kids_ipad", headers=headers)
		client.delete("/devices/kids_ipad?force=true", headers=headers)
		assert (
			db.get_activity()[0]["summary"]
			== "force-deleted, without releasing, the registry entry for kids_ipad"
		)
