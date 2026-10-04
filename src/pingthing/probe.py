# ----------------------------------------------------------------------------------------------------------------------
# Copyright (c) 2020 Warren Creemers
# See LICENSE in root folder for further information.
# ----------------------------------------------------------------------------------------------------------------------
"""
Network probes: ping, port scan and MAC lookup.

Everything here is async and non-blocking, so hundreds of probes can be in flight at once on a single thread.
"""

import asyncio
import html
import re
import shutil
import socket
import ssl
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from enum import Enum
from typing import NamedTuple

import icmplib
from getmac import get_mac_address

from pingthing.stats import PingFail, PingResult


# ----------------------------------------------------------------------------------------------------------------------
# Ping
# ----------------------------------------------------------------------------------------------------------------------
class Pinger:
    """
    Base class, a ping engine that limits how many pings are in flight at once.
    """
    name = "none"

    def __init__(self, max_in_flight: int):
        self._limit = asyncio.Semaphore(max_in_flight)

    async def ping(self, ip: str, time_out: float) -> PingResult:
        async with self._limit:
            try:
                return await self._ping(ip, time_out)
            except (OSError, icmplib.ICMPLibError):
                return PingFail.ERROR

    async def _ping(self, ip: str, time_out: float) -> PingResult:
        raise NotImplementedError

    async def ping_all(self, addresses: list[str], time_out: float) -> dict[str, PingResult]:
        results = await asyncio.gather(*[self.ping(ip, time_out) for ip in addresses])
        return dict(zip(addresses, results))

    async def close(self):
        pass


class IcmpPinger(Pinger):
    """
    Native ping via icmplib.
    Unprivileged sockets work on Windows, macOS and most current Linux distros.

    All pings share one socket, and replies are matched by sequence number. A socket per ping is much slower,
    on Windows every socket gets a copy of every reply, so a /24 sweep meant parsing ~65k packets and the
    round trip times were inflated by the queueing.
    """
    name = "icmp"

    def __init__(self, privileged: bool):
        super().__init__(max_in_flight=1024)
        self.privileged = privileged
        if privileged:
            self.name = "icmp (raw)"
        # raises SocketPermissionError if this kind of socket isn't allowed
        self._sock = icmplib.AsyncSocket(icmplib.ICMPv4Socket(privileged=privileged))
        self._id = icmplib.utils.unique_identifier()
        self._sequence = 0
        self._pending: dict[tuple[int, int], asyncio.Future] = {}
        self._receiver: asyncio.Task | None = None

    async def _receive(self):
        while True:
            try:
                reply = await self._sock.receive(None, timeout=60)
            except icmplib.TimeoutExceeded:
                continue
            except icmplib.ICMPLibError:
                await asyncio.sleep(0.1)  # eg: a closed socket, don't spin
                continue
            waiting = self._pending.pop((reply.id, reply.sequence), None)
            if waiting is not None and not waiting.done():
                waiting.set_result(reply)

    async def _ping(self, ip: str, time_out: float) -> PingResult:
        if self._receiver is None:
            self._receiver = asyncio.create_task(self._receive())

        self._sequence = (self._sequence + 1) % 0x10000
        request = icmplib.ICMPRequest(ip, id=self._id, sequence=self._sequence)
        self._sock.send(request)
        # on Linux the kernel picks the id, so read it back after sending
        key = (request.id, request.sequence)
        waiting = self._pending[key] = asyncio.get_running_loop().create_future()
        try:
            reply = await asyncio.wait_for(waiting, timeout=time_out)
        except TimeoutError:
            return PingFail.TIMEOUT
        finally:
            self._pending.pop(key, None)

        try:
            reply.raise_for_status()
        except icmplib.DestinationUnreachable:
            return PingFail.UNREACHABLE
        except icmplib.ICMPError:
            return PingFail.ERROR
        return (reply.time - request.time) * 1000

    async def close(self):
        if self._receiver is not None:
            self._receiver.cancel()
        self._sock.close()


