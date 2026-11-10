"""
leases.py — The Leases screen: every active lease, a live filter, the
"Check who's online" sweep, adding or editing a lease by hand, and removing
the leases that have expired.

The sweep is open to viewers, as in the Streamlit tab: it changes nothing in
Kea or the database. Adding, editing and removing leases are admin-only, and a
viewer's Kea client refuses them again underneath (ReadOnlyKeaClient).
"""

import time
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response

import db
from kea import KeaClient, KeaError
from quarantine_client import QuarantineServiceError
from web import cache, liveness
from web.deps import current_user, kea_client, require_admin
from web.kea_reads import read_kea
from web.leases_data import (
	DEFAULT_LEASE_SECONDS,
	MAX_LEASE_SECONDS,
	MIN_LEASE_SECONDS,
	expired_lease_ips,
	filter_rows,
	lease_rows,
	normalized_mac,
	validate_lease,
)
from web.templating import render

router = APIRouter()

LEASES_CHANGED_EVENT = "leases-changed"
_HTTP_NOT_FOUND = 404
# The activity log line names the addresses up to this many, then counts the rest.
_MAX_ADDRESSES_IN_ACTIVITY = 10


def _subnet(config: Optional[dict]) -> str:
	return ((config or {}).get("subnet4") or [{}])[0].get("subnet", "")


def _table_context(
	kea: KeaClient,
	query: str,
	sweep_error: Optional[str] = None,
	notice: Optional[dict] = None,
) -> dict:
	data = read_kea(kea)
	sweep = liveness.latest()
	rows = lease_rows(
		data["leases"], data["config"], sweep.responding if sweep else None, int(time.time())
	)
	shown = filter_rows(rows, query)
	return {
		"rows": shown,
		"total": len(rows),
		"query": query,
		"kea_error": data["kea_error"],
		"sweep": sweep,
		"sweep_age": liveness.age_label(sweep, time.time()) if sweep else "",
		"online_count": sum(1 for row in rows if row["online"]),
		"expired_count": sum(1 for row in rows if row["expired"]),
		"sweep_error": sweep_error,
		"notice": notice,
	}


