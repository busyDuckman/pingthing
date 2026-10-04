# ----------------------------------------------------------------------------------------------------------------------
# Copyright (c) 2020 Warren Creemers
# See LICENSE in root folder for further information.
# ----------------------------------------------------------------------------------------------------------------------
"""
Terminal UI (Textual), an htop style view of the monitor's hosts.

The monitor updates the hosts in the background, the UI reads them a couple of times a second.
"""

import time

from rich.segment import Segment
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click, Resize
from textual.geometry import Size
from textual.markup import escape
from textual.message import Message
from textual.screen import ModalScreen
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import (
    ContentSwitcher,
    Input,
    Label,
    Log,
    OptionList,
    ProgressBar,
    Sparkline,
    Static,
)

from pingthing import probe
from pingthing.columns import (
    COLUMN_GAP,
    Palette,
    UIContext,
    col_config,
    column_widths,
    fit,
    format_ms,
    histogram_rows,
    manufacturer_text,
    mask_mac,
    mask_name,
    matches,
    ping_kind,
    services_text,
    time_since_as_str,
)
from pingthing.monitor import Host, Monitor
from pingthing.stats import PingFail

REDRAW_TIME = 0.5  # seconds between updates of the table

CALM_THEME = Theme(
    name="pingthing",
    primary="#6f8fbf",
    secondary="#7fb4c0",
    accent="#d9b860",
    foreground="#d4d7de",
    background="#16191f",
    surface="#1d2128",
    panel="#262b35",
    success="#8fc48a",
    warning="#d9b860",
    error="#e07a6e",
    dark=True,
)

MONO_THEME = Theme(
    name="pingthing-mono",
    primary="#a0a0a0",
    secondary="#a0a0a0",
    accent="#ffffff",
    foreground="#ffffff",
    background="#000000",
    surface="#000000",
    panel="#303030",
    success="#ffffff",
    warning="#ffffff",
    error="#ffffff",
    dark=True,
)

# key, label, action; shown along the bottom like htop
FUNCTION_KEYS = [
    ("F1", "Help", "help"),
    ("F3", "Search", "search"),
    ("F4", "Filter", "filter"),
    ("F5", "Pause", "pause"),
    ("F6", "SortBy", "sort"),
    ("F10", "Quit", "quit"),
]

HELP = """\
[b]pingthing[/b], an htop style view of your local network.

[b]Keys[/b]
  Up/Down, PgUp/PgDn, Home/End   move the selection
  Enter or click                 details for the selected host
  F1  ?                          this help
  F3  /                          search, press F3 again for the next match
  F4  \\\\                          filter the table
  F5  space                      pause the display (pinging carries on)
  F6  >                          sort by a column, or click a column heading
  Esc                            close a window or clear the filter, otherwise quit
  F10 q                          quit

[b]Columns[/b]
  G is your gateway, * is this machine. Ping times are in milliseconds.
  ping, ave, min, max, sd   last ping and stats since pingthing started
  up                        percent of pings answered
  since                     time since the last missed ping, or how long it has been down (-)
  (n/a) timed out, (n/h) host unreachable, (err) error

[b]In the host window[/b]
  Left/Right  switch between graphs, port scan and traceroute
  g  graphs               p  full port scan       t  traceroute
  c  copy the address     w  open its web page
"""


# ----------------------------------------------------------------------------------------------------------------------
# Host table
# ----------------------------------------------------------------------------------------------------------------------
class TableHeader(Widget):
    """
    Column headings, click one to sort by it.
    """
    DEFAULT_CSS = """
    TableHeader { height: 1; background: $panel; color: $foreground; text-style: bold; }
    """

    class Clicked(Message):
        def __init__(self, column: str):
            self.column = column
            super().__init__()

    def __init__(self, table: "HostTable"):
        super().__init__()
        self.table = table

    def render(self) -> str:
        parts = []
        for c, w in zip(self.table.view, self.table.widths):
            cfg = col_config[c]
            heading = cfg.heading
            if c == self.table.sort_column:
                heading = f"{heading}{'▼' if self.table.sort_reverse else '▲'}"
            parts.append(fit(heading, w, right=cfg.right))
        return escape((" " * COLUMN_GAP).join(parts))

    def on_click(self, event: Click):
        column = self.table.column_at(event.x)
        if column is not None:
            self.post_message(self.Clicked(column))


