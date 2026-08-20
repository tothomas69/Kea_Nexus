"""test_quarantine_presence_check.py — Tests for presence_check.probe_device_now."""

from unittest.mock import patch

import pytest

from db import get_device, upsert_device
from kea import KeaError
from quarantine_service.presence_check import _run_one_pass, probe_device_now


@pytest.fixture
def registered_device(temp_db):
	upsert_device("tommy_pc", "desktop-tommy", group_tag="", os_fingerprint="")
	return "tommy_pc"


class TestProbeDeviceNow:
	def test_unregistered_device_returns_false(self, temp_db, stub_kea_client):
		kea = stub_kea_client()
		assert probe_device_now("not_registered", kea=kea) is False

	def test_no_current_lease_returns_false(self, temp_db, registered_device, stub_kea_client):
		kea = stub_kea_client(leases_by_hostname={})
		assert probe_device_now(registered_device, kea=kea) is False

	def test_kea_unreachable_returns_false(self, temp_db, registered_device):
		class RaisingKeaClient:
			def get_leases_by_hostname(self, hostname):
				raise KeaError("unreachable")

		assert probe_device_now(registered_device, kea=RaisingKeaClient()) is False

	def test_lease_with_no_ip_returns_false(self, temp_db, registered_device, stub_kea_client):
		kea = stub_kea_client(
			leases_by_hostname={"desktop-tommy": [{"hw-address": "aa:bb:cc:dd:ee:ff"}]}
		)
		assert probe_device_now(registered_device, kea=kea) is False

	def test_no_arp_reply_returns_false_and_does_not_touch_last_seen(
		self, temp_db, registered_device, stub_kea_client
	):
		kea = stub_kea_client(
			leases_by_hostname={
				"desktop-tommy": [{"ip-address": "10.0.0.5", "hw-address": "aa:bb:cc:dd:ee:ff"}]
			}
		)
		with patch("quarantine_service.presence_check._probe", return_value=False):
			assert probe_device_now(registered_device, kea=kea) is False
		assert get_device(registered_device)["last_seen_at"] == ""

	def test_arp_reply_returns_true_and_stamps_last_seen(
		self, temp_db, registered_device, stub_kea_client
	):
		kea = stub_kea_client(
			leases_by_hostname={
				"desktop-tommy": [{"ip-address": "10.0.0.5", "hw-address": "aa:bb:cc:dd:ee:ff"}]
			}
		)
		with patch("quarantine_service.presence_check._probe", return_value=True):
			assert probe_device_now(registered_device, kea=kea) is True

		device = get_device(registered_device)
		assert device["last_seen_ip_address"] == "10.0.0.5"
		assert device["last_seen_mac_address"] == "aa:bb:cc:dd:ee:ff"
		assert device["last_seen_at"] != ""

	def test_constructs_own_kea_client_when_none_passed(self, temp_db, registered_device):
		with patch("quarantine_service.presence_check.KeaClient") as mock_kea_cls:
			mock_kea_cls.return_value.get_leases_by_hostname.return_value = []
			assert probe_device_now(registered_device) is False
			mock_kea_cls.assert_called_once()


class TestRunOnePass:
	def test_probes_every_registered_device(self, temp_db):
		upsert_device("tommy_pc", "desktop-tommy")
		upsert_device("lillys_ipad", "lillys-ipad")
		with (
			patch("quarantine_service.presence_check.KeaClient"),
			patch("quarantine_service.presence_check.probe_device_now") as mock_probe,
		):
			_run_one_pass()
		probed_names = {call.args[0] for call in mock_probe.call_args_list}
		assert probed_names == {"tommy_pc", "lillys_ipad"}
