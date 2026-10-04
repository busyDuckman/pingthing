import ipaddress

import pytest
from textual.widgets import ContentSwitcher

from pingthing.columns import (
    DEFAULT_VIEW,
    Palette,
    UIContext,
    col_config,
    column_widths,
    format_ms,
    histogram_rows,
    matches,
    time_since_as_str,
)
from pingthing.monitor import Host, Monitor
from pingthing.probe import Pinger
from pingthing.stats import PingFail, PingStats
from pingthing.ui import HelpScreen, HostScreen, HostTable, PingThingApp


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
    for kind in Palette.COLOUR:
        assert bw(kind).color is None and bw(kind).bgcolor is None, kind
        assert bw.hex(kind) is None


def make_busy_host(ip="10.0.0.2") -> Host:
    host = Host(ip, name="a-very-long-host-name-indeed", name_done=True, mac="02:00:00:00:00:01",
                mac_done=True, ports=[22, 80, 443, 445, 3389, 8080, 12345])
    for v in [1.0, 2.0, PingFail.TIMEOUT, 3.0, 150.0]:
        host.stats.add(v, now=900)
    return host


def test_every_column_formats_and_sorts_a_fresh_and_a_busy_host():
    ctx = UIContext(gateway="10.0.0.1", own_address="10.0.0.2", now=1000.0)
    hosts = [Host("10.0.0.1"), make_busy_host()]
    for name, col in col_config.items():
        for host in hosts:
            assert len(col.print_func(col.width, host, ctx).txt) == col.width, name
        sorted(hosts, key=lambda h: col.sort_key(h, ctx))


def test_spare_width_goes_to_growing_columns():
    widths = column_widths(DEFAULT_VIEW, 200)
    grown = {c for c, w in zip(DEFAULT_VIEW, widths) if w > col_config[c].width}
    assert grown == {'services', 'manufacturer'}
    assert sum(widths) + len(widths) == 200


def test_histogram_shows_every_bucket():
    stats = PingStats()
    for v in [3.0, 3.5, 30.0, PingFail.TIMEOUT]:
        stats.add(v)
    rows = histogram_rows(stats, 10)
    assert [label for label, _, _ in rows] == ["<1", "1-2", "2-5", "5-10", "10-20", "20-50", "50-100", "100-200",
                                               "200-500", "500+", "failed"]
    assert [count for _, count, _ in rows] == [0, 0, 2, 0, 0, 1, 0, 0, 0, 0, 1]
    assert rows[2][2] == "█" * 10 and rows[0][2] == ""
    assert len(histogram_rows(PingStats(), 10)) == 11


def test_search_matches_any_detail():
    host = make_busy_host()
    assert matches(host, "10.0.0")
    assert matches(host, "HOST-NAME")
    assert matches(host, "rdp")
    assert not matches(host, "printer")


# ----------------------------------------------------------------------------------------------------------------------
# The app, driven headless
# ----------------------------------------------------------------------------------------------------------------------
def make_app() -> PingThingApp:
    monitor = Monitor(ipaddress.IPv4Network("10.0.0.0/24"), Pinger(1), internet="1.1.1.1")
    for n in [20, 3, 100]:
        host = monitor.add_host(f"10.0.0.{n}")
        host.stats.add(float(n))
    monitor.hosts["10.0.0.3"].name, monitor.hosts["10.0.0.3"].name_done = "printer", True
    return PingThingApp(monitor, DEFAULT_VIEW, gateway="10.0.0.3", own_address="10.0.0.20")


async def test_table_sorts_and_filters():
    app = make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        table = app.query_one(HostTable)
        assert [h.ip for h in table.hosts] == ["10.0.0.3", "10.0.0.20", "10.0.0.100"]

        app.sort_by("ping")
        app.sort_by("ping")  # again reverses
        assert [h.ip for h in table.hosts] == ["10.0.0.100", "10.0.0.20", "10.0.0.3"]

        await pilot.press("f4", *"print")
        assert [h.ip for h in table.hosts] == ["10.0.0.3"]
        await pilot.press("escape")
        assert len(table.hosts) == 3 and app.is_running
        await pilot.press("escape")  # nothing left to close, so quit
        assert not app.is_running


async def test_search_selects_and_keys_go_to_the_prompt():
    app = make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.press("slash", *".100")
        assert app.query_one(HostTable).selected.ip == "10.0.0.100"
        await pilot.press("space", "q")  # typed into the search, not pause and quit
        assert not app.paused and app.is_running


async def test_host_window_and_help_open():
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.press("down", "enter")
        assert isinstance(app.screen, HostScreen) and app.screen.host.ip == "10.0.0.20"
        assert app.screen.query_one("#pane", ContentSwitcher).current == "graphs"
        await pilot.press("left")
        assert app.screen.query_one("#pane", ContentSwitcher).current == "traceroute"
        await pilot.press("right", "right")
        assert app.screen.query_one("#pane", ContentSwitcher).current == "port_scan"
        await pilot.press("escape", "f1")
        assert isinstance(app.screen, HelpScreen)
