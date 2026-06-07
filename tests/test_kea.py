"""
test_kea.py — Tests for kea.py Kea Control Agent client.
All network calls are intercepted via the http_mock fixture (conftest.py).
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from kea import KeaClient, KeaError


def make_kea_response(data: dict | list):
	"""Build a mock httpx response that returns *data* from .json()."""
	resp = MagicMock()
	resp.json.return_value = data
	resp.raise_for_status = MagicMock()
	return resp


@pytest.fixture
def client():
	return KeaClient()


# ─── KeaClient.call ───────────────────────────────────────────────────────────


class TestCall:
	def test_unwraps_list_response(self, client, http_mock):
		http_mock.post.return_value = make_kea_response([{"result": 0, "text": "ok"}])
		result = client.call("version-get")
		assert result == {"result": 0, "text": "ok"}

	def test_returns_dict_response_directly(self, client, http_mock):
		http_mock.post.return_value = make_kea_response({"result": 0})
		result = client.call("version-get")
		assert result == {"result": 0}

	def test_includes_service_in_body(self, client, http_mock):
		http_mock.post.return_value = make_kea_response([{"result": 0}])
		client.call("version-get", service="dhcp4")
		_, kwargs = http_mock.post.call_args
		assert kwargs["json"]["service"] == ["dhcp4"]

	def test_includes_arguments_in_body(self, client, http_mock):
		http_mock.post.return_value = make_kea_response([{"result": 0}])
		client.call("lease4-get", arguments={"ip-address": "10.0.0.1"})
		_, kwargs = http_mock.post.call_args
		assert kwargs["json"]["arguments"] == {"ip-address": "10.0.0.1"}

	def test_raises_kea_error_on_connect_failure(self, client, http_mock):
		http_mock.post.side_effect = httpx.ConnectError("refused")
		with pytest.raises(KeaError, match="Cannot reach Kea CA"):
			client.call("version-get")

	def test_raises_kea_error_on_401(self, client, http_mock):
		mock_resp = MagicMock()
		mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
			"401 Unauthorized",
			request=MagicMock(),
			response=MagicMock(status_code=401),
		)
		http_mock.post.return_value = mock_resp
		with pytest.raises(KeaError, match="rejected credentials"):
			client.call("version-get")

	def test_raises_kea_error_on_other_http_error(self, client, http_mock):
		mock_resp = MagicMock()
		mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
			"503 Service Unavailable",
			request=MagicMock(),
			response=MagicMock(status_code=503),
		)
		http_mock.post.return_value = mock_resp
		with pytest.raises(KeaError, match="HTTP 503"):
			client.call("version-get")


# ─── KeaClient.ip_to_int ──────────────────────────────────────────────────────


class TestIpToInt:
	def test_all_zeros(self):
		assert KeaClient.ip_to_int("0.0.0.0") == 0

	def test_all_ones(self):
		assert KeaClient.ip_to_int("255.255.255.255") == 4_294_967_295

	def test_last_octet_only(self):
		assert KeaClient.ip_to_int("0.0.0.1") == 1

	def test_third_octet(self):
		assert KeaClient.ip_to_int("0.0.1.0") == 256

	def test_second_octet(self):
		assert KeaClient.ip_to_int("0.1.0.0") == 65_536

	def test_first_octet(self):
		assert KeaClient.ip_to_int("1.0.0.0") == 16_777_216

	def test_ordering_is_preserved(self):
		assert KeaClient.ip_to_int("10.0.0.1") < KeaClient.ip_to_int("10.0.0.2")
		assert KeaClient.ip_to_int("10.0.0.255") < KeaClient.ip_to_int("10.0.1.0")


# ─── KeaClient.get_pool_range ─────────────────────────────────────────────────


class TestGetPoolRange:
	_FALLBACK_START = "172.16.17.125"
	_FALLBACK_END = "172.16.17.209"
	_FALLBACK_SIZE = 85

	def test_parses_pool_string_from_config(self, client):
		config = {"subnet4": [{"pools": [{"pool": "10.0.0.1 - 10.0.0.100"}]}]}
		start, end, size = client.get_pool_range(config)
		assert start == "10.0.0.1"
		assert end == "10.0.0.100"
		assert size == 100

	def test_falls_back_on_none_config(self, client):
		start, end, size = client.get_pool_range(None)
		assert start == self._FALLBACK_START
		assert end == self._FALLBACK_END
		assert size == self._FALLBACK_SIZE

	def test_falls_back_on_empty_config(self, client):
		start, *_ = client.get_pool_range({})
		assert start == self._FALLBACK_START

	def test_falls_back_on_missing_pools_key(self, client):
		config = {"subnet4": [{"no-pools-here": []}]}
		start, *_ = client.get_pool_range(config)
		assert start == self._FALLBACK_START

	def test_falls_back_on_malformed_pool_string(self, client):
		config = {"subnet4": [{"pools": [{"pool": "not-a-range"}]}]}
		start, *_ = client.get_pool_range(config)
		assert start == self._FALLBACK_START


# ─── KeaClient.get_pool_stats ─────────────────────────────────────────────────


class TestGetPoolStats:
	_COLUMNS = [
		"total-addresses",
		"assigned-addresses",
		"declined-addresses",
		"cumulative-assigned-addresses",
	]

	def _make_stats_response(self, total, assigned, declined, cumulative):
		return {
			"result": 0,
			"arguments": {
				"result-set": {
					"columns": self._COLUMNS,
					"rows": [[total, assigned, declined, cumulative]],
				}
			},
		}

	def test_parses_stats_correctly(self, client):
		resp = self._make_stats_response(100, 50, 2, 200)
		with patch.object(client, "call", return_value=resp):
			stats = client.get_pool_stats()
		assert stats["total"] == 100
		assert stats["assigned"] == 50
		assert stats["declined"] == 2
		assert stats["available"] == 48  # 100 - 50 - 2
		assert stats["cumulative"] == 200

	def test_available_never_negative(self, client):
		# declined + assigned > total should clamp available to 0
		resp = self._make_stats_response(10, 8, 5, 0)
		with patch.object(client, "call", return_value=resp):
			stats = client.get_pool_stats()
		assert stats["available"] == 0

	def test_returns_zeros_when_no_rows(self, client):
		resp = {"result": 0, "arguments": {"result-set": {"columns": [], "rows": []}}}
		with patch.object(client, "call", return_value=resp):
			stats = client.get_pool_stats()
		assert stats == {"total": 0, "assigned": 0, "declined": 0, "available": 0, "cumulative": 0}

	def test_raises_on_non_zero_result(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "hook missing"}):
			with pytest.raises(KeaError, match="hook missing"):
				client.get_pool_stats()


# ─── KeaClient.get_leases ─────────────────────────────────────────────────────


class TestGetLeases:
	def test_returns_lease_list(self, client):
		leases = [{"ip-address": "10.0.0.1"}, {"ip-address": "10.0.0.2"}]
		resp = {"result": 0, "arguments": {"leases": leases}}
		with patch.object(client, "call", return_value=resp):
			assert client.get_leases() == leases

	def test_returns_empty_list_when_no_leases(self, client):
		resp = {"result": 0, "arguments": {"leases": []}}
		with patch.object(client, "call", return_value=resp):
			assert client.get_leases() == []

	def test_raises_on_error(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "fail"}):
			with pytest.raises(KeaError, match="fail"):
				client.get_leases()


# ─── KeaClient.get_lease_by_ip ────────────────────────────────────────────────


class TestGetLeaseByIp:
	def test_returns_lease_on_success(self, client):
		lease = {"ip-address": "10.0.0.1", "hw-address": "aa:bb:cc:dd:ee:ff"}
		resp = {"result": 0, "arguments": lease}
		with patch.object(client, "call", return_value=resp):
			assert client.get_lease_by_ip("10.0.0.1") == lease

	def test_returns_none_when_not_found(self, client):
		with patch.object(client, "call", return_value={"result": 3}):
			assert client.get_lease_by_ip("10.0.0.1") is None


# ─── KeaClient.delete_lease ───────────────────────────────────────────────────


class TestDeleteLease:
	def test_succeeds_on_result_zero(self, client):
		with patch.object(client, "call", return_value={"result": 0}):
			client.delete_lease("10.0.0.1")  # must not raise

	def test_treats_not_found_as_success(self, client):
		# result=3 means "not found" — still considered success for delete
		with patch.object(client, "call", return_value={"result": 3}):
			client.delete_lease("10.0.0.1")  # must not raise

	def test_raises_on_error(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "error"}):
			with pytest.raises(KeaError):
				client.delete_lease("10.0.0.1")


# ─── KeaClient.wipe_leases ────────────────────────────────────────────────────


class TestWipeLeases:
	def test_returns_deleted_count(self, client):
		resp = {"result": 0, "arguments": {"count": 42}}
		with patch.object(client, "call", return_value=resp):
			assert client.wipe_leases() == 42

	def test_raises_on_error(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "wipe failed"}):
			with pytest.raises(KeaError, match="wipe failed"):
				client.wipe_leases()


# ─── KeaClient.get_config / save_config ──────────────────────────────────────


class TestConfig:
	def test_get_config_returns_dhcp4_section(self, client):
		config = {"interfaces-config": {}, "subnet4": []}
		resp = {"result": 0, "arguments": {"Dhcp4": config}}
		with patch.object(client, "call", return_value=resp):
			assert client.get_config() == config

	def test_get_config_raises_when_dhcp4_key_missing(self, client):
		with patch.object(client, "call", return_value={"result": 0, "arguments": {}}):
			with pytest.raises(KeaError, match="no Dhcp4 key"):
				client.get_config()

	def test_get_config_raises_on_error(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "config-get failed"}):
			with pytest.raises(KeaError):
				client.get_config()

	def test_save_config_calls_set_then_write(self, client):
		ok = {"result": 0}
		with patch.object(client, "call", return_value=ok) as mock_call:
			client.save_config({"subnet4": []})
		commands = [c[0][0] for c in mock_call.call_args_list]
		assert commands == ["config-set", "config-write"]

	def test_save_config_raises_when_set_fails(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "set error"}):
			with pytest.raises(KeaError, match="set error"):
				client.save_config({})

	def test_save_config_raises_when_write_fails(self, client):
		# config-set succeeds but config-write fails
		responses = iter([{"result": 0}, {"result": 1, "text": "write error"}])
		with patch.object(client, "call", side_effect=lambda *a, **kw: next(responses)):
			with pytest.raises(KeaError, match="write error"):
				client.save_config({})


# ─── KeaClient.enable_dhcp / disable_dhcp ────────────────────────────────────


class TestDhcpControl:
	def test_enable_succeeds(self, client):
		with patch.object(client, "call", return_value={"result": 0}):
			client.enable_dhcp()  # must not raise

	def test_enable_raises_on_error(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "enable failed"}):
			with pytest.raises(KeaError, match="enable failed"):
				client.enable_dhcp()

	def test_disable_succeeds(self, client):
		with patch.object(client, "call", return_value={"result": 0}):
			client.disable_dhcp()  # must not raise

	def test_disable_with_max_period_includes_argument(self, client):
		with patch.object(client, "call", return_value={"result": 0}) as mock_call:
			client.disable_dhcp(max_period=300)
		_, kwargs = mock_call.call_args
		assert kwargs["arguments"] == {"max-period": 300}

	def test_disable_without_max_period_sends_no_arguments(self, client):
		with patch.object(client, "call", return_value={"result": 0}) as mock_call:
			client.disable_dhcp()
		_, kwargs = mock_call.call_args
		assert kwargs.get("arguments") is None

	def test_disable_raises_on_error(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "disable failed"}):
			with pytest.raises(KeaError, match="disable failed"):
				client.disable_dhcp()


# ─── KeaClient.get_status ─────────────────────────────────────────────────────


class TestGetStatus:
	def test_returns_both_services(self, client):
		ok_resp = {"result": 0, "arguments": {"extended": "2.4.0\nsome detail"}}
		with patch.object(client, "call", return_value=ok_resp):
			status = client.get_status()
		assert "ca" in status
		assert "dhcp4" in status

	def test_marks_service_up_on_result_zero(self, client):
		ok_resp = {"result": 0, "arguments": {"extended": "2.4.0"}}
		with patch.object(client, "call", return_value=ok_resp):
			assert client.get_status()["ca"].up is True

	def test_marks_service_down_on_kea_error(self, client):
		with patch.object(client, "call", side_effect=KeaError("unreachable")):
			status = client.get_status()
		assert status["ca"].up is False
		assert status["dhcp4"].up is False


# ─── KeaClient.get_leases_by_mac ──────────────────────────────────────────────


class TestGetLeasesByMac:
	def test_returns_matching_leases(self, client):
		leases = [{"ip-address": "10.0.0.1", "hw-address": "aa:bb:cc:dd:ee:ff"}]
		resp = {"result": 0, "arguments": {"leases": leases}}
		with patch.object(client, "call", return_value=resp):
			assert client.get_leases_by_mac("aa:bb:cc:dd:ee:ff") == leases

	def test_returns_empty_list_when_not_found(self, client):
		# result=3 means not found — treated as empty, not an error
		resp = {"result": 3, "arguments": {"leases": []}}
		with patch.object(client, "call", return_value=resp):
			assert client.get_leases_by_mac("ff:ff:ff:ff:ff:ff") == []

	def test_raises_on_unexpected_error(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "bad"}):
			with pytest.raises(KeaError, match="bad"):
				client.get_leases_by_mac("aa:bb:cc:dd:ee:ff")


# ─── KeaClient.get_leases_by_hostname ────────────────────────────────────────


class TestGetLeasesByHostname:
	def test_returns_matching_leases(self, client):
		leases = [{"ip-address": "10.0.0.5", "hostname": "myhost"}]
		resp = {"result": 0, "arguments": {"leases": leases}}
		with patch.object(client, "call", return_value=resp):
			assert client.get_leases_by_hostname("myhost") == leases

	def test_returns_empty_list_when_not_found(self, client):
		resp = {"result": 3, "arguments": {"leases": []}}
		with patch.object(client, "call", return_value=resp):
			assert client.get_leases_by_hostname("ghost") == []

	def test_raises_on_unexpected_error(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "bad"}):
			with pytest.raises(KeaError, match="bad"):
				client.get_leases_by_hostname("myhost")


# ─── KeaClient.add_lease / update_lease ──────────────────────────────────────


class TestAddLease:
	def test_succeeds_without_hostname(self, client):
		with patch.object(client, "call", return_value={"result": 0}) as mock_call:
			client.add_lease("10.0.0.1", "aa:bb:cc:dd:ee:ff")
		_, kwargs = mock_call.call_args
		assert kwargs["arguments"]["ip-address"] == "10.0.0.1"
		assert kwargs["arguments"]["hw-address"] == "aa:bb:cc:dd:ee:ff"
		assert "hostname" not in kwargs["arguments"]

	def test_includes_hostname_when_provided(self, client):
		with patch.object(client, "call", return_value={"result": 0}) as mock_call:
			client.add_lease("10.0.0.1", "aa:bb:cc:dd:ee:ff", hostname="myhost")
		_, kwargs = mock_call.call_args
		assert kwargs["arguments"]["hostname"] == "myhost"

	def test_raises_on_error(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "duplicate"}):
			with pytest.raises(KeaError, match="duplicate"):
				client.add_lease("10.0.0.1", "aa:bb:cc:dd:ee:ff")


class TestUpdateLease:
	def test_succeeds_without_hostname(self, client):
		with patch.object(client, "call", return_value={"result": 0}) as mock_call:
			client.update_lease("10.0.0.1", "aa:bb:cc:dd:ee:ff")
		_, kwargs = mock_call.call_args
		assert kwargs["arguments"]["ip-address"] == "10.0.0.1"
		assert "hostname" not in kwargs["arguments"]

	def test_includes_hostname_when_provided(self, client):
		with patch.object(client, "call", return_value={"result": 0}) as mock_call:
			client.update_lease("10.0.0.1", "aa:bb:cc:dd:ee:ff", hostname="updated")
		_, kwargs = mock_call.call_args
		assert kwargs["arguments"]["hostname"] == "updated"

	def test_raises_on_error(self, client):
		with patch.object(client, "call", return_value={"result": 1, "text": "not found"}):
			with pytest.raises(KeaError, match="not found"):
				client.update_lease("10.0.0.1", "aa:bb:cc:dd:ee:ff")
