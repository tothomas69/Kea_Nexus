"""
test_quarantine_pihole_block.py — Tests for quarantine_service/pihole_block.py.

Uses stub_pihole_client (conftest.py) — a duck-typed stand-in programmed
with canned (method, path) -> response JSON, so these tests exercise the
group/regex/client orchestration logic without touching real HTTP.
"""

from quarantine_service.pihole_block import block_via_pihole, unblock_via_pihole


class TestBlockViaPihole:
	def test_creates_group_and_regex_when_missing_then_assigns_client(self, stub_pihole_client):
		pihole = stub_pihole_client(
			responses={
				("GET", "/groups"): {"groups": []},
				("POST", "/groups"): {"groups": [{"id": 7, "name": "keanexus_quarantine"}]},
				("GET", "/domains/deny/regex"): {"domains": []},
			}
		)
		block_via_pihole(pihole, "172.16.17.50")

		methods_and_paths = [(method, path) for method, path, _ in pihole.calls]
		assert ("GET", "/groups") in methods_and_paths
		assert ("POST", "/groups") in methods_and_paths
		assert ("GET", "/domains/deny/regex") in methods_and_paths
		assert ("POST", "/domains/deny/regex") in methods_and_paths
		assert ("PUT", "/clients/172.16.17.50") in methods_and_paths

		put_call = next(c for c in pihole.calls if c[:2] == ("PUT", "/clients/172.16.17.50"))
		assert put_call[2]["groups"] == [7]

	def test_reuses_existing_group_and_regex_without_recreating(self, stub_pihole_client):
		pihole = stub_pihole_client(
			responses={
				("GET", "/groups"): {"groups": [{"id": 3, "name": "keanexus_quarantine"}]},
				("GET", "/domains/deny/regex"): {"domains": [{"domain": "(.*)", "groups": [3]}]},
			}
		)
		block_via_pihole(pihole, "172.16.17.50")

		methods = [(method, path) for method, path, _ in pihole.calls]
		assert ("POST", "/groups") not in methods
		assert ("POST", "/domains/deny/regex") not in methods
		put_call = next(c for c in pihole.calls if c[:2] == ("PUT", "/clients/172.16.17.50"))
		assert put_call[2]["groups"] == [3]

	def test_creates_regex_even_if_group_already_existed(self, stub_pihole_client):
		# Group exists but its deny-all regex is missing (e.g. someone
		# deleted it manually) — should still be (re)created.
		pihole = stub_pihole_client(
			responses={
				("GET", "/groups"): {"groups": [{"id": 3, "name": "keanexus_quarantine"}]},
				("GET", "/domains/deny/regex"): {"domains": []},
			}
		)
		block_via_pihole(pihole, "172.16.17.50")

		methods = [(method, path) for method, path, _ in pihole.calls]
		assert ("POST", "/groups") not in methods
		assert ("POST", "/domains/deny/regex") in methods

	def test_ignores_unrelated_groups_when_finding_quarantine_group(self, stub_pihole_client):
		pihole = stub_pihole_client(
			responses={
				("GET", "/groups"): {"groups": [{"id": 1, "name": "Default"}]},
				("POST", "/groups"): {"groups": [{"id": 9, "name": "keanexus_quarantine"}]},
				("GET", "/domains/deny/regex"): {"domains": []},
			}
		)
		block_via_pihole(pihole, "172.16.17.50")
		assert ("POST", "/groups") in [(method, path) for method, path, _ in pihole.calls]


class TestUnblockViaPihole:
	def test_deletes_client_entry(self, stub_pihole_client):
		pihole = stub_pihole_client()
		unblock_via_pihole(pihole, "172.16.17.50")
		assert pihole.calls == [("DELETE", "/clients/172.16.17.50", None)]
