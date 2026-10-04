# ----------------------------------------------------------------------------------------------------------------------
# Copyright (c) 2020 Warren Creemers
# See LICENSE in root folder for further information.
# ----------------------------------------------------------------------------------------------------------------------
"""
Running ping statistics per host.
"""

import math
import time
from enum import Enum


class PingFail(Enum):
    """
    Why a ping did not return a round trip time.
    """
    TIMEOUT = "n/a"
    UNREACHABLE = "n/h"
    ERROR = "err"


# A ping result is either a round trip time in milliseconds, or the reason it failed.
PingResult = float | PingFail


class PingStats:
    """
    A running stats class for: count(n), mean, sum and variance.
    Times are in milliseconds.
    """
    def __init__(self):
        self.n: int = 0
        self.mean: float = 0.0
        self.variance: float = 0.0
        self.sum: float = 0.0
        self.min: float | None = None
        self.max: float | None = None
        self.fails: int = 0
        self.last_fail: float | None = None
        self.last_ok: float | None = None
        self.last_ping: PingResult | None = None

    def __str__(self):
        return f"m(S)={self.mean:.1f} ({self.sd_sample():.1f}) b={self.min} w={self.max}"

    def sd_population(self) -> float:
        """
        Classical standard deviation.
        """
        return math.sqrt(self.variance / self.n) if self.n > 0 else 0.0

    def sd_sample(self) -> float:
        """
        Sample standard deviation.
        """
        return math.sqrt(self.variance / (self.n - 1)) if self.n > 1 else 0.0

    def percent_reachable(self) -> float:
        """
        Basically percent uptime, as a fraction 0..1
        """
        n_including_fails = self.n + self.fails
        return self.n / n_including_fails if n_including_fails > 0 else 0.0

    def add(self, ping: PingResult, now: float | None = None):
        """
        Add another value from the data set and update the stats.
        """
        now = time.time() if now is None else now
        self.last_ping = ping
        if isinstance(ping, PingFail):
            self.fails += 1
            self.last_fail = now
            return

        self.last_ok = now
        value = float(ping)
        self.n += 1
        self.sum += value
        m_prev = self.mean
        self.mean += (value - self.mean) / self.n
        self.variance += (value - self.mean) * (value - m_prev)

        if self.n == 1:
            self.min = self.max = value
        else:
            self.min = min(self.min, value)
            self.max = max(self.max, value)
