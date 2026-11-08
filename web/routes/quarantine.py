"""
quarantine.py — The Quarantine screen: the device registry, per-device and
group Quarantine/Release, and the audit log.

Paths sit under /devices and /groups rather than /quarantine/... because the
Overview already owns POST /quarantine/{name}: a device called "devices" or a
group called "release" would otherwise collide with a sibling route.

Every write is admin-only. Two HX-Trigger events keep the page live without a
reload: "registry-changed" redraws the device table after a dialog closes, and
"quarantine-changed" redraws the audit log after anything that writes to it.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response

import db
from helpers import distinct_real_hostnames
from kea import KeaClient
from quarantine_client import (
	QuarantineServiceError,
	trigger_presence_check,
	trigger_quarantine,
	trigger_release,
)
from web.deps import current_user, kea_client, require_admin
from web.kea_reads import read_kea
from web.quarantine_data import (
	MAX_RELEASE_ATTEMPTS_BEFORE_FORCE,
	ReleaseAttempts,
	device_rows,
	enforcement_succeeded,
	group_tags,
	log_rows,
	step_outcomes,
	validate_device,
)
from web.templating import render

router = APIRouter()

REGISTRY_CHANGED_EVENT = "registry-changed"
QUARANTINE_CHANGED_EVENT = "quarantine-changed"
_LOG_ROWS = 50
_HTTP_NOT_FOUND = 404

# One per process, like the Streamlit tab's per-session counter but shared by
# every admin: a release that keeps failing fails for everyone.
release_attempts = ReleaseAttempts()


def _is_admin(user: dict) -> bool:
	return user.get("role") == "admin"


def _registry_context(notice: Optional[dict] = None) -> dict:
	devices = db.get_devices()
	return {"devices": device_rows(devices), "groups": group_tags(devices), "notice": notice}


def _log_context() -> dict:
	return {"log": log_rows(db.get_quarantine_log(limit=_LOG_ROWS))}


def _device_or_404(friendly_name: str) -> dict:
	device = db.get_device(friendly_name)
	if device is None:
		raise HTTPException(status_code=_HTTP_NOT_FOUND, detail="No such registered device.")
	return device


# --- Pages and fragments --------------------------------------------------------------


@router.get("/quarantine")
def quarantine_page(request: Request, user: dict = Depends(current_user)) -> Response:
	context = {"user": user, "is_admin": _is_admin(user), **_registry_context(), **_log_context()}
	return render(request, "quarantine.html", context)


@router.get("/fragments/quarantine-registry")
def registry_fragment(request: Request, user: dict = Depends(current_user)) -> Response:
	context = {"user": user, "is_admin": _is_admin(user), **_registry_context()}
	return render(request, "quarantine/_registry.html", context)


@router.get("/fragments/quarantine-log")
def log_fragment(request: Request, user: dict = Depends(current_user)) -> Response:
	return render(request, "quarantine/_log.html", {"user": user, **_log_context()})


# --- Quarantine and Release -----------------------------------------------------------


def _run_action(
	request: Request, user: dict, target: str, release: bool, is_group: bool
) -> Response:
	"""Call the quarantine service and answer with the registry plus the outcome."""
	label = "Release" if release else "Quarantine"
	trigger = trigger_release if release else trigger_quarantine
	try:
		result = trigger(target, is_group=is_group)
		outcomes = step_outcomes(result)
		notice = {
			"ok": enforcement_succeeded(result),
			"text": result.get("summary") or f"{label} finished.",
			"outcomes": outcomes,
		}
	except QuarantineServiceError as exc:
		notice = {"ok": False, "text": f"{label} failed for {target}: {exc}", "outcomes": []}
	context = {"user": user, "is_admin": True, **_registry_context(notice)}
	response = render(request, "quarantine/_registry.html", context)
	response.headers["HX-Trigger"] = QUARANTINE_CHANGED_EVENT
	return response


@router.post("/devices/{friendly_name}/quarantine")
def quarantine_device(
	friendly_name: str, request: Request, user: dict = Depends(require_admin)
) -> Response:
	_device_or_404(friendly_name)
	return _run_action(request, user, friendly_name, release=False, is_group=False)


@router.post("/devices/{friendly_name}/release")
def release_device(
	friendly_name: str, request: Request, user: dict = Depends(require_admin)
) -> Response:
	_device_or_404(friendly_name)
	return _run_action(request, user, friendly_name, release=True, is_group=False)


def _group_or_404(tag: str) -> None:
	if tag not in {group["tag"] for group in group_tags(db.get_devices())}:
		raise HTTPException(status_code=_HTTP_NOT_FOUND, detail="No device carries that group tag.")


@router.post("/groups/{tag}/quarantine")
def quarantine_group(tag: str, request: Request, user: dict = Depends(require_admin)) -> Response:
	_group_or_404(tag)
	return _run_action(request, user, tag, release=False, is_group=True)


@router.post("/groups/{tag}/release")
def release_group(tag: str, request: Request, user: dict = Depends(require_admin)) -> Response:
	_group_or_404(tag)
	return _run_action(request, user, tag, release=True, is_group=True)


# --- Registry entries (the dialog) ----------------------------------------------------


def _hostname_options(kea: KeaClient, current_hostname: str) -> list[str]:
	"""Hostnames Kea has seen on a live lease, plus the entry's own if it has gone quiet.

	A reservation label never appears on a lease, so offering only observed
	hostnames stops a device being registered under a name the quarantine
	service can never match.
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
	name = existing["friendly_name"] if existing else ""
	context = {
		"values": values,
		"existing": existing,
		"error": error,
		"hostname_options": _hostname_options(kea, values.get("hostname", "")),
		"failed_releases": release_attempts.count(name) if existing else 0,
		"may_force": release_attempts.may_force(name) if existing else False,
		"max_release_attempts": MAX_RELEASE_ATTEMPTS_BEFORE_FORCE,
	}
	return render(request, "quarantine/_device_form.html", context)


