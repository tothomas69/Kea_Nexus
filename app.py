"""
app.py - KeaNexus DHCP Management Dashboard
Entry point. Page config, CSS injection, sidebar, and tab routing.
"""

from pathlib import Path
from typing import Optional

import streamlit as st

from auth import is_authenticated, logout
from db import init_db
from helpers import get_client, load_config, load_leases, load_pool_stats, load_status
from kea import KeaError
from ui_ipam import render_ipam
from ui_leases import render_leases
from ui_login import render_login
from ui_maintenance import render_maintenance
from ui_pool import render_pool
from ui_reservations import render_reservations
from ui_settings import render_settings

# --- Page config (must be first Streamlit call) -------------------------------
st.set_page_config(
	page_title="KeaNexus",
	page_icon="*",
	layout="wide",
	initial_sidebar_state="expanded",
	menu_items={"Get help": None, "Report a bug": None, "About": "KeaNexus . cyberwraith.net"},
)

# --- CSS loaded from style.css ------------------------------------------------
_css = (Path(__file__).parent / "style.css").read_text()
st.markdown(f"<style>{_css}</style>", unsafe_allow_html=True)

# --- Database initialisation --------------------------------------------------
init_db()

# --- Sidebar ------------------------------------------------------------------


def _sidebar_item(label: str, value: str) -> str:
	return (
		f'<div style="padding:8px 0;border-bottom:1px solid #e8ecf0;text-align:center">'
		f'<div style="font-family:monospace;font-size:10px;font-weight:700;color:#57606a;'
		f'text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px">{label}</div>'
		f'<div style="font-family:monospace;font-size:12px;color:#1f2328">{value}</div>'
		f"</div>"
	)


def render_sidebar(stats: Optional[dict], config: Optional[dict]) -> None:
	logo_path = Path(__file__).parent / "static" / "keanexus_logo.png"
	if logo_path.exists():
		st.sidebar.image(str(logo_path), use_container_width=True)

	if not (stats and config):
		return

	kea = get_client()
	start, end, _ = kea.get_pool_range(config)
	subnet = (config.get("subnet4") or [{}])[0].get("subnet", "?")
	router = "-"
	for opt in (config.get("subnet4") or [{}])[0].get("option-data", []):
		if opt.get("name") == "routers":
			router = opt.get("data", "-")

	total = stats["total"]
	cumul = stats.get("cumulative", 0)

	rows = (
		_sidebar_item("Pool Range", f"{start} – {end}")
		+ _sidebar_item("Subnet", subnet)
		+ _sidebar_item("Router", router)
		+ _sidebar_item("Address Pool", str(total))
		+ _sidebar_item("Lease Time", "24h")
		+ _sidebar_item("Stats Source", "API" if cumul > 0 else "lease list")
	)
	st.sidebar.markdown(
		f'<div style="margin-top:12px">{rows}</div>',
		unsafe_allow_html=True,
	)

	st.sidebar.markdown('<div style="margin-top:16px"></div>', unsafe_allow_html=True)
	if st.sidebar.button("Sign out", use_container_width=True):
		logout()
		st.rerun()


# --- Main ---------------------------------------------------------------------


def main() -> None:
	if not is_authenticated():
		render_login()
		return

	kea = get_client()
	leases = []
	try:
		leases = load_leases(kea)
	except KeaError as e:
		st.error(f"Could not load leases: {e}")

	stats = load_pool_stats(kea)
	config = load_config(kea)
	status = load_status(kea)

	render_sidebar(stats, config)

	tab_pool, tab_leases, tab_ipam, tab_res, tab_maint, tab_settings = st.tabs(
		[
			"Pool",
			"Leases",
			"IPAM",
			"Reservations",
			"Maintenance",
			"Settings",
		]
	)
	with tab_pool:
		render_pool(stats, config, status)
	with tab_leases:
		render_leases(leases, config)
	with tab_ipam:
		render_ipam(leases, config)
	with tab_res:
		render_reservations(config)
	with tab_maint:
		render_maintenance(leases)
	with tab_settings:
		render_settings(config)


if __name__ == "__main__":
	main()
