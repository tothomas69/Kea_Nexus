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

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from db import get_quarantine_log, insert_quarantine_log_entry
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
from quarantine_service.nmap_fingerprint import refresh_os_fingerprint
from quarantine_service.pihole_block import block_via_pihole, unblock_via_pihole
from quarantine_service.retry import run_with_retries

app = FastAPI(title="keanexus-quarantine")


class QuarantineRequest(BaseModel):
	target: str
	is_group: bool = False


def _get_kea_client() -> KeaClient:
	"""Factory, not a module-level singleton, so tests can patch it per-call."""
	return KeaClient()


def _get_pihole_client() -> PiholeClient:
	"""Factory, not a module-level singleton, so tests can patch it per-call."""
	return PiholeClient()


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


def _apply_pihole_step(pihole: PiholeClient, device: ResolvedDevice, action: str) -> bool:
	"""Apply (quarantine) or remove (release) the Pi-hole DNS block, with retries."""
	pihole_action_fn = block_via_pihole if action == "quarantine" else unblock_via_pihole
	return run_with_retries(
		step="pihole",
		friendly_name=device.friendly_name,
		action=action,
		attempt_fn=lambda ip=device.ip_address: pihole_action_fn(pihole, ip),
	)


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


def _enforce_device(
	kea: KeaClient, pihole: PiholeClient, device: ResolvedDevice, action: str
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
	return {
		"friendly_name": device.friendly_name,
		"kea_step_succeeded": _apply_kea_step(kea, device, action),
		"arp_step_succeeded": _apply_arp_step(kea, device, action),
		"pihole_step_succeeded": _apply_pihole_step(pihole, device, action),
		"fingerprint_refreshed": _refresh_fingerprint_step(device, action),
		"skipped_due_to_identity_drift": False,
	}


def _resolve_and_log(request: QuarantineRequest, action: str) -> dict:
	kea = _get_kea_client()
	pihole = _get_pihole_client()
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

		step_results.append(_enforce_device(kea, pihole, device, action))

	return {
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
