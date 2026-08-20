"""
test_helpers.py — Tests for pure utility functions in helpers.py.
Streamlit-cached loader functions are not tested here (require a live ST session).
"""

from unittest.mock import patch

import pytest

from helpers import (
	build_hostname_override_sets,
	build_reservation_type_sets,
	chip,
	distinct_real_hostnames,
	fmt_ttl,
	html_safe_mac,
	lease_for_reservation,
	lease_type,
	leases_to_df,
	real_hostname,
)

# ─── fmt_ttl ──────────────────────────────────────────────────────────────────


class TestFmtTtl:
	def test_zero_seconds_is_expired(self):
		assert fmt_ttl(0) == "expired"

	def test_negative_seconds_is_expired(self):
		assert fmt_ttl(-1) == "expired"

	def test_exactly_one_hour(self):
		assert fmt_ttl(3600) == "1h 0m"

	def test_one_hour_one_minute(self):
		assert fmt_ttl(3661) == "1h 1m"

	def test_less_than_one_hour(self):
		assert fmt_ttl(600) == "0h 10m"

	def test_59_minutes_59_seconds_rounds_down(self):
		assert fmt_ttl(3599) == "0h 59m"

	def test_multiple_hours(self):
		assert fmt_ttl(7200) == "2h 0m"

	def test_one_minute(self):
		assert fmt_ttl(60) == "0h 1m"


# ─── chip ─────────────────────────────────────────────────────────────────────


class TestChip:
	def test_contains_label(self):
		assert "active" in chip("active", "green")

	def test_contains_css_class(self):
		assert 'class="chip green"' in chip("active", "green")

	def test_is_span_element(self):
		result = chip("test", "red")
		assert result.startswith("<span")
		assert result.endswith("</span>")

	def test_different_classes_produce_different_output(self):
		assert chip("x", "green") != chip("x", "red")


# ─── html_safe_mac ────────────────────────────────────────────────────────────


class TestHtmlSafeMac:
	def test_encodes_colons(self):
		assert html_safe_mac("d2:de:e1:4d:e6:73") == "d2&#58;de&#58;e1&#58;4d&#58;e6&#58;73"

	def test_no_literal_colon_left_to_match_a_markdown_shortcode(self):
		assert ":" not in html_safe_mac("d2:de:e1:4d:e6:73")

	def test_empty_string_passthrough(self):
		assert html_safe_mac("") == ""

	def test_no_colons_unaffected(self):
		assert html_safe_mac("gateway") == "gateway"


# ─── leases_to_df ─────────────────────────────────────────────────────────────

_FIXED_NOW = 1_700_000_000


def _make_lease(
	ip: str, hostname: str = "", mac: str = "", state: int = 0, valid_lft: int = 86400
) -> dict:
	return {
		"ip-address": ip,
		"hostname": hostname,
		"hw-address": mac,
		"cltt": _FIXED_NOW - 100,
		"valid-lft": valid_lft,
		"state": state,
	}


@pytest.fixture
def frozen_time():
	with patch("helpers.time.time", return_value=_FIXED_NOW):
		yield