@router.get("/leases")
def leases_page(
	request: Request,
	q: str = Query(""),
	user: dict = Depends(current_user),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	context = {"user": user, "is_admin": user.get("role") == "admin", **_table_context(kea, q)}
	return render(request, "leases.html", context)


@router.get("/fragments/leases")
def leases_fragment(
	request: Request,
	q: str = Query(""),
	user: dict = Depends(current_user),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	context = {"user": user, "is_admin": user.get("role") == "admin", **_table_context(kea, q)}
	return render(request, "leases/_table.html", context)


@router.post("/leases/sweep")
def sweep_leases(
	request: Request,
	q: str = Form(""),
	user: dict = Depends(current_user),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	"""ARP-sweep the whole subnet, not just the filtered leases, so the result
	stays valid as the filter changes and the Address Map gets it too."""
	error = None
	try:
		liveness.sweep_network(kea)
	except KeaError as exc:
		error = f"Couldn't read Kea to know what to sweep: {exc}"
	except QuarantineServiceError as exc:
		error = f"The sweep needs the quarantine service, which didn't answer: {exc}"
	context = {
		"user": user,
		"is_admin": user.get("role") == "admin",
		**_table_context(kea, q, error),
	}
	return render(request, "leases/_table.html", context)


# --- Removing expired leases ----------------------------------------------------------


def _address_list(addresses: list[str]) -> str:
	shown = ", ".join(addresses[:_MAX_ADDRESSES_IN_ACTIVITY])
	hidden = len(addresses) - _MAX_ADDRESSES_IN_ACTIVITY
	return f"{shown} and {hidden} more" if hidden > 0 else shown


def _remove_expired(kea: KeaClient, username: str) -> dict:
	"""Delete every expired lease in Kea and say what happened.

	Reads Kea directly, not through the cache: a lease renewed in the last 30
	seconds must not be deleted because a cached copy says it ran out. One
	refused delete does not stop the others.
	"""
	try:
		expired = expired_lease_ips(kea.get_leases(), int(time.time()))
	except KeaError as exc:
		return {"ok": False, "text": f"Couldn't read the leases from Kea: {exc}"}
	if not expired:
		return {"ok": True, "text": "No lease has expired, so nothing was removed."}
	removed, failed = [], []
	for ip in expired:
		try:
			kea.delete_lease(ip)
			removed.append(ip)
		except KeaError as exc:
			failed.append(f"{ip} ({exc})")
	cache.forget("leases", "pool_stats")
	if removed:
		plural = "s" if len(removed) != 1 else ""
		db.insert_activity(
			username, f"removed {len(removed)} expired lease{plural}: {_address_list(removed)}"
		)
	text = f"Removed {len(removed)} expired lease{'s' if len(removed) != 1 else ''}."
	if failed:
		return {"ok": False, "text": f"{text} Kea refused {len(failed)}: {', '.join(failed)}"}
	return {"ok": True, "text": text}


@router.post("/leases/remove-expired")
def remove_expired_leases(
	request: Request,
	q: str = Form(""),
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	notice = _remove_expired(kea, user["username"])
	context = {"user": user, "is_admin": True, **_table_context(kea, q, notice=notice)}
	return render(request, "leases/_table.html", context)


# --- Adding and editing a lease -------------------------------------------------------


def _lease_form(
	request: Request, values: dict, editing: bool, error: Optional[str] = None
) -> Response:
	context = {
		"values": values,
		"editing": editing,
		"error": error,
		"min_seconds": MIN_LEASE_SECONDS,
		"max_seconds": MAX_LEASE_SECONDS,
	}
	return render(request, "leases/_lease_form.html", context)


def _find_lease(kea: KeaClient, ip: str) -> dict:
	for lease in read_kea(kea)["leases"]:
		if lease.get("ip-address") == ip:
			return lease
	raise HTTPException(status_code=_HTTP_NOT_FOUND, detail="No lease for that address.")


def _saved() -> Response:
	cache.forget("leases", "pool_stats")
	return Response(headers={"HX-Trigger": f"{LEASES_CHANGED_EVENT}, close-dialog"})


@router.get("/leases/new")
def new_lease_form(request: Request, user: dict = Depends(require_admin)) -> Response:
	values = {"ip": "", "mac": "", "hostname": "", "duration": str(DEFAULT_LEASE_SECONDS)}
	return _lease_form(request, values, editing=False)


@router.get("/leases/{ip}")
def edit_lease_form(
	ip: str,
	request: Request,
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	lease = _find_lease(kea, ip)
	values = {
		"ip": ip,
		"mac": lease.get("hw-address", ""),
		"hostname": lease.get("hostname", ""),
		"duration": str(lease.get("valid-lft", DEFAULT_LEASE_SECONDS)),
	}
	return _lease_form(request, values, editing=True)


def _save_lease(
	request: Request, user: dict, kea: KeaClient, values: dict, editing: bool
) -> Response:
	error, duration_seconds = validate_lease(
		values["ip"], values["mac"], values["duration"], _subnet(read_kea(kea)["config"])
	)
	if error:
		return _lease_form(request, values, editing, error)
	write = kea.update_lease if editing else kea.add_lease
	try:
		write(values["ip"], values["mac"], values["hostname"], duration_seconds)
	except KeaError as exc:
		return _lease_form(request, values, editing, f"Kea refused the lease: {exc}")
	verb = "edited the lease for" if editing else "added a lease for"
	db.insert_activity(user["username"], f"{verb} {values['ip']} ({values['mac']})")
	return _saved()


@router.post("/leases")
def add_lease(
	request: Request,
	ip: str = Form(""),
	mac: str = Form(""),
	hostname: str = Form(""),
	duration: str = Form(str(DEFAULT_LEASE_SECONDS)),
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	values = {
		"ip": ip.strip(),
		"mac": normalized_mac(mac),
		"hostname": hostname.strip(),
		"duration": duration.strip(),
	}
	return _save_lease(request, user, kea, values, editing=False)


@router.put("/leases/{ip}")
def update_lease(
	ip: str,
	request: Request,
	mac: str = Form(""),
	hostname: str = Form(""),
	duration: str = Form(str(DEFAULT_LEASE_SECONDS)),
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	_find_lease(kea, ip)
	values = {
		"ip": ip,
		"mac": normalized_mac(mac),
		"hostname": hostname.strip(),
		"duration": duration.strip(),
	}
	return _save_lease(request, user, kea, values, editing=True)
