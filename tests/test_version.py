"""test_version.py — APP_VERSION is the single source of truth for the sidebar version."""

import re

from version import APP_VERSION

# MAJOR.MINOR.PATCH, digits only. The sidebar shows it as "v" + APP_VERSION,
# so a stray "v" or a missing part would render as "vv1.5" or "v1.5".
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def test_app_version_is_major_minor_patch():
	assert _SEMVER_PATTERN.match(APP_VERSION), (
		f"APP_VERSION should look like 1.5.0, got {APP_VERSION!r}"
	)