class TestLeasesToDf:
	def test_returns_dataframe_with_expected_columns(self, frozen_time):
		df = leases_to_df([_make_lease("10.0.0.1")])
		assert set(["IP", "Hostname", "MAC", "Expires", "Status", "_state"]).issubset(df.columns)

	def test_rows_sorted_by_ip_numerically(self, frozen_time):
		leases = [_make_lease("10.0.0.10"), _make_lease("10.0.0.2"), _make_lease("10.0.0.1")]
		df = leases_to_df(leases)
		assert list(df["IP"]) == ["10.0.0.1", "10.0.0.2", "10.0.0.10"]

	def test_active_lease_shows_ok_status(self, frozen_time):
		df = leases_to_df([_make_lease("10.0.0.1", state=0)])
		assert "[OK] active" in df.iloc[0]["Status"]

	def test_declined_lease_shows_declined_status(self, frozen_time):
		df = leases_to_df([_make_lease("10.0.0.1", state=1)])
		assert "declined" in df.iloc[0]["Status"]

	def test_missing_hostname_shown_as_dash(self, frozen_time):
		df = leases_to_df([_make_lease("10.0.0.1", hostname="")])
		assert df.iloc[0]["Hostname"] == "-"

	def test_hostname_preserved_when_present(self, frozen_time):
		df = leases_to_df([_make_lease("10.0.0.1", hostname="myhost")])
		assert df.iloc[0]["Hostname"] == "myhost"

	def test_missing_mac_shown_as_dash(self, frozen_time):
		df = leases_to_df([_make_lease("10.0.0.1", mac="")])
		assert df.iloc[0]["MAC"] == "-"

	def test_empty_lease_list_returns_empty_dataframe(self, frozen_time):
		df = leases_to_df([])
		assert len(df) == 0

	def test_expired_lease_shows_expired(self, frozen_time):
		# cltt + valid_lft < now → expired
		lease = {
			"ip-address": "10.0.0.1",
			"hostname": "",
			"hw-address": "",
			"cltt": _FIXED_NOW - 200,
			"valid-lft": 100,
			"state": 0,
		}
		df = leases_to_df([lease])
		assert df.iloc[0]["Expires"] == "expired"


# ─── build_reservation_type_sets / lease_type / real_hostname ────────────────


def _make_config(reservations: list[dict]) -> dict:
	return {"subnet4": [{"reservations": reservations}]}


class TestBuildReservationTypeSets:
	def test_fixed_ip_reservation(self):
		config = _make_config([{"ip-address": "10.0.0.5", "hw-address": "aa:bb", "hostname": "x"}])
		fixed_ips, reserved_macs, name_hosts = build_reservation_type_sets(config)
		assert fixed_ips == {"10.0.0.5"}
		assert reserved_macs == set()

	def test_mac_only_reservation(self):
		config = _make_config([{"hw-address": "AA:BB", "hostname": "x"}])
		_, reserved_macs, _ = build_reservation_type_sets(config)
		assert reserved_macs == {"aa:bb"}

	def test_name_only_reservation(self):
		config = _make_config([{"hostname": "Some-Host"}])
		_, _, name_hosts = build_reservation_type_sets(config)
		assert name_hosts == {"some-host"}

	def test_none_config_returns_empty_sets(self):
		assert build_reservation_type_sets(None) == (set(), set(), set())


class TestLeaseType:
	def test_fixed_ip_match(self):
		lease = _make_lease("10.0.0.5")
		assert lease_type(lease, {"10.0.0.5"}, set(), set()) == "fixed"

	def test_reserved_mac_match(self):
		lease = _make_lease("10.0.0.9", mac="AA:BB")
		assert lease_type(lease, set(), {"aa:bb"}, set()) == "reserved"

	def test_name_only_match(self):
		lease = _make_lease("10.0.0.9", hostname="Some-Host")
		assert lease_type(lease, set(), set(), {"some-host"}) == "name-only"

	def test_no_match_is_dynamic(self):
		lease = _make_lease("10.0.0.9", hostname="whatever")
		assert lease_type(lease, set(), set(), set()) == "dynamic"


class TestBuildHostnameOverrideSets:
	def test_reservation_with_ip_and_hostname_is_an_override(self):
		config = _make_config([{"ip-address": "10.0.0.5", "hw-address": "aa:bb", "hostname": "x"}])
		override_ips, override_macs = build_hostname_override_sets(config)
		assert override_ips == {"10.0.0.5"}
		assert override_macs == {"aa:bb"}

	def test_reservation_without_hostname_is_not_an_override(self):
		config = _make_config([{"ip-address": "10.0.0.5", "hw-address": "AA:BB"}])
		override_ips, override_macs = build_hostname_override_sets(config)
		assert override_ips == set()
		assert override_macs == set()

	def test_reservation_with_empty_string_hostname_is_not_an_override(self):
		# Kea's config-get always includes every reservation field, "hostname"
		# included, defaulting to "" when never set — mere key presence isn't
		# enough to mean "an override is in effect."
		config = _make_config([{"ip-address": "10.0.0.5", "hw-address": "AA:BB", "hostname": ""}])
		override_ips, override_macs = build_hostname_override_sets(config)
		assert override_ips == set()
		assert override_macs == set()

	def test_name_only_reservation_is_not_an_override(self):
		# No ip-address/hw-address to key on — this isn't a lease-level
		# override, it's how Kea matches the reservation in the first place.
		config = _make_config([{"hostname": "some-host"}])
		override_ips, override_macs = build_hostname_override_sets(config)
		assert override_ips == set()
		assert override_macs == set()

	def test_none_config_returns_empty_sets(self):
		assert build_hostname_override_sets(None) == (set(), set())


