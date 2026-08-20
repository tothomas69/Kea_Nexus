"""
test_quarantine_main.py — Tests for quarantine_service/main.py FastAPI routes.

Uses FastAPI's TestClient (no real network) and patches _get_kea_client so
each test controls exactly what leases/config Kea "returns", without
touching httpx. start_arp_disruption/stop_arp_disruption,
block_via_pihole/unblock_via_pihole, and refresh_os_fingerprint are also
patched here — these route tests care that they're called correctly with
the right identity data, not about real threads, sockets, or nmap
subprocesses, which are covered by their own dedicated test files.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import db
from quarantine_service.main import app


@pytest.fixture
def client(temp_db, monkeypatch):
	monkeypatch.setenv("QUARANTINE_API_TOKEN", "test-token")
	return TestClient(app)


def _auth_header() -> dict:
	return {"Authorization": "Bearer test-token"}


class _DriftingKeaClient:
	"""Returns one IP on the first lease lookup (identity resolution) and a
	different IP on the second (the pre-fire safety check) — simulates a
	lease changing in the window between the two, which
	verify_identity_unchanged exists to catch.
	"""

	def __init__(self):
		self._call_count = 0

	def get_leases_by_hostname(self, hostname: str) -> list[dict]:
		self._call_count += 1
		ip_address = "172.16.17.50" if self._call_count == 1 else "172.16.17.99"
		return [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": ip_address}]

	def get_config(self) -> dict:
		return {"subnet4": [{"reservations": [], "option-data": []}]}

	def save_config(self, dhcp4_config: dict) -> None:
		pass


class TestQuarantineEndpoint:
	def test_skips_enforcement_when_identity_drifted_before_firing(self, client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		stub = _DriftingKeaClient()
		with (
			patch("quarantine_service.main._get_kea_client", return_value=stub),
			patch("quarantine_service.main.start_arp_disruption") as mock_start_arp,
			patch("quarantine_service.main.block_via_pihole") as mock_block_pihole,
		):
			response = client.post(
				"/quarantine", json={"target": "tommy_laptop"}, headers=_auth_header()
			)
		assert response.status_code == 200
		step_result = response.json()["step_results"][0]
		assert step_result["skipped_due_to_identity_drift"] is True
		assert step_result["kea_step_succeeded"] is False
		assert step_result["arp_step_succeeded"] is False
		assert step_result["pihole_step_succeeded"] is False
		mock_start_arp.assert_not_called()
		mock_block_pihole.assert_not_called()
		log_entries = db.get_quarantine_log("tommy_laptop")
		assert len(log_entries) == 1
		assert log_entries[0]["succeeded"] == 0

	def test_requires_auth(self, client):
		response = client.post("/quarantine", json={"target": "tommy_laptop"})
		assert response.status_code == 401

	def test_resolves_and_applies_all_four_enforcement_steps(self, client, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		stub = stub_kea_client(
			leases_by_hostname={
				"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]
			},
			dhcp4_config={
				"subnet4": [
					{
						"reservations": [],
						"option-data": [{"name": "routers", "data": "172.16.17.1"}],
					}
				]
			},
		)
		with (
			patch("quarantine_service.main._get_kea_client", return_value=stub),
			patch("quarantine_service.main.start_arp_disruption") as mock_start_arp,
			patch("quarantine_service.main.block_via_pihole") as mock_block_pihole,
			patch(
				"quarantine_service.main.refresh_os_fingerprint", return_value="Linux 5.X"
			) as mock_refresh,
		):
			response = client.post(
				"/quarantine", json={"target": "tommy_laptop"}, headers=_auth_header()
			)
		assert response.status_code == 200
		body = response.json()
		assert body["action"] == "quarantine"
		assert body["resolved_devices"][0]["mac_address"] == "aa:bb:cc:dd:ee:ff"
		step_result = body["step_results"][0]
		assert step_result["kea_step_succeeded"] is True
		assert step_result["arp_step_succeeded"] is True
		assert step_result["pihole_step_succeeded"] is True
		assert step_result["fingerprint_refreshed"] is True
		# One row each for identity_resolution, kea, arp, pihole, nmap_refresh
		assert len(db.get_quarantine_log("tommy_laptop")) == 5
		# The DROP reservation was actually pushed to "Kea"
		saved_reservations = stub.saved_configs[0]["subnet4"][0]["reservations"]
		assert saved_reservations[0]["hw-address"] == "aa:bb:cc:dd:ee:ff"
		assert saved_reservations[0]["client-classes"] == ["DROP"]
		# ARP disruption started with the resolved device's live identity + gateway
		mock_start_arp.assert_called_once_with(
			"tommy_laptop", "172.16.17.50", "aa:bb:cc:dd:ee:ff", "172.16.17.1"
		)
		mock_block_pihole.assert_called_once()
		assert mock_block_pihole.call_args.args[1] == "172.16.17.50"
		mock_refresh.assert_called_once_with("tommy_laptop", "172.16.17.50")

	def test_unregistered_target_returns_404(self, client, stub_kea_client):
		stub = stub_kea_client()
		with patch("quarantine_service.main._get_kea_client", return_value=stub):
			response = client.post("/quarantine", json={"target": "nobody"}, headers=_auth_header())
		assert response.status_code == 404

	def test_device_not_on_network_returns_409(self, client, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		stub = stub_kea_client()
		with patch("quarantine_service.main._get_kea_client", return_value=stub):
			response = client.post(
				"/quarantine", json={"target": "tommy_laptop"}, headers=_auth_header()
			)
		assert response.status_code == 409

	def test_group_target_resolves_multiple_devices(self, client, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", group_tag="kids")
		db.upsert_device("lilly_laptop", hostname="lilly-kubuntu", group_tag="kids")
		stub = stub_kea_client(
			leases_by_hostname={
				"tommy-kubuntu": [
					{"hw-address": "aa:aa:aa:aa:aa:aa", "ip-address": "172.16.17.50"}
				],
				"lilly-kubuntu": [
					{"hw-address": "bb:bb:bb:bb:bb:bb", "ip-address": "172.16.17.51"}
				],
			},
			dhcp4_config={"subnet4": [{"reservations": [], "option-data": []}]},
		)
		with (
			patch("quarantine_service.main._get_kea_client", return_value=stub),
			patch("quarantine_service.main.start_arp_disruption"),
			patch("quarantine_service.main.block_via_pihole"),
			patch("quarantine_service.main.refresh_os_fingerprint", return_value="Linux 5.X"),
		):
			response = client.post(
				"/quarantine",
				json={"target": "kids", "is_group": True},
				headers=_auth_header(),
			)
		assert response.status_code == 200
		assert len(response.json()["resolved_devices"]) == 2
		# Both devices' MACs ended up in the same saved config's DROP class
		final_reservations = stub.saved_configs[-1]["subnet4"][0]["reservations"]
		dropped_macs = {
			r["hw-address"] for r in final_reservations if "DROP" in r["client-classes"]
		}
		assert dropped_macs == {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

	def test_group_target_enforces_online_devices_despite_one_offline(
		self, client, stub_kea_client
	):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", group_tag="kids")
		db.upsert_device("lilly_laptop", hostname="lilly-kubuntu", group_tag="kids")
		stub = stub_kea_client(
			leases_by_hostname={
				"tommy-kubuntu": [{"hw-address": "aa:aa:aa:aa:aa:aa", "ip-address": "172.16.17.50"}]
				# lilly-kubuntu has no current lease (offline)
			},
			dhcp4_config={"subnet4": [{"reservations": [], "option-data": []}]},
		)
		with (
			patch("quarantine_service.main._get_kea_client", return_value=stub),
			patch("quarantine_service.main.start_arp_disruption"),
			patch("quarantine_service.main.block_via_pihole"),
			patch("quarantine_service.main.refresh_os_fingerprint", return_value="Linux 5.X"),
		):
			response = client.post(
				"/quarantine",
				json={"target": "kids", "is_group": True},
				headers=_auth_header(),
			)
		assert response.status_code == 200
		body = response.json()
		assert len(body["resolved_devices"]) == 1
		assert body["resolved_devices"][0]["friendly_name"] == "tommy_laptop"
		assert body["step_results"][0]["kea_step_succeeded"] is True

	def test_group_target_returns_409_when_all_devices_offline(self, client, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", group_tag="kids")
		db.upsert_device("lilly_laptop", hostname="lilly-kubuntu", group_tag="kids")
		stub = stub_kea_client()  # no leases for anyone
		with patch("quarantine_service.main._get_kea_client", return_value=stub):
			response = client.post(
				"/quarantine",
				json={"target": "kids", "is_group": True},
				headers=_auth_header(),
			)
		assert response.status_code == 409


class TestReleaseEndpoint:
	def test_removes_all_four_enforcement_steps(self, client, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		stub = stub_kea_client(
			leases_by_hostname={
				"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]
			},
			dhcp4_config={
				"subnet4": [
					{
						"reservations": [
							{"hw-address": "aa:bb:cc:dd:ee:ff", "client-classes": ["DROP"]}
						]
					}
				]
			},
		)
		with (
			patch("quarantine_service.main._get_kea_client", return_value=stub),
			patch("quarantine_service.main.stop_arp_disruption") as mock_stop_arp,
			patch("quarantine_service.main.unblock_via_pihole") as mock_unblock_pihole,
			patch("quarantine_service.main.refresh_os_fingerprint", return_value=""),
		):
			response = client.post(
				"/release", json={"target": "tommy_laptop"}, headers=_auth_header()
			)
		assert response.status_code == 200
		assert response.json()["action"] == "release"
		assert stub.saved_configs[-1]["subnet4"][0]["reservations"] == []
		mock_stop_arp.assert_called_once_with("tommy_laptop")
		mock_unblock_pihole.assert_called_once()
		assert mock_unblock_pihole.call_args.args[1] == "172.16.17.50"


class TestStatusEndpoint:
	def test_requires_auth(self, client):
		response = client.get("/status/tommy_laptop")
		assert response.status_code == 401

	def test_returns_log_entries(self, client):
		db.insert_quarantine_log_entry(
			"tommy_laptop", "quarantine", "identity_resolution", succeeded=True, attempt_count=1
		)
		response = client.get("/status/tommy_laptop", headers=_auth_header())
		assert response.status_code == 200
		assert len(response.json()["log"]) == 1

	def test_returns_empty_log_for_unknown_device(self, client):
		response = client.get("/status/nobody", headers=_auth_header())
		assert response.status_code == 200
		assert response.json()["log"] == []


class TestPresenceCheckEndpoint:
	def test_requires_auth(self, client):
		response = client.post("/presence-check/tommy_laptop")
		assert response.status_code == 401

	def test_returns_seen_true_when_device_answers(self, client):
		with patch("quarantine_service.main.probe_device_now", return_value=True) as mock_probe:
			response = client.post("/presence-check/tommy_laptop", headers=_auth_header())
		assert response.status_code == 200
		assert response.json() == {"friendly_name": "tommy_laptop", "seen": True}
		mock_probe.assert_called_once_with("tommy_laptop")

	def test_returns_seen_false_when_device_does_not_answer(self, client):
		with patch("quarantine_service.main.probe_device_now", return_value=False):
			response = client.post("/presence-check/nobody", headers=_auth_header())
		assert response.status_code == 200
		assert response.json() == {"friendly_name": "nobody", "seen": False}


class TestStartupSchemaCreation:
	def test_init_db_runs_on_startup(self, temp_db):
		"""Regression test for a real deployment bug: keanexus-quarantine must
		create its own schema on boot rather than relying on KeaNexus's
		Streamlit script having run first. Streamlit defers executing app.py
		until a browser session connects — it does not run eagerly just
		because the container started — so a fresh deploy where nobody has
		opened the dashboard yet left quarantine_log/device_registry missing
		entirely on first real deployment. TestClient only triggers FastAPI's
		startup event when used as a context manager, unlike the plain
		`client` fixture used elsewhere in this file.
		"""
		with patch("quarantine_service.main.init_db") as mock_init_db:
			with TestClient(app):
				pass
		mock_init_db.assert_called_once()


class TestSecondaryPihole:
	def test_only_primary_written_when_secondary_not_configured(
		self, client, stub_kea_client, monkeypatch
	):
		monkeypatch.delenv("PIHOLE_SECONDARY_API_URL", raising=False)
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		stub = stub_kea_client(
			leases_by_hostname={
				"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]
			},
			dhcp4_config={"subnet4": [{"reservations": [], "option-data": []}]},
		)
		with (
			patch("quarantine_service.main._get_kea_client", return_value=stub),
			patch("quarantine_service.main.start_arp_disruption"),
			patch("quarantine_service.main.block_via_pihole") as mock_block_pihole,
			patch("quarantine_service.main.refresh_os_fingerprint", return_value=""),
		):
			response = client.post(
				"/quarantine", json={"target": "tommy_laptop"}, headers=_auth_header()
			)
		assert response.status_code == 200
		mock_block_pihole.assert_called_once()
		# Only one "pihole_primary" row — no "pihole_secondary" row exists at all
		steps = {entry["step"] for entry in db.get_quarantine_log("tommy_laptop")}
		assert "pihole_primary" in steps
		assert "pihole_secondary" not in steps

	def test_both_instances_written_when_secondary_configured(
		self, client, stub_kea_client, monkeypatch
	):
		monkeypatch.setenv("PIHOLE_SECONDARY_API_URL", "https://pihole-secondary.example")
		monkeypatch.setenv("PIHOLE_SECONDARY_API_PASSWORD", "secondary-pw")
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		stub = stub_kea_client(
			leases_by_hostname={
				"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]
			},
			dhcp4_config={"subnet4": [{"reservations": [], "option-data": []}]},
		)
		with (
			patch("quarantine_service.main._get_kea_client", return_value=stub),
			patch("quarantine_service.main.start_arp_disruption"),
			patch("quarantine_service.main.block_via_pihole") as mock_block_pihole,
			patch("quarantine_service.main.refresh_os_fingerprint", return_value=""),
		):
			response = client.post(
				"/quarantine", json={"target": "tommy_laptop"}, headers=_auth_header()
			)
		assert response.status_code == 200
		# Called once per Pi-hole instance, both against the same device IP
		assert mock_block_pihole.call_count == 2
		called_ips = {call.args[1] for call in mock_block_pihole.call_args_list}
		assert called_ips == {"172.16.17.50"}
		# Both instances got their own distinct, independently-retried log row
		steps = {entry["step"] for entry in db.get_quarantine_log("tommy_laptop")}
		assert "pihole_primary" in steps
		assert "pihole_secondary" in steps

	def test_pihole_step_succeeded_false_if_only_secondary_fails(
		self, client, stub_kea_client, monkeypatch
	):
		monkeypatch.setenv("PIHOLE_SECONDARY_API_URL", "https://pihole-secondary.example")
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		stub = stub_kea_client(
			leases_by_hostname={
				"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]
			},
			dhcp4_config={"subnet4": [{"reservations": [], "option-data": []}]},
		)
		# Primary succeeds, secondary always raises
		call_count = {"n": 0}

		def flaky_block(pihole, ip):
			call_count["n"] += 1
			if call_count["n"] > 1:
				raise RuntimeError("secondary unreachable")

		with (
			patch("quarantine_service.main._get_kea_client", return_value=stub),
			patch("quarantine_service.main.start_arp_disruption"),
			patch("quarantine_service.main.block_via_pihole", side_effect=flaky_block),
			patch("quarantine_service.main.refresh_os_fingerprint", return_value=""),
			patch("quarantine_service.retry.time.sleep"),  # skip real backoff delay
		):
			response = client.post(
				"/quarantine", json={"target": "tommy_laptop"}, headers=_auth_header()
			)
		assert response.status_code == 200
		# Partial success (primary OK, secondary failed) must not read as a full success
		assert response.json()["step_results"][0]["pihole_step_succeeded"] is False


class TestLastQuarantinedStamp:
	def test_quarantine_stamps_last_quarantined_at(self, client, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		assert db.get_device("tommy_laptop")["last_quarantined_at"] == ""
		stub = stub_kea_client(
			leases_by_hostname={
				"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]
			},
			dhcp4_config={"subnet4": [{"reservations": [], "option-data": []}]},
		)
		with (
			patch("quarantine_service.main._get_kea_client", return_value=stub),
			patch("quarantine_service.main.start_arp_disruption"),
			patch("quarantine_service.main.block_via_pihole"),
			patch("quarantine_service.main.refresh_os_fingerprint", return_value=""),
		):
			response = client.post(
				"/quarantine", json={"target": "tommy_laptop"}, headers=_auth_header()
			)
		assert response.status_code == 200
		assert db.get_device("tommy_laptop")["last_quarantined_at"] != ""

	def test_release_does_not_stamp_last_quarantined_at(self, client, stub_kea_client):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		stub = stub_kea_client(
			leases_by_hostname={
				"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]
			},
			dhcp4_config={"subnet4": [{"reservations": [], "option-data": []}]},
		)
		with (
			patch("quarantine_service.main._get_kea_client", return_value=stub),
			patch("quarantine_service.main.stop_arp_disruption"),
			patch("quarantine_service.main.unblock_via_pihole"),
			patch("quarantine_service.main.refresh_os_fingerprint", return_value=""),
		):
			response = client.post(
				"/release", json={"target": "tommy_laptop"}, headers=_auth_header()
			)
		assert response.status_code == 200
		assert db.get_device("tommy_laptop")["last_quarantined_at"] == ""

	def test_preserves_other_fields_when_stamping(self, client, stub_kea_client):
		db.upsert_device(
			"tommy_laptop", hostname="tommy-kubuntu", group_tag="kids", notes="school laptop"
		)
		stub = stub_kea_client(
			leases_by_hostname={
				"tommy-kubuntu": [{"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "172.16.17.50"}]
			},
			dhcp4_config={"subnet4": [{"reservations": [], "option-data": []}]},
		)
		with (
			patch("quarantine_service.main._get_kea_client", return_value=stub),
			patch("quarantine_service.main.start_arp_disruption"),
			patch("quarantine_service.main.block_via_pihole"),
			patch("quarantine_service.main.refresh_os_fingerprint", return_value=""),
		):
			client.post("/quarantine", json={"target": "tommy_laptop"}, headers=_auth_header())
		device = db.get_device("tommy_laptop")
		assert device["group_tag"] == "kids"
		assert device["notes"] == "school laptop"
