# ----------------------------------------------------------------------------------------------------------------------
# Copyright (c) 2020 Warren Creemers
# See LICENSE in root folder for further information.
# ----------------------------------------------------------------------------------------------------------------------
"""
Terminal UI, draws the monitor's host table.
"""

import asyncio
import time
from collections.abc import Callable
from typing import NamedTuple

from asciimatics.screen import Screen

from pingthing.monitor import Host, Monitor
from pingthing.probe import tcp_ports_we_care_about
from pingthing.stats import PingFail, PingResult
from pingthing.vendors import is_random_mac

COLOUR_BLACK = Screen.COLOUR_BLACK
COLOUR_RED = Screen.COLOUR_RED
COLOUR_GREEN = Screen.COLOUR_GREEN
COLOUR_YELLOW = Screen.COLOUR_YELLOW
COLOUR_BLUE = Screen.COLOUR_BLUE
COLOUR_MAGENTA = Screen.COLOUR_MAGENTA
COLOUR_CYAN = Screen.COLOUR_CYAN
COLOUR_WHITE = Screen.COLOUR_WHITE
A_BOLD = Screen.A_BOLD
A_NORMAL = Screen.A_NORMAL
A_REVERSE = Screen.A_REVERSE

FRAME_TIME = 0.05  # seconds between checks for key presses
REDRAW_TIME = 0.5  # seconds between redraws when nothing else happens


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


def get_ping_colour(ping: PingResult | None) -> int:
    if ping is None:
        return COLOUR_WHITE
    if isinstance(ping, PingFail):
        return COLOUR_MAGENTA
    if ping > 100:
        return COLOUR_RED
    if ping > 20:
        return COLOUR_YELLOW
    if ping > 5:
        return COLOUR_CYAN
    return COLOUR_GREEN


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


# ----------------------------------------------------------------------------------------------------------------------
# Columns
# ----------------------------------------------------------------------------------------------------------------------
class Cell(NamedTuple):
    txt: str
    fore: int = COLOUR_WHITE
    back: int = COLOUR_BLACK
    attr: int = A_NORMAL


class ColConfig:
    def __init__(self, heading: str, width: int, print_func: Callable[[int, Host, "UIContext"], Cell]):
        self.heading = heading
        self.width = width
        self.print_func = print_func


class UIContext(NamedTuple):
    """
    Things a column needs to know beyond the host itself.
    """
    gateway: str | None
    own_address: str | None
    now: float


def format_flag(w: int, host: Host, ctx: UIContext) -> Cell:
    if host.ip == ctx.gateway:
        return Cell(fit('G', w), COLOUR_YELLOW, attr=A_BOLD)
    if host.ip == ctx.own_address:
        return Cell(fit('*', w), COLOUR_CYAN, attr=A_BOLD)
    return Cell(fit('', w))


def format_ip(w: int, host: Host, ctx: UIContext) -> Cell:
    return Cell(fit(host.ip, w), attr=A_BOLD)


def format_ping(w: int, host: Host, ctx: UIContext) -> Cell:
    ping = host.stats.last_ping
    return Cell(fit(format_ms(ping), w - 1, right=True) + ' ', get_ping_colour(ping), attr=A_BOLD)


def _stat_cell(w: int, value: float | None) -> Cell:
    return Cell(fit(format_ms(value), w - 1, right=True) + ' ', get_ping_colour(value))


def format_mean(w: int, host: Host, ctx: UIContext) -> Cell:
    return _stat_cell(w, host.stats.mean if host.stats.n > 0 else None)


def format_best(w: int, host: Host, ctx: UIContext) -> Cell:
    return _stat_cell(w, host.stats.min)


def format_worst(w: int, host: Host, ctx: UIContext) -> Cell:
    return _stat_cell(w, host.stats.max)


def format_sd(w: int, host: Host, ctx: UIContext) -> Cell:
    sd = host.stats.sd_sample() if host.stats.n > 1 else None
    return Cell(fit(format_ms(sd), w - 1, right=True) + ' ')