class SystemPinger(Pinger):
    """
    Fallback that runs the OS ping command, for systems where ICMP sockets are not allowed.
    Process creation is heavier, so fewer run at once.
    """
    name = "system ping"

    def __init__(self):
        super().__init__(max_in_flight=64)

    async def _ping(self, ip: str, time_out: float) -> PingResult:
        start = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *system_ping_command(ip, time_out),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=time_out + 2)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return PingFail.TIMEOUT
        elapsed_ms = (time.perf_counter() - start) * 1000
        return parse_system_ping(proc.returncode, out.decode(errors='replace'), elapsed_ms)


def system_ping_command(ip: str, time_out: float, platform: str = sys.platform) -> list[str]:
    if platform == 'win32':
        return ['ping', '-n', '1', '-w', str(int(time_out * 1000)), ip]
    if platform == 'darwin':
        # macOS takes -W in milliseconds
        return ['ping', '-c', '1', '-W', str(int(time_out * 1000)), ip]
    # Linux (iputils and busybox) take -W in whole seconds
    return ['ping', '-c', '1', '-W', str(max(1, round(time_out))), ip]


# Matches "time=1.23 ms", "time<1ms", "Zeit=4ms" and so on; the units are the same in every locale.
_rtt_regex = re.compile(r'[=<]\s*(\d+(?:[.,]\d+)?)\s*ms', flags=re.IGNORECASE)
# Windows reports a successful reply with a TTL; "Destination host unreachable" has none.
_ttl_regex = re.compile(r'TTL=\d+', flags=re.IGNORECASE)


def parse_system_ping(return_code: int, output: str, elapsed_ms: float, platform: str = sys.platform) -> PingResult:
    """
    Turn the output of the ping command into a round trip time.
    """
    if return_code != 0:
        return PingFail.TIMEOUT

    if 'unreachable' in output.lower() or (platform == 'win32' and not _ttl_regex.search(output)):
        # Windows returns 0 for "Destination host unreachable"
        return PingFail.UNREACHABLE

    match = _rtt_regex.search(output)
    if match is None:
        # Replied, but the output is in a format we don't know; wall time is a fair upper bound.
        return elapsed_ms
    return float(match.group(1).replace(',', '.'))


async def choose_pinger() -> Pinger:
    """
    Pick the best ping engine that works on this machine, without needing root.
    """
    for privileged in (False, True):
        try:
            pinger = IcmpPinger(privileged)
        except (icmplib.ICMPLibError, OSError):
            continue
        if not isinstance(await pinger.ping('127.0.0.1', 1), PingFail):
            return pinger
        await pinger.close()
    return SystemPinger()


# ----------------------------------------------------------------------------------------------------------------------
# Port Scan
# ----------------------------------------------------------------------------------------------------------------------
tcp_ports_we_care_about = {
       20: 'FTP-data',     21: 'FTP',          22: 'SSH',          23: 'Telnet',       25: 'SMTP',
       53: 'DNS',          80: 'HTTP',        110: 'POP3',        119: 'NNTP',        135: 'EPMAP',
      139: 'NetBIOS',     143: 'IMAP',        177: 'XDMCP',       194: 'IRC',         389: 'LDAP',
      443: 'HTTPS',       445: 'SMB',         548: 'AFP',         554: 'RTSP',        631: 'IPP',
     1119: 'BattleNET',  1220: 'QTSS',       1234: 'VLC',        1433: 'MSSQL',      1755: 'MMS',
     1883: 'MQTT',       1935: 'RTMP',       2375: 'Docker',     2376: 'DockerSSL',  2377: 'DockerSwrm',
     3306: 'MySQL',      3389: 'RDP',        5000: 'UPnP',       5432: 'Postgres',   5900: 'VNC',
     5938: 'TeamViewer', 5984: 'CouchDB',    6000: 'X11',        7070: 'RTSP',       8080: 'HTTP-alt',
     8200: 'GoToMyPC',   8443: 'HTTPS-alt',  9001: 'HSQLDB',     9100: 'Printer',    9150: 'Tor',
     9418: 'git',       27036: 'Steam-Stream', 32400: 'Plex',  32764: 'Router-Backdoor',
}


