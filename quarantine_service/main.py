"""
main.py — keanexus-quarantine FastAPI service.

PR 6 scope: the pre-fire safety check. /quarantine and /release resolve a
friendly_name or group_tag, then for each resolved device: re-verify its
identity hasn't drifted since resolution (see identity.verify_identity_
unchanged), and if it's still current, apply/remove the Kea DROP-class
deny, start/stop ARP disruption, apply/remove the Pi-hole DNS block, and
refresh the device's OS fingerprint via nmap. All four enforcement steps
go through retry.run_with_retries. This completes the orchestration from
docs/quarantine-feature-design.md — remaining work is Siri Shortcut setup,
which is configuration rather than code (see docs/siri-shortcut-setup.md).
"""

import logging
import os
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from db import get_device, get_quarantine_log, init_db, insert_quarantine_log_entry, upsert_device
from kea import KeaClient, KeaError
from pihole import PiholeClient
from quarantine_service.arp_disrupt import start_arp_disruption, stop_arp_disruption
from quarantine_service.auth import require_bearer_token
from quarantine_service.identity import (
	DeviceNotOnNetworkError,
	DeviceNotRegisteredError,
	ResolvedDevice,
	resolve_target,
	verify_identity_unchanged,
)
from quarantine_service.kea_deny import deny_via_kea, undo_deny_via_kea
from quarantine_service.liveness import MAX_SWEEP_ADDRESSES, sweep
from quarantine_service.nmap_fingerprint import refresh_os_fingerprint
from quarantine_service.pihole_block import block_via_pihole, unblock_via_pihole
from quarantine_service.presence_check import probe_device_now, start_presence_check_loop
from quarantine_service.retry import run_with_retries

# Without this, Python's root logger sits at its default WARNING level and
# every logger.info(...) call anywhere in this service (presence_check.py's
# startup confirmation, and anything added later) is silently dropped before
# it ever reaches `docker logs` — uvicorn configures its own request-logging
# loggers, not the application's.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="keanexus-quarantine")


@app.on_event("startup")
def _create_schema_on_startup() -> None:
	"""Ensure device_registry/quarantine_log exist before the first request.

	KeaNexus's own Streamlit script also calls init_db(), but Streamlit
	defers running app.py until a browser session actually connects — it
	doesn't execute eagerly just because the container started. FastAPI
	does run this eagerly on process start, so calling it here makes this
	service self-sufficient regardless of whether anyone's opened the
	KeaNexus dashboard yet. init_db() is idempotent (CREATE TABLE IF NOT
	EXISTS), so this is safe to run alongside KeaNexus's own call to it.
	"""
	init_db()


@app.on_event("startup")
def _start_presence_check_on_startup() -> None:
	"""Kick off the periodic ARP presence probe (see presence_check.py).

	Separate startup handler from schema creation above — keeps "make sure
	the DB is ready" and "start a long-running background loop" as two
	distinct, independently-reasoned-about steps rather than one function
	doing two unrelated things. FastAPI runs all @app.on_event("startup")
	handlers in registration order, so schema creation still happens first.
	"""
	start_presence_check_loop()


class QuarantineRequest(BaseModel):
	target: str
	is_group: bool = False


class LivenessSweepRequest(BaseModel):
	ip_addresses: list[str]


def _get_kea_client() -> KeaClient:
	"""Factory, not a module-level singleton, so tests can patch it per-call."""
	return KeaClient()


def _get_pihole_clients() -> list[PiholeClient]:
	"""Factory returning every Pi-hole instance to write the block/unblock to.

	Primary is always included. Secondary is included only when
	PIHOLE_SECONDARY_API_URL is set — the two Pi-hole instances on this
	network are fully independent, no Nebula/Gravity/Orbital Sync between
	them, so a device blocked only on the primary could still resolve DNS
	through the secondary if it's ever used as a fallback resolver. Not a
	module-level singleton, same reasoning as _get_kea_client: tests need
	to patch this per-call.
	"""
	clients = [PiholeClient()]
	secondary_url = os.environ.get("PIHOLE_SECONDARY_API_URL", "")
	if secondary_url:
		secondary_password = os.environ.get("PIHOLE_SECONDARY_API_PASSWORD", "")
		clients.append(PiholeClient(base_url=secondary_url, password=secondary_password))
	return clients


