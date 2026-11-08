"""
test_web.py — Tests for the FastAPI + HTMX web UI's phase 0: sign-in and
sign-out, the session cookie, CSRF, role checks, the Help panel, startup work
and the health check.

Every test runs against a temporary database (temp_db) with KEANEXUS_SECRET
set, through FastAPI's TestClient. Background threads are never started.
"""

import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import auth
import db
from kea import KeaClient, ReadOnlyKeaClient
from web import deps
from web.help_content import section_headings, table_of_contents
from web.main import background_work_enabled, create_app
from web.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, SESSION_COOKIE_NAME

_GOOD_PASSWORD = "correct-horse-battery"  # pragma: allowlist secret
_OTHER_PASSWORD = "different-horse-battery"  # pragma: allowlist secret
_TEST_SECRET = "test-cookie-secret"  # pragma: allowlist secret
_HTMX_HEADERS = {"HX-Request": "true"}


@pytest.fixture
def secret():
	with patch.dict(os.environ, {"KEANEXUS_SECRET": _TEST_SECRET}):
		yield _TEST_SECRET


@pytest.fixture
def client(temp_db, secret):
	with TestClient(create_app(start_background_work=False)) as test_client:
		yield test_client


def _add_user(username="tom", role="admin"):
	auth.create_user(username, _GOOD_PASSWORD, role)


def _csrf_headers(client: TestClient) -> dict:
	"""Load the login page to get a CSRF cookie, and return the header that must echo it."""
	client.get("/login")
	return {CSRF_HEADER_NAME: client.cookies[CSRF_COOKIE_NAME], **_HTMX_HEADERS}


def _sign_in(client: TestClient, username="tom", password=_GOOD_PASSWORD):
	return client.post(
		"/login",
		data={"username": username, "password": password},
		headers=_csrf_headers(client),
	)


class TestHealthCheck:
	def test_needs_no_sign_in(self, client):
		response = client.get("/healthz")
		assert response.status_code == 200
		assert response.json() == {"ok": True}


class TestSignIn:
	def test_signed_out_page_load_redirects_to_login(self, client):
		response = client.get("/", follow_redirects=False)
		assert response.status_code == 303
		assert response.headers["location"] == "/login"

	def test_signed_out_htmx_request_gets_a_full_page_redirect(self, client):
		response = client.get("/", headers=_HTMX_HEADERS, follow_redirects=False)
		assert response.headers["HX-Redirect"] == "/login"

	def test_login_page_sets_the_csrf_cookie(self, client):
		response = client.get("/login")
		assert response.status_code == 200
		assert 'name="username"' in response.text
		assert client.cookies.get(CSRF_COOKIE_NAME)

	def test_correct_credentials_set_the_session_and_open_the_overview(self, client):
		_add_user()
		response = _sign_in(client)
		assert response.headers["HX-Redirect"] == "/"
		assert client.cookies.get(SESSION_COOKIE_NAME)
		overview = client.get("/")
		assert overview.status_code == 200
		assert "Overview" in overview.text
		assert "tom" in overview.text

	def test_session_cookie_is_http_only_and_same_site(self, client):
		_add_user()
		response = _sign_in(client)
		cookie_header = response.headers["set-cookie"].lower()
		assert "httponly" in cookie_header
		assert "samesite=lax" in cookie_header

	def test_plain_form_post_gets_an_ordinary_redirect(self, client):
		_add_user()
		client.get("/login")
		response = client.post(
			"/login",
			data={"username": "tom", "password": _GOOD_PASSWORD},
			headers={CSRF_HEADER_NAME: client.cookies[CSRF_COOKIE_NAME]},
			follow_redirects=False,
		)
		assert response.status_code == 303
		assert response.headers["location"] == "/"

	def test_wrong_password_shows_the_error_and_sets_no_session(self, client):
		_add_user()
		response = _sign_in(client, password=_OTHER_PASSWORD)
		assert response.status_code == 200
		assert "Wrong username or password." in response.text
		assert not client.cookies.get(SESSION_COOKIE_NAME)

	def test_missing_secret_explains_why_sign_in_failed(self, temp_db):
		_add_user()
		with patch.dict(os.environ, {}, clear=True):
			with TestClient(create_app(start_background_work=False)) as test_client:
				response = _sign_in(test_client)
		assert "KEANEXUS_SECRET is not set" in response.text
		assert not test_client.cookies.get(SESSION_COOKIE_NAME)

	def test_signed_in_user_visiting_login_goes_to_the_overview(self, client):
		_add_user()
		_sign_in(client)
		response = client.get("/login", follow_redirects=False)
		assert response.headers["location"] == "/"

	def test_sign_out_ends_the_session(self, client):
		_add_user()
		_sign_in(client)
		response = client.post("/logout", headers=_csrf_headers(client))
		assert response.headers["HX-Redirect"] == "/login"
		assert client.get("/", follow_redirects=False).status_code == 303

	def test_password_change_ends_an_open_session(self, client):
		_add_user()
		_sign_in(client)
		auth.reset_password("tom", _OTHER_PASSWORD)
		assert client.get("/", follow_redirects=False).status_code == 303

	def test_disable_ends_an_open_session(self, client):
		_add_user()
		_add_user("amy")
		_sign_in(client, username="amy")
		auth.set_disabled("amy", True, acting_username="tom")
		assert client.get("/", follow_redirects=False).status_code == 303

	def test_viewer_is_labelled_as_such(self, client):
		_add_user(role="viewer")
		_sign_in(client)
		assert "viewer" in client.get("/").text


