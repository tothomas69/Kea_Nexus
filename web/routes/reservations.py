"""
reservations.py — The Reservations screen: Kea host reservations with
KeaNexus's own labels, and add, edit and delete for admins.

Every write reads Kea's config fresh, never from the cache: the quarantine
service edits the same config (its DROP class), and saving a copy up to two
minutes old would undo whatever it changed in between. Labels are written to
the database only after Kea accepts the change, so a refused save leaves both
sides as they were.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

import db
from kea import KeaClient, KeaError
from web import cache
from web.deps import current_user, kea_client, require_admin
from web.kea_reads import read_kea
from web.leases_data import normalized_mac
from web.reservations_data import (
	find_reservation,
	reservation_rows,
	validate_reservation,
	with_reservation_removed,
	with_reservation_saved,
)
from web.templating import render

router = APIRouter()

RESERVATIONS_CHANGED_EVENT = "reservations-changed"
_HTTP_NOT_FOUND = 404


def _labels() -> dict[str, str]:
	return {row["mac_address"]: row["label"] for row in db.get_reservation_labels()}


def _table_context(kea: KeaClient) -> dict:
	data = read_kea(kea)
	rows = reservation_rows(data["config"], data["leases"], _labels())
	return {"rows": rows, "kea_error": data["config"] is None}


@router.get("/reservations")
def reservations_page(
	request: Request, user: dict = Depends(current_user), kea: KeaClient = Depends(kea_client)
) -> Response:
	context = {"user": user, "is_admin": user.get("role") == "admin", **_table_context(kea)}
	return render(request, "reservations.html", context)


@router.get("/fragments/reservations")
def reservations_fragment(
	request: Request, user: dict = Depends(current_user), kea: KeaClient = Depends(kea_client)
) -> Response:
	context = {"user": user, "is_admin": user.get("role") == "admin", **_table_context(kea)}
	return render(request, "reservations/_table.html", context)


# --- The dialog -----------------------------------------------------------------------


def _form(
	request: Request, values: dict, original_mac: str, error: Optional[str] = None
) -> Response:
	context = {"values": values, "original_mac": original_mac, "error": error}
	return render(request, "reservations/_reservation_form.html", context)


def _fresh_config(kea: KeaClient) -> dict:
	"""Kea's config straight from Kea, for a write. KeaError propagates to the caller."""
	return kea.get_config()


def _saved() -> Response:
	cache.forget("config", "leases")
	return Response(headers={"HX-Trigger": f"{RESERVATIONS_CHANGED_EVENT}, close-dialog"})


@router.get("/reservations/new")
def new_reservation_form(request: Request, user: dict = Depends(require_admin)) -> Response:
	return _form(request, {"mac": "", "ip": "", "label": ""}, "")


@router.get("/reservations/{mac}")
def edit_reservation_form(
	mac: str,
	request: Request,
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	mac = normalized_mac(mac)
	reservation = find_reservation(read_kea(kea)["config"], mac)
	if reservation is None:
		raise HTTPException(status_code=_HTTP_NOT_FOUND, detail="No reservation for that MAC.")
	values = {
		"mac": mac,
		"ip": reservation.get("ip-address") or "",
		"label": _labels().get(mac) or reservation.get("hostname") or "",
	}
	return _form(request, values, mac)


def _save(
	request: Request, user: dict, kea: KeaClient, values: dict, original_mac: str
) -> Response:
	try:
		config = _fresh_config(kea)
	except KeaError as exc:
		return _form(
			request, values, original_mac, f"Kea can't be reached, so nothing was saved: {exc}"
		)
	if original_mac and find_reservation(config, original_mac) is None:
		raise HTTPException(
			status_code=_HTTP_NOT_FOUND, detail="That reservation no longer exists."
		)
	error = validate_reservation(values["mac"], values["ip"], values["label"], config, original_mac)
	if error:
		return _form(request, values, original_mac, error)
	try:
		kea.save_config(with_reservation_saved(config, original_mac, values["mac"], values["ip"]))
	except KeaError as exc:
		return _form(request, values, original_mac, f"Kea refused the change: {exc}")
	if original_mac and original_mac != values["mac"]:
		db.delete_reservation_label(original_mac)
	db.upsert_reservation_label(values["mac"], values["label"])
	verb = "edited the reservation for" if original_mac else "added a reservation for"
	address = values["ip"] or "no fixed address"
	db.insert_activity(user["username"], f"{verb} {values['label']} ({values['mac']}, {address})")
	return _saved()


def _form_values(mac: str, ip: str, label: str) -> dict:
	return {"mac": normalized_mac(mac), "ip": ip.strip(), "label": label.strip()}


@router.post("/reservations")
def add_reservation(
	request: Request,
	mac: str = Form(""),
	ip: str = Form(""),
	label: str = Form(""),
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	return _save(request, user, kea, _form_values(mac, ip, label), "")


@router.put("/reservations/{original_mac}")
def update_reservation(
	original_mac: str,
	request: Request,
	mac: str = Form(""),
	ip: str = Form(""),
	label: str = Form(""),
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	return _save(request, user, kea, _form_values(mac, ip, label), normalized_mac(original_mac))


@router.delete("/reservations/{mac}")
def delete_reservation(
	mac: str,
	request: Request,
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	mac = normalized_mac(mac)
	try:
		config = _fresh_config(kea)
	except KeaError as exc:
		return _delete_refused(
			request, mac, None, f"Kea can't be reached, so nothing was deleted: {exc}"
		)
	reservation = find_reservation(config, mac)
	if reservation is None:
		raise HTTPException(status_code=_HTTP_NOT_FOUND, detail="No reservation for that MAC.")
	if "DROP" in (reservation.get("client-classes") or []):
		# Deleting this entry would also delete the quarantine service's DROP
		# class, putting the device back on DHCP behind the service's back.
		return _delete_refused(
			request,
			mac,
			config,
			"This device is quarantined. Release it on the Quarantine screen first.",
		)
	try:
		kea.save_config(with_reservation_removed(config, mac))
	except KeaError as exc:
		return _delete_refused(request, mac, config, f"Kea refused the change: {exc}")
	db.delete_reservation_label(mac)
	db.insert_activity(user["username"], f"deleted the reservation for {mac}")
	return _saved()


def _delete_refused(request: Request, mac: str, config: Optional[dict], error: str) -> Response:
	"""Redraw the edit form with why the delete didn't happen."""
	reservation = find_reservation(config, mac) or {}
	values = {
		"mac": mac,
		"ip": reservation.get("ip-address") or "",
		"label": _labels().get(mac, ""),
	}
	return _form(request, values, mac, error)
