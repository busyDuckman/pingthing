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