class TestRealHostname:
	def test_ip_matching_override_is_blank(self):
		lease = _make_lease("10.0.0.5", hostname="reservation-label")
		assert real_hostname(lease, {"10.0.0.5"}, set()) == ""

	def test_mac_matching_override_is_blank(self):
		lease = _make_lease("10.0.0.9", hostname="reservation-label", mac="AA:BB")
		assert real_hostname(lease, set(), {"aa:bb"}) == ""

	def test_no_matching_override_is_real(self):
		lease = _make_lease("10.0.0.9", hostname="actual-device-name")
		assert real_hostname(lease, set(), set()) == "actual-device-name"

	def test_reserved_lease_without_override_is_real(self):
		# A reservation with no "hostname" key (post-migration) still matches
		# by IP/MAC for lease_type purposes, but isn't a hostname override.
		lease = _make_lease("10.0.0.5", hostname="actual-device-name")
		assert real_hostname(lease, set(), set()) == "actual-device-name"


class TestDistinctRealHostnames:
	def test_excludes_reservation_label_overrides(self):
		config = _make_config(
			[{"ip-address": "10.0.0.5", "hw-address": "aa:bb", "hostname": "label"}]
		)
		leases = [_make_lease("10.0.0.5", hostname="label")]
		assert distinct_real_hostnames(leases, config) == []

	def test_includes_reserved_hostname_without_override(self):
		config = _make_config([{"ip-address": "10.0.0.5", "hw-address": "aa:bb"}])
		leases = [_make_lease("10.0.0.5", hostname="real-name")]
		assert distinct_real_hostnames(leases, config) == ["real-name"]

	def test_includes_dynamic_hostnames(self):
		leases = [_make_lease("10.0.0.9", hostname="real-name")]
		assert distinct_real_hostnames(leases, None) == ["real-name"]

	def test_sorted_and_deduplicated(self):
		leases = [
			_make_lease("10.0.0.9", hostname="beta"),
			_make_lease("10.0.0.10", hostname="alpha"),
			_make_lease("10.0.0.11", hostname="alpha"),
		]
		assert distinct_real_hostnames(leases, None) == ["alpha", "beta"]

	def test_empty_hostnames_excluded(self):
		leases = [_make_lease("10.0.0.9", hostname="")]
		assert distinct_real_hostnames(leases, None) == []


class TestLeaseForReservation:
	def test_matches_by_mac(self):
		reservation = {"hw-address": "AA:BB", "ip-address": "10.0.0.5"}
		lease = _make_lease("10.0.0.5", mac="aa:bb")
		assert lease_for_reservation(reservation, [lease]) is lease

	def test_mac_match_is_case_insensitive(self):
		reservation = {"hw-address": "aa:bb"}
		lease = _make_lease("10.0.0.5", mac="AA:BB")
		assert lease_for_reservation(reservation, [lease]) is lease

	def test_falls_back_to_ip_when_no_mac(self):
		reservation = {"ip-address": "10.0.0.5"}
		lease = _make_lease("10.0.0.5", mac="aa:bb")
		assert lease_for_reservation(reservation, [lease]) is lease

	def test_returns_none_when_no_lease_matches(self):
		reservation = {"hw-address": "aa:bb", "ip-address": "10.0.0.5"}
		other_lease = _make_lease("10.0.0.9", mac="cc:dd")
		assert lease_for_reservation(reservation, [other_lease]) is None

	def test_returns_none_for_empty_reservation(self):
		assert lease_for_reservation({}, [_make_lease("10.0.0.5")]) is None

	def test_returns_none_for_empty_lease_list(self):
		reservation = {"hw-address": "aa:bb"}
		assert lease_for_reservation(reservation, []) is None