class HostTable(ScrollView, can_focus=True):
    """
    The hosts, one per line. Drawn a line at a time so a large network stays cheap to update.
    """
    DEFAULT_CSS = """
    HostTable { height: 1fr; scrollbar-size-vertical: 1; overflow-x: hidden; }
    """

    BINDINGS = [
        Binding("up", "cursor(-1)", show=False),
        Binding("down", "cursor(1)", show=False),
        Binding("pageup", "page(-1)", show=False),
        Binding("pagedown", "page(1)", show=False),
        Binding("home", "cursor(-1000000)", show=False),
        Binding("end", "cursor(1000000)", show=False),
        Binding("enter", "open", show=False),
    ]

    class Opened(Message):
        def __init__(self, host: Host):
            self.host = host
            super().__init__()

    def __init__(self, view: list[str], palette: Palette, **kwargs):
        super().__init__(**kwargs)
        self.view = view
        self.palette = palette
        self.hosts: list[Host] = []
        self.ctx = UIContext(None, None, time.time())
        self.cursor = 0
        self.widths = column_widths(view, 80)
        self.sort_column = 'ip'
        self.sort_reverse = False

    @property
    def selected(self) -> Host | None:
        return self.hosts[self.cursor] if self.hosts else None

    def show(self, hosts: list[Host], ctx: UIContext):
        """
        Replace the rows, keeping the same host selected.
        """
        selected = self.selected
        self.hosts = hosts
        self.ctx = ctx
        if selected is not None and selected in hosts:
            self.cursor = hosts.index(selected)
        self.cursor = max(0, min(self.cursor, len(hosts) - 1))
        self.virtual_size = Size(self.size.width, len(hosts))
        self._scroll_to_cursor()
        self.refresh()

    def select(self, index: int):
        if self.hosts:
            self.cursor = max(0, min(index, len(self.hosts) - 1))
            self._scroll_to_cursor()
            self.refresh()

    def column_at(self, x: int) -> str | None:
        start = 0
        for c, w in zip(self.view, self.widths):
            if start <= x < start + w + COLUMN_GAP:
                return c
            start += w + COLUMN_GAP
        return None

    def _scroll_to_cursor(self):
        top = int(self.scroll_y)
        height = self.scrollable_content_region.height
        if self.cursor < top:
            self.scroll_to(y=self.cursor, animate=False)
        elif height and self.cursor >= top + height:
            self.scroll_to(y=self.cursor - height + 1, animate=False)

    def on_resize(self, event: Resize):
        self.widths = column_widths(self.view, self.scrollable_content_region.width)
        self.virtual_size = Size(self.size.width, len(self.hosts))
        self.refresh()

    def render_line(self, y: int) -> Strip:
        width = self.scrollable_content_region.width
        row = y + int(self.scroll_y)
        base = self.rich_style
        if row >= len(self.hosts):
            if row == 0:
                return Strip([Segment(fit(" scanning...", width), base + self.palette('pending'))])
            return Strip.blank(width, base)

        if row == self.cursor:
            base = base + self.palette('cursor')
        gap = Segment(" " * COLUMN_GAP, base)
        segments = []
        for c, w in zip(self.view, self.widths):
            cell = col_config[c].print_func(w, self.hosts[row], self.ctx)
            segments += [Segment(cell.txt, base + self.palette(cell.kind, cell.bold)), gap]
        return Strip(segments).crop_extend(0, width, base)

    def action_cursor(self, delta: int):
        self.select(self.cursor + delta)

    def action_page(self, direction: int):
        self.select(self.cursor + direction * max(1, self.scrollable_content_region.height - 1))

    def action_open(self):
        if self.selected is not None:
            self.post_message(self.Opened(self.selected))

    def on_click(self, event: Click):
        row = event.y + int(self.scroll_y)
        if row >= len(self.hosts):
            return
        if row == self.cursor or event.chain > 1:
            self.post_message(self.Opened(self.hosts[row]))
        self.select(row)