def format_up_time(w: int, host: Host, ctx: UIContext) -> Cell:
    up = host.stats.percent_reachable()
    if up >= 1.0:
        return Cell(fit("100%", w - 1, right=True) + ' ', COLOUR_GREEN)
    return Cell(fit(f"{(up * 100):.1f}%", w - 1, right=True) + ' ', COLOUR_YELLOW if up > 0.9 else COLOUR_RED)


def format_last_outage(w: int, host: Host, ctx: UIContext) -> Cell:
    stats = host.stats
    if stats.last_fail is None:
        return Cell(fit("-", w), COLOUR_GREEN)
    if stats.last_ok is None or stats.last_ok < stats.last_fail:
        # down now, show how long for
        since = stats.last_ok
        return Cell(fit(f"-{time_since_as_str(since, ctx.now)}" if since else "down", w), COLOUR_RED)
    return Cell(fit(time_since_as_str(stats.last_fail, ctx.now), w), COLOUR_CYAN)


def format_name(w: int, host: Host, ctx: UIContext) -> Cell:
    if not host.name_done:
        return Cell(fit('...', w), COLOUR_MAGENTA)
    if host.name is None:
        return Cell(fit('(unknown)', w), COLOUR_RED)
    return Cell(fit(host.name, w))


def format_services(w: int, host: Host, ctx: UIContext) -> Cell:
    if host.ports is None:
        return Cell(fit('scanning', w), COLOUR_MAGENTA, COLOUR_CYAN)
    ports = host.ports
    txt = ",".join(tcp_ports_we_care_about[p] for p in ports)
    if len(txt) > w:
        # redo with marquee effect
        seconds = int(ctx.now)
        ports = [ports[(i + seconds) % len(ports)] for i in range(len(ports))]
        txt = ",".join(tcp_ports_we_care_about[p] for p in ports)
    return Cell(fit(txt, w), COLOUR_BLACK, COLOUR_CYAN)


def format_mac(w: int, host: Host, ctx: UIContext) -> Cell:
    if not host.mac_done:
        return Cell(fit('scanning', w), COLOUR_MAGENTA)
    return Cell(fit(host.mac or 'n/a', w), COLOUR_MAGENTA)


def format_manufacturer(w: int, host: Host, ctx: UIContext) -> Cell:
    if not host.mac_done:
        return Cell(fit('', w))
    if host.mac is None:
        return Cell(fit('n/a', w), COLOUR_YELLOW)
    if host.vendor is None:
        if is_random_mac(host.mac):
            return Cell(fit('(random MAC)', w), COLOUR_CYAN)
        return Cell(fit('?', w), COLOUR_YELLOW)
    m = host.vendor
    txt = f"({m.country}) {m.company}" if m.country else m.company
    return Cell(fit(txt, w), COLOUR_RED if m.company == 'Private' else COLOUR_WHITE)


PING_COL_SIZE = 7

col_config = {
    'flag': ColConfig('', 2, format_flag),
    'ip': ColConfig('|ip', 16, format_ip),
    'ping': ColConfig('|ping', PING_COL_SIZE, format_ping),
    'mean': ColConfig('|ave', PING_COL_SIZE, format_mean),
    'best': ColConfig('|min', PING_COL_SIZE, format_best),
    'worst': ColConfig('|max', PING_COL_SIZE, format_worst),
    'sd': ColConfig('|sd', PING_COL_SIZE, format_sd),
    'up-time': ColConfig('|up', 7, format_up_time),
    'last-outage': ColConfig('|since', 8, format_last_outage),
    'name': ColConfig('|name', 20, format_name),
    'services': ColConfig('|services', 14, format_services),
    'mac': ColConfig('|mac', 18, format_mac),
    'manufacturer': ColConfig('|manufacturer', 20, format_manufacturer),
}

