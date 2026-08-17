"""
test_quarantine_arp_disrupt.py — Tests for quarantine_service/arp_disrupt.py.

sendp is always patched — these tests never touch a real socket, and use a
tiny send_interval_seconds so loop-based assertions don't slow the suite
down. Thread timing means a small poll-with-timeout helper is used instead
of a fixed sleep, to avoid flakiness on a slow machine.
"""

import time
from unittest.mock import patch

import pytest

from quarantine_service import arp_disrupt


def _wait_until(condition_fn, timeout_seconds: float = 1.0, poll_seconds: float = 0.01) -> bool:
	deadline = time.monotonic() + timeout_seconds
	while time.monotonic() < deadline:
		if condition_fn():
			return True
		time.sleep(poll_seconds)
	return False


@pytest.fixture(autouse=True)
def _cleanup_disruptions():
	"""Ensure no background thread survives a test, even if it fails mid-way."""
	yield
	for friendly_name in list(arp_disrupt._active_disruptions.keys()):
		arp_disrupt.stop_arp_disruption(friendly_name)


class TestSendPoisonedArpReply:
	def test_builds_expected_packet_and_sends_it(self):
		with patch("quarantine_service.arp_disrupt.sendp") as mock_sendp:
			arp_disrupt.send_poisoned_arp_reply(
				"eth0", "172.16.17.50", "aa:bb:cc:dd:ee:ff", "172.16.17.1"
			)
		assert mock_sendp.call_count == 1
		packet = mock_sendp.call_args.args[0]
		assert packet[arp_disrupt.Ether].dst == "aa:bb:cc:dd:ee:ff"
		assert packet[arp_disrupt.ARP].op == 2
		assert packet[arp_disrupt.ARP].psrc == "172.16.17.1"
		assert packet[arp_disrupt.ARP].hwsrc == arp_disrupt.BLACKHOLE_MAC
		assert packet[arp_disrupt.ARP].pdst == "172.16.17.50"
		assert packet[arp_disrupt.ARP].hwdst == "aa:bb:cc:dd:ee:ff"
		assert mock_sendp.call_args.kwargs["iface"] == "eth0"


class TestStartArpDisruption:
	def test_registers_active_disruption_immediately(self):
		with patch("quarantine_service.arp_disrupt.sendp"):
			arp_disrupt.start_arp_disruption(
				"tommy_laptop",
				"172.16.17.50",
				"aa:bb:cc:dd:ee:ff",
				"172.16.17.1",
				send_interval_seconds=0.01,
			)
			assert arp_disrupt.is_disrupting("tommy_laptop") is True

	def test_sends_packets_repeatedly_while_running(self):
		with patch("quarantine_service.arp_disrupt.sendp") as mock_sendp:
			arp_disrupt.start_arp_disruption(
				"tommy_laptop",
				"172.16.17.50",
				"aa:bb:cc:dd:ee:ff",
				"172.16.17.1",
				send_interval_seconds=0.01,
			)
			assert _wait_until(lambda: mock_sendp.call_count >= 3, timeout_seconds=1.0)

	def test_restarting_stops_the_previous_loop(self):
		with patch("quarantine_service.arp_disrupt.sendp"):
			arp_disrupt.start_arp_disruption(
				"tommy_laptop",
				"172.16.17.50",
				"aa:bb:cc:dd:ee:ff",
				"172.16.17.1",
				send_interval_seconds=0.01,
			)
			first_thread = arp_disrupt._active_disruptions["tommy_laptop"].thread

			arp_disrupt.start_arp_disruption(
				"tommy_laptop",
				"172.16.17.60",
				"11:22:33:44:55:66",
				"172.16.17.1",
				send_interval_seconds=0.01,
			)

			assert _wait_until(lambda: not first_thread.is_alive())
			assert arp_disrupt.is_disrupting("tommy_laptop") is True
			second_thread = arp_disrupt._active_disruptions["tommy_laptop"].thread
			assert second_thread is not first_thread

	def test_a_failed_send_does_not_kill_the_loop(self):
		with patch(
			"quarantine_service.arp_disrupt.sendp", side_effect=OSError("network down")
		) as mock_sendp:
			arp_disrupt.start_arp_disruption(
				"tommy_laptop",
				"172.16.17.50",
				"aa:bb:cc:dd:ee:ff",
				"172.16.17.1",
				send_interval_seconds=0.01,
			)
			assert _wait_until(lambda: mock_sendp.call_count >= 3, timeout_seconds=1.0)
			assert arp_disrupt.is_disrupting("tommy_laptop") is True


class TestStopArpDisruption:
	def test_stops_a_running_loop(self):
		with patch("quarantine_service.arp_disrupt.sendp"):
			arp_disrupt.start_arp_disruption(
				"tommy_laptop",
				"172.16.17.50",
				"aa:bb:cc:dd:ee:ff",
				"172.16.17.1",
				send_interval_seconds=0.01,
			)
			arp_disrupt.stop_arp_disruption("tommy_laptop")
		assert arp_disrupt.is_disrupting("tommy_laptop") is False

	def test_no_op_for_unknown_device(self):
		arp_disrupt.stop_arp_disruption("nobody")  # must not raise

	def test_no_more_packets_sent_after_stop(self):
		with patch("quarantine_service.arp_disrupt.sendp") as mock_sendp:
			arp_disrupt.start_arp_disruption(
				"tommy_laptop",
				"172.16.17.50",
				"aa:bb:cc:dd:ee:ff",
				"172.16.17.1",
				send_interval_seconds=0.01,
			)
			assert _wait_until(lambda: mock_sendp.call_count >= 1)
			arp_disrupt.stop_arp_disruption("tommy_laptop")
			count_at_stop = mock_sendp.call_count
			time.sleep(0.1)
			assert mock_sendp.call_count == count_at_stop


class TestIsDisrupting:
	def test_false_for_device_with_no_active_loop(self):
		assert arp_disrupt.is_disrupting("nobody") is False
