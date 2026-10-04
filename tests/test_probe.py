import asyncio

import pytest

from pingthing import probe
from pingthing.stats import PingFail

LINUX_OK = """PING 192.168.0.1 (192.168.0.1) 56(84) bytes of data.
64 bytes from 192.168.0.1: icmp_seq=1 ttl=64 time=3.42 ms

--- 192.168.0.1 ping statistics ---
1 packets transmitted, 1 received, 0% packet loss, time 0ms
"""

MACOS_OK = """PING 192.168.0.1 (192.168.0.1): 56 data bytes
64 bytes from 192.168.0.1: icmp_seq=0 ttl=64 time=12.034 ms
"""

WINDOWS_OK = """
Pinging 192.168.0.69 with 32 bytes of data:
Reply from 192.168.0.69: bytes=32 time=28ms TTL=64
"""

WINDOWS_FAST = "Reply from 127.0.0.1: bytes=32 time<1ms TTL=128"

WINDOWS_GERMAN = "Antwort von 192.168.0.1: Bytes=32 Zeit=7ms TTL=64"

WINDOWS_UNREACHABLE = """
Pinging 192.168.0.250 with 32 bytes of data:
Reply from 192.168.0.130: Destination host unreachable.
"""


@pytest.mark.parametrize("platform, output, expected", [
    ("linux", LINUX_OK, 3.42),
    ("darwin", MACOS_OK, 12.034),
    ("win32", WINDOWS_OK, 28.0),
    ("win32", WINDOWS_FAST, 1.0),
    ("win32", WINDOWS_GERMAN, 7.0),
])
def test_parse_system_ping(platform, output, expected):
    assert probe.parse_system_ping(0, output, 99.0, platform) == expected


def test_parse_system_ping_failures():
    assert probe.parse_system_ping(1, "", 1000.0, "linux") is PingFail.TIMEOUT
    assert probe.parse_system_ping(0, WINDOWS_UNREACHABLE, 5.0, "win32") is PingFail.UNREACHABLE


def test_system_ping_command_timeouts():
    assert probe.system_ping_command("1.2.3.4", 1.5, "win32")[1:5] == ["-n", "1", "-w", "1500"]
    assert probe.system_ping_command("1.2.3.4", 1.5, "darwin")[3:5] == ["-W", "1500"]
    assert probe.system_ping_command("1.2.3.4", 0.2, "linux")[3:5] == ["-W", "1"]


def test_ping_localhost():
    async def run():
        pinger = await probe.choose_pinger()
        try:
            return pinger.name, await pinger.ping_all(["127.0.0.1"], 2)
        finally:
            await pinger.close()

    name, results = asyncio.run(run())
    assert isinstance(results["127.0.0.1"], float), name


def test_system_pinger_localhost():
    async def run():
        return await probe.SystemPinger().ping("127.0.0.1", 2)

    assert isinstance(asyncio.run(run()), float)


def test_port_scan_finds_a_listening_port():
    async def run():
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            return port, await probe.port_open("127.0.0.1", port, 2)

    port, is_open = asyncio.run(run())
    assert is_open, port


async def test_a_refused_port_is_closed_not_unknown():
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    # Windows retries a refused connection for a couple of seconds before giving up
    assert await probe.check_port("127.0.0.1", port, 5) is probe.PortState.CLOSED


async def test_quick_scan_that_learns_nothing_returns_none(monkeypatch):
    async def check_port(ip, port, time_out):
        return probe.PortState.UNKNOWN
    monkeypatch.setattr(probe, "check_port", check_port)
    assert await probe.quick_port_scan("10.0.0.5") is None


def test_never_sends_web_requests_to_printer_ports():
    assert not probe.worth_checking_for_web(9100)
    assert not probe.worth_checking_for_web(9103)
    assert not probe.worth_checking_for_web(22)  # known, and not web
    assert probe.worth_checking_for_web(8080)
    assert probe.worth_checking_for_web(12345)  # unknown, worth asking


def test_parse_http_response():
    page = b"HTTP/1.1 200 OK\r\nServer: x\r\n\r\n<html><head><TITLE>\n  Router &amp; Admin </TITLE>"
    assert probe.parse_http_response(page) == ("200 OK", "Router & Admin")
    assert probe.parse_http_response(b"HTTP/1.0 302 Found\r\n\r\n") == ("302 Found", None)
    assert probe.parse_http_response(b"SSH-2.0-OpenSSH_9.6\r\n") is None


async def test_finds_a_plain_http_page():
    async def serve(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<title>Test page</title>")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        pages = await probe.find_web_pages("127.0.0.1", [port], time_out=2)
    assert pages == [probe.WebPage(port, f"http://127.0.0.1:{port}", "200 OK", "Test page")]


async def test_in_daemon_thread_returns_results_and_raises_errors():
    assert await probe.in_daemon_thread(sum, [1, 2, 3]) == 6
    with pytest.raises(ValueError):
        await probe.in_daemon_thread(int, "not a number")
