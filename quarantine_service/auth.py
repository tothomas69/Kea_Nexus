"""
auth.py — Bearer token authentication for the quarantine service.

Every request must present a matching token; there is no session concept
here, unlike KeaNexus's own browser-session auth.py. Comparison uses
hmac.compare_digest for the same constant-time-comparison reason KeaNexus's
own auth.py does — avoids leaking token length/content through timing.

The token is read from the environment on every call rather than cached at
import time, so a container restart (or a test) always sees the current
value.
"""

import hmac
import os

from fastapi import Header, HTTPException

_BEARER_PREFIX = "Bearer "


def require_bearer_token(authorization: str = Header(default="")) -> None:
	"""FastAPI dependency: raise 401/500 unless a matching Bearer token is present."""
	expected_token = os.environ.get("QUARANTINE_API_TOKEN", "")
	if not expected_token:
		raise HTTPException(status_code=500, detail="QUARANTINE_API_TOKEN is not configured")

	if not authorization.startswith(_BEARER_PREFIX):
		raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

	presented_token = authorization[len(_BEARER_PREFIX) :]
	if not hmac.compare_digest(presented_token, expected_token):
		raise HTTPException(status_code=401, detail="Invalid token")