def _resolved_device_to_dict(device: ResolvedDevice) -> dict:
	return {
		"friendly_name": device.friendly_name,
		"hostname": device.hostname,
		"mac_address": device.mac_address,
		"ip_address": device.ip_address,
	}


def _get_gateway_ip(kea: KeaClient) -> str:
	"""Extract the subnet's gateway/router IP from the live Kea config."""
	config = kea.get_config()
	subnet = (config.get("subnet4") or [{}])[0]
	for option in subnet.get("option-data", []):
		if option.get("name") == "routers":
			return option.get("data", "")
	return ""


def _apply_kea_step(kea: KeaClient, device: ResolvedDevice, action: str) -> bool:
	"""Run the Kea deny (quarantine) or undo-deny (release) step with retries."""
	kea_action_fn = deny_via_kea if action == "quarantine" else undo_deny_via_kea
	return run_with_retries(
		step="kea",
		friendly_name=device.friendly_name,
		action=action,
		# Default arg binds mac_address at lambda-creation time, not call
		# time — avoids the classic late-binding bug if this ever runs in a loop.
		attempt_fn=lambda mac=device.mac_address: kea_action_fn(kea, mac),
	)


def _apply_arp_step(kea: KeaClient, device: ResolvedDevice, action: str) -> bool:
	"""Start (quarantine) or stop (release) the ARP disruption loop, with retries."""
	if action == "quarantine":
		gateway_ip = _get_gateway_ip(kea)
		return run_with_retries(
			step="arp",
			friendly_name=device.friendly_name,
			action=action,
			attempt_fn=lambda: start_arp_disruption(
				device.friendly_name, device.ip_address, device.mac_address, gateway_ip
			),
		)

	return run_with_retries(
		step="arp",
		friendly_name=device.friendly_name,
		action=action,
		attempt_fn=lambda: stop_arp_disruption(device.friendly_name),
	)


def _apply_pihole_step(
	pihole_clients: list[PiholeClient], device: ResolvedDevice, action: str
) -> bool:
	"""Apply (quarantine) or remove (release) the Pi-hole DNS block on every
	configured Pi-hole instance, each with its own retries and its own
	audit log row (step name includes which instance) — so a partial
	failure (e.g. secondary unreachable) is visible per-instance rather
	than collapsed into one ambiguous result. Returns True only if every
	configured instance succeeded; a partial success still leaves a real
	gap, so callers shouldn't treat it as fully blocked.
	"""
	pihole_action_fn = block_via_pihole if action == "quarantine" else unblock_via_pihole
	step_names = ["pihole_primary", "pihole_secondary"]

	all_succeeded = True
	for pihole, step_name in zip(pihole_clients, step_names):
		succeeded = run_with_retries(
			step=step_name,
			friendly_name=device.friendly_name,
			action=action,
			attempt_fn=lambda ip=device.ip_address, p=pihole: pihole_action_fn(p, ip),
		)
		all_succeeded = all_succeeded and succeeded

	return all_succeeded


def _refresh_fingerprint_step(device: ResolvedDevice, action: str) -> bool:
	"""Refresh the device's OS fingerprint via nmap, with retries.

	Runs for both quarantine and release — its purpose is keeping
	device_registry's identity signal fresh whenever a live IP is
	available, independent of which direction the enforcement action went.
	"""
	return run_with_retries(
		step="nmap_refresh",
		friendly_name=device.friendly_name,
		action=action,
		attempt_fn=lambda: refresh_os_fingerprint(device.friendly_name, device.ip_address),
	)


def _skipped_step_result(friendly_name: str) -> dict:
	"""step_results entry for a device skipped by the pre-fire safety check.

	Keeps the same key shape as a normal entry (all four step flags
	present, just False) so callers don't need to special-case a
	differently-shaped dict — only skipped_due_to_identity_drift changes.
	"""
	return {
		"friendly_name": friendly_name,
		"kea_step_succeeded": False,
		"arp_step_succeeded": False,
		"pihole_step_succeeded": False,
		"fingerprint_refreshed": False,
		"skipped_due_to_identity_drift": True,
	}


