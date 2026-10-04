import asyncio
import ipaddress
import math
import time

import pytest

from pingthing import probe
from pingthing.cli import parse_args
from pingthing.monitor import EXPLORE_CHUNK, FIRST_EXPLORE_CHUNK, Monitor, retry_delay
from pingthing.probe import Pinger


def make_monitor(net="10.0.0.0/24"):
    return Monitor(ipaddress.IPv4Network(net), Pinger(1))


def test_add_host_only_once():
    m = make_monitor()
    assert m.add_host("10.0.0.1").ip == "10.0.0.1"
    assert m.add_host("10.0.0.1") is None


def test_exploration_covers_network_then_slows_down():
    m = make_monitor("10.0.0.0/23")  # 510 hosts
    seen = m.next_to_explore() + m.next_to_explore()
    assert len(seen) == 510 and len(set(seen)) == 510
    assert len(m.next_to_explore()) == EXPLORE_CHUNK
    assert FIRST_EXPLORE_CHUNK > EXPLORE_CHUNK


def test_exploration_skips_known_hosts():
    m = make_monitor("10.0.0.0/28")
    m.add_host("10.0.0.1")
    assert "10.0.0.1" not in m.next_to_explore()


def test_retries_back_off_to_15_minutes():
    assert [retry_delay(n) for n in range(1, 8)] == [2, 5, 10, 30, 60, 120, 240]
    assert retry_delay(50) == 15 * 60


async def test_failed_lookup_is_retried_after_a_later_ping(monkeypatch):
    names = iter([None, "printer"])

    async def host_name(ip):
        return next(names)
    monkeypatch.setattr(probe, "host_name", host_name)

    m = make_monitor()
    host = m.add_host("10.0.0.5")
    await m._lookup_name(host)
    assert host.name is None and host.name_done
    assert host.name_retry_at - time.monotonic() == pytest.approx(2, abs=1)

    async with asyncio.TaskGroup() as tg:
        m.retry_lookups(host, tg)  # too soon, nothing happens
    assert host.name is None

    host.name_retry_at = time.monotonic()
    async with asyncio.TaskGroup() as tg:
        m.retry_lookups(host, tg)
    assert host.name == "printer" and host.name_retry_at == math.inf


def test_args():
    args = parse_args(["--range", "192.168.1.7/24", "--time_out", "0.5", "--view", "ip, ping", "--bw"])
    assert args.range == ipaddress.IPv4Network("192.168.1.0/24")
    assert args.time_out == 0.5
    assert args.view == ["ip", "ping"]
    assert args.bw


@pytest.mark.parametrize("argv", [["--range", "nonsense"], ["--view", "ip,bogus"], ["--time_out", "0"]])
def test_bad_args(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)
