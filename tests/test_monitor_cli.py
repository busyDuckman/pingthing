import ipaddress

import pytest

from pingthing.cli import parse_args
from pingthing.monitor import EXPLORE_CHUNK, FIRST_EXPLORE_CHUNK, Monitor
from pingthing.probe import Pinger
from pingthing.stats import PingFail


def make_monitor(net="10.0.0.0/24"):
    return Monitor(ipaddress.IPv4Network(net), Pinger(1))


def test_new_hosts_are_added_and_known_hosts_record_failures():
    m = make_monitor()
    new = m.update({"10.0.0.1": 3.0, "10.0.0.2": PingFail.TIMEOUT})
    assert [h.ip for h in new] == ["10.0.0.1"]
    assert "10.0.0.2" not in m.hosts
    assert m.hosts["10.0.0.1"].stats.n == 0  # discovery ping isn't a fair sample

    m.update({"10.0.0.1": PingFail.TIMEOUT})
    m.update({"10.0.0.1": 4.0})
    stats = m.hosts["10.0.0.1"].stats
    assert (stats.n, stats.fails, stats.mean) == (1, 1, 4.0)


def test_exploration_covers_network_then_slows_down():
    m = make_monitor("10.0.0.0/23")  # 510 hosts
    seen = m.next_to_explore() + m.next_to_explore()
    assert len(seen) == 510 and len(set(seen)) == 510
    assert len(m.next_to_explore()) == EXPLORE_CHUNK
    assert FIRST_EXPLORE_CHUNK > EXPLORE_CHUNK


def test_exploration_skips_known_hosts():
    m = make_monitor("10.0.0.0/28")
    m.update({"10.0.0.1": 1.0})
    assert "10.0.0.1" not in m.next_to_explore()


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
