import statistics

import pytest

from pingthing.stats import PingFail, PingStats


def test_running_stats_match_batch_stats():
    values = [4.0, 8.0, 15.0, 16.0, 23.0, 42.0]
    s = PingStats()
    for v in values:
        s.add(v)
    assert s.n == len(values)
    assert s.mean == pytest.approx(statistics.mean(values))
    assert s.sd_sample() == pytest.approx(statistics.stdev(values))
    assert s.sd_population() == pytest.approx(statistics.pstdev(values))
    assert (s.min, s.max) == (4.0, 42.0)


def test_failures_count_against_uptime_not_mean():
    s = PingStats()
    s.add(10.0, now=100)
    s.add(PingFail.TIMEOUT, now=102)
    s.add(20.0, now=104)
    s.add(PingFail.UNREACHABLE, now=106)
    assert s.mean == 15.0
    assert s.fails == 2
    assert s.percent_reachable() == 0.5
    assert s.last_ok == 104
    assert s.last_fail == 106
    assert s.last_ping is PingFail.UNREACHABLE
