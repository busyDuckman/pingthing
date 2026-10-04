# ----------------------------------------------------------------------------------------------------------------------
# Copyright (c) 2020 Warren Creemers
# See LICENSE in root folder for further information.
# ----------------------------------------------------------------------------------------------------------------------
"""
Keeps the table of hosts up to date. The UI only reads from it.
"""

import asyncio
import ipaddress
import math
import time
from dataclasses import dataclass, field

from pingthing import probe
from pingthing.stats import PingFail, PingStats
from pingthing.vendors import MACInfo, VendorLookup

# How many not yet seen addresses to try each interval, so a /24 is re-explored about every 16 seconds.
# The first pass covers everything quickly.
FIRST_EXPLORE_CHUNK = 256
EXPLORE_CHUNK = 32

GOLDEN_RATIO = 0.6180339887

# Limits on slower background lookups
MAX_PORT_SCANS = 2
MAX_MAC_SCANS = 4
MAX_NAME_LOOKUPS = 8

# A name or MAC lookup that finds nothing is retried after the host next answers a ping. Quickly at first, as
# the first try often races the host's ARP entry, then backing off so a host that will never have a name settles
# at one try every 15 minutes.
RETRY_SOON = (2.0, 5.0, 10.0)
RETRY_LATER = 30.0  # doubling from here
RETRY_MAX = 15 * 60.0


def retry_delay(attempts: int) -> float:
    """
    Seconds to wait before the next try, after this many failed attempts.
    """
    if attempts <= len(RETRY_SOON):
        return RETRY_SOON[attempts - 1]
    return min(RETRY_MAX, RETRY_LATER * 2 ** (attempts - len(RETRY_SOON) - 1))


# A port scan that learned nothing is retried more conservatively, it's 50 connections each time.
PORT_RETRY_FIRST = 10.0  # doubling from here


def port_retry_delay(attempts: int) -> float:
    return min(RETRY_MAX, PORT_RETRY_FIRST * 2 ** (attempts - 1))


@dataclass
class Host:
    ip: str
    stats: PingStats = field(default_factory=PingStats)
    name: str | None = None
    name_done: bool = False
    mac: str | None = None
    mac_done: bool = False
    vendor: MACInfo | None = None
    ports: list[int] | None = None  # None until scanned
    # when to retry a lookup that found nothing (time.monotonic), never until one has failed
    name_attempts: int = 0
    name_retry_at: float = math.inf
    mac_attempts: int = 0
    mac_retry_at: float = math.inf
    ports_attempts: int = 0
    ports_retry_at: float = math.inf

    @property
    def sort_key(self):
        return ipaddress.IPv4Address(self.ip)


