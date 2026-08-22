"""
pool_history.py — Periodic sampler that builds KeaNexus's pool utilisation history.

Kea reports only the present: `stat-lease4-summary` answers "how full is the
pool right now" and keeps no history of its own. Any trend chart therefore has
to be assembled from readings taken over time, which is what the loop here
does — one row in `db.pool_samples` per interval.

**Known limitation, and the reason this lives in the Streamlit container rather
than a service of its own:** Streamlit does not execute `app.py` until a
browser session actually connects (the same trap already documented for
`init_db()` in the quarantine service). So after a container restart, sampling
does not resume until somebody next opens KeaNexus. Once it has started it runs
for the life of the process, independent of whether anyone is still watching,
which is the property that actually matters — the alternative of sampling
inside the page render would only ever record while a browser was open, and
would draw a flat line across every overnight gap.

Consumers must therefore treat gaps as gaps: `db.get_pool_samples` returns
exactly the readings that were taken, and nothing interpolates across a period
where no sample exists.
"""

import logging
import os
import threading

from db import insert_pool_sample, prune_pool_samples
from kea import KeaClient, KeaError

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_INTERVAL_SECONDS = 300.0
DEFAULT_RETENTION_DAYS = 30

_sampler_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None
_registry_lock = threading.Lock()


def _sample_interval_seconds() -> float:
	"""Read the interval from the environment on each call, not at import."""
	raw = os.environ.get("POOL_SAMPLE_INTERVAL_SECONDS", "")
	if not raw:
		return DEFAULT_SAMPLE_INTERVAL_SECONDS
	return max(float(raw), 1.0)


def _retention_days() -> int:
	raw = os.environ.get("POOL_SAMPLE_RETENTION_DAYS", "")
	if not raw:
		return DEFAULT_RETENTION_DAYS
	return max(int(raw), 1)


def take_sample_now(kea: KeaClient | None = None) -> bool:
	"""Take and store one reading. Returns False if Kea was unreachable.

	Kea being down is an ordinary, recoverable condition for a sampler —
	the loop should keep running and pick the series back up when Kea
	returns, so this reports failure rather than raising.
	"""
	client = kea or KeaClient()
	try:
		stats = client.get_pool_stats()
	except KeaError as exc:
		logger.warning("Pool sample skipped, Kea unreachable: %s", exc)
		return False

	insert_pool_sample(
		total=stats["total"],
		assigned=stats["assigned"],
		declined=stats["declined"],
		available=stats["available"],
		cumulative=stats["cumulative"],
	)
	return True


def _run_sampler_loop(stop_event: threading.Event) -> None:
	"""Sample, prune, sleep, repeat until stop_event is set."""
	while not stop_event.is_set():
		try:
			take_sample_now()
			prune_pool_samples(_retention_days())
		except Exception:
			# A sampler that dies on one bad pass silently stops producing
			# history, and nothing else would notice until someone opened
			# the chart and found it frozen. Log and keep going instead.
			logger.exception("Pool sampler pass failed; continuing")
		stop_event.wait(_sample_interval_seconds())


def start_pool_sampler() -> None:
	"""Start the background sampler once per process. Idempotent.

	Called from app.py behind @st.cache_resource, but guarded here too — a
	second loop would double every sample, and the guard costs nothing.
	"""
	global _sampler_thread, _stop_event
	with _registry_lock:
		if _sampler_thread is not None and _sampler_thread.is_alive():
			return
		_stop_event = threading.Event()
		_sampler_thread = threading.Thread(
			target=_run_sampler_loop,
			args=(_stop_event,),
			name="pool-sampler",
			daemon=True,
		)
		_sampler_thread.start()
		logger.info("Pool sampler started at %.0fs interval", _sample_interval_seconds())


def stop_pool_sampler(timeout_seconds: float = 5.0) -> None:
	"""Stop the loop and wait for the thread to finish.

	Production never calls this — the daemon thread dies with the process —
	but a test that started a loop must be able to stop it deterministically
	rather than leaking a thread into the rest of the suite.
	"""
	global _sampler_thread, _stop_event
	with _registry_lock:
		thread, stop_event = _sampler_thread, _stop_event
		_sampler_thread, _stop_event = None, None
	if stop_event is None or thread is None:
		return
	stop_event.set()
	thread.join(timeout_seconds)
	if thread.is_alive():
		raise TimeoutError("pool sampler did not stop within timeout_seconds")


def is_sampling() -> bool:
	"""Whether a sampler loop is currently running in this process."""
	with _registry_lock:
		return _sampler_thread is not None and _sampler_thread.is_alive()
