"""
kea_reads.py — Cached Kea reads shared by every web screen.

Each read goes through web/cache.py so a page made of several fragments asks
Kea once per TTL, not once per fragment. An unreachable Kea yields None rather
than an exception: every screen still renders, with a banner, instead of a 500.
"""

from typing import Optional

from helpers import pool_stats_with_fallback
from kea import KeaClient, KeaError
from web import cache

LEASES_CACHE_SECONDS = 30
POOL_STATS_CACHE_SECONDS = 30
CONFIG_CACHE_SECONDS = 120


def safe_read(key: str, ttl_seconds: float, load) -> Optional[object]:
	"""A cached Kea read that yields None, not an exception, when Kea is unreachable."""
	try:
		return cache.cached(key, ttl_seconds, load)
	except KeaError:
		return None


def read_kea(kea: KeaClient) -> dict:
	"""Leases, config and pool stats, plus whether Kea failed to answer."""
	leases = safe_read("leases", LEASES_CACHE_SECONDS, kea.get_leases)
	config = safe_read("config", CONFIG_CACHE_SECONDS, kea.get_config)
	stats = safe_read("pool_stats", POOL_STATS_CACHE_SECONDS, lambda: pool_stats_with_fallback(kea))
	return {
		"leases": leases or [],
		"config": config,
		"stats": stats,
		"kea_error": leases is None or config is None,
	}
