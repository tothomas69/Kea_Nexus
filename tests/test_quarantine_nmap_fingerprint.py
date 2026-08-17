"""
test_quarantine_nmap_fingerprint.py — Tests for quarantine_service/nmap_fingerprint.py.

subprocess.run is always patched — these tests never actually invoke nmap.
"""

import subprocess
from unittest.mock import patch

import pytest

import db
from quarantine_service.nmap_fingerprint import refresh_os_fingerprint

_SAMPLE_XML_WITH_MATCH = """<?xml version="1.0"?>
<nmaprun>
  <host>
    <os>
      <osmatch name="Linux 5.X" accuracy="95"/>
      <osmatch name="Linux 4.X" accuracy="80"/>
    </os>
  </host>
</nmaprun>
"""

_SAMPLE_XML_NO_OS_ELEMENT = """<?xml version="1.0"?>
<nmaprun>
  <host></host>
</nmaprun>
"""

_SAMPLE_XML_EMPTY_OS_ELEMENT = """<?xml version="1.0"?>
<nmaprun>
  <host><os></os></host>
</nmaprun>
"""


def _completed_process(stdout: str) -> subprocess.CompletedProcess:
	return subprocess.CompletedProcess(args=["nmap"], returncode=0, stdout=stdout, stderr="")


class TestRefreshOsFingerprint:
	def test_persists_top_accuracy_match(self, temp_db):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		with patch(
			"quarantine_service.nmap_fingerprint.subprocess.run",
			return_value=_completed_process(_SAMPLE_XML_WITH_MATCH),
		):
			result = refresh_os_fingerprint("tommy_laptop", "172.16.17.50")

		assert result == "Linux 5.X"
		assert db.get_device("tommy_laptop")["os_fingerprint"] == "Linux 5.X"

	def test_preserves_other_registry_fields(self, temp_db):
		db.upsert_device(
			"tommy_laptop", hostname="tommy-kubuntu", group_tag="kids", notes="school laptop"
		)
		with patch(
			"quarantine_service.nmap_fingerprint.subprocess.run",
			return_value=_completed_process(_SAMPLE_XML_WITH_MATCH),
		):
			refresh_os_fingerprint("tommy_laptop", "172.16.17.50")

		device = db.get_device("tommy_laptop")
		assert device["group_tag"] == "kids"
		assert device["notes"] == "school laptop"

	def test_missing_os_element_returns_empty_and_does_not_overwrite(self, temp_db):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", os_fingerprint="Linux 5.X")
		with patch(
			"quarantine_service.nmap_fingerprint.subprocess.run",
			return_value=_completed_process(_SAMPLE_XML_NO_OS_ELEMENT),
		):
			result = refresh_os_fingerprint("tommy_laptop", "172.16.17.50")

		assert result == ""
		assert db.get_device("tommy_laptop")["os_fingerprint"] == "Linux 5.X"  # untouched

	def test_empty_os_element_returns_empty_and_does_not_overwrite(self, temp_db):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu", os_fingerprint="Linux 5.X")
		with patch(
			"quarantine_service.nmap_fingerprint.subprocess.run",
			return_value=_completed_process(_SAMPLE_XML_EMPTY_OS_ELEMENT),
		):
			result = refresh_os_fingerprint("tommy_laptop", "172.16.17.50")

		assert result == ""
		assert db.get_device("tommy_laptop")["os_fingerprint"] == "Linux 5.X"

	def test_unregistered_device_does_not_raise(self, temp_db):
		with patch(
			"quarantine_service.nmap_fingerprint.subprocess.run",
			return_value=_completed_process(_SAMPLE_XML_WITH_MATCH),
		):
			result = refresh_os_fingerprint("nobody", "172.16.17.50")
		assert result == "Linux 5.X"  # scan itself still succeeded

	def test_nmap_failure_propagates_for_retry_wrapper(self, temp_db):
		db.upsert_device("tommy_laptop", hostname="tommy-kubuntu")
		with patch(
			"quarantine_service.nmap_fingerprint.subprocess.run",
			side_effect=subprocess.TimeoutExpired(cmd="nmap", timeout=45),
		):
			with pytest.raises(subprocess.TimeoutExpired):
				refresh_os_fingerprint("tommy_laptop", "172.16.17.50")
