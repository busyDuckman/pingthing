# ----------------------------------------------------------------------------------------------------------------------
# Copyright (c) 2020 Warren Creemers
# See LICENSE in root folder for further information.
# ----------------------------------------------------------------------------------------------------------------------
"""
Work out which network we are on, so it doesn't need to be typed in.
"""

import ipaddress
import re
import socket
import subprocess
import sys
from typing import NamedTuple

import psutil

# Networks bigger than this are scanned as the /22 around our address, rather than (eg) 65k addresses of a /16.
SMALLEST_PREFIX = 22
FALLBACK_PREFIX = 24


class LocalNetwork(NamedTuple):
    address: str | None
    network: ipaddress.IPv4Network
    gateway: str | None
    interface: str | None


def primary_ipv4() -> str | None:
    """
    The address this machine uses to reach the outside world.
    Connecting a UDP socket sends nothing, it just makes the OS choose a route.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('192.0.2.1', 9))  # TEST-NET-1, never routed for real
            return s.getsockname()[0]
    except OSError:
        pass

    # no default route, take the first private address we can find
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family == socket.AF_INET and ipaddress.IPv4Address(a.address).is_private \
                    and not ipaddress.IPv4Address(a.address).is_loopback:
                return a.address
    return None


def interface_for(address: str) -> tuple[str | None, str | None]:
    """
    Interface name and netmask for one of our addresses.
    """
    for name, addrs in psutil.net_if_addrs().items():
        for a in addrs:
            if a.family == socket.AF_INET and a.address == address:
                return name, a.netmask
    return None, None


def network_for(address: str, netmask: str | None) -> ipaddress.IPv4Network:
    """
    The network to scan for an address, clamped to a sensible size.
    """
    prefix = FALLBACK_PREFIX
    if netmask:
        try:
            prefix = ipaddress.IPv4Network(f'0.0.0.0/{netmask}').prefixlen
        except ValueError:
            pass
    if prefix > 30:
        # point to point links and VPNs often report a /32; guess the usual home network size
        prefix = FALLBACK_PREFIX
    prefix = max(prefix, SMALLEST_PREFIX)
    return ipaddress.IPv4Network(f'{address}/{prefix}', strict=False)


# ----------------------------------------------------------------------------------------------------------------------
# Default gateway, the OS knows but each one tells you differently.
# ----------------------------------------------------------------------------------------------------------------------
def parse_linux_route(proc_net_route: str) -> str | None:
    """
    Parse /proc/net/route, where addresses are little endian hex.
    """
    for line in proc_net_route.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 3 and fields[1] == '00000000' and fields[2] != '00000000':
            return str(ipaddress.IPv4Address(bytes.fromhex(fields[2])[::-1]))
    return None


def parse_macos_route(route_output: str) -> str | None:
    """
    Parse "route -n get default".
    """
    match = re.search(r'gateway:\s*(\d+\.\d+\.\d+\.\d+)', route_output)
    return match.group(1) if match else None


def parse_windows_route(route_output: str) -> str | None:
    """
    Parse "route print -4 0.0.0.0", picking the lowest metric default route.
    """
    routes = []
    for line in route_output.splitlines():
        fields = line.split()
        if len(fields) == 5 and fields[0] == '0.0.0.0' and fields[1] == '0.0.0.0':
            try:
                ipaddress.IPv4Address(fields[2])
                routes.append((int(fields[4]), fields[2]))
            except ValueError:
                continue  # "On-link" and the like
    return min(routes)[1] if routes else None


def default_gateway(platform: str = sys.platform) -> str | None:
    try:
        if platform.startswith('linux'):
            with open('/proc/net/route') as f:
                return parse_linux_route(f.read())
        if platform == 'darwin':
            cmd, parser = ['route', '-n', 'get', 'default'], parse_macos_route
        elif platform == 'win32':
            cmd, parser = ['route', 'print', '-4', '0.0.0.0'], parse_windows_route
        else:
            return None
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return parser(res.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def detect_network() -> LocalNetwork:
    address = primary_ipv4()
    if address is None:
        return LocalNetwork(None, ipaddress.IPv4Network('192.168.0.0/24'), None, None)
    interface, netmask = interface_for(address)
    return LocalNetwork(address, network_for(address, netmask), default_gateway(), interface)