def port_name(port: int) -> str:
    return tcp_ports_we_care_about.get(port, str(port))


class PortState(Enum):
    OPEN = "open"
    CLOSED = "closed"    # the host refused the connection, so we know
    UNKNOWN = "unknown"  # timed out or errored: a firewall dropping it, the host asleep, or a problem our end


async def check_port(ip: str, port: int, time_out: float) -> PortState:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=time_out)
    except ConnectionRefusedError:
        return PortState.CLOSED
    except (OSError, TimeoutError):
        return PortState.UNKNOWN
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return PortState.OPEN


async def port_open(ip: str, port: int, time_out: float) -> bool:
    return await check_port(ip, port, time_out) is PortState.OPEN


async def quick_port_scan(ip: str, time_out: float = 2) -> list[int] | None:
    """
    Check the common ports all at once, rather than waiting on 50 timeouts in sequence.

    :return: the open ports, or None if the scan learned nothing (no port was open, and none were refused)
    """
    ports = list(tcp_ports_we_care_about.keys())
    states = await asyncio.gather(*[check_port(ip, p, time_out) for p in ports])
    if all(state is PortState.UNKNOWN for state in states):
        return None
    return [p for p, state in zip(ports, states) if state is PortState.OPEN]


async def full_port_scan(ip: str, time_out: float = 0.5, workers: int = 1024,
                         progress: Callable[[int, list[int]], None] | None = None) -> list[int]:
    """
    Check every TCP port, a pool of workers at a time.

    :param progress: called with (ports checked, open ports so far) as the scan goes
    """
    ports = iter(range(1, 0x10000))
    found = []
    checked = 0

    async def worker():
        nonlocal checked
        for port in ports:
            if await port_open(ip, port, time_out):
                found.append(port)
                found.sort()
            checked += 1
            if progress is not None:
                progress(checked, found)

    async with asyncio.TaskGroup() as tg:
        for _ in range(workers):
            tg.create_task(worker())
    return found


# ----------------------------------------------------------------------------------------------------------------------
# Web pages
# ----------------------------------------------------------------------------------------------------------------------
# Common ports that serve web pages. Other common ports are known not to, so aren't asked.
WEB_PORTS = {80, 443, 631, 5000, 8080, 8443, 32400}

# Never send anything to these: raw printer ports print whatever arrives, so a web request is a page of garbage.
NEVER_PROBE = {515, *range(9100, 9110)}


class WebPage(NamedTuple):
    port: int
    url: str
    status: str  # eg: "200 OK"
    title: str | None


def worth_checking_for_web(port: int) -> bool:
    if port in NEVER_PROBE:
        return False
    return port in WEB_PORTS or port not in tcp_ports_we_care_about


_title_regex = re.compile(rb'<title[^>]*>(.*?)</title>', flags=re.IGNORECASE | re.DOTALL)


def parse_http_response(data: bytes) -> tuple[str, str | None] | None:
    """
    :return: (status, page title) from the start of an HTTP response, or None if it isn't one
    """
    if not data.startswith(b"HTTP/"):
        return None
    status = data.split(b"\r\n", 1)[0].decode(errors='replace').split(" ", 1)[1:]
    title = _title_regex.search(data)
    if title is not None:
        title = " ".join(html.unescape(title.group(1).decode(errors='replace')).split())[:80] or None
    return (status[0].strip() if status else "?"), title


