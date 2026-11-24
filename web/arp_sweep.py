"""
arp_sweep.py — One batched ARP sweep: which of these IPs has a powered-on
device behind it right now, and what MAC answered.

Backs "Check who's online" and the background sweep (web/liveness.py keeps
the result). This used to live in the keanexus-quarantine service, the only
container that sat on the LAN's layer 2. With that service gone, the web
container itself runs with network_mode: host and the NET_RAW capability
(docker-compose.yml), which is what a raw ARP socket needs.

Why ARP rather than ICMP: on the same L2 segment a device cannot refuse to
answer ARP and stay reachable, since its host firewall sits above layer 2,
whereas ICMP echo is routinely dropped (Windows blocks it by default) and
would report plenty of live devices as offline.

All requests go out in one scapy srp() call, so the whole sweep costs a single
`timeout` wait however many addresses it covers: a full /24 takes ~3 s.

The MAC in each reply is kept, not just the address: an ARP reply is the only
way KeaNexus can see a MAC for a statically configured device that never asks
Kea for anything.
"""

import logging
import os
from typing import Optional

from scapy.all import ARP, Ether, srp
from scapy.error import Scapy_Exception

_logger = logging.getLogger(__name__)

DEFAULT_SWEEP_TIMEOUT_SECONDS = 3.0

# A sweep covers the subnet's /24 plus any leased address outside it, so a few
# hundred addresses in practice. The cap keeps a bug from turning one sweep
# into an unbounded broadcast flood on the LAN.
MAX_SWEEP_ADDRESSES = 1024

# Optional: pin the sweep to one interface (e.g. "eth0") when the host has
# several and scapy's default route picks the wrong one.
_INTERFACE_ENV = "ARP_INTERFACE"


class ArpSweepError(Exception):
	"""The sweep couldn't send: most often the container lacks NET_RAW or host networking."""


def sweep(
	ip_addresses: list[str],
	timeout_seconds: float = DEFAULT_SWEEP_TIMEOUT_SECONDS,
	interface: Optional[str] = None,
) -> dict[str, str]:
	"""ARP-probe every address and return {ip: mac} for the ones that answered.

	MACs are lowercased. An address that doesn't answer is simply absent: a
	sweep can prove presence, never absence. Raises ArpSweepError when the
	packets can't be sent at all, ValueError past MAX_SWEEP_ADDRESSES.
	"""
	targets = sorted({ip.strip() for ip in ip_addresses if ip.strip()})
	if len(targets) > MAX_SWEEP_ADDRESSES:
		raise ValueError(f"{len(targets)} addresses is more than the {MAX_SWEEP_ADDRESSES} cap")
	if not targets:
		return {}

	interface = interface or os.environ.get(_INTERFACE_ENV) or None

	# A list-valued pdst expands to one packet per address, and srp waits
	# `timeout` once for the whole batch.
	packets = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(op=1, pdst=targets)
	try:
		answered, _unanswered = srp(
			packets, iface=interface, timeout=timeout_seconds, verbose=False
		)
	except PermissionError as exc:
		raise ArpSweepError(
			"no permission to send raw packets; the container needs cap_add NET_RAW"
		) from exc
	except (OSError, Scapy_Exception) as exc:
		raise ArpSweepError(f"couldn't send the ARP sweep: {exc}") from exc

	requested = set(targets)
	# Keep only replies about addresses we asked for: a broadcast sweep can
	# pick up gratuitous ARP or a proxy-ARP router answering for a range, and
	# believing those would mark addresses live on evidence that isn't theirs.
	responding = {
		reply.psrc: reply.hwsrc.lower() for _sent, reply in answered if reply.psrc in requested
	}

	_logger.info("ARP sweep: %d of %d addresses answered", len(responding), len(requested))
	return responding
