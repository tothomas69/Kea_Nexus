"""
app.py - KeaNexus DHCP Management Dashboard
Entry point. Page config, CSS injection, sidebar, and tab routing.
"""

from pathlib import Path
from typing import Optional

import extra_streamlit_components as stx
import streamlit as st

from auth import SESSION_COOKIE_NAME, is_authenticated, logout, restore_session_from_cookie
from db import init_db
from helpers import get_client, load_config, load_leases, load_pool_stats, load_status
from kea import KeaError
from ui_ipam import render_ipam
from ui_leases import render_leases
from ui_login import render_login
from ui_maintenance import render_maintenance
from ui_pool import render_pool
from ui_quarantine import render_quarantine
from ui_reservations import render_reservations
from ui_settings import render_settings
from version import APP_VERSION

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

# --- Session cookie -------------------------------------------------------------
# Backs login persistence across a page reload — see auth.py's module
# docstring. @st.cache_resource so the same CookieManager instance (and its
# underlying component) is reused across reruns instead of re-declared.


@st.cache_resource
def _cookie_manager() -> stx.CookieManager:
	return stx.CookieManager()


# --- Sidebar ------------------------------------------------------------------


def _sidebar_item(label: str, value: str) -> str:
	return (
		f'<div style="padding:8px 0;border-bottom:1px solid #e8ecf0;text-align:center">'
		f'<div style="font-family:monospace;font-size:10px;font-weight:700;color:#57606a;'
		f'text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px">{label}</div>'
		f'<div style="font-family:monospace;font-size:12px;color:#1f2328">{value}</div>'
		f"</div>"
	)


def _sidebar_version_block(status: dict) -> str:
	"""
	Build the version info block for the sidebar.
	Shows KeaNexus app version, Kea daemon version, and connection mode.
	Always rendered regardless of Kea connectivity — useful for diagnostics.
	KeaNexus only supports the Kea 3.0+ direct API — there is no mode toggle.
	"""
	dhcp4 = status.get("dhcp4")
	kea_ver = dhcp4.version if dhcp4 and hasattr(dhcp4, "version") else "—"

	return (
		'<div style="margin-top:16px;padding-top:8px">'
		+ _sidebar_item("KeaNexus", f"v{APP_VERSION}")
		+ _sidebar_item("Kea DHCP", kea_ver)
		+ _sidebar_item("API Mode", "Direct API")
		+ "</div>"
	)


def render_sidebar(
	stats: Optional[dict], config: Optional[dict], status: dict, cookie_manager: stx.CookieManager
) -> None:
	logo_path = Path(__file__).parent / "static" / "keanexus_logo.png"
	if logo_path.exists():
		st.sidebar.image(str(logo_path), use_container_width=True)

	if stats and config:
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

	# Version block always renders regardless of whether stats/config loaded —
	# useful for diagnosing connection problems when Kea is unreachable.
	st.sidebar.markdown(
		_sidebar_version_block(status),
		unsafe_allow_html=True,
	)

	st.sidebar.markdown('<div style="margin-top:16px"></div>', unsafe_allow_html=True)
	if st.sidebar.button("Sign out", key="sign_out_button", use_container_width=True):
		logout()
		# .delete() raises KeyError if the cookie hasn't synced into the
		# component's internal cache yet — guard rather than crash sign-out.
		if SESSION_COOKIE_NAME in cookie_manager.cookies:
			cookie_manager.delete(SESSION_COOKIE_NAME)
		st.rerun()


# --- Main ---------------------------------------------------------------------


def main() -> None:
	cookie_manager = _cookie_manager()

	# A page reload starts a fresh Streamlit session (blank session_state) —
	# restore it from the session cookie before falling back to the login
	# page. The cookie's value can lag behind on the very first render of a
	# brand-new session (the component's JS hasn't reported back yet), so
	# this can occasionally still show the login page for one rerun even
	# when a valid cookie exists — it resolves itself once the component
	# syncs and triggers its own rerun.
	if not is_authenticated():
		restore_session_from_cookie(cookie_manager.get(SESSION_COOKIE_NAME))

	if not is_authenticated():
		render_login(cookie_manager)
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

	render_sidebar(stats, config, status, cookie_manager)

	tab_pool, tab_leases, tab_ipam, tab_res, tab_maint, tab_quarantine, tab_settings = st.tabs(
		[
			"Pool",
			"Leases",
			"IPAM",
			"Reservations",
			"Maintenance",
			"Quarantine",
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
		render_reservations(config, leases)
	with tab_maint:
		render_maintenance(leases)
	with tab_quarantine:
		render_quarantine(leases, config)
	with tab_settings:
		render_settings(config)


if __name__ == "__main__":
	main()
