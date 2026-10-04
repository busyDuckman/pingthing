# ----------------------------------------------------------------------------------------------------------------------
# Copyright (c) 2020 Warren Creemers
# See LICENSE in root folder for further information.
# ----------------------------------------------------------------------------------------------------------------------
"""
Command line entry point.
"""

import argparse
import asyncio
import ipaddress

from pingthing import __version__
from pingthing.columns import DEFAULT_VIEW, col_config
from pingthing.monitor import Monitor
from pingthing.netinfo import detect_network
from pingthing.probe import choose_pinger
from pingthing.ui import PingThingApp


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='pingthing', description='Summary of your local network, htop style.')
    parser.add_argument('--range', type=str, required=False, default=None,
                        help='network range, eg: 192.168.0.0/24 (default: detect the local network)')
    parser.add_argument('--time_out', type=float, required=False, default=1.0,
                        help='time out for ping, in seconds (default: 1)')
    parser.add_argument('--interval', type=float, required=False, default=2.0,
                        help='seconds between pings to each host (default: 2)')
    parser.add_argument('--view', type=str, required=False, default=",".join(DEFAULT_VIEW),
                        help=f'columns to show (default: {",".join(DEFAULT_VIEW)})')
    parser.add_argument('--internet', type=str, required=False, default='1.1.1.1',
                        help="address to ping as a measure of internet latency, 'off' to skip (default: 1.1.1.1)")
    parser.add_argument('--no-ports', dest='port_scan', action='store_false', default=True,
                        help="don't scan hosts for common services")
    parser.add_argument('--screen-shot', dest='screen_shot', action='store_true', default=False,
                        help='hide MAC addresses and host names, for sharing screenshots')
    parser.add_argument('--bw', action='store_true', default=False,
                        help='black/white mode (colour blind safe)')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    args = parser.parse_args(argv)

    if args.range is not None:
        try:
            args.range = ipaddress.IPv4Network(args.range, strict=False)
        except ValueError as e:
            parser.error(f"--range: {e}")

    args.view = [c.strip() for c in args.view.split(",") if c.strip()]
    unknown = [c for c in args.view if c not in col_config]
    if unknown or not args.view:
        parser.error(f"--view: unknown column(s) {', '.join(unknown)}; choose from {', '.join(DEFAULT_VIEW)}")

    if args.internet.lower() in ('', 'off', 'none'):
        args.internet = None

    if args.time_out <= 0 or args.interval <= 0:
        parser.error("--time_out and --interval must be positive")

    return args


async def run(args: argparse.Namespace):
    local = await asyncio.to_thread(detect_network)
    network = args.range or local.network
    pinger = await choose_pinger()

    monitor = Monitor(network, pinger, time_out=args.time_out, interval=args.interval, port_scan=args.port_scan,
                      internet=args.internet)
    app = PingThingApp(monitor, args.view, bw=args.bw, gateway=local.gateway, own_address=local.address,
                       redact=args.screen_shot)

    try:
        async with asyncio.TaskGroup() as tg:
            scanning = tg.create_task(monitor.run())
            await app.run_async()
            scanning.cancel()
    finally:
        await pinger.close()


def main(argv=None):
    args = parse_args(argv)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
