import ipaddress

import pytest

from pingthing.monitor import Host, Monitor
from pingthing.probe import Pinger
from pingthing.stats import PingFail
from pingthing.ui import (
    A_REVERSE,
    COLOUR_BLACK,
    COLOUR_RED,
    COLOUR_WHITE,
    DEFAULT_VIEW,
    UI,
    Cell,
    Palette,
    UIContext,
    col_config,
    format_ms,
    time_since_as_str,
)


@pytest.mark.parametrize("value, expected", [
    (4.5, "4.50"),
    (45.67, "45.7"),
    (456.7, "457"),
    (PingFail.TIMEOUT, "(n/a)"),
    (PingFail.UNREACHABLE, "(n/h)"),
])
def test_format_ms(value, expected):
    assert format_ms(value) == expected


@pytest.mark.parametrize("seconds_ago, expected", [
    (5, "5s"),
    (65, "1m05s"),
    (59 * 60 + 59, "59m59s"),
    (2 * 3600 + 5 * 60, "2h05m"),         # used to show 0h, hours were always 0
    (23 * 3600 + 59 * 60, "23h59m"),
    (3 * 86400 + 4 * 3600, "3d04h"),     # used to show 0d
])
def test_time_since(seconds_ago, expected):
    assert time_since_as_str(1_000_000 - seconds_ago, now=1_000_000) == expected


def test_bw_palette_has_no_colour():
    bw = Palette(bw=True)
    # meaning that was carried by colour becomes reverse video
    assert bw(Cell("x", COLOUR_RED)) == (COLOUR_WHITE, COLOUR_BLACK, A_REVERSE)


def test_every_column_formats_a_fresh_and_a_busy_host():
    ctx = UIContext(gateway="10.0.0.1", own_address="10.0.0.2", now=1000.0)
    fresh = Host("10.0.0.1")
    busy = Host("10.0.0.2", name="a-very-long-host-name-indeed", name_done=True, mac="02:00:00:00:00:01",
                mac_done=True, ports=[22, 80, 443, 445, 3389, 8080])
    for v in [1.0, 2.0, PingFail.TIMEOUT, 3.0]:
        busy.stats.add(v, now=900)
    for name, col in col_config.items():
        for host in (fresh, busy):
            cell = col.print_func(col.width, host, ctx)
            assert len(cell.txt) == col.width, name


class FakeScreen:
    def __init__(self, width=160, height=12):
        self.width, self.height = width, height
        self.lines = {}

    def print_at(self, txt, x, y, colour=7, bg=0, attr=0):
        line = self.lines.get(y, " " * self.width)
        self.lines[y] = (line[:x] + txt + line[x + len(txt):])[:max(self.width, x + len(txt))]

    def refresh(self):
        pass


def test_draw_fills_the_screen():
    monitor = Monitor(ipaddress.IPv4Network("10.0.0.0/24"), Pinger(1))
    for ip in ["10.0.0.20", "10.0.0.3", "10.0.0.100"]:
        monitor.add_host(ip)
    screen = FakeScreen()
    UI(monitor, DEFAULT_VIEW, gateway="10.0.0.3").draw(screen)
    assert all(len(line) == screen.width for line in screen.lines.values())
