# ----------------------------------------------------------------------------------------------------------------------
# Copyright (c) 2020 Warren Creemers
# See LICENSE in root folder for further information.
# ----------------------------------------------------------------------------------------------------------------------
"""
Manufacturer lookup from the first half of a MAC address (the OUI).

Uses a bundled copy of https://standards-oui.ieee.org/oui/oui.csv, refreshed by scripts/update_oui.py.
"""

import csv
import re
import threading
from importlib import resources
from typing import NamedTuple


class MACInfo(NamedTuple):
    oui: str
    country: str
    company: str


_legal_tla_regex = r'[^\w](s,r,o|s,r,l|s,l|r,o|s,a|d,o,o|o,o|pte|S,p,A|b,v|a,s|d,d|de[^\w]France|a .* Comp.*)[^\w]'
_legal_tla_regex = re.compile(_legal_tla_regex.replace(',', r'[\\\,\.\/\;]'), flags=re.IGNORECASE)

_legal_nonsense_regex = re.compile(
    r"[^\w](ltd|co|ltd|llc|inc|corp|group|pos|pvt|pte|aps|sa|PLC|pty|AB|ans|int|Corporate|"
    r"GmbH|KG|ASA|AG|corporation|MFG|oy|dd|Limited|Sdn|Bhd|PUB|SYS|DIV|s|p|dd|y|BV)[^\w]",
    flags=re.IGNORECASE)


def _sentence_case(s: str) -> str:
    return s[:1] + s[1:].lower()


def clean_company_name(name: str) -> str:
    """
    "SAMSUNG ELECTRONICS CO.,LTD" -> "Samsung Electronics"
    """
    company = name + ' '  # helps the regex match things at the end

    # remove complex legal shit
    company = _legal_tla_regex.sub(' ', company)

    # remove simple legal shit
    # extra padding to stop chained legal stuff defeating the regex eg: "pty,ltd"
    company = re.sub(r'[^\w\-]', '   ', company)
    company = _legal_nonsense_regex.sub(' ', company)

    # Convert long words to sentence case
    words = [w.strip() for w in company.split(' ')]
    words = [w if len(w) <= 4 else _sentence_case(w) for w in words]
    return " ".join(w for w in words if len(w) > 0)


def country_from_address(address: str) -> str:
    """
    The IEEE address field ends with a two letter country code, usually followed by a postcode.
    """
    tokens = address.replace(",", ' ').replace(".", ' ').replace(";", ' ').split(' ')
    tokens = [t for t in tokens if len(t) == 2 and t.isalpha() and t.isupper()]
    return tokens[-1] if tokens else ''


def load_manufacturer_info(lines=None) -> dict[str, MACInfo]:
    """
    :param lines: CSV lines to read, defaults to the bundled IEEE list.
    """
    if lines is None:
        with resources.files('pingthing').joinpath('data/oui.csv').open(encoding='utf-8') as f:
            return load_manufacturer_info(f)

    lut = {}
    rows = csv.reader(lines, delimiter=',', quotechar='"')
    next(rows, None)  # header
    for row in rows:
        if len(row) < 4:
            continue
        oui = row[1].strip().upper()
        lut[oui] = MACInfo(oui, country_from_address(row[3]), clean_company_name(row[2]))
    return lut


def is_random_mac(mac: str) -> bool:
    """
    True for "locally administered" addresses, which phones and laptops use for privacy; they have no vendor.
    """
    digits = re.sub(r'[^0-9A-F]', '', mac.upper())
    return len(digits) >= 2 and bool(int(digits[:2], 16) & 0b10)


class VendorLookup:
    """
    Loads the list on first use, it takes a moment.
    Safe to call from several threads.
    """
    def __init__(self):
        self._lut: dict[str, MACInfo] | None = None
        self._lock = threading.Lock()

    def lookup(self, mac: str) -> MACInfo | None:
        with self._lock:
            if self._lut is None:
                self._lut = load_manufacturer_info()
        oui = re.sub(r'[^0-9A-F]', '', mac.upper())[:6]
        return self._lut.get(oui)
