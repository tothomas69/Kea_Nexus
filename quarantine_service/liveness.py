"""
liveness.py — Batched ARP sweep answering "which of these IPs has a
powered-on device behind it right now".

Backs the Leases tab's "Check who's online" button (via main.py's
POST /liveness-sweep). Distinct from presence_check.py despite both
sending ARP who-has requests: presence_check probes one *registered*
device on a timer and writes last_seen_* breadcrumbs to device_registry,
whereas this probes an arbitrary caller-supplied list of addresses, keeps
no state at all, and never touches the database.

Why ARP rather than ICMP: this service runs with network_mode: host, so
it sits on the same L2 segment as the leases it's asked about. A device
on that segment cannot refuse to answer ARP and remain reachable — its
own host firewall sits above layer 2 — whereas ICMP echo is routinely
dropped (Windows blocks it from off-subnet sources by default), which
would report plenty of live devices as offline. KeaNexus itself can't do
this: it runs on the default bridge network, off-segment, where ARP has
nothing to reach.

All requests go out in one scapy srp call rather than one call per
address, so the whole sweep costs a single `timeout` wait no matter how
many IPs are asked about — a full /24 comes back in about two seconds.
"""

import logging
import os
from typing import Optional

from scapy.all import ARP, Ether, srp

logger = logging.getLogger(__name__)

DEFAULT_SWEEP_TIMEOUT_SECONDS = 3.0

# A sweep is driven by the caller's lease list, which is bounded by the
# pool size in practice. This cap exists so a malformed or hostile request
# can't turn one HTTP call into an unbounded packet flood on the LAN.
MAX_SWEEP_ADDRESSES = 1024


def sweep(
	ip_addresses: list[str],
	timeout_seconds: float = DEFAULT_SWEEP_TIMEOUT_SECONDS,
	interface: Optional[str] = None,
) -> set[str]:
	"""ARP-probe every address in `ip_addresses` and return the subset that
	answered.

	Returns a set rather than a list because every caller asks "is this one
	live", never "what order did they answer in". An address that doesn't
	answer is simply absent — this deliberately reports presence, never
	absence, since a sweep can only ever prove the former.
	"""
	assert len(ip_addresses) <= MAX_SWEEP_ADDRESSES, (
		f"sweep called with {len(ip_addresses)} addresses, more than the {MAX_SWEEP_ADDRESSES} cap"
	)

	targets = sorted({ip.strip() for ip in ip_addresses if ip.strip()})
	if not targets:
		return set()

	interface = interface or os.environ.get("ARP_INTERFACE") or None

	# scapy expands a list-valued pdst into one packet per address and srp
	# waits `timeout` once for the whole batch, so this is a single sweep
	# rather than len(targets) sequential probes.
	packets = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(op=1, pdst=targets)
	answered, _unanswered = srp(packets, iface=interface, timeout=timeout_seconds, verbose=False)

	requested = set(targets)
	# Filter against what was asked for: a broadcast sweep can pick up
	# replies from addresses nobody asked about (gratuitous ARP, a proxy-ARP
	# router answering on behalf of a range), and reporting those back would
	# mark leases live on evidence that isn't about them.
	responding = {reply.psrc for _sent, reply in answered if reply.psrc in requested}

	logger.info("ARP sweep: %d of %d addresses answered", len(responding), len(requested))
	return responding