def _stamp_quarantine_state(device: ResolvedDevice, action: str) -> None:
	"""Record this device's current quarantine state, for the registry UI's
	Status indicator, plus last_quarantined_at on quarantine only.

	is_quarantined is set unconditionally from the action taken (not gated
	on individual step success) — same convention as last_quarantined_at
	below, which has always been stamped this way: it reflects the last
	enforcement direction requested, not a live per-step health check. A
	partial failure is already visible in the action's own step-result
	checkmarks; this field answers "was this device released since its
	last quarantine," not "is every enforcement step currently healthy."
	last_quarantined_at is only touched on quarantine — it means exactly
	"last quarantined," not "last touched."
	"""
	current = get_device(device.friendly_name)
	if current is None:
		return
	upsert_device(
		current["friendly_name"],
		current["hostname"],
		group_tag=current["group_tag"],
		os_fingerprint=current["os_fingerprint"],
		last_seen_mac_address=current["last_seen_mac_address"],
		last_seen_ip_address=current["last_seen_ip_address"],
		last_seen_at=current["last_seen_at"],
		last_quarantined_at=(
			datetime.now(timezone.utc).isoformat()
			if action == "quarantine"
			else current["last_quarantined_at"]
		),
		is_quarantined=(action == "quarantine"),
		notes=current["notes"],
	)


def _enforce_device(
	kea: KeaClient, pihole_clients: list[PiholeClient], device: ResolvedDevice, action: str
) -> dict:
	"""Run the full enforcement sequence for one already-verified-current device."""
	insert_quarantine_log_entry(
		device.friendly_name,
		action,
		"identity_resolution",
		succeeded=True,
		attempt_count=1,
		detail=f"mac={device.mac_address} ip={device.ip_address}",
	)
	result = {
		"friendly_name": device.friendly_name,
		"kea_step_succeeded": _apply_kea_step(kea, device, action),
		"arp_step_succeeded": _apply_arp_step(kea, device, action),
		"pihole_step_succeeded": _apply_pihole_step(pihole_clients, device, action),
		"fingerprint_refreshed": _refresh_fingerprint_step(device, action),
		"skipped_due_to_identity_drift": False,
	}
	_stamp_quarantine_state(device, action)
	return result


def _display_name(friendly_name: str) -> str:
	"""Registry keys are snake_case ("tommy_laptop"); this is the form a
	person reads on a phone notification."""
	return friendly_name.replace("_", " ").title()


def _join_names(display_names: list[str]) -> str:
	"""Joins names the way a person would say them out loud: one name alone,
	two joined by "and", three or more comma-separated with a final "and"."""
	if len(display_names) == 1:
		return display_names[0]
	return f"{', '.join(display_names[:-1])} and {display_names[-1]}"


def _enforcement_succeeded(step_result: dict) -> bool:
	"""Whether the three steps that actually take a device off the network
	(or put it back on) all worked.

	Deliberately ignores fingerprint_refreshed: the nmap refresh is identity
	upkeep that runs in both directions, so a device whose scan came back
	inconclusive is still fully quarantined. Counting it here would report
	failure to someone who only wants to know whether the wifi is off.
	"""
	if step_result.get("skipped_due_to_identity_drift"):
		return False
	return bool(
		step_result.get("kea_step_succeeded")
		and step_result.get("arp_step_succeeded")
		and step_result.get("pihole_step_succeeded")
	)


_SUCCESS_SENTENCES = {
	"quarantine": "{names} {is_are} off the internet.",
	"release": "{names} {is_are} back online.",
}
_FAILURE_SENTENCES = {
	"quarantine": "Couldn't take {names} off the internet — try again.",
	"release": "Couldn't put {names} back online — try again.",
}
_NOTHING_TO_DO_SENTENCE = "Nothing to do — nothing matching that name is on the network."


def _sentence(template: str, friendly_names: list[str]) -> str:
	return template.format(
		names=_join_names([_display_name(name) for name in friendly_names]),
		is_are="is" if len(friendly_names) == 1 else "are",
	)


def _human_summary(action: str, step_results: list[dict]) -> str:
	"""One plain-English sentence describing what actually happened.

	step_results is the honest record and stays in the response for the
	KeaNexus UI and for debugging, but it is a list of dicts of booleans —
	fine on a laptop, useless as the thing a Siri Shortcut shows on a phone
	screen. This is the line to display or speak there instead. A mixed
	outcome yields both sentences so a partial failure is never rounded up
	into "done".
	"""
	if not step_results:
		return _NOTHING_TO_DO_SENTENCE

	succeeded = [s["friendly_name"] for s in step_results if _enforcement_succeeded(s)]
	failed = [s["friendly_name"] for s in step_results if not _enforcement_succeeded(s)]

	sentences = []
	if succeeded:
		sentences.append(_sentence(_SUCCESS_SENTENCES[action], succeeded))
	if failed:
		sentences.append(_sentence(_FAILURE_SENTENCES[action], failed))
	return " ".join(sentences)