DEFAULT_VIEW = list(col_config.keys())

# columns that get any spare screen width
GROWING_COLUMNS = ('services', 'manufacturer')


# ----------------------------------------------------------------------------------------------------------------------
# Screen
# ----------------------------------------------------------------------------------------------------------------------
class Palette:
    """
    Colours, or for black and white mode, attributes that carry the same meaning.
    """
    def __init__(self, bw: bool):
        self.bw = bw

    def __call__(self, cell: Cell) -> tuple[int, int, int]:
        if not self.bw:
            return cell.fore, cell.back, cell.attr
        if cell.back != COLOUR_BLACK or cell.fore in (COLOUR_RED, COLOUR_MAGENTA):
            return COLOUR_WHITE, COLOUR_BLACK, A_REVERSE
        if cell.fore == COLOUR_YELLOW:
            return COLOUR_WHITE, COLOUR_BLACK, A_BOLD
        return COLOUR_WHITE, COLOUR_BLACK, cell.attr


class UI:
    def __init__(self, monitor: Monitor, view: list[str], bw: bool = False,
                 gateway: str | None = None, own_address: str | None = None):
        self.monitor = monitor
        self.view = view
        self.palette = Palette(bw)
        self.gateway = gateway
        self.own_address = own_address

    def _print(self, screen: Screen, cell: Cell, x: int, y: int):
        fore, back, attr = self.palette(cell)
        screen.print_at(cell.txt, x, y, colour=fore, bg=back, attr=attr)

    def draw(self, screen: Screen):
        ctx = UIContext(self.gateway, self.own_address, time.time())
        m = self.monitor

        # share spare width evenly between the columns that benefit from it (or the last column)
        widths = [col_config[c].width for c in self.view]
        growers = [i for i, c in enumerate(self.view) if c in GROWING_COLUMNS] or [len(widths) - 1]
        spare = max(0, screen.width - sum(widths))
        for n, i in enumerate(growers):
            widths[i] += spare // len(growers) + (1 if n < spare % len(growers) else 0)

        # heading
        self._print(screen, Cell('PING THING (press q to exit)'.center(screen.width), COLOUR_MAGENTA, COLOUR_YELLOW),
                    0, 0)

        # col headings
        heading = "".join(fit(col_config[c].heading, w) for c, w in zip(self.view, widths))
        self._print(screen, Cell(fit(heading, screen.width), COLOUR_YELLOW, COLOUR_MAGENTA), 0, 1)

        # list scan results
        hosts = m.sorted_hosts()
        for row in range(screen.height - 3):
            y = row + 2
            if row < len(hosts):
                x = 0
                for c, w in zip(self.view, widths):
                    self._print(screen, col_config[c].print_func(w, hosts[row], ctx), x, y)
                    x += w
            else:
                txt = " scanning..." if row == 0 and not hosts else ""
                self._print(screen, Cell(fit(txt, screen.width)), 0, y)

        # show info bar at bottom
        info = (f" ping unit=ms  hosts={len(hosts)}  sweeps={m.sweeps}  network={m.network}"
                f"  gateway={self.gateway or '?'}  ping engine={m.pinger.name}  time_out={m.time_out}s")
        self._print(screen, Cell(fit(info, screen.width), COLOUR_WHITE, COLOUR_BLUE), 0, screen.height - 1)
        screen.refresh()

    async def run(self):
        """
        Draw until the user presses q.
        """
        screen = Screen.open()
        try:
            last_draw = 0.0
            while True:
                if screen.has_resized():
                    screen.close()
                    screen = Screen.open()
                    last_draw = 0.0

                if time.monotonic() - last_draw >= REDRAW_TIME:
                    self.draw(screen)
                    last_draw = time.monotonic()

                ev = screen.get_key()
                if ev in (ord('Q'), ord('q'), 3):  # 3 is ctrl+c on Windows
                    return
                await asyncio.sleep(FRAME_TIME)
        finally:
            screen.close()