def _form_values(device: Optional[dict]) -> dict:
	device = device or {}
	return {
		"friendly_name": device.get("friendly_name", ""),
		"hostname": device.get("hostname", ""),
		"group_tag": device.get("group_tag", ""),
		"notes": device.get("notes", ""),
	}


def _closed_dialog() -> Response:
	return Response(
		headers={
			"HX-Trigger": f"{REGISTRY_CHANGED_EVENT}, {QUARANTINE_CHANGED_EVENT}, close-dialog"
		}
	)


def _check_presence(friendly_name: str) -> None:
	"""Best effort: fill in Last MAC / IP / Seen now, not at the service's next 5-minute pass."""
	try:
		trigger_presence_check(friendly_name)
	except QuarantineServiceError:
		pass


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
	_check_presence(values["friendly_name"])
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
	# upsert_device overwrites every column, so everything the service owns
	# (fingerprint, last-seen breadcrumbs, quarantine state) is carried over.
	db.upsert_device(
		friendly_name,
		values["hostname"],
		group_tag=values["group_tag"],
		os_fingerprint=existing.get("os_fingerprint", ""),
		last_seen_mac_address=existing.get("last_seen_mac_address", ""),
		last_seen_ip_address=existing.get("last_seen_ip_address", ""),
		last_seen_at=existing.get("last_seen_at", ""),
		last_quarantined_at=existing.get("last_quarantined_at", ""),
		is_quarantined=bool(existing.get("is_quarantined")),
		notes=values["notes"],
	)
	_check_presence(friendly_name)
	return _closed_dialog()


@router.delete("/devices/{friendly_name}")
def delete_device(
	friendly_name: str,
	request: Request,
	force: bool = Query(False),
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	"""Delete a registry entry, releasing the device first when it is quarantined.

	The registry row is the only way back to a device's Kea DROP class,
	Pi-hole overrides and ARP thread, so a quarantined device is released
	before its row goes. Force is honoured only once the release has failed
	MAX_RELEASE_ATTEMPTS_BEFORE_FORCE times.
	"""
	existing = _device_or_404(friendly_name)
	if not existing.get("is_quarantined") or (force and release_attempts.may_force(friendly_name)):
		return _delete(friendly_name)

	try:
		result = trigger_release(friendly_name)
		released = enforcement_succeeded(result)
		reason = result.get("summary") or "Some release steps failed."
	except QuarantineServiceError as exc:
		released, reason = False, str(exc)
	if released:
		return _delete(friendly_name)

	release_attempts.record_failure(friendly_name)
	error = f"Not deleted: the release did not fully succeed. {reason}"
	return _device_form(request, kea, _form_values(existing), db.get_device(friendly_name), error)


def _delete(friendly_name: str) -> Response:
	db.delete_device(friendly_name)
	release_attempts.clear(friendly_name)
	return _closed_dialog()
