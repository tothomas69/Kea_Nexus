"""
dashboard_data.py — Pure data shaping for the Dashboard tab's metrics and charts.

Kept out of `ui_dashboard.py` on purpose: `ui_*.py` needs a live Streamlit
server and is excluded from coverage, so anything with real logic in it would
be untestable. Every function here takes plain data and returns plain data —
no Streamlit, no Kea client, no database — which is what makes the Dashboard's
numbers checkable.

All timestamps in and out are UTC; converting for display is the renderer's
job, matching this project's "UTC internally, convert at the edge" convention.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from helpers import build_reservation_type_sets, lease_type

# Ordered soonest-expiring first, so the ordinal colour ramp reads in the same
# direction as the buckets. Upper bound is exclusive and in seconds; the final
# bucket is open-ended.
EXPIRY_BUCKETS: list[tuple[str, Optional[int]]] = [
	("< 1h", 3600),
	("1–6h", 6 * 3600),
	("6–24h", 24 * 3600),
	("> 24h", None),
]

# Fixed order so a type never changes position (or colour) as counts shift.
LEASE_TYPES = ["dynamic", "reserved", "fixed", "name-only"]

_DEFAULT_VALID_LIFETIME_SECONDS = 86400


def _seconds_until_expiry(lease: dict, now_epoch: float) -> float:
	"""Seconds remaining on a lease, negative once expired.

	Kea reports `cltt` (client last transmission time) plus `valid-lft`
	rather than an expiry timestamp, so expiry is derived — the same
	arithmetic `ui_leases.py` and `ui_ipam.py` already do.
	"""
	cltt = lease.get("cltt", 0)
	valid_lifetime = lease.get("valid-lft", _DEFAULT_VALID_LIFETIME_SECONDS)
	return cltt + valid_lifetime - now_epoch


def lease_expiry_buckets(leases: list[dict], now_epoch: float) -> list[dict]:
	"""Count active leases by how soon they expire, in EXPIRY_BUCKETS order.

	Declined leases (state 1) are excluded — they hold an address but are
	not a device renewing on a timer, so counting them would misreport
	renewal churn. Already-expired leases fall into the first bucket rather
	than being dropped: an expired lease Kea has not yet reclaimed is
	exactly the thing worth seeing.

	Every bucket is always present, including empty ones, so the chart's
	x-axis is stable rather than reshaping itself as the data changes.
	"""
	counts = {label: 0 for label, _ in EXPIRY_BUCKETS}
	for lease in leases:
		if lease.get("state", 0) == 1:
			continue
		remaining = _seconds_until_expiry(lease, now_epoch)
		for label, upper_bound in EXPIRY_BUCKETS:
			if upper_bound is None or remaining < upper_bound:
				counts[label] += 1
				break
	return [{"bucket": label, "count": counts[label]} for label, _ in EXPIRY_BUCKETS]


def lease_composition(leases: list[dict], config: Optional[dict]) -> list[dict]:
	"""Count active leases by reservation type, in LEASE_TYPES order.

	Reuses helpers.lease_type so the Dashboard's classification can never
	drift from what the Leases and IPAM tabs show.
	"""
	fixed_ips, reserved_macs, name_hosts = build_reservation_type_sets(config)
	counts = {name: 0 for name in LEASE_TYPES}
	for lease in leases:
		if lease.get("state", 0) == 1:
			continue
		counts[lease_type(lease, fixed_ips, reserved_macs, name_hosts)] += 1
	return [{"type": name, "count": counts[name]} for name in LEASE_TYPES]


def utilisation_points(samples: list[dict], gap_seconds: float) -> list[dict]:
	"""Turn pool samples into plottable points, breaking the line across gaps.

	The sampler only runs while KeaNexus's process is alive (see
	pool_history.py), so the series legitimately has holes. Drawing straight
	through one would assert a measurement that was never taken — an
	overnight gap would read as steady utilisation. Instead a None-valued
	point is inserted wherever consecutive samples are more than
	gap_seconds apart, which Vega-Lite renders as a break in the line.

	Returns dicts with `sampled_at` (an aware UTC datetime) and
	`utilisation_pct` (float, or None for an inserted break).
	"""
	assert gap_seconds > 0, "gap_seconds must be positive"

	points: list[dict] = []
	previous_time: Optional[datetime] = None
	for sample in samples:
		sampled_at = datetime.fromisoformat(sample["sampled_at"])
		if previous_time is not None and (sampled_at - previous_time).total_seconds() > gap_seconds:
			# Placed one second past the last real reading so the break sits
			# at the start of the gap rather than floating in the middle.
			points.append(
				{"sampled_at": previous_time + timedelta(seconds=1), "utilisation_pct": None}
			)
		points.append({"sampled_at": sampled_at, "utilisation_pct": _utilisation_pct(sample)})
		previous_time = sampled_at
	return points


def _utilisation_pct(sample: dict) -> float:
	"""Percentage of the pool in use, counting declined addresses as used.

	A declined address is one Kea has been told is already taken, so it is
	unavailable even though nothing holds a lease on it — the existing
	gauge counts it the same way.
	"""
	total = sample["total"]
	if not total:
		return 0.0
	return round((sample["assigned"] + sample["declined"]) / total * 100, 1)


# How far before the requested cutoff a baseline sample may sit and still be
# treated as measuring that window. One hour covers an ordinary missed sample
# or two at the default five-minute interval, without letting a long gap pass
# itself off as a short window.
DEFAULT_BASELINE_TOLERANCE_SECONDS = 3600


def leases_issued_since(
	samples: list[dict],
	window_hours: float,
	now: datetime,
	baseline_tolerance_seconds: float = DEFAULT_BASELINE_TOLERANCE_SECONDS,
) -> Optional[int]:
	"""How many leases Kea handed out in the last window_hours.

	Derived from `cumulative-assigned-addresses`, which only ever counts up,
	so the delta between two samples is the number issued between them.

	**This is deliberately not computed from lease data.** A lease's `cltt`
	is the client's *last* transmission, not its first — it is rewritten on
	every renewal, so a device that renews hourly would look brand new
	forever. The cumulative counter is the only honest source for "new
	since".

	Returns None when the history cannot actually answer the question, which
	covers two distinct cases: no sample old enough to measure against at
	all, and — because the sampler leaves real gaps — a baseline that sits so
	far before the cutoff that the delta would span much more than the window
	asked for. Reporting ten hours of leases as "the last hour" would be
	worse than reporting nothing.
	"""
	assert window_hours > 0, "window_hours must be positive"
	assert baseline_tolerance_seconds >= 0, "baseline_tolerance_seconds must not be negative"
	if len(samples) < 2:
		return None

	cutoff = now - timedelta(hours=window_hours)
	baseline = None
	for sample in samples:
		if datetime.fromisoformat(sample["sampled_at"]) <= cutoff:
			baseline = sample
		else:
			break
	if baseline is None:
		return None

	baseline_age = (cutoff - datetime.fromisoformat(baseline["sampled_at"])).total_seconds()
	if baseline_age > baseline_tolerance_seconds:
		return None

	# Kea restarting resets the counter, which would show as a negative
	# delta — report nothing rather than a nonsense figure.
	issued = samples[-1]["cumulative"] - baseline["cumulative"]
	return issued if issued >= 0 else None


def quarantined_count(devices: list[dict]) -> int:
	"""Devices currently quarantined, per the registry's live flag.

	Counts `is_quarantined`, never `last_quarantined_at` — the timestamp is
	deliberately never cleared on release, so it cannot answer "right now".
	"""
	return sum(1 for device in devices if device.get("is_quarantined"))


def utc_now() -> datetime:
	"""Single seam for 'now', so tests pin time instead of racing it."""
	return datetime.now(timezone.utc)
