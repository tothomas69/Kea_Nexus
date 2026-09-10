"""
reconcile.py — Detect enforcement that no longer has a registry owner.

Quarantine writes to three places that outlive this process: a DROP-class
reservation in Kea's config, a client override on each Pi-hole, and (only
while the service stays up) an ARP disruption thread. The only index back to
all three is a `device_registry` row, because `/release` resolves its target
through `identity.resolve_target`. Delete that row and the enforcement is
still live with nothing pointing at it — `/release` returns 404 before it
reaches a single step.

That is not hypothetical; it stranded a device for two days on 2026-09-07.
See "Incident 2026-09-07" in docs/quarantine-feature-design.md.

This module answers the question nothing else in the system asks: is every
piece of live enforcement still owned by a device the registry believes is
quarantined? It only ever reads and reports. Clearing an orphan is a
deliberate operator action, never automatic — if the registry were ever
empty or unreadable at startup, every legitimate quarantine would look like
an orphan, and "release everything" is the worst available failure mode for
a feature whose entire job is keeping devices off the network.
"""

import logging
from dataclasses import dataclass, field

from db import get_devices
from kea import KeaClient, KeaError
from pihole import PiholeClient, PiholeError
from quarantine_service.kea_deny import list_drop_class_mac_addresses
from quarantine_service.pihole_block import list_blocked_ip_addresses

logger = logging.getLogger(__name__)


@dataclass
class OrphanReport:
	"""Enforcement found with no matching quarantined registry entry.

	`unreachable` collects instances that couldn't be checked at all (Kea
	down, a Pi-hole unreachable). Those are deliberately not treated as
	"no orphans found" — a check that couldn't run is not a clean result,
	and reporting it as one is how this problem stayed invisible.
	"""

	kea_mac_addresses: list[str] = field(default_factory=list)
	pihole_ip_addresses: dict[str, list[str]] = field(default_factory=dict)
	unreachable: list[str] = field(default_factory=list)

	@property
	def has_orphans(self) -> bool:
		return bool(self.kea_mac_addresses) or any(self.pihole_ip_addresses.values())

	@property
	def total_count(self) -> int:
		return len(self.kea_mac_addresses) + sum(
			len(ips) for ips in self.pihole_ip_addresses.values()
		)


def _quarantined_identities() -> tuple[set[str], set[str]]:
	"""(MACs, IPs) of every device the registry currently believes is quarantined.

	These come from `last_seen_mac_address`/`last_seen_ip_address`, which are
	breadcrumbs rather than identity (see identity.py) — they're refreshed on
	every resolve and every presence probe. That makes them the best index
	available here, but not a perfect one: a device whose MAC changed after
	being quarantined will have enforcement under the old MAC and a registry
	row carrying the new one, and will therefore look like an orphan. That
	false positive is acceptable precisely because nothing is auto-cleared —
	an operator sees the report and decides.
	"""
	macs = set()
	ips = set()
	for device in get_devices():
		if not device["is_quarantined"]:
			continue
		if device["last_seen_mac_address"]:
			macs.add(device["last_seen_mac_address"].lower())
		if device["last_seen_ip_address"]:
			ips.add(device["last_seen_ip_address"])
	return macs, ips


def find_orphaned_enforcement(
	kea: KeaClient, pihole_clients: dict[str, PiholeClient]
) -> OrphanReport:
	"""Compare live enforcement against the registry and report what's stranded.

	pihole_clients is keyed by instance label ("primary"/"secondary") so a
	report can say *which* Pi-hole still carries a block — they're
	independent, and an override on one but not the other is a real and
	confusing state to be in.

	Never raises for an unreachable backend: a reconciliation check that
	takes the service down on startup because Pi-hole is rebooting would be
	worse than the problem it detects. Failures land in `unreachable`.
	"""
	report = OrphanReport()
	quarantined_macs, quarantined_ips = _quarantined_identities()

	try:
		drop_macs = list_drop_class_mac_addresses(kea.get_config())
		report.kea_mac_addresses = sorted(set(drop_macs) - quarantined_macs)
	except (KeaError, ValueError) as exc:
		report.unreachable.append(f"Kea: {exc}")

	for label, pihole in pihole_clients.items():
		try:
			blocked_ips = list_blocked_ip_addresses(pihole)
			orphaned = sorted(set(blocked_ips) - quarantined_ips)
			if orphaned:
				report.pihole_ip_addresses[label] = orphaned
		except PiholeError as exc:
			report.unreachable.append(f"Pi-hole {label}: {exc}")

	return report


def log_orphan_report(report: OrphanReport) -> None:
	"""Write the report to the service log.

	Logged on every startup regardless of outcome, including the clean case.
	A silent check is indistinguishable from a check that never ran, and the
	whole point here is making invisible state visible.
	"""
	for failure in report.unreachable:
		logger.warning("Orphan check could not complete — %s", failure)

	if not report.has_orphans:
		if not report.unreachable:
			logger.info("Orphan check: no stranded enforcement found.")
		return

	logger.warning(
		"Orphan check: %d piece(s) of enforcement have no quarantined registry "
		"owner. These devices are blocked with nothing tracking them.",
		report.total_count,
	)
	for mac in report.kea_mac_addresses:
		logger.warning("  Kea DROP class with no registry owner: %s", mac)
	for label, ips in report.pihole_ip_addresses.items():
		for ip in ips:
			logger.warning("  Pi-hole (%s) block with no registry owner: %s", label, ip)
	logger.warning(
		"  See docs/quarantine-troubleshooting.md, or the Quarantine tab, to clear these."
	)
