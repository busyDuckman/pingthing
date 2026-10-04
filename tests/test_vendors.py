import pytest

from pingthing.vendors import (
    VendorLookup,
    clean_company_name,
    country_from_address,
    is_random_mac,
)


@pytest.mark.parametrize("raw, expected", [
    ("SAMSUNG ELECTRONICS CO.,LTD", "Samsung Electronics"),
    ("Apple, Inc.", "Apple"),
    ("TP-LINK TECHNOLOGIES CO.,LTD.", "Tp-link Technologies"),
])
def test_clean_company_name(raw, expected):
    assert clean_company_name(raw) == expected


def test_country_from_address():
    assert country_from_address("2181 Buchanan Loop Ferndale WA US 98248 ") == "US"


def test_bundled_list():
    lookup = VendorLookup()
    m = lookup.lookup("00:d0:ef:12:34:56")
    assert m is not None and m.company == "IGT"


def test_random_mac():
    assert is_random_mac("2A:44:E1:1B:4B:C0")
    assert not is_random_mac("5C:87:9C:07:8A:41")
