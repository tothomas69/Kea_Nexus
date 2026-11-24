"""
main.py — The KeaNexus web app: FastAPI + Jinja2 + HTMX.

Run with:
	uvicorn web.main:app --host 0.0.0.0 --port 8000 --proxy-headers

Startup work runs in the lifespan, when the container starts, not when the
first browser connects.
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
from web.routes import (
	auth_routes,
	devices,
	help_routes,
	leases,
	maintenance,
	overview,
	pages,
	reservations,
	search,
	settings,
)
from web.security import require_csrf
from web.templating import redirect

_WEB_STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"
# The repo's own static/ folder, which holds the logo files.
_BRAND_DIRECTORY = Path(__file__).resolve().parent.parent / "static"

# "off" stops the pool sampler. Set while the Streamlit UI ran beside this one
# with its own sampler, since two would write every reading twice; unset since
# the cutover.
_BACKGROUND_WORK_ENV = "KEANEXUS_WEB_BACKGROUND_WORK"
_BACKGROUND_WORK_OFF = "off"


# The background liveness sweep has its own switch, on by default. Set it to
# "off" when running somewhere without host networking or NET_RAW (a laptop
# dev run), where every sweep would only fail and log.
_LIVENESS_SWEEP_ENV = "KEANEXUS_WEB_LIVENESS_SWEEP"


def background_work_enabled() -> bool:
	return os.environ.get(_BACKGROUND_WORK_ENV, "on").strip().lower() != _BACKGROUND_WORK_OFF


def liveness_sweep_enabled() -> bool:
	return os.environ.get(_LIVENESS_SWEEP_ENV, "on").strip().lower() != _BACKGROUND_WORK_OFF


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
	app.include_router(devices.router)
	app.include_router(leases.router)
	app.include_router(reservations.router)
	app.include_router(maintenance.router)
	app.include_router(settings.router)
	app.include_router(search.router)
	app.include_router(help_routes.router)
	return app


app = create_app(
	start_background_work=background_work_enabled(),
	start_liveness_sweep=liveness_sweep_enabled(),
)
