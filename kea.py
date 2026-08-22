"""
kea.py — Kea HTTP client for KeaNexus.

Talks directly to the kea-dhcp4 HTTP listener introduced in Kea 3.0.
No "service" wrapper is sent — the daemon already knows what it is.

Connection is configured via environment variables:
  KEA_API_URL      — URL to the kea-dhcp4 HTTP listener (default http://172.16.17.215:8004)
  KEA_API_USER     — basic-auth username (leave blank if listener has no auth)
  KEA_API_PASSWORD — basic-auth password
"""

import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()


class KeaError(Exception):
	"""Raised when a Kea command returns a non-zero result or the server is unreachable."""


@dataclass
class ServiceStatus:
	up: bool
	version: str
	name: str


class KeaClient:
	"""
	Synchronous Kea API client for the kea-dhcp4 direct HTTP listener (Kea 3.0+).

	Environment variables:
	  KEA_API_URL      — URL to kea-dhcp4 listener (default http://172.16.17.215:8004)
	  KEA_API_USER     — basic-auth username (optional)
	  KEA_API_PASSWORD — basic-auth password (optional)
	"""

	def __init__(self) -> None:
		base = os.getenv("KEA_API_URL", "http://172.16.17.215:8004").rstrip("/")
		self.url = base + "/"
		self.user = os.getenv("KEA_API_USER", "")
		self.pwd = os.getenv("KEA_API_PASSWORD", "")
		self._timeout = 8.0

	# ─── Core ─────────────────────────────────────────────────────────────────

	def call(self, command: str, arguments: Optional[dict] = None) -> dict:
		"""
		Issue a Kea API command and return the first (unwrapped) response dict.

		The "service" key is intentionally omitted — sending it to a direct
		listener causes a "forwarding not supported" error in Kea 3.0+.
		"""
		body: dict = {"command": command}
		if arguments is not None:
			body["arguments"] = arguments

		auth = (self.user, self.pwd) if self.user else None

		try:
			with httpx.Client(timeout=self._timeout) as client:
				resp = client.post(self.url, json=body, auth=auth)
			resp.raise_for_status()
		except httpx.ConnectError as exc:
			raise KeaError(f"Cannot reach Kea at {self.url}") from exc
		except httpx.HTTPStatusError as exc:
			code = exc.response.status_code
			if code == 401:
				raise KeaError("Kea rejected credentials (401)") from exc
			raise KeaError(f"Kea returned HTTP {code}") from exc

		data = resp.json()
		return data[0] if isinstance(data, list) else data

	# ─── Status ───────────────────────────────────────────────────────────────

	def get_status(self) -> dict[str, ServiceStatus]:
		"""
		Check health and version for the kea-dhcp4 daemon.
		Returns a dict with a "dhcp4" key for compatibility with ui_dashboard.py.
		The "ca" key is returned as a placeholder since there is no CA in Kea 3.0+.
		"""

		def parse(resp: dict, name: str) -> ServiceStatus:
			up = resp.get("result") == 0
			ext = (resp.get("arguments") or {}).get("extended", "")
			# Extract just the version number from the first line of extended info.
			ver = ext.split("\n")[0].split(" ")[0].strip() if ext else ("ok" if up else "—")
			return ServiceStatus(up=up, version=ver, name=name)

		try:
			d4_r = self.call("version-get")
		except KeaError as e:
			d4_r = {"result": -1, "text": str(e)}

		return {
			# No CA in Kea 3.0+ — placeholder keeps ui_dashboard.py health card working.
			"ca": ServiceStatus(up=True, version="n/a", name="Kea API"),
			"dhcp4": parse(d4_r, "DHCP Operational"),
		}

	# ─── DHCP service control ─────────────────────────────────────────────────

	def enable_dhcp(self) -> None:
		"""Re-enable DHCP after it was disabled."""
		r = self.call("dhcp-enable")
		if r.get("result") != 0:
			raise KeaError(r.get("text", "dhcp-enable failed"))

	def disable_dhcp(self, max_period: int = 0) -> None:
		"""
		Suspend DHCP — Kea stops responding to DISCOVER/REQUEST packets.
		max_period > 0 auto-re-enables after that many seconds (Kea-side timer).
		max_period = 0 means disabled until explicitly re-enabled via enable_dhcp().
		Existing leases are NOT revoked; clients keep their IPs until expiry.
		"""
		args = {"max-period": max_period} if max_period > 0 else {}
		r = self.call("dhcp-disable", arguments=args if args else None)
		if r.get("result") != 0:
			raise KeaError(r.get("text", "dhcp-disable failed"))

	# ─── Pool stats ───────────────────────────────────────────────────────────

	def get_pool_stats(self) -> dict:
		"""
		Return pool utilization from stat-lease4-summary.
		More accurate than counting leases manually — Kea maintains its own counters.
		Requires the stat_cmds hook to be loaded in kea-dhcp4.conf.
		Returns: total, assigned (active), declined, available, cumulative.
		"""
		r = self.call("stat-lease4-summary")
		if r.get("result") != 0:
			raise KeaError(r.get("text", "stat-lease4-summary failed"))

		rs = r.get("arguments", {}).get("result-set", {})
		cols = rs.get("columns", [])
		rows = rs.get("rows", [])

		if not rows:
			return {"total": 0, "assigned": 0, "declined": 0, "available": 0, "cumulative": 0}

		row = dict(zip(cols, rows[0]))
		total = row.get("total-addresses", 0)
		assigned = row.get("assigned-addresses", 0)
		declined = row.get("declined-addresses", 0)

		return {
			"total": total,
			"assigned": assigned,
			"declined": declined,
			"available": max(total - assigned - declined, 0),
			"cumulative": row.get("cumulative-assigned-addresses", 0),
		}

	# ─── Lease operations ─────────────────────────────────────────────────────

	def get_leases(self) -> list[dict]:
		"""Return all DHCPv4 leases (active + declined) from Kea memory."""
		r = self.call("lease4-get-all")
		if r.get("result") != 0:
			raise KeaError(r.get("text", "lease4-get-all failed"))
		return r.get("arguments", {}).get("leases", [])

	def get_lease_by_ip(self, ip: str) -> Optional[dict]:
		"""
		Look up a single lease by IP address.
		Returns the lease dict or None if not found (result=3 means not-found in Kea).
		"""
		r = self.call("lease4-get", arguments={"ip-address": ip})
		if r.get("result") != 0:
			return None
		return r.get("arguments")

	def get_leases_by_mac(self, mac: str) -> list[dict]:
		"""
		Find all leases (current and historical) for a hardware address.
		Useful for tracing which IP a device has held over time.
		"""
		r = self.call("lease4-get-by-hw-address", arguments={"hwaddr": mac})
		if r.get("result") not in (0, 3):
			raise KeaError(r.get("text", "lease4-get-by-hw-address failed"))
		return r.get("arguments", {}).get("leases", [])

	def get_leases_by_hostname(self, hostname: str) -> list[dict]:
		"""Find all leases matching a hostname string."""
		r = self.call("lease4-get-by-hostname", arguments={"hostname": hostname})
		if r.get("result") not in (0, 3):
			raise KeaError(r.get("text", "lease4-get-by-hostname failed"))
		return r.get("arguments", {}).get("leases", [])

	def add_lease(
		self, ip: str, mac: str, hostname: str = "", valid_lft: int = 86400, subnet_id: int = 1
	) -> None:
		"""
		Manually inject a lease into Kea's database.
		Useful for pre-registering a device before it first connects,
		or for administrative overrides. Kea honours this like any regular lease.
		"""
		args: dict = {
			"ip-address": ip,
			"hw-address": mac,
			"subnet-id": subnet_id,
			"valid-lft": valid_lft,
			"expire": int(time.time()) + valid_lft,
			"fqdn-fwd": False,
			"fqdn-rev": False,
		}
		if hostname:
			args["hostname"] = hostname
		r = self.call("lease4-add", arguments=args)
		if r.get("result") != 0:
			raise KeaError(r.get("text", "lease4-add failed"))

	def update_lease(
		self, ip: str, mac: str, hostname: str = "", valid_lft: int = 86400, subnet_id: int = 1
	) -> None:
		"""
		Update an existing lease in place.
		The IP address is the primary key — all other fields are replaced.
		Useful for correcting a hostname or extending a lease administratively.
		"""
		args: dict = {
			"ip-address": ip,
			"hw-address": mac,
			"subnet-id": subnet_id,
			"valid-lft": valid_lft,
			"expire": int(time.time()) + valid_lft,
			"fqdn-fwd": False,
			"fqdn-rev": False,
		}
		if hostname:
			args["hostname"] = hostname
		r = self.call("lease4-update", arguments=args)
		if r.get("result") != 0:
			raise KeaError(r.get("text", "lease4-update failed"))

	def delete_lease(self, ip: str) -> None:
		"""Delete a lease by IP. result=3 (not found) is treated as success."""
		r = self.call("lease4-del", arguments={"ip-address": ip})
		if r.get("result") not in (0, 3):
			raise KeaError(r.get("text", f"Failed to delete lease {ip}"))

	def wipe_leases(self, subnet_id: int = 1) -> int:
		"""
		Delete ALL leases in a subnet from Kea's database.
		Returns the count of deleted leases.
		⚠️  Irreversible — all devices will lose their IPs on next renewal.
		"""
		r = self.call("lease4-wipe", arguments={"subnet-id": subnet_id})
		if r.get("result") != 0:
			raise KeaError(r.get("text", "lease4-wipe failed"))
		return r.get("arguments", {}).get("count", 0)

	# ─── Config ───────────────────────────────────────────────────────────────

	def get_config(self) -> dict:
		"""Return the full Dhcp4 running config dict."""
		r = self.call("config-get")
		if r.get("result") != 0:
			raise KeaError(r.get("text", "config-get failed"))
		cfg = r.get("arguments", {}).get("Dhcp4")
		if cfg is None:
			raise KeaError("config-get: no Dhcp4 key in response")
		return cfg

	def save_config(self, dhcp4_config: dict) -> None:
		"""
		Push a modified Dhcp4 config into Kea (memory + disk).
		config-set  → takes effect immediately, no service interruption.
		config-write → persists to kea-dhcp4.conf so it survives restarts.
		"""
		set_r = self.call("config-set", arguments={"Dhcp4": dhcp4_config})
		if set_r.get("result") != 0:
			raise KeaError(f"config-set: {set_r.get('text', 'no detail')}")

		wr_r = self.call("config-write")
		if wr_r.get("result") != 0:
			raise KeaError(f"config-write: {wr_r.get('text', 'no detail')}")

	# ─── Pool helpers ─────────────────────────────────────────────────────────

	def get_pool_range(self, config: Optional[dict] = None) -> tuple[str, str, int]:
		"""Extract pool start, end and size from config. Falls back to known defaults."""
		try:
			pool_str = config["subnet4"][0]["pools"][0]["pool"]  # type: ignore
			start, end = [p.strip() for p in pool_str.split("-", 1)]
			size = self.ip_to_int(end) - self.ip_to_int(start) + 1
			return start, end, size
		except (TypeError, KeyError, IndexError, ValueError):
			return "172.16.17.125", "172.16.17.209", 85

	@staticmethod
	def ip_to_int(ip: str) -> int:
		parts = ip.strip().split(".")
		return sum(int(p) << (8 * (3 - i)) for i, p in enumerate(parts))
