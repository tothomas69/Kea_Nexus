"""
test_docker_compose_mounts.py — Keep docker-compose.yml honest about the tree.

`docker-compose.yml` bind-mounts KeaNexus's Python modules one file at a time,
which drifts silently: renaming a module leaves a mount pointing at a path that
no longer exists (Docker then helpfully creates a *directory* there), and adding
one leaves it unmounted, so the container runs a stale copy baked into the image
while the mounted modules around it are current. That mismatch is how a rename
turns into an ImportError at container start, with nothing in the test suite
noticing.

Parsed with a regex rather than PyYAML — the volume list is a flat sequence of
`- ./x:/app/x` strings, and this is not worth a new dependency.
"""

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_COMPOSE = _REPO_ROOT / "docker-compose.yml"

# Modules the KeaNexus container never imports, so they need no mount.
# pihole.py exists at the repo root only so quarantine_service can import it;
# that service gets its code from its own Dockerfile COPY, not a bind mount.
_NOT_REQUIRED_IN_KEANEXUS = {"pihole.py"}


def _mounted_host_paths() -> list[str]:
	"""Every `./<something>` bind-mount source in the compose file."""
	return re.findall(r"^\s*-\s+\./([^:\s]+):", _COMPOSE.read_text(), flags=re.MULTILINE)


def _repo_root_modules() -> set[str]:
	return {path.name for path in _REPO_ROOT.glob("*.py")}


class TestComposeMounts:
	@pytest.mark.parametrize("mount_source", _mounted_host_paths())
	def test_every_mount_source_exists(self, mount_source):
		"""A mount pointing at a missing path makes Docker create a directory
		there, shadowing the file the container expected."""
		assert (_REPO_ROOT / mount_source).exists(), (
			f"docker-compose.yml mounts ./{mount_source}, which does not exist. "
			"If a module was renamed, update the mount to match."
		)

	@pytest.mark.parametrize("module", sorted(_repo_root_modules() - _NOT_REQUIRED_IN_KEANEXUS))
	def test_every_app_module_is_mounted(self, module):
		"""An unmounted module is served from the image instead of the host, so
		the container mixes current and stale code after a plain restart."""
		assert module in _mounted_host_paths(), (
			f"{module} is not bind-mounted in docker-compose.yml. Add it, or add "
			"it to _NOT_REQUIRED_IN_KEANEXUS if the KeaNexus container never "
			"imports it."
		)

	def test_the_exclusion_list_stays_accurate(self):
		"""Guard the guard: an exclusion for a file that no longer exists would
		quietly stop covering anything."""
		missing = {name for name in _NOT_REQUIRED_IN_KEANEXUS if not (_REPO_ROOT / name).exists()}
		assert not missing, f"_NOT_REQUIRED_IN_KEANEXUS names files that no longer exist: {missing}"


class TestComposeIsWellFormed:
	"""The mount checks above use a regex, which happily matches lines in a file
	Docker cannot parse. These catch that."""

	def test_all_volume_entries_share_one_indentation(self):
		"""A hand-edited line at the wrong indent makes the YAML invalid while
		still matching the mount regex — exactly how a broken compose file got
		committed once."""
		indents = {
			len(line) - len(line.lstrip(" "))
			for line in _COMPOSE.read_text().splitlines()
			if re.match(r"^\s*-\s+\./", line)
		}
		assert len(indents) == 1, (
			f"volume entries use mixed indentation {sorted(indents)}; YAML needs one level"
		)

	def test_no_tabs_anywhere(self):
		"""YAML forbids tabs for indentation outright, and this repo's prettier
		config sets useTabs — so never let prettier format this file."""
		assert "\t" not in _COMPOSE.read_text(), "docker-compose.yml contains a tab"
