"""
cache.py — A small time-limited cache for Kea reads.

Replaces Streamlit's @st.cache_data for the web UI. Every Overview load and
every 30-second map refresh would otherwise hit Kea several times per open
browser. Results are the same for admins and viewers (both only read here),
so entries are keyed by name alone. A failed load is never cached, so the
next request tries Kea again.
"""

import threading
import time
from typing import Any, Callable

_entries: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def cached(key: str, ttl_seconds: float, load: Callable[[], Any]) -> Any:
	"""Return the stored value for key if younger than ttl_seconds, else load() and store it."""
	assert ttl_seconds > 0, "ttl_seconds must be positive"
	now = time.monotonic()
	with _lock:
		stored = _entries.get(key)
		if stored and now - stored[0] < ttl_seconds:
			return stored[1]
	value = load()
	with _lock:
		_entries[key] = (time.monotonic(), value)
	return value


def forget(*keys: str) -> None:
	"""Drop entries after a write, so the next page shows the change at once."""
	with _lock:
		for key in keys:
			_entries.pop(key, None)


def clear() -> None:
	"""Drop everything. Used by tests."""
	with _lock:
		_entries.clear()