class Monitor:
    def __init__(self, network: ipaddress.IPv4Network, pinger: probe.Pinger, time_out: float = 1.0,
                 interval: float = 2.0, port_scan: bool = True, internet: str | None = None):
        """
        :param internet: an address outside the network to ping, as a measure of internet latency
        """
        self.network = network
        self.pinger = pinger
        self.time_out = time_out
        self.interval = interval
        self.port_scan = port_scan
        self.hosts: dict[str, Host] = {}
        self.internet = Host(internet) if internet else None
        self.vendors = VendorLookup()

        hosts = list(network.hosts()) or [network.network_address]
        self._unexplored = [str(ip) for ip in hosts]
        self._explore_pos = 0
        self._first_pass = True
        self._tasks: asyncio.TaskGroup | None = None  # while running
        self._port_limit = asyncio.Semaphore(MAX_PORT_SCANS)
        self._mac_limit = asyncio.Semaphore(MAX_MAC_SCANS)
        self._name_limit = asyncio.Semaphore(MAX_NAME_LOOKUPS)

    def sorted_hosts(self) -> list[Host]:
        return sorted(self.hosts.values(), key=lambda h: h.sort_key)

    def next_to_explore(self) -> list[str]:
        """
        The next chunk of the network we haven't heard from. The first pass covers everything quickly.
        """
        chunk_size = FIRST_EXPLORE_CHUNK if self._first_pass else EXPLORE_CHUNK
        chunk = self._unexplored[self._explore_pos:self._explore_pos + chunk_size]
        self._explore_pos += chunk_size
        if self._explore_pos >= len(self._unexplored):
            self._explore_pos = 0
            self._first_pass = False
        return [ip for ip in chunk if ip not in self.hosts]

    def add_host(self, ip: str) -> Host | None:
        """
        :return: the new host, or None if we already knew it
        """
        if ip in self.hosts:
            return None
        host = self.hosts[ip] = Host(ip)
        return host

    async def run(self):
        """
        Explore the network a chunk at a time, and keep pinging every host found.

        Pings are spread out rather than sent in bursts. Each host is pinged on its own timer, so the table
        updates a few rows at a time instead of all at once. It's kinder to the network too, on wifi a burst
        of pings (and the ARP broadcasts for empty addresses) delays the replies and inflates the times.
        """
        async with asyncio.TaskGroup() as tg:
            self._tasks = tg
            if self.internet is not None:
                tg.create_task(self._watch(self.internet, tg))
            while True:
                chunk = self.next_to_explore()
                for ip in chunk:
                    tg.create_task(self._explore(ip, tg))
                    await asyncio.sleep(self.interval / len(chunk))
                if not chunk:
                    await asyncio.sleep(self.interval)

    async def _explore(self, ip: str, tg: asyncio.TaskGroup):
        if isinstance(await self.pinger.ping(ip, self.time_out), PingFail):
            return
        host = self.add_host(ip)
        if host is None:
            return
        tg.create_task(self._watch(host, tg))
        tg.create_task(self._lookup_name(host))
        tg.create_task(self._lookup_mac(host))
        if self.port_scan:
            tg.create_task(self._scan_ports(host))

    async def _watch(self, host: Host, tg: asyncio.TaskGroup):
        """
        Ping one host every interval. The discovery ping isn't counted, it's not a fair sample.
        A reply is also the cue to retry any lookups that found nothing.

        Each host gets its own slot in the interval. Slots step by the golden ratio, which keeps them evenly
        spread however many hosts turn up, so a similar number of rows change on each redraw.
        """
        slot = ((len(self.hosts) * GOLDEN_RATIO) % 1.0) * self.interval
        while True:
            since_slot = (time.monotonic() - slot) % self.interval
            await asyncio.sleep(self.interval - since_slot)
            ping = await self.pinger.ping(host.ip, self.time_out)
            host.stats.add(ping)
            if not isinstance(ping, PingFail):
                self.retry_lookups(host, tg)

    def retry_lookups(self, host: Host, tg: asyncio.TaskGroup):
        now = time.monotonic()
        if now >= host.name_retry_at:
            tg.create_task(self._lookup_name(host))
        if now >= host.mac_retry_at:
            tg.create_task(self._lookup_mac(host))
        if self.port_scan and now >= host.ports_retry_at:
            tg.create_task(self._scan_ports(host))

    def rescan(self) -> bool:
        """
        Look up every host's name, MAC address and ports again, and sweep the network for new hosts.
        Ping stats are kept, and so are earlier results if a lookup now finds nothing.

        :return: False if the monitor isn't running yet
        """
        if self._tasks is None:
            return False
        self._explore_pos, self._first_pass = 0, True
        for host in self.hosts.values():
            host.name_attempts = host.mac_attempts = host.ports_attempts = 0
            self._tasks.create_task(self._lookup_name(host))
            self._tasks.create_task(self._lookup_mac(host))
            if self.port_scan:
                self._tasks.create_task(self._scan_ports(host))
        return True

    async def _lookup_name(self, host: Host):
        host.name_retry_at = math.inf  # not while this one runs
        async with self._name_limit:
            name = await probe.host_name(host.ip)
        host.name_attempts += 1
        if name is None:
            host.name_retry_at = time.monotonic() + retry_delay(host.name_attempts)
        else:
            host.name = name
        host.name_done = True

    async def _lookup_mac(self, host: Host):
        host.mac_retry_at = math.inf  # not while this one runs
        async with self._mac_limit:
            mac = await probe.mac_scan(host.ip)
            # the first lookup loads the vendor list, which takes a moment
            vendor = await asyncio.to_thread(self.vendors.lookup, mac) if mac else None
        host.mac_attempts += 1
        if mac is None:
            host.mac_retry_at = time.monotonic() + retry_delay(host.mac_attempts)
        else:
            host.mac, host.vendor = mac, vendor
        host.mac_done = True

    async def _scan_ports(self, host: Host):
        host.ports_retry_at = math.inf  # not while this one runs
        async with self._port_limit:
            ports = await probe.quick_port_scan(host.ip)
        host.ports_attempts += 1
        if ports is None:
            host.ports_retry_at = time.monotonic() + port_retry_delay(host.ports_attempts)
            if host.ports is None:
                host.ports = []  # show nothing found, rather than scanning, until a retry knows better
        else:
            host.ports = ports