def _resolve_and_log(request: QuarantineRequest, action: str) -> dict:
	kea = _get_kea_client()
	pihole_clients = _get_pihole_clients()
	try:
		resolved_devices = resolve_target(kea, request.target, request.is_group)
	except DeviceNotRegisteredError as exc:
		raise HTTPException(status_code=404, detail=str(exc)) from exc
	except DeviceNotOnNetworkError as exc:
		raise HTTPException(status_code=409, detail=str(exc)) from exc
	except KeaError as exc:
		raise HTTPException(status_code=502, detail=f"Kea unreachable: {exc}") from exc

	step_results = []
	for device in resolved_devices:
		if not verify_identity_unchanged(kea, device):
			insert_quarantine_log_entry(
				device.friendly_name,
				action,
				"identity_resolution",
				succeeded=False,
				attempt_count=1,
				detail="IP/MAC changed between resolution and enforcement; "
				"skipped for safety. Retry the request.",
			)
			step_results.append(_skipped_step_result(device.friendly_name))
			continue

		step_results.append(_enforce_device(kea, pihole_clients, device, action))

	return {
		# First key deliberately: a Siri Shortcut that just dumps the
		# response shows this line before any of the diagnostic detail.
		"summary": _human_summary(action, step_results),
		"target": request.target,
		"action": action,
		"resolved_devices": [_resolved_device_to_dict(device) for device in resolved_devices],
		"step_results": step_results,
		"note": "Kea deny/release, ARP disruption, and Pi-hole block all applied where "
		"identity was confirmed current. OS fingerprint refreshed where the scan "
		"was conclusive. Devices flagged skipped_due_to_identity_drift had no "
		"enforcement applied this call — retry the request for them.",
	}


@app.post("/quarantine", dependencies=[Depends(require_bearer_token)])
def quarantine(request: QuarantineRequest) -> dict:
	"""Resolve the target's live identity and apply all four enforcement steps."""
	return _resolve_and_log(request, action="quarantine")


@app.post("/release", dependencies=[Depends(require_bearer_token)])
def release(request: QuarantineRequest) -> dict:
	"""Resolve the target's live identity and remove all four enforcement steps."""
	return _resolve_and_log(request, action="release")


@app.get("/status/{friendly_name}", dependencies=[Depends(require_bearer_token)])
def status(friendly_name: str) -> dict:
	"""Return recent quarantine log entries for a single device."""
	return {"friendly_name": friendly_name, "log": get_quarantine_log(friendly_name)}


@app.post("/presence-check/{friendly_name}", dependencies=[Depends(require_bearer_token)])
def presence_check(friendly_name: str) -> dict:
	"""Immediately ARP-probe one registered device, bypassing the background
	loop's interval — called by KeaNexus right after a device is added or
	edited so Last MAC/Last IP/Last Seen don't sit blank waiting for the
	loop's next pass (see presence_check.py)."""
	seen = probe_device_now(friendly_name)
	return {"friendly_name": friendly_name, "seen": seen}


@app.post("/liveness-sweep", dependencies=[Depends(require_bearer_token)])
def liveness_sweep(request: LivenessSweepRequest) -> dict:
	"""ARP-probe a caller-supplied list of addresses and report which answered.

	Stateless — unlike /presence-check this writes nothing to the registry
	and the addresses need not belong to registered devices. Backs the
	Leases tab's "Check who's online" button, which passes the IPs of the
	leases it's currently showing (see liveness.py for why ARP rather than
	ICMP, and why this can't run inside KeaNexus's own container).
	"""
	if len(request.ip_addresses) > MAX_SWEEP_ADDRESSES:
		raise HTTPException(
			status_code=413,
			detail=f"At most {MAX_SWEEP_ADDRESSES} addresses per sweep, "
			f"got {len(request.ip_addresses)}.",
		)

	responding = sweep(request.ip_addresses)
	return {
		"checked": len(set(request.ip_addresses)),
		# Sorted for a stable response; the sweep itself returns an unordered set.
		"responding": sorted(responding),
	}