# ----------------------------------------------------------------------------------------------------------------------
# Windows
# ----------------------------------------------------------------------------------------------------------------------
class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,q,f1,question_mark", "dismiss", "Close")]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > VerticalScroll {
        width: 90; max-width: 100%; height: auto; max-height: 90%;
        border: round $primary; background: $surface; padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(HELP)


class SortScreen(ModalScreen[str]):
    """
    Pick a column to sort by.
    """
    BINDINGS = [Binding("escape,q", "dismiss", "Close")]
    DEFAULT_CSS = """
    SortScreen { align: center middle; }
    SortScreen > OptionList { width: 30; height: auto; max-height: 80%; border: round $primary; }
    """

    def __init__(self, view: list[str], current: str):
        super().__init__()
        self.view = view
        self.current = current

    def compose(self) -> ComposeResult:
        options = OptionList(*self.view)
        options.border_title = "Sort by"
        options.highlighted = self.view.index(self.current) if self.current in self.view else 0
        yield options

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected):
        self.dismiss(self.view[event.option_index])


class HostScreen(ModalScreen):
    """
    Everything we know about one host, a latency graph and some tools.
    The keys along the bottom switch what is shown, the current one is highlighted.
    """
    BINDINGS = [
        Binding("escape,q", "dismiss", "Close"),
        Binding("left", "step(-1)", "Previous view", priority=True),
        Binding("right", "step(1)", "Next view", priority=True),
        Binding("g", "graphs", "Graphs"),
        Binding("p", "port_scan", "Full port scan"),
        Binding("t", "traceroute", "Traceroute"),
        Binding("c", "copy", "Copy address"),
        Binding("w", "open_web", "Open web page"),
    ]
    DEFAULT_CSS = """
    HostScreen { align: center middle; }
    #host {
        width: 100; max-width: 100%; height: 32; max-height: 100%;
        border: round $primary; background: $surface; padding: 0 1;
    }
    #host .section { color: $text-muted; margin-top: 1; }
    #facts { height: auto; }
    #pane { height: 1fr; }
    #graphs { height: 1fr; overflow-y: auto; }
    #graph { height: 1fr; min-height: 2; }
    #histogram { height: auto; }
    #pane Log { height: 1fr; border: round $panel; }
    #port_scan ProgressBar { margin-top: 1; }
    """

    WEB_PORTS = [(443, "https"), (8443, "https"), (80, "http"), (8080, "http")]
    PANES = ("graphs", "port_scan", "traceroute")
    HINTS = [("graphs", "g graphs"), ("port_scan", "p port scan"), ("traceroute", "t traceroute"),
             ("copy", "c copy"), ("open_web", "w web"), ("close", "esc close")]

    def __init__(self, host: Host, monitor: Monitor, ctx: UIContext, palette: Palette):
        super().__init__()
        self.host = host
        self.monitor = monitor
        self.ctx = ctx
        self.palette = palette
        self._started: set[str] = set()  # tools that have been run

    def compose(self) -> ComposeResult:
        with Vertical(id="host") as box:
            box.border_title = self.host.ip
            yield Static(id="facts")
            with ContentSwitcher(initial="graphs", id="pane"):
                with Vertical(id="graphs"):
                    yield Label("Latency, last pings (failed pings show as 0)", classes="section")
                    yield Sparkline([], id="graph")
                    yield Label("Histogram", classes="section")
                    yield Static(id="histogram")
                with Vertical(id="port_scan"):
                    yield ProgressBar(total=0xffff, show_eta=False)
                    yield Log(id="port_scan-log")
                with Vertical(id="traceroute"):
                    yield Log(id="traceroute-log")

    def on_mount(self):
        self._show("graphs")
        self.query_one("#port_scan-log", Log).write_line(
            "Press p to scan all 65535 TCP ports, it takes about half a minute.")
        self.query_one("#traceroute-log", Log).write_line("Press t to trace the route to this host.")
        self.update()
        self.set_interval(REDRAW_TIME, self.update)

    def action_step(self, direction: int):
        current = self.PANES.index(self.query_one("#pane", ContentSwitcher).current)
        self._show(self.PANES[(current + direction) % len(self.PANES)])

    def _show(self, pane: str):
        self.query_one("#pane", ContentSwitcher).current = pane
        hints = []
        for name, hint in self.HINTS:
            hints.append(f"[b $accent]{hint}[/]" if name == pane else hint)
        self.query_one("#host").border_subtitle = " · ".join(hints)

    def _coloured(self, txt: str, kind: str) -> str:
        colour = self.palette.hex(kind)
        return f"[{colour}]{escape(txt)}[/]" if colour else escape(txt)

    def update(self):
        host, stats = self.host, self.host.stats
        now = time.time()
        if host.ip == self.ctx.gateway:
            role = " (gateway)"
        elif host.ip == self.ctx.own_address:
            role = " (this machine)"
        else:
            role = ""
        up = f"{stats.percent_reachable() * 100:.1f}%" if stats.n + stats.fails else "-"
        if stats.last_fail is None:
            outage = "none"
        else:
            outage = f"{time_since_as_str(stats.last_fail, now)} ago"

        def ms(value):
            return self._coloured(format_ms(value), ping_kind(value))

        name = host.name if host.name_done else "..."
        if self.ctx.redact and host.name:
            name = mask_name(name)
        mac = host.mac if host.mac_done else "..."
        services = services_text(host) if host.ports is not None else "scanning..."
        self.query_one("#facts", Static).update(
            f"[b]{escape(host.ip)}[/b]{role}  {escape(name or '(unknown name)')}\n"
            f"mac {escape((mask_mac(mac) if self.ctx.redact and mac else mac) or 'n/a')}  "
            f"{escape(manufacturer_text(host))}\n"
            f"services {escape(services or 'none found')}\n"
            f"ping {ms(stats.last_ping)}  ave {ms(stats.mean if stats.n else None)}  "
            f"min {ms(stats.min)}  max {ms(stats.max)}  sd {format_ms(stats.sd_sample() if stats.n > 1 else None)}"
            f"  up {up}  last outage {outage}  ({stats.n + stats.fails} pings)"
        )

        # failed pings are drawn as 0, drawing them at the time out would flatten everything else
        self.query_one("#graph", Sparkline).data = [
            0 if isinstance(p, PingFail) else p for p in stats.history
        ] or [0]

        self.query_one("#histogram", Static).update("\n".join(
            f"{label + ('' if label == 'failed' else ' ms'):>11} {count:6}  "
            f"{self._coloured(bar, 'fail' if label == 'failed' else 'fine')}"
            for label, count, bar in histogram_rows(stats, 50)
        ))

    def action_graphs(self):
        self._show("graphs")

    def _open_tool(self, pane: str) -> Log | None:
        """
        Show a tool's output. The tool runs the first time, and again if it is picked while showing.
        :return: the log to write to if the tool should run
        """
        already_showing = self.query_one("#pane", ContentSwitcher).current == pane
        self._show(pane)
        if pane in self._started and not already_showing:
            return None
        self._started.add(pane)
        log = self.query_one(f"#{pane}-log", Log)
        log.clear()
        return log

    def action_port_scan(self):
        log = self._open_tool("port_scan")
        if log is not None:
            log.write_line(f"Scanning all TCP ports on {self.host.ip}...")
            self._port_scan(log)

    @work(exclusive=True, group="port_scan")
    async def _port_scan(self, log: Log):
        bar = self.query_one("#port_scan ProgressBar", ProgressBar)
        bar.update(progress=0)
        last_report = 0.0

        def progress(checked: int, found: list[int]):
            nonlocal last_report
            if time.monotonic() - last_report > 0.25 or checked == 0xffff:
                last_report = time.monotonic()
                bar.update(progress=checked)

        found_before = set(self.host.ports or [])
        found = await probe.full_port_scan(self.host.ip, progress=progress)
        for port in found:
            new = "" if port in found_before else "  (new)"
            log.write_line(f"{port:>5}  {probe.port_name(port)}{new}")
        log.write_line(f"{len(found)} open port{'' if len(found) == 1 else 's'}")
        self.host.ports = found

    def action_traceroute(self):
        log = self._open_tool("traceroute")
        if log is not None:
            self._traceroute(log)

    @work(exclusive=True, group="traceroute")
    async def _traceroute(self, log: Log):
        async for line in probe.traceroute(self.host.ip):
            log.write_line(line)

    def action_copy(self):
        self.app.copy_to_clipboard(self.host.ip)
        self.notify(f"Copied {self.host.ip}")

    def action_open_web(self):
        for port, scheme in self.WEB_PORTS:
            if port in (self.host.ports or []):
                default = {"http": 80, "https": 443}[scheme]
                url = f"{scheme}://{self.host.ip}" + ("" if port == default else f":{port}")
                self.app.open_url(url)
                self.notify(f"Opening {url}")
                return
        self.notify("No web server found on this host", severity="warning")


