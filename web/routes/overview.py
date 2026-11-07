"""
overview.py — The Overview page: key figures, the Address Map with hover and
pinned details, the Quarantine card, Activity, and the header's health dots.

Writes from this page (Quarantine/Release, static entries) are admin-only and
answer with an "overview-changed" HX-Trigger, which every live panel listens
for, so the map, the card and Activity all redraw right after an action
instead of waiting for their 30-second poll.
"""

import re
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

import db
from address_map import (
	AddressRow,
	classify_addresses,
	gateway_ip,
	last_octet,
	subnet_prefix,
	subnet_reservations,
)
from dashboard_charts import (
	HISTORY_WINDOW_HOURS,
	composition_chart,
	expiry_chart,
	utilisation_chart,
)
from helpers import build_hostname_override_sets
from kea import KeaClient
from quarantine_client import QuarantineServiceError, trigger_quarantine, trigger_release
from web import cache
from web.deps import current_user, kea_client, require_admin
from web.health import service_health
from web.kea_reads import read_kea
from web.overview_data import (
	activity,
	issued_recently,
	legend,
	map_cells,
	pool_figures,
	quarantine_rows,
)
from web.templating import render

router = APIRouter()

_HEALTH_CACHE_SECONDS = 20
_SECONDS_PER_HOUR = 60 * 60
_HISTORY_WINDOW_SECONDS = 26 * _SECONDS_PER_HOUR  # a day of samples, plus the issued-count baseline
_ACTIVITY_LINES = 6
_ACTIVITY_LOG_ROWS = 200

OVERVIEW_CHANGED_EVENT = "overview-changed"
_HTTP_NOT_FOUND = 404
_MAC_PATTERN = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
STATIC_NOT_ALLOWED_MESSAGE = "Static records are only for addresses outside the DHCP pool."
BAD_MAC_MESSAGE = "MAC addresses look like aa:bb:cc:dd:ee:ff."


# --- Reading Kea and the database -----------------------------------------------------


def _address_rows(kea: KeaClient, leases: list[dict], config: Optional[dict]) -> list[AddressRow]:
	pool_start, pool_end, _ = kea.get_pool_range(config)
	override_ips, override_macs = build_hostname_override_sets(config or {})
	labels = {entry["mac_address"].lower(): entry["label"] for entry in db.get_reservation_labels()}
	return classify_addresses(
		subnet_prefix(config),
		last_octet(pool_start),
		last_octet(pool_end),
		gateway_ip(config),
		leases,
		subnet_reservations(config),
		db.get_static_entries(),
		labels,
		override_ips,
		override_macs,
		now_epoch=int(time.time()),
	)


def _map_context(kea: KeaClient) -> dict:
	data = read_kea(kea)
	rows = _address_rows(kea, data["leases"], data["config"])
	return {
		"cells": map_cells(rows, db.get_devices()),
		"legend": legend(),
		"subnet": ((data["config"] or {}).get("subnet4") or [{}])[0].get("subnet", ""),
		"kea_error": data["kea_error"],
		**data,
	}


def _quarantine_context(notice: Optional[dict] = None) -> dict:
	return {"devices": quarantine_rows(db.get_devices()), "notice": notice}


def _activity_context() -> dict:
	log_entries = db.get_quarantine_log(limit=_ACTIVITY_LOG_ROWS)
	return {"activity": activity(log_entries, _ACTIVITY_LINES)}


def _kpi_context(stats: Optional[dict], config: Optional[dict]) -> dict:
	since = datetime.fromtimestamp(time.time() - _HISTORY_WINDOW_SECONDS, tz=timezone.utc)
	samples = db.get_pool_samples(since=since.isoformat())
	devices = db.get_devices()
	reservations = subnet_reservations(config)
	return {
		"pool": pool_figures(stats),
		"issued": issued_recently(samples, datetime.now(timezone.utc)),
		"reservation_count": len(reservations),
		"fixed_count": sum(1 for reservation in reservations if reservation.get("ip-address")),
		"quarantined_names": [d["friendly_name"] for d in devices if d.get("is_quarantined")],
	}


def _charts_context(leases: list[dict], config: Optional[dict]) -> dict:
	"""The three Dashboard charts as Vega-Lite specs plus their table twins.

	Same builders as the Streamlit Dashboard (dashboard_charts.py), so both UIs
	draw identical charts. The trend is None until there are two samples.
	"""
	since = datetime.fromtimestamp(
		time.time() - HISTORY_WINDOW_HOURS * _SECONDS_PER_HOUR, tz=timezone.utc
	)
	trend = utilisation_chart(db.get_pool_samples(since=since.isoformat()))
	expiry, expiry_table = expiry_chart(leases, datetime.now(timezone.utc))
	composition, composition_table = composition_chart(leases, config)
	return {
		"history_hours": HISTORY_WINDOW_HOURS,
		"trend": {"spec": trend[0].to_dict(), "table": trend[1]} if trend else None,
		"expiry": {"spec": expiry.to_dict(), "table": expiry_table},
		"composition": {"spec": composition.to_dict(), "table": composition_table},
	}


def _is_admin(user: dict) -> bool:
	return user["role"] == "admin"


# --- Pages and fragments -----------------------------------------------------------------


