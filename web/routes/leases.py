"""
leases.py — The Leases screen: every active lease, a live filter, the
"Check who's online" sweep, and adding or editing a lease by hand.

The sweep is open to viewers, as in the Streamlit tab: it changes nothing in
Kea or the database. Adding and editing leases are admin-only, and a viewer's
Kea client refuses them again underneath (ReadOnlyKeaClient).
"""

import time
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response

from kea import KeaClient, KeaError
from quarantine_client import QuarantineServiceError
from web import cache, liveness
from web.deps import current_user, kea_client, require_admin
from web.kea_reads import read_kea
from web.leases_data import (
	DEFAULT_LEASE_SECONDS,
	MAX_LEASE_SECONDS,
	MIN_LEASE_SECONDS,
	filter_rows,
	lease_rows,
	normalized_mac,
	validate_lease,
)
from web.templating import render

router = APIRouter()

LEASES_CHANGED_EVENT = "leases-changed"
_HTTP_NOT_FOUND = 404


def _subnet(config: Optional[dict]) -> str:
	return ((config or {}).get("subnet4") or [{}])[0].get("subnet", "")


def _table_context(kea: KeaClient, query: str, sweep_error: Optional[str] = None) -> dict:
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
		"sweep_error": sweep_error,
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
	"""ARP-sweep every leased address, not just the filtered ones, so the result
	stays valid as the filter changes."""
	addresses = [
		lease["ip-address"] for lease in read_kea(kea)["leases"] if lease.get("ip-address")
	]
	error = None
	try:
		liveness.sweep_now(addresses)
	except QuarantineServiceError as exc:
		error = f"The sweep needs the quarantine service, which didn't answer: {exc}"
	context = {
		"user": user,
		"is_admin": user.get("role") == "admin",
		**_table_context(kea, q, error),
	}
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


def _save_lease(request: Request, kea: KeaClient, values: dict, editing: bool) -> Response:
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
	return _save_lease(request, kea, values, editing=False)


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
	return _save_lease(request, kea, values, editing=True)
