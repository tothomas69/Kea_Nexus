"""
db.py — SQLite persistence layer for KeaNexus.
Manages the local database for IPAM static address records.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

_DB_PATH = Path("/app/data/keanexus.db")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
	"""Yield a connection, committing/rolling back and always closing it.

	sqlite3.Connection's own context manager only commits or rolls back on
	exit — it never closes the connection, which leaked one handle per call
	across the whole module. Wrapping it here keeps every `with _connect()
	as conn:` call site unchanged while guaranteeing the close.
	"""
	_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
	conn = sqlite3.connect(str(_DB_PATH))
	conn.row_factory = sqlite3.Row
	try:
		with conn:
			yield conn
	finally:
		conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
	"""Add `column` to `table` if it doesn't already exist.

	CREATE TABLE IF NOT EXISTS only helps brand-new databases — an
	already-deployed table (like device_registry on netstack) predates any
	column added after its first deploy, and a plain CREATE TABLE won't
	retrofit it. PRAGMA table_info is the standard SQLite way to check a
	column's existence before ALTERing, so this is safe to call on every
	startup regardless of whether the column is already there.
	"""
	existing_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
	if column not in existing_cols:
		conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
	"""Create the database schema. Safe to call on every startup."""
	with _connect() as conn:
		conn.execute("""
			CREATE TABLE IF NOT EXISTS ipam_static (
				ip_address   TEXT PRIMARY KEY,
				hostname     TEXT NOT NULL DEFAULT '',
				mac_address  TEXT NOT NULL DEFAULT '',
				description  TEXT NOT NULL DEFAULT '',
				notes        TEXT NOT NULL DEFAULT ''
			)
		""")
		conn.execute("""
			CREATE TABLE IF NOT EXISTS device_registry (
				friendly_name          TEXT PRIMARY KEY,
				hostname               TEXT NOT NULL,
				group_tag              TEXT NOT NULL DEFAULT '',
				os_fingerprint         TEXT NOT NULL DEFAULT '',
				last_seen_mac_address  TEXT NOT NULL DEFAULT '',
				last_seen_ip_address   TEXT NOT NULL DEFAULT '',
				last_seen_at           TEXT NOT NULL DEFAULT '',
				last_quarantined_at    TEXT NOT NULL DEFAULT '',
				notes                  TEXT NOT NULL DEFAULT ''
			)
		""")
		# device_registry may already exist from before last_seen_at existed —
		# CREATE TABLE IF NOT EXISTS above is a no-op in that case, so the
		# column has to be retrofitted explicitly.
		_ensure_column(conn, "device_registry", "last_seen_at", "TEXT NOT NULL DEFAULT ''")
		conn.execute("""
			CREATE TABLE IF NOT EXISTS quarantine_log (
				log_id         INTEGER PRIMARY KEY AUTOINCREMENT,
				friendly_name  TEXT NOT NULL,
				action         TEXT NOT NULL,
				step           TEXT NOT NULL,
				succeeded      INTEGER NOT NULL,
				attempt_count  INTEGER NOT NULL,
				detail         TEXT NOT NULL DEFAULT '',
				occurred_at    TEXT NOT NULL
			)
		""")
		conn.commit()


def get_static_entries() -> list[dict]:
	"""Return all static IPAM entries sorted by IP address."""
	with _connect() as conn:
		rows = conn.execute("SELECT * FROM ipam_static ORDER BY ip_address").fetchall()
	return [dict(row) for row in rows]


def get_static_entry(ip_address: str) -> Optional[dict]:
	"""Return a single static entry by IP, or None if not found."""
	with _connect() as conn:
		row = conn.execute(
			"SELECT * FROM ipam_static WHERE ip_address = ?", (ip_address,)
		).fetchone()
	return dict(row) if row else None


def upsert_static_entry(
	ip_address: str,
	hostname: str = "",
	mac_address: str = "",
	description: str = "",
	notes: str = "",
) -> None:
	"""Insert or update a static IPAM entry."""
	with _connect() as conn:
		conn.execute(
			"""
			INSERT INTO ipam_static (ip_address, hostname, mac_address, description, notes)
			VALUES (?, ?, ?, ?, ?)
			ON CONFLICT(ip_address) DO UPDATE SET
				hostname    = excluded.hostname,
				mac_address = excluded.mac_address,
				description = excluded.description,
				notes       = excluded.notes
			""",
			(ip_address, hostname, mac_address, description, notes),
		)
		conn.commit()


def delete_static_entry(ip_address: str) -> None:
	"""Remove a static IPAM entry by IP address."""
	with _connect() as conn:
		conn.execute("DELETE FROM ipam_static WHERE ip_address = ?", (ip_address,))
		conn.commit()


# --- Device registry -----------------------------------------------------
# Source of truth for "who is this device" for the quarantine feature.
# Keyed on friendly_name rather than MAC, since MAC can be changed by the
# device itself. hostname + os_fingerprint are the durable identity signal;
# last_seen_mac_address / last_seen_ip_address are breadcrumbs, not identity.


def get_devices() -> list[dict]:
	"""Return all registered devices sorted by friendly name."""
	with _connect() as conn:
		rows = conn.execute("SELECT * FROM device_registry ORDER BY friendly_name").fetchall()
	return [dict(row) for row in rows]


def get_device(friendly_name: str) -> Optional[dict]:
	"""Return a single device by friendly name, or None if not found."""
	with _connect() as conn:
		row = conn.execute(
			"SELECT * FROM device_registry WHERE friendly_name = ?", (friendly_name,)
		).fetchone()
	return dict(row) if row else None


def upsert_device(
	friendly_name: str,
	hostname: str,
	group_tag: str = "",
	os_fingerprint: str = "",
	last_seen_mac_address: str = "",
	last_seen_ip_address: str = "",
	last_seen_at: str = "",
	last_quarantined_at: str = "",
	notes: str = "",
) -> None:
	"""Insert or update a device registry entry."""
	assert friendly_name, "friendly_name must be a non-empty string"
	assert hostname, "hostname must be a non-empty string"
	with _connect() as conn:
		conn.execute(
			"""
			INSERT INTO device_registry (
				friendly_name, hostname, group_tag, os_fingerprint,
				last_seen_mac_address, last_seen_ip_address, last_seen_at,
				last_quarantined_at, notes
			)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
			ON CONFLICT(friendly_name) DO UPDATE SET
				hostname               = excluded.hostname,
				group_tag              = excluded.group_tag,
				os_fingerprint         = excluded.os_fingerprint,
				last_seen_mac_address  = excluded.last_seen_mac_address,
				last_seen_ip_address   = excluded.last_seen_ip_address,
				last_seen_at           = excluded.last_seen_at,
				last_quarantined_at    = excluded.last_quarantined_at,
				notes                  = excluded.notes
			""",
			(
				friendly_name,
				hostname,
				group_tag,
				os_fingerprint,
				last_seen_mac_address,
				last_seen_ip_address,
				last_seen_at,
				last_quarantined_at,
				notes,
			),
		)
		conn.commit()


def touch_last_seen(friendly_name: str, mac_address: str = "", ip_address: str = "") -> None:
	"""Stamp last_seen_at = now for a device, confirmed alive right now.

	Called by the quarantine service's periodic ARP presence probe
	(presence_check.py) whenever a device answers an ARP request —
	independent of whether it's ever been quarantined. mac_address/
	ip_address are optional breadcrumbs; pass empty to leave the existing
	values untouched (e.g. if the caller only confirmed presence, not a
	fresh MAC/IP pair). No-ops if the device isn't registered.
	"""
	existing = get_device(friendly_name)
	if existing is None:
		return
	upsert_device(
		existing["friendly_name"],
		existing["hostname"],
		group_tag=existing["group_tag"],
		os_fingerprint=existing["os_fingerprint"],
		last_seen_mac_address=mac_address or existing["last_seen_mac_address"],
		last_seen_ip_address=ip_address or existing["last_seen_ip_address"],
		last_seen_at=datetime.now(timezone.utc).isoformat(),
		last_quarantined_at=existing["last_quarantined_at"],
		notes=existing["notes"],
	)


def delete_device(friendly_name: str) -> None:
	"""Remove a device registry entry by friendly name."""
	with _connect() as conn:
		conn.execute("DELETE FROM device_registry WHERE friendly_name = ?", (friendly_name,))
		conn.commit()


# --- Quarantine log --------------------------------------------------------
# Append-only audit trail written by the (future) keanexus-quarantine
# service. Lives here now so the schema and read path can be built and
# tested ahead of the orchestration logic that will write to it.


def insert_quarantine_log_entry(
	friendly_name: str,
	action: str,
	step: str,
	succeeded: bool,
	attempt_count: int,
	detail: str = "",
) -> None:
	"""Append one row to the quarantine audit log.

	action is "quarantine" or "release"; step is "arp", "pihole", "kea", or
	"nmap_refresh". occurred_at is stamped here as UTC so callers never need
	to reason about timezones.
	"""
	assert friendly_name, "friendly_name must be a non-empty string"
	assert action in ("quarantine", "release"), "action must be 'quarantine' or 'release'"
	assert attempt_count >= 1, "attempt_count must be at least 1"
	occurred_at = datetime.now(timezone.utc).isoformat()
	with _connect() as conn:
		conn.execute(
			"""
			INSERT INTO quarantine_log (
				friendly_name, action, step, succeeded, attempt_count, detail, occurred_at
			)
			VALUES (?, ?, ?, ?, ?, ?, ?)
			""",
			(friendly_name, action, step, int(succeeded), attempt_count, detail, occurred_at),
		)
		conn.commit()


def get_quarantine_log(friendly_name: Optional[str] = None, limit: int = 100) -> list[dict]:
	"""Return quarantine log rows, most recent first.

	Filters to a single device when friendly_name is given, otherwise
	returns the most recent entries across all devices.
	"""
	assert limit >= 1, "limit must be at least 1"
	with _connect() as conn:
		if friendly_name:
			rows = conn.execute(
				"""
				SELECT * FROM quarantine_log
				WHERE friendly_name = ?
				ORDER BY log_id DESC
				LIMIT ?
				""",
				(friendly_name, limit),
			).fetchall()
		else:
			rows = conn.execute(
				"SELECT * FROM quarantine_log ORDER BY log_id DESC LIMIT ?", (limit,)
			).fetchall()
	return [dict(row) for row in rows]
