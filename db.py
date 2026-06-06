"""
db.py — SQLite persistence layer for KeaNexus.
Manages the local database for IPAM static address records.
"""
import sqlite3
from pathlib import Path
from typing import Optional

_DB_PATH = Path("/app/data/keanexus.db")


def _connect() -> sqlite3.Connection:
	_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
	conn = sqlite3.connect(str(_DB_PATH))
	conn.row_factory = sqlite3.Row
	return conn


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
		conn.commit()


def get_static_entries() -> list[dict]:
	"""Return all static IPAM entries sorted by IP address."""
	with _connect() as conn:
		rows = conn.execute(
			"SELECT * FROM ipam_static ORDER BY ip_address"
		).fetchall()
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
		conn.execute(
			"DELETE FROM ipam_static WHERE ip_address = ?", (ip_address,)
		)
		conn.commit()
