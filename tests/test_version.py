"""test_version.py — APP_VERSION is the single source of truth for the version the UI shows."""

import os
import re
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.test_web_overview import _SECRET, _sign_in
from version import APP_VERSION
from web.main import create_app

# MAJOR.MINOR.PATCH, digits only. The masthead shows it as "v" + APP_VERSION,
# so a stray "v" or a missing part would render as "vv1.5" or "v1.5".
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def test_app_version_is_major_minor_patch():
	assert _SEMVER_PATTERN.match(APP_VERSION), (
		f"APP_VERSION should look like 1.5.0, got {APP_VERSION!r}"
	)


def test_masthead_and_help_show_the_version(temp_db):
	"""Every signed-in page carries the version under the logo and in Help."""
	with patch.dict(os.environ, {"KEANEXUS_SECRET": _SECRET}):
		with TestClient(create_app(start_background_work=False)) as client:
			_sign_in(client)
			page = client.get("/help").text
	assert page.count(f"v{APP_VERSION}") == 2
