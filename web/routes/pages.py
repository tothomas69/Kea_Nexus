"""
pages.py — The Overview page and the container health check.

The Overview is a placeholder in phase 0; phase 1 fills it with the KPI row,
the address map, the quarantine card, the activity feed and the charts.
"""

from fastapi import APIRouter, Depends, Request, Response

from web.deps import current_user
from web.templating import render

router = APIRouter()


@router.get("/")
def overview(request: Request, user: dict = Depends(current_user)) -> Response:
	return render(request, "overview.html", {"user": user})


@router.get("/healthz")
def health_check() -> dict:
	"""Docker HEALTHCHECK target. Deliberately touches nothing: it says the process is up."""
	return {"ok": True}
