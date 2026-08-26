"""
test_quarantine_liveness.py — Tests for quarantine_service/liveness.py.

scapy's srp is always patched: these tests care that the right addresses
are probed and that the right replies are believed, never about real
packets on a real interface.
"""

from unittest.mock import patch

import pytest

from quarantine_service.liveness import MAX_SWEEP_ADDRESSES, sweep


class _Reply:
	"""Minimal stand-in for a scapy ARP reply — sweep() only reads psrc."""

	def __init__(self, psrc: str):
		self.psrc = psrc


def _answered(*ip_addresses: str) -> list:
	"""srp returns (answered, unanswered); answered holds (sent, reply) pairs."""
	return [(None, _Reply(ip)) for ip in ip_addresses]


class TestSweep:
	def test_returns_only_addresses_that_answered(self):
		with patch(
			"quarantine_service.liveness.srp",
			return_value=(_answered("172.16.17.10"), []),
		):
			result = sweep(["172.16.17.10", "172.16.17.11"])

		assert result == {"172.16.17.10"}

	def test_probes_every_requested_address_in_one_call(self):
		with patch("quarantine_service.liveness.srp", return_value=([], [])) as mock_srp:
			sweep(["172.16.17.10", "172.16.17.11", "172.16.17.12"])

		assert mock_srp.call_count == 1, "sweep must batch, not probe one IP at a time"
		packets = mock_srp.call_args.args[0]
		assert packets.pdst == ["172.16.17.10", "172.16.17.11", "172.16.17.12"]

	def test_deduplicates_and_sorts_targets(self):
		with patch("quarantine_service.liveness.srp", return_value=([], [])) as mock_srp:
			sweep(["172.16.17.11", "172.16.17.10", "172.16.17.11"])

		assert mock_srp.call_args.args[0].pdst == ["172.16.17.10", "172.16.17.11"]

	def test_ignores_replies_from_addresses_nobody_asked_about(self):
		"""A broadcast sweep can pick up gratuitous ARP or a proxy-ARP router
		answering for a range — those must not mark a lease live."""
		with patch(
			"quarantine_service.liveness.srp",
			return_value=(_answered("172.16.17.10", "172.16.17.99"), []),
		):
			result = sweep(["172.16.17.10"])

		assert result == {"172.16.17.10"}

	def test_empty_input_sends_nothing(self):
		with patch("quarantine_service.liveness.srp", return_value=([], [])) as mock_srp:
			assert sweep([]) == set()

		mock_srp.assert_not_called()

	def test_blank_and_whitespace_addresses_are_dropped(self):
		with patch("quarantine_service.liveness.srp", return_value=([], [])) as mock_srp:
			assert sweep(["", "   "]) == set()

		mock_srp.assert_not_called()

	def test_whitespace_around_an_address_is_stripped(self):
		with patch("quarantine_service.liveness.srp", return_value=([], [])) as mock_srp:
			sweep([" 172.16.17.10 "])

		assert mock_srp.call_args.args[0].pdst == ["172.16.17.10"]

	def test_uses_arp_interface_env_var_when_set(self, monkeypatch):
		monkeypatch.setenv("ARP_INTERFACE", "eth0")
		with patch("quarantine_service.liveness.srp", return_value=([], [])) as mock_srp:
			sweep(["172.16.17.10"])

		assert mock_srp.call_args.kwargs["iface"] == "eth0"

	def test_explicit_interface_overrides_env_var(self, monkeypatch):
		monkeypatch.setenv("ARP_INTERFACE", "eth0")
		with patch("quarantine_service.liveness.srp", return_value=([], [])) as mock_srp:
			sweep(["172.16.17.10"], interface="eth1")

		assert mock_srp.call_args.kwargs["iface"] == "eth1"

	def test_blank_arp_interface_env_var_means_auto_select(self, monkeypatch):
		"""A blank `ARP_INTERFACE=` line in .env sets the var to "", which
		scapy must not receive as an interface name."""
		monkeypatch.setenv("ARP_INTERFACE", "")
		with patch("quarantine_service.liveness.srp", return_value=([], [])) as mock_srp:
			sweep(["172.16.17.10"])

		assert mock_srp.call_args.kwargs["iface"] is None

	def test_rejects_more_addresses_than_the_cap(self):
		too_many = [f"10.0.{i // 256}.{i % 256}" for i in range(MAX_SWEEP_ADDRESSES + 1)]
		with pytest.raises(AssertionError, match="more than the"):
			sweep(too_many)