@router.get("/")
def overview(
	request: Request, user: dict = Depends(current_user), kea: KeaClient = Depends(kea_client)
) -> Response:
	context = _map_context(kea)
	context.update(_kpi_context(context["stats"], context["config"]))
	context.update(_quarantine_context())
	context.update(_activity_context())
	context.update(_charts_context(context["leases"], context["config"]))
	return render(request, "overview.html", {"user": user, "is_admin": _is_admin(user), **context})


@router.get("/fragments/address-map")
def address_map_fragment(
	request: Request, user: dict = Depends(current_user), kea: KeaClient = Depends(kea_client)
) -> Response:
	return render(request, "overview/_address_map.html", {"user": user, **_map_context(kea)})


@router.get("/fragments/quarantine")
def quarantine_fragment(request: Request, user: dict = Depends(current_user)) -> Response:
	return render(
		request,
		"overview/_quarantine_card.html",
		{"user": user, "is_admin": _is_admin(user), **_quarantine_context()},
	)


@router.get("/fragments/activity")
def activity_fragment(request: Request, user: dict = Depends(current_user)) -> Response:
	return render(request, "overview/_activity.html", {"user": user, **_activity_context()})


@router.get("/fragments/health")
def health_fragment(
	request: Request, user: dict = Depends(current_user), kea: KeaClient = Depends(kea_client)
) -> Response:
	services = cache.cached("health", _HEALTH_CACHE_SECONDS, lambda: service_health(kea))
	return render(request, "overview/_health.html", {"services": services})


# --- Quarantine and Release ----------------------------------------------------------------


def _run_quarantine_action(
	request: Request, user: dict, friendly_name: str, release: bool
) -> Response:
	if db.get_device(friendly_name) is None:
		raise HTTPException(status_code=_HTTP_NOT_FOUND, detail="No such registered device.")
	try:
		result = trigger_release(friendly_name) if release else trigger_quarantine(friendly_name)
		notice = {"ok": True, "text": result.get("summary") or "Done."}
	except QuarantineServiceError as exc:
		notice = {
			"ok": False,
			"text": f"The quarantine service refused or could not be reached: {exc}",
		}
	response = render(
		request,
		"overview/_quarantine_card.html",
		{"user": user, "is_admin": True, **_quarantine_context(notice)},
	)
	response.headers["HX-Trigger"] = OVERVIEW_CHANGED_EVENT
	return response


@router.post("/quarantine/{friendly_name}")
def quarantine_device(
	friendly_name: str, request: Request, user: dict = Depends(require_admin)
) -> Response:
	return _run_quarantine_action(request, user, friendly_name, release=False)


@router.post("/quarantine/{friendly_name}/release")
def release_device(
	friendly_name: str, request: Request, user: dict = Depends(require_admin)
) -> Response:
	return _run_quarantine_action(request, user, friendly_name, release=True)


# --- Static entries (the IPAM tab's job, now on the pinned card) -----------------------------


def _editable_row(kea: KeaClient, ip: str) -> AddressRow:
	"""The address's current row, refusing any address that can't hold a static record."""
	data = read_kea(kea)
	for row in _address_rows(kea, data["leases"], data["config"]):
		if row.ip == ip:
			if not row.editable:
				raise HTTPException(status_code=_HTTP_NOT_FOUND, detail=STATIC_NOT_ALLOWED_MESSAGE)
			return row
	raise HTTPException(status_code=_HTTP_NOT_FOUND, detail="That address is not in this subnet.")


def _static_form(request: Request, ip: str, values: dict, error: Optional[str]) -> Response:
	existing = db.get_static_entry(ip) is not None
	return render(
		request,
		"overview/_static_form.html",
		{"ip": ip, "values": values, "error": error, "existing": existing},
	)


@router.get("/address/{ip}/static")
def static_entry_form(
	ip: str,
	request: Request,
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	row = _editable_row(kea, ip)
	stored = db.get_static_entry(ip) or {}
	values = {
		"hostname": stored.get("hostname", row.label if row.label != "gateway" else ""),
		"mac_address": stored.get("mac_address", row.mac),
		"description": stored.get("description", ""),
		"notes": stored.get("notes", ""),
	}
	return _static_form(request, ip, values, None)


@router.post("/address/{ip}/static")
def save_static_entry(
	ip: str,
	request: Request,
	hostname: str = Form(""),
	mac_address: str = Form(""),
	description: str = Form(""),
	notes: str = Form(""),
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	_editable_row(kea, ip)
	values = {
		"hostname": hostname.strip(),
		"mac_address": mac_address.strip().lower(),
		"description": description.strip(),
		"notes": notes.strip(),
	}
	if values["mac_address"] and not _MAC_PATTERN.match(values["mac_address"]):
		return _static_form(request, ip, values, BAD_MAC_MESSAGE)
	db.upsert_static_entry(
		ip, values["hostname"], values["mac_address"], values["description"], values["notes"]
	)
	return _closed_dialog()


@router.delete("/address/{ip}/static")
def delete_static_entry(
	ip: str,
	request: Request,
	user: dict = Depends(require_admin),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	_editable_row(kea, ip)
	db.delete_static_entry(ip)
	return _closed_dialog()


def _closed_dialog() -> Response:
	"""Empty the dialog and tell the page to close it and redraw the map."""
	return Response(headers={"HX-Trigger": f"{OVERVIEW_CHANGED_EVENT}, close-dialog"})