# ----------------------------------------------------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------------------------------------------------
class PingThingApp(App):
    TITLE = "pingthing"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #summary { height: 1; background: $surface; padding: 0 1; }
    #prompt { height: 1; display: none; background: $panel; }
    #prompt.visible { display: block; }
    #prompt Label { width: auto; padding: 0 1; color: $accent; text-style: bold; }
    #prompt Input { border: none; height: 1; padding: 0; width: 1fr; background: $panel; }
    #keys { height: 1; background: $surface; link-style: none; link-color: $foreground; }
    """

    BINDINGS = [
        Binding("f1,question_mark", "help", "Help"),
        Binding("f3,slash", "search", "Search"),
        Binding("f4,backslash", "filter", "Filter"),
        Binding("f5,space", "pause", "Pause"),
        Binding("f6,greater_than_sign", "sort", "Sort"),
        Binding("f10,q", "quit", "Quit"),
        Binding("escape", "escape", show=False),
    ]

    def __init__(self, monitor: Monitor, view: list[str], bw: bool = False,
                 gateway: str | None = None, own_address: str | None = None, redact: bool = False):
        super().__init__()
        self.monitor = monitor
        self.palette = Palette(bw)
        self.view = view
        self.gateway = gateway
        self.own_address = own_address
        self.redact = redact
        self.paused = False
        self.prompt_mode: str | None = None  # "search" or "filter" while the prompt is open
        self.filter_text = ""
        self.register_theme(CALM_THEME)
        self.register_theme(MONO_THEME)
        self.theme = MONO_THEME.name if bw else CALM_THEME.name

    def compose(self) -> ComposeResult:
        self.table = HostTable(self.view, self.palette)
        yield Static(id="summary")
        yield TableHeader(self.table)
        yield self.table
        with Horizontal(id="prompt"):
            yield Label()
            yield Input()
        yield Static(self._function_keys(), id="keys")

    def on_mount(self):
        self.table.focus()
        self.refresh_view()
        self.set_interval(REDRAW_TIME, self.refresh_view)

    # --- drawing ------------------------------------------------------------------------------------------------------
    def _ui_context(self) -> UIContext:
        return UIContext(self.gateway, self.own_address, time.time(), self.redact)

    def _function_keys(self) -> str:
        parts = []
        for key, label, action in FUNCTION_KEYS:
            parts.append(f"[@click=app.{action}][b]{key}[/b][reverse]{label:<7}[/reverse][/]")
        return "".join(parts)

    def _latency(self, host: Host | None) -> str:
        if host is None or host.stats.last_ping is None:
            return "-"
        ping = host.stats.last_ping
        txt = format_ms(ping) + ("" if isinstance(ping, PingFail) else " ms")
        colour = self.palette.hex(ping_kind(ping))
        return f"[{colour}]{escape(txt)}[/]" if colour else escape(txt)

    def _summary(self, hosts: list[Host], shown: int) -> str:
        down = sum(1 for h in hosts if isinstance(h.stats.last_ping, PingFail))
        m = self.monitor
        parts = [
            f"[b]{escape(str(m.network))}[/b]",
            f"hosts {len(hosts) - down} up, {down} down" + (f" ({shown} shown)" if self.filter_text else ""),
            f"gateway {self._latency(m.hosts.get(self.gateway))}",
        ]
        if m.internet is not None:
            parts.append(f"internet {self._latency(m.internet)}")
        parts.append(f"[dim]{escape(m.pinger.name)}, every {m.interval:g}s[/dim]")
        if self.filter_text:
            parts.append(f"filter: [b]{escape(self.filter_text)}[/b]")
        if self.paused:
            parts.append("[reverse b] PAUSED [/]")
        return "   ".join(parts)

    def refresh_view(self):
        if self.paused:
            return
        ctx = self._ui_context()
        hosts = self.monitor.sorted_hosts()
        shown = [h for h in hosts if matches(h, self.filter_text)] if self.filter_text else hosts
        sort_key = col_config[self.table.sort_column].sort_key
        shown = sorted(shown, key=lambda h: sort_key(h, ctx), reverse=self.table.sort_reverse)
        self.table.show(shown, ctx)
        self.query_one(TableHeader).refresh()
        self.query_one("#summary", Static).update(self._summary(hosts, len(shown)))

    def sort_by(self, column: str):
        if column == self.table.sort_column:
            self.table.sort_reverse = not self.table.sort_reverse
        else:
            self.table.sort_column, self.table.sort_reverse = column, False
        self._redraw()

    def _redraw(self):
        """
        Redraw now, even when paused.
        """
        paused, self.paused = self.paused, False
        self.refresh_view()
        self.paused = paused
        self.query_one("#summary", Static).update(self._summary(self.monitor.sorted_hosts(), len(self.table.hosts)))

    # --- search and filter --------------------------------------------------------------------------------------------
    def _open_prompt(self, mode: str):
        self.prompt_mode = mode
        prompt = self.query_one("#prompt")
        prompt.add_class("visible")
        prompt.query_one(Label).update("Search:" if mode == "search" else "Filter:")
        entry = prompt.query_one(Input)
        entry.value = self.filter_text if mode == "filter" else ""
        entry.focus()

    def _close_prompt(self):
        self.prompt_mode = None
        self.query_one("#prompt").remove_class("visible")
        self.table.focus()

    def _search(self, text: str, start: int):
        hosts = self.table.hosts
        if not text or not hosts:
            return
        for i in range(len(hosts)):
            index = (start + i) % len(hosts)
            if matches(hosts[index], text):
                self.table.select(index)
                return

    @on(Input.Changed)
    def prompt_changed(self, event: Input.Changed):
        if self.prompt_mode == "search":
            self._search(event.value, self.table.cursor)
        elif self.prompt_mode == "filter":
            self.filter_text = event.value
            self._redraw()

    @on(Input.Submitted)
    def prompt_submitted(self, event: Input.Submitted):
        self._close_prompt()

    def action_search(self):
        if self.prompt_mode == "search":
            # F3 again finds the next match
            self._search(self.query_one("#prompt Input", Input).value, self.table.cursor + 1)
        else:
            self._open_prompt("search")

    def action_filter(self):
        self._open_prompt("filter")

    async def action_escape(self):
        """
        Close the search or filter prompt, then clear the filter, then quit.
        """
        mode = self.prompt_mode
        if mode is not None:
            self._close_prompt()
        if mode != "search" and self.filter_text:
            self.filter_text = ""
            self._redraw()
        elif mode is None:
            await self.action_quit()

    # --- other keys ---------------------------------------------------------------------------------------------------
    def action_help(self):
        self.push_screen(HelpScreen())

    def action_pause(self):
        self.paused = not self.paused
        self.query_one("#summary", Static).update(self._summary(self.monitor.sorted_hosts(), len(self.table.hosts)))

    def action_sort(self):
        def chosen(column: str | None):
            if column is not None:
                self.sort_by(column)
        self.push_screen(SortScreen(self.view, self.table.sort_column), chosen)

    @on(TableHeader.Clicked)
    def header_clicked(self, event: TableHeader.Clicked):
        self.sort_by(event.column)

    @on(HostTable.Opened)
    def host_opened(self, event: HostTable.Opened):
        self.push_screen(HostScreen(event.host, self.monitor, self._ui_context(), self.palette))
