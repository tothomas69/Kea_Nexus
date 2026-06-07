"""
test_helpers.py — Tests for pure utility functions in helpers.py.
Streamlit-cached loader functions are not tested here (require a live ST session).
"""

from unittest.mock import patch

import pytest

from helpers import chip, fmt_ttl, leases_to_df

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