async def _http_get(ip: str, port: int, scheme: str, time_out: float) -> WebPage | None:
    context = None
    if scheme == "https":
        # devices on a LAN nearly all use self signed certificates, we only want to know a page is there
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    data = b""
    try:
        async with asyncio.timeout(time_out):
            reader, writer = await asyncio.open_connection(ip, port, ssl=context)
            try:
                writer.write(f"GET / HTTP/1.1\r\nHost: {ip}\r\nUser-Agent: pingthing\r\n"
                             f"Connection: close\r\n\r\n".encode())
                await writer.drain()
                while len(data) < 16384 and (chunk := await reader.read(4096)):
                    data += chunk
            finally:
                writer.close()
    except (OSError, TimeoutError, ssl.SSLError):
        pass  # a partial response is still enough to recognise
    response = parse_http_response(data)
    if response is None:
        return None
    default_port = {"http": 80, "https": 443}[scheme]
    url = f"{scheme}://{ip}" + ("" if port == default_port else f":{port}")
    return WebPage(port, url, *response)


async def web_page(ip: str, port: int, time_out: float = 3) -> WebPage | None:
    """
    The web page on an open port, if there is one.
    HTTPS is tried first, many HTTPS servers answer a plain request with an HTTP error, which would look like a page.
    """
    for scheme in ("https", "http"):
        page = await _http_get(ip, port, scheme, time_out)
        if page is not None:
            return page
    return None


async def find_web_pages(ip: str, ports: list[int], time_out: float = 3) -> list[WebPage]:
    candidates = [p for p in ports if worth_checking_for_web(p)]
    pages = await asyncio.gather(*[web_page(ip, p, time_out) for p in candidates])
    return [page for page in pages if page is not None]


# ----------------------------------------------------------------------------------------------------------------------
# Traceroute
# ----------------------------------------------------------------------------------------------------------------------
def traceroute_command(ip: str, platform: str = sys.platform) -> list[str] | None:
    """
    :return: the OS command to trace the route to ip, or None if there isn't one installed
    """
    if platform == 'win32':
        return ['tracert', '-d', '-w', '1000', ip]
    if shutil.which('traceroute'):
        return ['traceroute', '-n', '-w', '1', ip]
    if shutil.which('tracepath'):
        # many Linux distros ship tracepath but not traceroute
        return ['tracepath', '-n', ip]
    return None


async def traceroute(ip: str) -> AsyncIterator[str]:
    """
    Run the system traceroute, yielding its output a line at a time.
    """
    command = traceroute_command(ip)
    if command is None:
        yield "traceroute is not installed"
        return
    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        async for line in proc.stdout:
            line = line.decode(errors='replace').rstrip()
            if line:
                yield line
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


# ----------------------------------------------------------------------------------------------------------------------
# MAC address and host name
# ----------------------------------------------------------------------------------------------------------------------
async def in_daemon_thread(func: Callable, *args):
    """
    Like asyncio.to_thread, but the thread doesn't hold up exit.

    Lookups like reverse DNS block for seconds and can't be cancelled. asyncio.run waits for its worker threads
    before returning, so quitting would wait on lookups nobody needs any more.
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def settle(result, error):
        if future.done():
            return  # cancelled while we were busy
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def run():
        try:
            outcome = (func(*args), None)
        except Exception as e:
            outcome = (None, e)
        try:
            loop.call_soon_threadsafe(settle, *outcome)
        except RuntimeError:
            pass  # the loop has closed, we're exiting

    threading.Thread(target=run, name=f"pingthing-{func.__name__}", daemon=True).start()
    return await future


def _mac_scan(ip: str) -> str | None:
    try:
        mac = get_mac_address(ip=ip, network_request=True)
    except Exception:
        return None
    return mac.upper().strip() if mac else None


async def mac_scan(ip: str) -> str | None:
    """
    MAC address from the OS ARP table; None if unknown (eg: our own address, or a host beyond a router).
    getmac blocks, so it runs on a worker thread.
    """
    return await in_daemon_thread(_mac_scan, ip)


async def host_name(ip: str) -> str | None:
    try:
        name, _ = await in_daemon_thread(socket.getnameinfo, (ip, 0), 0)
    except OSError:
        return None
    return None if name == ip else name
