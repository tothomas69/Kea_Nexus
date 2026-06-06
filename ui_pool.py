"""
ui_pool.py - Pool utilization dashboard tab for KeaNexus.
"""
from typing import Optional
import streamlit as st


def _card(title: str, body: str) -> str:
	return (
		f'<div style="background:#fff;border:1px solid #d0d7de;border-radius:8px;'
		f'padding:18px 20px;height:100%">'
		f'<div style="font-family:monospace;font-size:15px;font-weight:700;color:#1f2328;'
		f'text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px">{title}</div>'
		f'{body}'
		f'</div>'
	)


def _gauge(pct: int, used: int, total: int, avail: int) -> str:
	"""CSS conic-gradient donut ring showing pool utilization percentage."""
	fill_deg    = round(pct * 3.6, 1)  # 1% = 3.6°
	fill_color  = "#cf222e" if pct >= 90 else ("#b45309" if pct >= 75 else "#1a7f37")
	avail_color = "#cf222e" if avail == 0 else ("#b45309" if avail < 10 else "#1a7f37")

	gradient = (
		f"conic-gradient(from -90deg, "
		f"{fill_color} 0deg {fill_deg}deg, "
		f"#e5e7eb {fill_deg}deg 360deg)"
	)

	return (
		f'<div style="display:flex;justify-content:center;padding:8px 0">'
		f'<div style="position:relative;width:160px;height:160px">'
		f'<div style="position:absolute;inset:0;border-radius:50%;background:{gradient}"></div>'
		f'<div style="position:absolute;inset:22px;border-radius:50%;background:#fff"></div>'
		f'<div style="position:absolute;inset:0;display:flex;flex-direction:column;'
		f'align-items:center;justify-content:center;font-family:monospace">'
		f'<span style="font-size:26px;font-weight:700;color:{fill_color};line-height:1">{pct}%</span>'
		f'<span style="font-size:11px;color:#6e7781;margin-top:4px">{used} / {total}</span>'
		f'<span style="font-size:11px;color:#6e7781;margin-top:2px">'
		f'Available: <span style="font-weight:700;color:{avail_color}">{avail}</span>'
		f'</span>'
		f'</div>'
		f'</div>'
		f'</div>'
	)


def _health_row(label: str, detail: str, up: bool, last: bool = False) -> str:
	dot    = "#1a7f37" if up else "#cf222e"
	bg     = "#dafbe1" if up else "#ffebe9"
	text   = "#1a7f37" if up else "#cf222e"
	badge  = "UP" if up else "DOWN"
	border = "" if last else "border-bottom:1px solid #f0f2f5;"
	return (
		f'<div style="display:flex;align-items:center;gap:8px;padding:8px 0;{border}'
		f'font-family:monospace">'
		f'<div style="width:8px;height:8px;border-radius:50%;background:{dot};flex-shrink:0"></div>'
		f'<span style="color:#57606a;font-weight:400;font-size:11px;flex:1">{label}</span>'
		f'<span style="color:#8c959f;font-size:10px">{detail}</span>'
		f'<span style="background:{bg};color:{text};font-size:10px;font-weight:700;'
		f'padding:2px 7px;border-radius:10px;margin-left:6px">{badge}</span>'
		f'</div>'
	)


def _metric_row(label: str, value, alert: bool = False, last: bool = False) -> str:
	val_color = "#cf222e" if alert else "#1f2328"
	border = "" if last else "border-bottom:1px solid #f0f2f5;"
	return (
		f'<div style="display:flex;justify-content:space-between;align-items:center;'
		f'padding:8px 0;{border}font-family:monospace;font-size:13px">'
		f'<span style="color:#57606a">{label}</span>'
		f'<span style="font-weight:700;color:{val_color}">{value}</span>'
		f'</div>'
	)


def render_pool(
	stats: Optional[dict],
	config: Optional[dict],
	status: dict,
) -> None:
	if stats is None:
		st.error("Could not load pool stats. Check that Kea is reachable and the stat_cmds hook is loaded.")
		return

	dhcp4   = status["dhcp4"]
	ca      = status["ca"]
	dhcp_on = st.session_state.get("dhcp_enabled", True)

	assigned = stats["assigned"]
	declined = stats["declined"]
	avail    = stats["available"]
	total    = stats["total"]
	pct      = round((assigned + declined) / total * 100) if total else 0

	res       = (config or {}).get("subnet4", [{}])[0].get("reservations", [])
	fixed_ips = sum(1 for r in res if r.get("ip-address"))

	col_gauge, col_health, col_leases = st.columns(3)

	with col_gauge:
		body = _gauge(pct, assigned + declined, total, avail)
		st.markdown(_card("Pool Utilization", body), unsafe_allow_html=True)

	with col_health:
		def svc_detail(svc) -> str:
			return svc.version if svc.version not in ("-", "ok") else ""

		body = (
			_health_row(dhcp4.name, svc_detail(dhcp4), dhcp4.up)
			+ _health_row(ca.name,   svc_detail(ca),    ca.up)
			+ _health_row("DHCP Leasing Service", "enabled" if dhcp_on else "disabled", dhcp_on, last=True)
		)
		st.markdown(_card("Service Health", body), unsafe_allow_html=True)

	with col_leases:
		body = (
			_metric_row("Active Leases",    assigned)
			+ _metric_row("Available Leases", avail)
			+ _metric_row("Declined Leases",  declined,  alert=declined > 0)
			+ _metric_row("Reserved",         len(res))
			+ _metric_row("Fixed IPs",        fixed_ips, last=True)
		)
		st.markdown(_card("Lease Summary", body), unsafe_allow_html=True)
