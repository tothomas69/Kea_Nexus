"""test_maintenance_data.py — Tests for web/maintenance_data.py."""

import pytest

from web import maintenance_data as md

_NOW = 1_700_000_000


def test_declined_rows_sorted_numerically_and_only_declined():
	leases = [
		{"ip-address": "172.16.17.100", "state": 1, "cltt": _NOW, "valid-lft": 3600},
		{"ip-address": "172.16.17.9", "state": 1, "cltt": _NOW},
		{"ip-address": "172.16.17.5", "state": 0},
		{"state": 1},
	]
	assert md.declined_rows(leases, _NOW) == [
		{"ip": "172.16.17.9", "frees_in": "24h 0m"},
		{"ip": "172.16.17.100", "frees_in": "1h 0m"},
	]


@pytest.mark.parametrize(
	("text", "error_fragment", "seconds"),
	[
		("300", None, 300),
		("0", None, 0),
		("3600", None, 3600),
		("3601", "between", 0),
		("-1", "between", 0),
		("soon", "whole number", 0),
	],
)
def test_validate_auto_enable(text, error_fragment, seconds):
	error, parsed = md.validate_auto_enable(text)
	assert parsed == seconds
	assert (error is None) if error_fragment is None else (error_fragment in error)


@pytest.mark.parametrize(
	("typed", "ok"), [("WIPE", True), (" WIPE ", True), ("wipe", False), ("", False)]
)
def test_wipe_confirmed(typed, ok):
	assert md.wipe_confirmed(typed) is ok
