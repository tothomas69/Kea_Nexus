"""
pages.py — The container health check. The Overview lives in routes/overview.py.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def health_check() -> dict:
	"""Docker HEALTHCHECK target. Deliberately touches nothing: it says the process is up."""
	return {"ok": True}
