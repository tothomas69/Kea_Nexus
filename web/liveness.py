"""
liveness.py — Who is on the network right now, from ARP sweeps.

Kea knows which addresses are leased, not which devices are powered on: a
lease outlives its device by hours. keanexus-quarantine shares the LAN segment
and can ARP-sweep it (see quarantine_client.trigger_liveness_sweep). This
module keeps the latest sweep in memory, runs one every
LIVENESS_SWEEP_INTERVAL_SECONDS in a background thread, and runs one on
demand for the Leases screen's button.

Every sweep covers the whole subnet, not only leased addresses, and keeps the
MAC that answered for each address. A statically configured device never talks
to Kea, so an ARP reply is the only place KeaNexus can learn it is there and
what its hardware address is. Like the Address Map, this assumes a /24.

A sweep only proves presence. A device that didn't answer is shown unchanged,
never as offline.

The latest sweep is also saved to SQLite (db.save_liveness_sweep), so after a
restart the screens show the last result with its real age instead of "not
checked yet". The age label keeps an old result from looking current. A
database failure is logged and never stops a sweep: the in-memory result is
what the screens use.
"""

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping, Optional

import db
from address_map import subnet_prefix
from kea import KeaClient
from quarantine_client import trigger_liveness_sweep

_logger = logging.getLogger(__name__)

_INTERVAL_ENV = "LIVENESS_SWEEP_INTERVAL_SECONDS"
_DEFAULT_INTERVAL_SECONDS = 300
_MIN_INTERVAL_SECONDS = 30
_STOP_JOIN_TIMEOUT_SECONDS = 5.0
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
# .0 is the network address and .255 the broadcast: neither is a host.
_FIRST_HOST_OCTET = 1
_LAST_HOST_OCTET = 254


@dataclass(frozen=True)
class SweepResult:
	"""One finished sweep. `seen_macs` is {ip: mac} for every address that
	answered; the MAC is "" when the quarantine service didn't report one."""

	seen_macs: Mapping[str, str]
	checked: int
	checked_at_epoch: float

	@property
	def responding(self) -> frozenset[str]:
		return frozenset(self.seen_macs)


_latest: Optional[SweepResult] = None
# True once latest() has looked in the database, so a process with no stored
# sweep asks SQLite once, not on every page.
_loaded_from_db = False
_lock = threading.Lock()
_stop_event: Optional[threading.Event] = None
_thread: Optional[threading.Thread] = None


def latest() -> Optional[SweepResult]:
	"""The most recent sweep, or None if none has ever been saved.

	The first call after a start reads the stored sweep from SQLite.
	"""
	global _latest, _loaded_from_db
	with _lock:
		if _latest is None and not _loaded_from_db:
			_latest = _load_stored()
			_loaded_from_db = True
		return _latest


def _load_stored() -> Optional[SweepResult]:
	try:
		stored = db.get_liveness_sweep()
	except (sqlite3.Error, OSError, ValueError):
		_logger.warning("Couldn't read the stored liveness sweep", exc_info=True)
		return None
	if stored is None:
		return None
	return SweepResult(
		MappingProxyType(dict(stored["seen_macs"])), stored["checked"], stored["checked_at_epoch"]
	)


def _store(result: SweepResult) -> None:
	try:
		db.save_liveness_sweep(dict(result.seen_macs), result.checked, result.checked_at_epoch)
	except (sqlite3.Error, OSError):
		_logger.warning("Couldn't save the liveness sweep", exc_info=True)


def sweep_now(ip_addresses: list[str]) -> SweepResult:
	"""Sweep these addresses and keep the result. Raises QuarantineServiceError."""
	global _latest
	seen_macs = MappingProxyType(dict(trigger_liveness_sweep(ip_addresses)))
	result = SweepResult(seen_macs, len(set(ip_addresses)), time.time())
	with _lock:
		_latest = result
	_store(result)
	return result


def reset() -> None:
	"""Forget the latest sweep in memory, so the next latest() reads SQLite again. Used by tests."""
	global _latest, _loaded_from_db
	with _lock:
		_latest = None
		_loaded_from_db = False


def sweep_interval_seconds() -> int:
	try:
		configured = int(os.environ.get(_INTERVAL_ENV, _DEFAULT_INTERVAL_SECONDS))
	except ValueError:
		configured = _DEFAULT_INTERVAL_SECONDS
	return max(configured, _MIN_INTERVAL_SECONDS)


def network_addresses(config: Optional[dict], leases: list[dict]) -> list[str]:
	"""Every host address in the subnet's /24, plus any leased address outside it."""
	prefix = subnet_prefix(config)
	addresses = {f"{prefix}.{octet}" for octet in range(_FIRST_HOST_OCTET, _LAST_HOST_OCTET + 1)}
	addresses.update(lease["ip-address"] for lease in leases if lease.get("ip-address"))
	return sorted(addresses, key=_address_sort_key)


def _address_sort_key(ip_address: str) -> tuple:
	return tuple(int(part) if part.isdigit() else 0 for part in ip_address.split("."))


def sweep_network(kea: KeaClient) -> SweepResult:
	"""Sweep the whole subnet. Raises on Kea or quarantine service failure."""
	return sweep_now(network_addresses(kea.get_config(), kea.get_leases()))


def _run_loop(
	stop_event: threading.Event, interval_seconds: int, kea_factory: Callable[[], KeaClient]
) -> None:
	while not stop_event.is_set():
		try:
			sweep_network(kea_factory())
		except Exception:
			# The quarantine service is optional and Kea can blip: either is an
			# ordinary condition, and a loop that died on it would stop sweeping
			# silently for the life of the process.
			_logger.warning("Liveness sweep failed; will try again next interval", exc_info=True)
		stop_event.wait(interval_seconds)


def start_sweep_loop(kea_factory: Callable[[], KeaClient] = KeaClient) -> None:
	"""Start the background sweep. Idempotent: a second loop would double the ARP traffic."""
	global _stop_event, _thread
	if _thread is not None and _thread.is_alive():
		return
	_stop_event = threading.Event()
	_thread = threading.Thread(
		target=_run_loop,
		args=(_stop_event, sweep_interval_seconds(), kea_factory),
		name="liveness-sweep",
		daemon=True,
	)
	_thread.start()


def stop_sweep_loop() -> None:
	"""Stop the background sweep. Production never calls this; tests do."""
	global _stop_event, _thread
	if _stop_event is None or _thread is None:
		return
	_stop_event.set()
	_thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
	_stop_event, _thread = None, None


def is_sweeping() -> bool:
	return _thread is not None and _thread.is_alive()


def age_label(result: SweepResult, now_epoch: float) -> str:
	"""How long ago a sweep ran, in words: a stale green row should look stale."""
	seconds = max(int(now_epoch - result.checked_at_epoch), 0)
	if seconds < _SECONDS_PER_MINUTE:
		return "just now"
	minutes = seconds // _SECONDS_PER_MINUTE
	if minutes < _MINUTES_PER_HOUR:
		return f"{minutes} min ago"
	return f"{minutes // _MINUTES_PER_HOUR} h {minutes % _MINUTES_PER_HOUR} min ago"
