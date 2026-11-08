"""test_search_data.py — Tests for web/search_data.py, the header search's rules."""

from address_map import AddressRow
from web.search_data import MAX_RESULTS, search

ROWS = [
	AddressRow("172.16.17.1", 1, "gateway", label="gateway"),
	AddressRow("172.16.17.10", 10, "static", label="NAS", mac="aa:bb:cc:00:00:10"),
	AddressRow(
		"172.16.17.120", 120, "leased", hostname="kids-ipad", mac="f0:18:98:3a:77:10", info="2h"
	),
	AddressRow(
		"172.16.17.177",
		177,
		"reserved",
		label="Tom's Mac",
		hostname="macbook-pro",
		mac="a0:ce:c8:8f:42:e5",
	),
	AddressRow("172.16.17.200", 200, "scope"),
	AddressRow("172.16.17.250", 250, "free"),
]
DEVICES = [
	{
		"friendly_name": "kids_ipad",
		"hostname": "kids-ipad",
		"last_seen_ip_address": "172.16.17.120",
		"is_quarantined": 1,
	},
]


def test_too_short_finds_nothing():
	assert search(ROWS, DEVICES, " k ") == []


def test_devices_first_then_addresses():
	results = search(ROWS, DEVICES, "kids")
	assert [result["title"] for result in results] == ["kids_ipad", "kids-ipad"]
	assert results[0] == {
		"title": "kids_ipad",
		"detail": "kids-ipad · 172.16.17.120 · quarantined",
		"href": "/quarantine",
		"kind": "device",
	}
	assert results[1]["href"] == "/leases?q=172.16.17.120"


def test_label_beats_hostname_for_the_title_and_mac_matches():
	result = search(ROWS, [], "A0:CE")[0]
	assert result["title"] == "Tom's Mac"
	assert result["detail"] == "172.16.17.177 · macbook-pro · a0:ce:c8:8f:42:e5 · reserved"


def test_static_without_lease_links_to_the_map():
	assert search(ROWS, [], "nas")[0]["href"] == "/"


def test_empty_addresses_only_match_by_ip():
	assert search(ROWS, [], "scope") == []
	titles = [result["title"] for result in search(ROWS, [], "17.250")]
	assert titles == ["172.16.17.250"]


def test_capped():
	rows = [AddressRow(f"172.16.17.{n}", n, "leased", hostname=f"device-{n}") for n in range(20)]
	assert len(search(rows, [], "device")) == MAX_RESULTS
