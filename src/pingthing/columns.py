# ----------------------------------------------------------------------------------------------------------------------
# Copyright (c) 2020 Warren Creemers
# See LICENSE in root folder for further information.
# ----------------------------------------------------------------------------------------------------------------------
"""
The host table's columns: how each is formatted and sorted.

Cells carry a meaning (good, slow, bad...) rather than a colour, the palette decides how that looks.
"""

import ipaddress
import math
import time
from collections.abc import Callable
from typing import Any, NamedTuple

from rich.style import Style

from pingthing.monitor import Host
from pingthing.probe import port_name
from pingthing.stats import HISTOGRAM_EDGES, PingFail, PingResult, PingStats
from pingthing.vendors import is_random_mac


# ----------------------------------------------------------------------------------------------------------------------
# Text formatting
# ----------------------------------------------------------------------------------------------------------------------
def format_ms(ping: PingResult | None) -> str:
    """
    Milliseconds, with as many decimal places as fit in a narrow column.
    """
    if ping is None:
        return "-"
    if isinstance(ping, PingFail):
        return f"({ping.value})"
    if ping < 10:
        return f"{ping:.2f}"
    if ping < 100:
        return f"{ping:.1f}"
    return str(round(ping))


def ping_kind(ping: PingResult | None) -> str:
    if ping is None:
        return 'plain'
    if isinstance(ping, PingFail):
        return 'fail'
    if ping > 100:
        return 'bad'
    if ping > 20:
        return 'slow'
    if ping > 5:
        return 'fine'
    return 'good'


