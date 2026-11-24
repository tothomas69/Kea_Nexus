"""
devices.py — The Devices screen: the device registry, where a device gets a
friendly name, a group tag and notes.

The table shows each device's current IP and MAC from its live lease (matched
by hostname) and whether it answered the latest "who's online" sweep, so
nothing on it goes stale between visits.

Every write is admin-only. A dialog save or delete sends "devices-changed",
which redraws the table.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

import db
from helpers import distinct_real_hostnames
from kea import KeaClient
from web import liveness
from web.deps import current_user, kea_client, require_admin
from web.devices_data import device_rows, group_tags, validate_device
from web.kea_reads import read_kea
from web.templating import render

router = APIRouter()

DEVICES_CHANGED_EVENT = "devices-changed"
_HTTP_NOT_FOUND = 404


def _is_admin(user: dict) -> bool:
	return user.get("role") == "admin"


def _registry_context(kea: KeaClient) -> dict:
	devices = db.get_devices()
	sweep = liveness.latest()
	seen_macs = sweep.seen_macs if sweep else None
	return {
		"devices": device_rows(devices, read_kea(kea)["leases"], seen_macs),
		"groups": group_tags(devices),
	}


def _device_or_404(friendly_name: str) -> dict:
	device = db.get_device(friendly_name)
	if device is None:
		raise HTTPException(status_code=_HTTP_NOT_FOUND, detail="No such registered device.")
	return device


# --- Page and fragment ----------------------------------------------------------------


@router.get("/devices")
def devices_page(
	request: Request, user: dict = Depends(current_user), kea: KeaClient = Depends(kea_client)
) -> Response:
	context = {"user": user, "is_admin": _is_admin(user), **_registry_context(kea)}
	return render(request, "devices.html", context)


@router.get("/fragments/devices")
def devices_fragment(
	request: Request, user: dict = Depends(current_user), kea: KeaClient = Depends(kea_client)
) -> Response:
	context = {"user": user, "is_admin": _is_admin(user), **_registry_context(kea)}
	return render(request, "devices/_registry.html", context)


# --- Registry entries (the dialog) ----------------------------------------------------


def _hostname_options(kea: KeaClient, current_hostname: str) -> list[str]:
	"""Hostnames Kea has seen on a live lease, plus the entry's own if it has gone quiet.

	A reservation label never appears on a lease, so offering only observed
	hostnames stops a device being registered under a name that never matches.
	"""
	data = read_kea(kea)
	options = distinct_real_hostnames(data["leases"], data["config"])
	if current_hostname and current_hostname not in options:
		options = sorted({*options, current_hostname})
	return options


def _device_form(
	request: Request,
	kea: KeaClient,
	values: dict,
	existing: Optional[dict],
	error: Optional[str] = None,
) -> Response:
	context = {
		"values": values,
		"existing": existing,
		"error": error,
		"hostname_options": _hostname_options(kea, values.get("hostname", "")),
	}
	return render(request, "devices/_device_form.html", context)


def _form_values(device: Optional[dict]) -> dict:
	device = device or {}
	return {
		"friendly_name": device.get("friendly_name", ""),
		"hostname": device.get("hostname", ""),
		"group_tag": device.get("group_tag", ""),
		"notes": device.get("notes", ""),
	}


def _closed_dialog() -> Response:
	return Response(headers={"HX-Trigger": f"{DEVICES_CHANGED_EVENT}, close-dialog"})


@router.get("/devices/new")
def new_device_form(
	request: Request, user: dict = Depends(require_admin), kea: KeaClient = Depends(kea_client)
) -> Response:
	return _device_form(request, kea, _form_values(None), None)


@router.get("/devices/{friendly_name}")
def edit_device_form(
	friendly_name: str,
	request: Request,
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	device = _device_or_404(friendly_name)
	return _device_form(request, kea, _form_values(device), device)


@router.post("/devices")
def create_device(
	request: Request,
	friendly_name: str = Form(""),
	hostname: str = Form(""),
	group_tag: str = Form(""),
	notes: str = Form(""),
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	values = {
		"friendly_name": friendly_name.strip(),
		"hostname": hostname.strip(),
		"group_tag": group_tag.strip(),
		"notes": notes.strip(),
	}
	name_taken = db.get_device(values["friendly_name"]) is not None
	error = validate_device(values["friendly_name"], values["hostname"], True, name_taken)
	if error:
		return _device_form(request, kea, values, None, error)
	db.upsert_device(
		values["friendly_name"],
		values["hostname"],
		group_tag=values["group_tag"],
		notes=values["notes"],
	)
	db.insert_activity(user["username"], f"registered {values['friendly_name']}")
	return _closed_dialog()


@router.put("/devices/{friendly_name}")
def update_device(
	friendly_name: str,
	request: Request,
	hostname: str = Form(""),
	group_tag: str = Form(""),
	notes: str = Form(""),
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	existing = _device_or_404(friendly_name)
	values = {
		"friendly_name": friendly_name,
		"hostname": hostname.strip(),
		"group_tag": group_tag.strip(),
		"notes": notes.strip(),
	}
	error = validate_device(friendly_name, values["hostname"], False, True)
	if error:
		return _device_form(request, kea, values, existing, error)
	db.upsert_device(
		friendly_name, values["hostname"], group_tag=values["group_tag"], notes=values["notes"]
	)
	db.insert_activity(user["username"], f"edited the registry entry for {friendly_name}")
	return _closed_dialog()


@router.delete("/devices/{friendly_name}")
def delete_device(
	friendly_name: str, request: Request, user: dict = Depends(require_admin)
) -> Response:
	_device_or_404(friendly_name)
	db.delete_device(friendly_name)
	db.insert_activity(user["username"], f"deleted the registry entry for {friendly_name}")
	return _closed_dialog()
