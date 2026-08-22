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
from pool_history import start_pool_sampler
from ui_dashboard import render_dashboard
from ui_docs import render_docs_nav, render_open_article
from ui_ipam import render_ipam
from ui_leases import render_leases
from ui_login import render_login
from ui_maintenance import render_maintenance
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


# --- Pool utilisation history -------------------------------------------------
# Kea keeps no history of its own, so any utilisation trend chart is built from
# readings this background loop takes — see pool_history.py, including why it
# only starts once a browser session has connected. @st.cache_resource makes
# Streamlit run this once per process rather than on every script rerun.
@st.cache_resource
def _start_pool_sampler() -> bool:
	start_pool_sampler()
	return True


_start_pool_sampler()

# --- Session cookie -------------------------------------------------------------
# Backs login persistence across a page reload — see auth.py's module
# docstring. Deliberately NOT @st.cache_resource: CookieManager.__init__
# itself calls a Streamlit component (a widget-like command), and Streamlit
# forbids widget commands inside a cached function (CachedWidgetWarning).
# The component stabilizes itself across reruns via its own internal
# key="init" parameter, not via Python object identity, so a plain
# uncached call is correct here — main() only constructs one per run anyway.

# Session-scoped (not module-scoped) so a genuinely fresh session always
# gets its own single grace-stop, rather than sharing state across sessions.
_COOKIE_SYNC_FLAG = "_cookie_sync_attempted"


def _cookie_manager() -> stx.CookieManager:
	return stx.CookieManager()


# --- Sidebar ------------------------------------------------------------------


def _sidebar_item(label: str, value: str, show_divider: bool = True) -> str:
	border = "border-bottom:1px solid #e8ecf0;" if show_divider else ""
	return (
		f'<div style="padding:8px 0;{border}text-align:center">'
		f'<div style="font-family:monospace;font-size:10px;font-weight:700;color:#57606a;'
		f'text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px">{label}</div>'
		f'<div style="font-family:monospace;font-size:12px;color:#1f2328">{value}</div>'
		f"</div>"
	)


def _sidebar_value_only(value: str, show_divider: bool = True) -> str:
	"""Same visual slot as _sidebar_item but with no label line above the
	value — used for the top version row, where "KeaNexus" as a heading is
	redundant right above the app's own logo. Tighter vertical padding than
	_sidebar_item's 8px — a single line doesn't need as much breathing room,
	and this row sits directly above the logo, which should read as close
	beneath it rather than another evenly-spaced row."""
	border = "border-bottom:1px solid #e8ecf0;" if show_divider else ""
	return (
		f'<div style="padding:2px 0 4px;{border}text-align:center">'
		f'<div style="font-family:monospace;font-size:12px;color:#1f2328">{value}</div>'
		f"</div>"
	)


def _sidebar_version_block(status: dict) -> str:
	"""
	Build the version info block for the sidebar.
	Shows Kea daemon version and connection mode.
	Always rendered regardless of Kea connectivity — useful for diagnostics.
	KeaNexus only supports the Kea 3.0+ direct API — there is no mode toggle.
	"""
	dhcp4 = status.get("dhcp4")
	kea_ver = dhcp4.version if dhcp4 and hasattr(dhcp4, "version") else "—"

	return (
		'<div style="margin-top:6px">'
		+ _sidebar_item("Kea DHCP", kea_ver, show_divider=False)
		+ _sidebar_item("API Mode", "Direct API")
		+ "</div>"
	)


def render_sidebar(
	stats: Optional[dict], config: Optional[dict], status: dict, cookie_manager: stx.CookieManager
) -> None:
	st.sidebar.markdown(
		_sidebar_value_only(f"v{APP_VERSION}", show_divider=False), unsafe_allow_html=True
	)

	logo_path = Path(__file__).parent / "static" / "keanexus_logo.png"
	if logo_path.exists():
		st.sidebar.image(str(logo_path), width="stretch")

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
			f'<div style="margin-top:6px">{rows}</div>',
			unsafe_allow_html=True,
		)

	# Version block always renders regardless of whether stats/config loaded —
	# useful for diagnosing connection problems when Kea is unreachable.
	st.sidebar.markdown(
		_sidebar_version_block(status),
		unsafe_allow_html=True,
	)

	render_docs_nav()

	# Deliberately larger than the gaps between the info rows above: Sign out
	# is destructive and shouldn't sit flush against a docs link someone is
	# scanning down through.
	st.sidebar.markdown('<div style="margin-top:22px"></div>', unsafe_allow_html=True)
	if st.sidebar.button("Sign out", key="sign_out_button", width="stretch"):
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
	# page. The component needs one round trip to the browser before
	# .get_all() reflects real cookies; on a brand-new session its very
	# first call returns {} regardless of what's actually stored, which
	# would otherwise show the login page even when a valid cookie exists.
	# A changed component return value always triggers exactly one automatic
	# Streamlit rerun, so st.stop() here — gated to fire at most once per
	# session via _COOKIE_SYNC_FLAG, never on every run — waits for that
	# round trip instead of racing it. A genuinely cookie-less visitor still
	# reaches the login page normally on the rerun that follows.
	if not is_authenticated():
		all_cookies = cookie_manager.get_all()
		if not all_cookies and not st.session_state.get(_COOKIE_SYNC_FLAG):
			st.session_state[_COOKIE_SYNC_FLAG] = True
			st.stop()
		restore_session_from_cookie(all_cookies.get(SESSION_COOKIE_NAME))

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
	# After the sidebar, so the button that requests an article has already run.
	render_open_article()

	# key= makes Streamlit remember the active tab across a rerun (e.g. the
	# Quarantine tab's st.rerun() after a Quarantine/Release action). Without
	# it, st.tabs() has no persisted selection and always snaps back to the
	# first tab on any rerun — from the user's side, clicking Quarantine
	# would bounce them to Dashboard with the Quarantine tab's old content still
	# fading out underneath.
	tab_dashboard, tab_leases, tab_ipam, tab_res, tab_maint, tab_quarantine, tab_settings = st.tabs(
		[
			"Dashboard",
			"Leases",
			"IPAM",
			"Reservations",
			"Maintenance",
			"Quarantine",
			"Settings",
		],
		key="main_tabs",
	)
	with tab_dashboard:
		render_dashboard(stats, config, status, leases)
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
