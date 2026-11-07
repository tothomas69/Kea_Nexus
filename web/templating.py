"""
templating.py — Jinja2 setup and the one helper every page renders through.

render() hands each template the CSRF token, the navigation and the app
version, and sets the CSRF cookie the first time a browser visits, so no route
can forget them.
"""

from pathlib import Path
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from version import APP_VERSION
from web.security import csrf_token_for, set_csrf_cookie

_TEMPLATES_DIRECTORY = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=_TEMPLATES_DIRECTORY)
templates.env.globals["app_version"] = APP_VERSION

# (label, path). A path of None means that screen has not moved over from the
# Streamlit UI yet; base.html shows it greyed out. Each migration phase fills
# one in (see docs/fastapi-htmx-migration.md).
NAV_ITEMS: list[tuple[str, Optional[str]]] = [
	("Overview", "/"),
	("Leases", "/leases"),
	("Reservations", "/reservations"),
	("Quarantine", "/quarantine"),
	("Maintenance", "/maintenance"),
	("Settings", "/settings"),
]

_HTTP_SEE_OTHER = 303


def is_htmx(request: Request) -> bool:
	"""True when the request came from HTMX rather than a full page load."""
	return request.headers.get("HX-Request") == "true"


def render(
	request: Request,
	template_name: str,
	context: Optional[dict] = None,
	status_code: int = 200,
) -> Response:
	"""Render a template with the shared context, setting the CSRF cookie if new."""
	csrf_token, csrf_token_is_new = csrf_token_for(request)
	full_context = {
		"csrf_token": csrf_token,
		"nav_items": NAV_ITEMS,
		"current_path": request.url.path,
		**(context or {}),
	}
	response = templates.TemplateResponse(
		request, template_name, full_context, status_code=status_code
	)
	if csrf_token_is_new:
		set_csrf_cookie(response, request, csrf_token)
	return response


def redirect(request: Request, url: str) -> Response:
	"""Send the browser to another page, for HTMX and plain requests alike.

	HTMX follows an ordinary redirect inside its own request and would swap
	the whole new page into a fragment, so it gets HX-Redirect instead, which
	makes it do a full page navigation.
	"""
	if is_htmx(request):
		return Response(headers={"HX-Redirect": url})
	return RedirectResponse(url, status_code=_HTTP_SEE_OTHER)
