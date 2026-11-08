"""
main.py — The KeaNexus web app: FastAPI + Jinja2 + HTMX.

Run with:
	uvicorn web.main:app --host 0.0.0.0 --port 8000 --proxy-headers

Startup work runs in the lifespan, when the container starts, not when the
first browser connects (the Streamlit UI's long-standing limitation).
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from auth import bootstrap_admin_from_env
from db import init_db
from pool_history import start_pool_sampler
from web.deps import NotSignedIn
from web.liveness import start_sweep_loop
from web.routes import auth_routes, help_routes, leases, overview, pages, quarantine, reservations
from web.security import require_csrf
from web.templating import redirect

_WEB_STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"
# The repo's own static/ folder, which holds the logo files.
_BRAND_DIRECTORY = Path(__file__).resolve().parent.parent / "static"

# While the Streamlit UI still runs beside this one, only one of them may run
# the pool sampler, or every reading lands in pool_samples twice. Compose sets
# this to "off" for keanexus-web until cutover (phase 7).
_BACKGROUND_WORK_ENV = "KEANEXUS_WEB_BACKGROUND_WORK"
_BACKGROUND_WORK_OFF = "off"


# The liveness sweep has no Streamlit twin to collide with, so it runs during
# the overlap too. It needs the optional quarantine service; without its URL
# there is nothing to sweep through, so the loop doesn't start.
_LIVENESS_SWEEP_ENV = "KEANEXUS_WEB_LIVENESS_SWEEP"
_QUARANTINE_URL_ENV = "QUARANTINE_SERVICE_URL"


def background_work_enabled() -> bool:
	return os.environ.get(_BACKGROUND_WORK_ENV, "on").strip().lower() != _BACKGROUND_WORK_OFF


def liveness_sweep_enabled() -> bool:
	switched_on = os.environ.get(_LIVENESS_SWEEP_ENV, "on").strip().lower() != _BACKGROUND_WORK_OFF
	return switched_on and bool(os.environ.get(_QUARANTINE_URL_ENV, "").strip())


def _redirect_to_login(request: Request, exc: NotSignedIn) -> Response:
	return redirect(request, "/login")


def create_app(start_background_work: bool = True, start_liveness_sweep: bool = False) -> FastAPI:
	"""Build the app. Tests leave both switches off to keep threads out."""

	@asynccontextmanager
	async def lifespan(app: FastAPI):
		init_db()
		bootstrap_admin_from_env()
		if start_background_work:
			start_pool_sampler()
		if start_liveness_sweep:
			start_sweep_loop()
		yield

	app = FastAPI(
		title="KeaNexus",
		lifespan=lifespan,
		dependencies=[Depends(require_csrf)],
		# No public API docs: this app serves pages, and the routes are not a
		# contract anyone outside it should build on.
		docs_url=None,
		redoc_url=None,
		openapi_url=None,
	)
	app.mount("/static", StaticFiles(directory=_WEB_STATIC_DIRECTORY), name="static")
	app.mount("/brand", StaticFiles(directory=_BRAND_DIRECTORY), name="brand")
	app.add_exception_handler(NotSignedIn, _redirect_to_login)
	app.include_router(auth_routes.router)
	app.include_router(pages.router)
	app.include_router(overview.router)
	app.include_router(quarantine.router)
	app.include_router(leases.router)
	app.include_router(reservations.router)
	app.include_router(help_routes.router)
	return app


app = create_app(
	start_background_work=background_work_enabled(),
	start_liveness_sweep=liveness_sweep_enabled(),
)