class TestCsrf:
	def test_post_without_token_is_refused(self, client):
		_add_user()
		client.get("/login")
		response = client.post("/login", data={"username": "tom", "password": _GOOD_PASSWORD})
		assert response.status_code == 403
		assert not client.cookies.get(SESSION_COOKIE_NAME)

	def test_post_with_mismatched_token_is_refused(self, client):
		client.get("/login")
		response = client.post("/logout", headers={CSRF_HEADER_NAME: "not-the-token"})
		assert response.status_code == 403

	def test_post_without_csrf_cookie_is_refused(self, client):
		response = client.post("/logout", headers={CSRF_HEADER_NAME: "anything"})
		assert response.status_code == 403

	def test_token_is_written_into_the_page_for_htmx(self, client):
		response = client.get("/login")
		assert client.cookies[CSRF_COOKIE_NAME] in response.text

	def test_existing_token_is_reused_not_rotated(self, client):
		client.get("/login")
		first_token = client.cookies[CSRF_COOKIE_NAME]
		second = client.get("/login")
		assert "set-cookie" not in second.headers
		assert first_token in second.text


class TestRoles:
	def test_require_admin_refuses_viewers(self):
		with pytest.raises(HTTPException) as refused:
			deps.require_admin({"username": "amy", "role": "viewer"})
		assert refused.value.status_code == 403

	def test_require_admin_passes_admins(self):
		admin = {"username": "tom", "role": "admin"}
		assert deps.require_admin(admin) == admin

	def test_viewers_get_the_read_only_kea_client(self):
		assert isinstance(deps.kea_client({"username": "amy", "role": "viewer"}), ReadOnlyKeaClient)

	def test_admins_get_the_full_kea_client(self):
		kea = deps.kea_client({"username": "tom", "role": "admin"})
		assert isinstance(kea, KeaClient)
		assert not isinstance(kea, ReadOnlyKeaClient)


class TestHelp:
	def test_needs_sign_in(self, client):
		assert client.get("/help", follow_redirects=False).status_code == 303

	def test_htmx_gets_just_the_table_of_contents(self, client):
		_add_user()
		_sign_in(client)
		response = client.get("/help", headers=_HTMX_HEADERS)
		assert response.status_code == 200
		assert "<header" not in response.text
		for article in table_of_contents():
			assert article["title"] in response.text

	def test_direct_visit_gets_a_full_page(self, client):
		_add_user()
		_sign_in(client)
		response = client.get("/help")
		assert "<header" in response.text
		assert "Siri Shortcut Setup" in response.text

	def test_every_table_of_contents_link_lands_on_a_real_heading(self, client):
		_add_user()
		_sign_in(client)
		for article in table_of_contents():
			page = client.get(f"/help/{article['filename']}").text
			for section in article["sections"]:
				assert f'id="{section["anchor"]}"' in page, (
					f"{article['filename']}: no heading with id {section['anchor']}"
				)

	@pytest.mark.parametrize("filename", ["as-built-project-guide.md", "prd.md", "..%2F.env"])
	def test_anything_off_the_allowlist_is_not_found(self, client, filename):
		_add_user()
		_sign_in(client)
		assert client.get(f"/help/{filename}").status_code == 404

	def test_allowlisted_article_missing_from_disk_is_not_found(self, client):
		_add_user()
		_sign_in(client)
		with patch("web.help_content.read_article", side_effect=FileNotFoundError):
			assert client.get("/help/siri-shortcut-setup.md").status_code == 404

	def test_section_headings_skip_code_blocks(self):
		source = "# Title\n\n## First\n\n```bash\n## not a heading\n```\n\n## Second step\n"
		assert section_headings(source) == [
			{"text": "First", "anchor": "first"},
			{"text": "Second step", "anchor": "second-step"},
		]


class TestStartup:
	def test_creates_the_first_admin_from_env(self, temp_db, secret):
		env = {"KEANEXUS_USERNAME": "admin", "KEANEXUS_PASSWORD": _GOOD_PASSWORD}
		with patch.dict(os.environ, env):
			with TestClient(create_app(start_background_work=False)):
				pass
		assert db.get_user("admin")["role"] == "admin"

	def test_starts_the_pool_sampler_only_when_asked(self, temp_db, secret):
		with patch("web.main.start_pool_sampler") as start_sampler:
			with TestClient(create_app(start_background_work=False)):
				pass
			start_sampler.assert_not_called()
			with TestClient(create_app(start_background_work=True)):
				pass
			start_sampler.assert_called_once()

	@pytest.mark.parametrize(
		("value", "expected"), [(None, True), ("on", True), ("off", False), (" OFF ", False)]
	)
	def test_background_work_switch(self, value, expected):
		env = {} if value is None else {"KEANEXUS_WEB_BACKGROUND_WORK": value}
		with patch.dict(os.environ, env, clear=True):
			assert background_work_enabled() is expected


class TestFavicon:
	def test_favicon_served_at_the_root_without_sign_in(self, client):
		response = client.get("/favicon.ico")
		assert response.status_code == 200
		assert response.headers["content-type"] == "image/x-icon"
		assert response.content[:4] == b"\x00\x00\x01\x00"  # ICO header

	@pytest.mark.parametrize("name", ["favicon-32.png", "favicon-192.png", "apple-touch-icon.png"])
	def test_png_icons_served_from_brand(self, client, name):
		response = client.get(f"/brand/{name}")
		assert response.status_code == 200
		assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

	def test_every_page_links_the_icons(self, client):
		page = client.get("/login").text
		assert 'href="/favicon.ico"' in page
		assert 'rel="apple-touch-icon"' in page
		assert 'keanexus_logo.png"' not in page.split("<body")[0]
