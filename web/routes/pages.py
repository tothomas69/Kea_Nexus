"""
pages.py — The container health check and the root favicon. The Overview lives in routes/overview.py.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/healthz")
def health_check() -> dict:
	"""Docker HEALTHCHECK target. Deliberately touches nothing: it says the process is up."""
	return {"ok": True}


_FAVICON_PATH = Path(__file__).resolve().parents[2] / "static" / "favicon.ico"


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
	"""The orb favicon. Browsers ask for /favicon.ico on their own, before or
	without reading the page's <link rel="icon">, so it is served at the root
	too, with no sign-in."""
	return FileResponse(_FAVICON_PATH, media_type="image/x-icon")
