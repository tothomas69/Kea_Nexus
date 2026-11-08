"""
maintenance.py — The Maintenance screen: turn DHCP off and on, clear declined
leases, and wipe the lease database. Every action here is admin-only and
changes Kea at once, so each risky one asks for confirmation first.
"""

import time
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

from kea import KeaClient, KeaError
from web import cache
from web.deps import current_user, kea_client, require_admin
from web.kea_reads import read_kea
from web.maintenance_data import (
	DEFAULT_AUTO_ENABLE_SECONDS,
	MAX_AUTO_ENABLE_SECONDS,
	WIPE_CONFIRMATION_WORD,
	declined_rows,
	validate_auto_enable,
	wipe_confirmed,
)
from web.templating import render

router = APIRouter()

MAINTENANCE_CHANGED_EVENT = "maintenance-changed"
_HTTP_NOT_FOUND = 404
_DEFAULT_SUBNET_ID = 1


def _dhcp_state(kea: KeaClient) -> dict:
	"""Never cached: after a disable with a timer, Kea turns DHCP back on by itself."""
	try:
		enabled = kea.get_dhcp_enabled()
	except KeaError:
		return {"dhcp_enabled": None, "dhcp_unreachable": True}
	return {"dhcp_enabled": enabled, "dhcp_unreachable": False}


def _context(kea: KeaClient, user: dict, notice: Optional[dict] = None) -> dict:
	data = read_kea(kea)
	return {
		"user": user,
		"is_admin": user.get("role") == "admin",
		"declined": declined_rows(data["leases"], int(time.time())),
		"lease_count": len(data["leases"]),
		"kea_error": data["kea_error"],
		"notice": notice,
		"default_auto_enable": DEFAULT_AUTO_ENABLE_SECONDS,
		"max_auto_enable": MAX_AUTO_ENABLE_SECONDS,
		"wipe_word": WIPE_CONFIRMATION_WORD,
		**_dhcp_state(kea),
	}


def _panels(
	request: Request, kea: KeaClient, user: dict, notice: Optional[dict] = None
) -> Response:
	return render(request, "maintenance/_panels.html", _context(kea, user, notice))


@router.get("/maintenance")
def maintenance_page(
	request: Request, user: dict = Depends(current_user), kea: KeaClient = Depends(kea_client)
) -> Response:
	return render(request, "maintenance.html", _context(kea, user))


@router.get("/fragments/maintenance")
def maintenance_fragment(
	request: Request, user: dict = Depends(current_user), kea: KeaClient = Depends(kea_client)
) -> Response:
	return _panels(request, kea, user)


# --- DHCP service -----------------------------------------------------------------------


@router.get("/maintenance/dhcp/disable")
def disable_form(request: Request, user: dict = Depends(require_admin)) -> Response:
	context = {
		"seconds": str(DEFAULT_AUTO_ENABLE_SECONDS),
		"max_seconds": MAX_AUTO_ENABLE_SECONDS,
		"error": None,
	}
	return render(request, "maintenance/_disable_form.html", context)


@router.post("/maintenance/dhcp/disable")
def disable_dhcp(
	request: Request,
	seconds: str = Form(str(DEFAULT_AUTO_ENABLE_SECONDS)),
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	error, auto_enable_seconds = validate_auto_enable(seconds.strip())
	if error is None:
		try:
			kea.disable_dhcp(max_period=auto_enable_seconds)
		except KeaError as exc:
			error = f"Kea refused: {exc}"
	if error:
		context = {"seconds": seconds, "max_seconds": MAX_AUTO_ENABLE_SECONDS, "error": error}
		return render(request, "maintenance/_disable_form.html", context)
	return Response(headers={"HX-Trigger": f"{MAINTENANCE_CHANGED_EVENT}, close-dialog"})


@router.post("/maintenance/dhcp/enable")
def enable_dhcp(
	request: Request, user: dict = Depends(require_admin), kea: KeaClient = Depends(kea_client)
) -> Response:
	try:
		kea.enable_dhcp()
		notice = {"ok": True, "text": "DHCP is on again."}
	except KeaError as exc:
		notice = {"ok": False, "text": f"Kea refused: {exc}"}
	return _panels(request, kea, user, notice)


# --- Declined leases ------------------------------------------------------------------


def _clear(kea: KeaClient, addresses: list[str]) -> list[str]:
	"""Delete each lease; return one message per address Kea refused."""
	errors = []
	for address in addresses:
		try:
			kea.delete_lease(address)
		except KeaError as exc:
			errors.append(f"{address}: {exc}")
	cache.forget("leases", "pool_stats")
	return errors


def _cleared_notice(cleared: int, errors: list[str]) -> dict:
	if errors:
		return {"ok": False, "text": "Some leases weren't cleared. " + "; ".join(errors)}
	return {"ok": True, "text": f"Cleared {cleared} declined lease{'s' if cleared != 1 else ''}."}


@router.post("/maintenance/declined/clear")
def clear_all_declined(
	request: Request, user: dict = Depends(require_admin), kea: KeaClient = Depends(kea_client)
) -> Response:
	addresses = [row["ip"] for row in declined_rows(read_kea(kea)["leases"], int(time.time()))]
	errors = _clear(kea, addresses)
	return _panels(request, kea, user, _cleared_notice(len(addresses) - len(errors), errors))


@router.post("/maintenance/declined/{ip}/clear")
def clear_one_declined(
	ip: str,
	request: Request,
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	declined = {row["ip"] for row in declined_rows(read_kea(kea)["leases"], int(time.time()))}
	if ip not in declined:
		# Only declined leases: this button must never become a way to delete a live lease.
		raise HTTPException(
			status_code=_HTTP_NOT_FOUND, detail="No declined lease for that address."
		)
	errors = _clear(kea, [ip])
	return _panels(request, kea, user, _cleared_notice(1 - len(errors), errors))


# --- Wipe --------------------------------------------------------------------------------


@router.get("/maintenance/wipe")
def wipe_form(
	request: Request, user: dict = Depends(require_admin), kea: KeaClient = Depends(kea_client)
) -> Response:
	context = {
		"lease_count": len(read_kea(kea)["leases"]),
		"wipe_word": WIPE_CONFIRMATION_WORD,
		"error": None,
	}
	return render(request, "maintenance/_wipe_form.html", context)


@router.post("/maintenance/wipe")
def wipe_leases(
	request: Request,
	confirm: str = Form(""),
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	error = None
	if not wipe_confirmed(confirm):
		error = f"Type {WIPE_CONFIRMATION_WORD} to confirm. Nothing was wiped."
	else:
		try:
			kea.wipe_leases(subnet_id=_subnet_id(kea))
		except KeaError as exc:
			error = f"Kea refused: {exc}"
	if error:
		context = {
			"lease_count": len(read_kea(kea)["leases"]),
			"wipe_word": WIPE_CONFIRMATION_WORD,
			"error": error,
		}
		return render(request, "maintenance/_wipe_form.html", context)
	cache.forget("leases", "pool_stats")
	return Response(
		headers={"HX-Trigger": f"{MAINTENANCE_CHANGED_EVENT}, leases-changed, close-dialog"}
	)


def _subnet_id(kea: KeaClient) -> int:
	"""The subnet's own id from Kea's config; 1, Kea's usual first id, if it has none."""
	config = read_kea(kea)["config"] or {}
	return ((config.get("subnet4") or [{}])[0]).get("id", _DEFAULT_SUBNET_ID)