def time_since_as_str(when: float | None, now: float | None = None) -> str:
    if when is None:
        return "n/a"  # no event recorded yet

    seconds = int((time.time() if now is None else now) - when)
    secs = seconds % 60
    mins = (seconds // 60) % 60
    hrs = (seconds // (60 * 60)) % 24
    days = seconds // (24 * 60 * 60)

    if seconds < 60:
        return f"{secs}s"
    if seconds < 60 * 60:
        return f"{mins}m{secs:02}s"
    if seconds < 24 * 60 * 60:
        return f"{hrs}h{mins:02}m"
    return f"{days}d{hrs:02}h"


def fit(txt: str, width: int, right: bool = False) -> str:
    txt = txt[:width]
    return txt.rjust(width) if right else txt.ljust(width)


def histogram_rows(stats: PingStats, bar_width: int) -> list[tuple[str, int, str]]:
    """
    The ping histogram as (bucket label, count, bar) rows, every bucket and then the failed pings.
    """
    edges = [0, *HISTOGRAM_EDGES, math.inf]
    labels = ["<1"] + [f"{lo}-{hi}" for lo, hi in zip(edges[1:-2], edges[2:-1])] + [f"{HISTOGRAM_EDGES[-1]}+"]
    rows = [*zip(labels, stats.histogram), ("failed", stats.fails)]
    most = max(count for _, count in rows) or 1
    return [(label, count, "█" * math.ceil(bar_width * count / most)) for label, count in rows]


# ----------------------------------------------------------------------------------------------------------------------
# Palette
# ----------------------------------------------------------------------------------------------------------------------
class Palette:
    """
    How each kind of cell looks. In black and white mode, attributes carry the same meaning as the colours.
    """
    COLOUR = {
        'plain': Style(),
        'good': Style(color='#8fc48a'),
        'fine': Style(color="#b7b8b8"),
        'slow': Style(color='#d9b860'),
        'bad': Style(color='#e07a6e'),
        'fail': Style(color='#e07a6e'),
        'pending': Style(color='#8c8fa1', italic=True),
        'dim': Style(color='#8c8fa1'),
        'service': Style(color='#a8b8e0'),
        'gateway': Style(color='#d9b860', bold=True),
        'self': Style(color='#7fb4c0', bold=True),
        'cursor': Style(bgcolor='#2e3b52'),
    }
    BW = {
        'plain': Style(),
        'good': Style(),
        'fine': Style(),
        'slow': Style(bold=True),
        'bad': Style(reverse=True),
        'fail': Style(reverse=True),
        'pending': Style(dim=True),
        'dim': Style(dim=True),
        'service': Style(),
        'gateway': Style(bold=True),
        'self': Style(bold=True),
        'cursor': Style(underline=True, bold=True),
    }

    def __init__(self, bw: bool = False):
        self.bw = bw
        self.styles = self.BW if bw else self.COLOUR

    def __call__(self, kind: str, bold: bool = False) -> Style:
        style = self.styles[kind]
        return style + Style(bold=True) if bold else style

    def hex(self, kind: str) -> str | None:
        """
        The colour for a kind of cell, for markup. None in black and white mode.
        """
        colour = self.styles[kind].color
        return None if self.bw or colour is None else colour.triplet.hex


# ----------------------------------------------------------------------------------------------------------------------
# Columns
# ----------------------------------------------------------------------------------------------------------------------
class Cell(NamedTuple):
    txt: str
    kind: str = 'plain'
    bold: bool = False


class UIContext(NamedTuple):
    """
    Things a column needs to know beyond the host itself.
    """
    gateway: str | None
    own_address: str | None
    now: float


class ColConfig(NamedTuple):
    heading: str
    width: int
    print_func: Callable[[int, Host, UIContext], Cell]
    sort_key: Callable[[Host, UIContext], Any]
    right: bool = False  # right aligned


def format_flag(w: int, host: Host, ctx: UIContext) -> Cell:
    if host.ip == ctx.gateway:
        return Cell(fit('G', w), 'gateway')
    if host.ip == ctx.own_address:
        return Cell(fit('*', w), 'self')
    return Cell(fit('', w))


def format_ip(w: int, host: Host, ctx: UIContext) -> Cell:
    return Cell(fit(host.ip, w), bold=True)


def format_ping(w: int, host: Host, ctx: UIContext) -> Cell:
    ping = host.stats.last_ping
    return Cell(fit(format_ms(ping), w, right=True), ping_kind(ping), bold=True)


def _stat_cell(w: int, value: float | None) -> Cell:
    return Cell(fit(format_ms(value), w, right=True), ping_kind(value))


def format_mean(w: int, host: Host, ctx: UIContext) -> Cell:
    return _stat_cell(w, host.stats.mean if host.stats.n > 0 else None)


def format_best(w: int, host: Host, ctx: UIContext) -> Cell:
    return _stat_cell(w, host.stats.min)


def format_worst(w: int, host: Host, ctx: UIContext) -> Cell:
    return _stat_cell(w, host.stats.max)


def format_sd(w: int, host: Host, ctx: UIContext) -> Cell:
    sd = host.stats.sd_sample() if host.stats.n > 1 else None
    return Cell(fit(format_ms(sd), w, right=True))


def format_up_time(w: int, host: Host, ctx: UIContext) -> Cell:
    if host.stats.n + host.stats.fails == 0:
        return Cell(fit("-", w, right=True))
    up = host.stats.percent_reachable()
    if up >= 1.0:
        return Cell(fit("100%", w, right=True), 'good')
    return Cell(fit(f"{(up * 100):.1f}%", w, right=True), 'slow' if up > 0.9 else 'bad')


def format_last_outage(w: int, host: Host, ctx: UIContext) -> Cell:
    stats = host.stats
    if stats.last_fail is None:
        return Cell(fit("-", w), 'good')
    if stats.last_ok is None or stats.last_ok < stats.last_fail:
        # down now, show how long for
        since = stats.last_ok
        return Cell(fit(f"-{time_since_as_str(since, ctx.now)}" if since else "down", w), 'bad')
    return Cell(fit(time_since_as_str(stats.last_fail, ctx.now), w), 'fine')


def format_name(w: int, host: Host, ctx: UIContext) -> Cell:
    if not host.name_done:
        return Cell(fit('...', w), 'pending')
    if host.name is None:
        return Cell(fit('(unknown)', w), 'dim')
    return Cell(fit(host.name, w))


def services_text(host: Host) -> str:
    return ",".join(port_name(p) for p in host.ports or [])


def format_services(w: int, host: Host, ctx: UIContext) -> Cell:
    if host.ports is None:
        return Cell(fit('scanning', w), 'pending')
    ports = host.ports
    txt = services_text(host)
    if len(txt) > w:
        # redo with marquee effect
        seconds = int(ctx.now)
        ports = [ports[(i + seconds) % len(ports)] for i in range(len(ports))]
        txt = ",".join(port_name(p) for p in ports)
    return Cell(fit(txt, w), 'service')


def format_mac(w: int, host: Host, ctx: UIContext) -> Cell:
    if not host.mac_done:
        return Cell(fit('scanning', w), 'pending')
    return Cell(fit(host.mac or 'n/a', w), 'dim')


def manufacturer_text(host: Host) -> str:
    if not host.mac_done:
        return ''
    if host.mac is None:
        return 'n/a'
    if host.vendor is None:
        return '(random MAC)' if is_random_mac(host.mac) else '?'
    m = host.vendor
    return f"({m.country}) {m.company}" if m.country else m.company


def format_manufacturer(w: int, host: Host, ctx: UIContext) -> Cell:
    txt = manufacturer_text(host)
    if host.vendor is None:
        return Cell(fit(txt, w), 'dim')
    return Cell(fit(txt, w), 'slow' if host.vendor.company == 'Private' else 'plain')


# Sort keys. Missing values go last whichever way the column is sorted.
def _number(value: float | None) -> float:
    return math.inf if value is None or isinstance(value, PingFail) else value


def _text(value: str | None) -> str:
    return value.lower() if value else "￿"


col_config = {
    'flag': ColConfig('', 1, format_flag,
                      lambda h, ctx: (h.ip != ctx.gateway, h.ip != ctx.own_address)),
    'ip': ColConfig('ip', 15, format_ip,
                    lambda h, ctx: ipaddress.IPv4Address(h.ip)),
    'ping': ColConfig('ping', 6, format_ping,
                      lambda h, ctx: _number(h.stats.last_ping), right=True),
    'mean': ColConfig('ave', 6, format_mean,
                      lambda h, ctx: _number(h.stats.mean if h.stats.n else None), right=True),
    'best': ColConfig('min', 6, format_best,
                      lambda h, ctx: _number(h.stats.min), right=True),
    'worst': ColConfig('max', 6, format_worst,
                       lambda h, ctx: _number(h.stats.max), right=True),
    'sd': ColConfig('sd', 6, format_sd,
                    lambda h, ctx: _number(h.stats.sd_sample() if h.stats.n > 1 else None), right=True),
    'up-time': ColConfig('up', 6, format_up_time,
                         lambda h, ctx: h.stats.percent_reachable(), right=True),
    'last-outage': ColConfig('since', 7, format_last_outage,
                             lambda h, ctx: -(h.stats.last_fail or 0)),
    'name': ColConfig('name', 20, format_name,
                      lambda h, ctx: _text(h.name)),
    'services': ColConfig('services', 14, format_services,
                          lambda h, ctx: -len(h.ports or [])),
    'mac': ColConfig('mac', 17, format_mac,
                     lambda h, ctx: _text(h.mac)),
    'manufacturer': ColConfig('manufacturer', 20, format_manufacturer,
                              lambda h, ctx: _text(manufacturer_text(h))),
}

DEFAULT_VIEW = list(col_config.keys())

# columns that get any spare screen width
GROWING_COLUMNS = ('services', 'manufacturer')

COLUMN_GAP = 1


def column_widths(view: list[str], width: int) -> list[int]:
    """
    Column widths for a screen width, spare width is shared evenly between the columns that benefit from it
    (or the last column).
    """
    widths = [col_config[c].width for c in view]
    growers = [i for i, c in enumerate(view) if c in GROWING_COLUMNS] or [len(widths) - 1]
    spare = max(0, width - sum(widths) - COLUMN_GAP * len(widths))
    for n, i in enumerate(growers):
        widths[i] += spare // len(growers) + (1 if n < spare % len(growers) else 0)
    return widths


def matches(host: Host, text: str) -> bool:
    """
    For search and filter: does any of the host's details contain the text.
    """
    text = text.lower()
    fields = [host.ip, host.name, host.mac, manufacturer_text(host), services_text(host)]
    return any(text in f.lower() for f in fields if f)
