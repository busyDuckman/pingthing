# ----------------------------------------------------------------------------------------------------------------------
# Copyright (c) 2020 Warren Creemers
# See LICENSE in root folder for further information.
# ----------------------------------------------------------------------------------------------------------------------
"""
Keeps the table of hosts up to date. The UI only reads from it.
"""

import asyncio
import ipaddress
import time
from dataclasses import dataclass, field

from pingthing import probe
from pingthing.stats import PingFail, PingStats
from pingthing.vendors import MACInfo, VendorLookup

# How many not yet seen addresses to try each sweep, so a /24 is re-explored about every 16 seconds.
FIRST_EXPLORE_CHUNK = 256
EXPLORE_CHUNK = 32

# Limits on slower background lookups
MAX_PORT_SCANS = 2
MAX_MAC_SCANS = 4
MAX_NAME_LOOKUPS = 8


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

    @property
    def sort_key(self):
        return ipaddress.IPv4Address(self.ip)


class Monitor:
    def __init__(self, network: ipaddress.IPv4Network, pinger: probe.Pinger, time_out: float = 1.0,
                 interval: float = 2.0, port_scan: bool = True):
        self.network = network
        self.pinger = pinger
        self.time_out = time_out
        self.interval = interval
        self.port_scan = port_scan
        self.hosts: dict[str, Host] = {}
        self.sweeps = 0
        self.vendors = VendorLookup()

        hosts = list(network.hosts()) or [network.network_address]
        self._unexplored = [str(ip) for ip in hosts]
        self._explore_pos = 0
        self._first_pass = True
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

    def update(self, results: dict[str, probe.PingResult], now: float | None = None) -> list[Host]:
        """
        Record a sweep. Known hosts that didn't answer count as a failure.
        A new host's first reply isn't counted, it was timed during exploration so it's not a fair sample.
        :return: newly found hosts
        """
        new_hosts = []
        for ip, ping in results.items():
            if ip in self.hosts:
                self.hosts[ip].stats.add(ping, now)
            elif not isinstance(ping, PingFail):
                host = self.hosts[ip] = Host(ip)
                new_hosts.append(host)
        self.sweeps += 1
        return new_hosts

    async def run(self):
        async with asyncio.TaskGroup() as tg:
            while True:
                started = time.monotonic()

                # Time known hosts first. Exploring sends a burst of pings to empty addresses, and the ARP
                # broadcasts for them (slow on wifi) would delay the replies and inflate the times.
                results = await self.pinger.ping_all(list(self.hosts.keys()), self.time_out)
                results |= await self.pinger.ping_all(self.next_to_explore(), self.time_out)

                for host in self.update(results):
                    tg.create_task(self._lookup_name(host))
                    tg.create_task(self._lookup_mac(host))
                    if self.port_scan:
                        tg.create_task(self._scan_ports(host))
                await asyncio.sleep(max(0.0, self.interval - (time.monotonic() - started)))

    async def _lookup_name(self, host: Host):
        async with self._name_limit:
            host.name = await probe.host_name(host.ip)
            host.name_done = True

    async def _lookup_mac(self, host: Host):
        async with self._mac_limit:
            host.mac = await probe.mac_scan(host.ip)
            # the first lookup loads the vendor list, which takes a moment
            host.vendor = await asyncio.to_thread(self.vendors.lookup, host.mac) if host.mac else None
            host.mac_done = True

    async def _scan_ports(self, host: Host):
        async with self._port_limit:
            host.ports = await probe.quick_port_scan(host.ip)
