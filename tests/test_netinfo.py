import ipaddress

from pingthing import netinfo

LINUX_ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t0000A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0
eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
"""

MACOS_ROUTE = """   route to: default
destination: default
       mask: default
    gateway: 192.168.1.254
  interface: en0
"""

WINDOWS_ROUTE = """IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0     192.168.0.69    192.168.0.130     60
          0.0.0.0          0.0.0.0         10.8.0.1         10.8.0.5     25
===========================================================================
Persistent Routes:
  None
"""


def test_parse_linux_route():
    assert netinfo.parse_linux_route(LINUX_ROUTE) == "192.168.0.1"


def test_parse_macos_route():
    assert netinfo.parse_macos_route(MACOS_ROUTE) == "192.168.1.254"


def test_parse_windows_route_picks_lowest_metric():
    assert netinfo.parse_windows_route(WINDOWS_ROUTE) == "10.8.0.1"


def test_network_for():
    assert netinfo.network_for("192.168.0.130", "255.255.255.0") == ipaddress.IPv4Network("192.168.0.0/24")
    # too big, clamp
    assert netinfo.network_for("10.1.2.3", "255.0.0.0") == ipaddress.IPv4Network("10.1.0.0/22")
    # VPN style /32, guess a /24
    assert netinfo.network_for("10.1.2.3", "255.255.255.255") == ipaddress.IPv4Network("10.1.2.0/24")
    assert netinfo.network_for("10.1.2.3", None) == ipaddress.IPv4Network("10.1.2.0/24")


def test_detect_network_runs_here():
    local = netinfo.detect_network()
    assert local.network.num_addresses <= 1024
    if local.address:
        assert ipaddress.IPv4Address(local.address) in local.network
