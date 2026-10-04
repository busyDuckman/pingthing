# Pingthing refresh

This idea had good bones, however I cobbled it together quickly and left it alone after it worked well enough for me.

The goal: **htop for your LAN**, a simple but professional tool that installs with one command and works on the
first try.

Options in brackets are candidates, not decisions.

## Current state

- Single Python script, asciimatics terminal UI, tested on Linux only.
- Uses Ray for parallelism, pinned to 2020 versions.
- Default subnet hardcoded to 192.168.0.0/24.
- Bundled 2020 manufacturer list.

## Phase 1: Install and run everywhere

- Package it with a command, publish to PyPI (pipx, uv, plain pip).
- Replace Ray with something lighter (asyncio, thread pool, keep Ray).
- Ping on Windows, macOS and Linux, ideally without root (icmplib, system ping, TCP probe fallback).
- Auto-detect subnet and gateway (psutil, ifaddr, netifaces fork, per-OS route parsing).
- Keep the manufacturer list current (mac-vendor-lookup, manuf, bundled copy with update script).
- Fix known bugs: outage times past an hour, error states, units, black and white mode, timeout option, hostname
  caching, CPU use, port labels.
- Unit tests, and CI on all three OSes (GitHub Actions).

## Phase 2: htop-style interface

- Move the UI to Textual, replacing asciimatics.
- Summary header: hosts up/down, gateway and internet latency.
- Sortable, scrollable table that handles resizing.
- Select a row with the keyboard or mouse to open a detail window for that host: latency sparkline, histogram, full port
  scan, traceroute and other common actions.
- Search (F3) and filter (F4) by IP, name, MAC or manufacturer.
- Pause, help screen, function key bar.
- Calmer colour scheme; working black and white theme.

## Phase 3: Discovery and release

- A workplace mode, as the default scanning can trip corporate network security monitoring.
- Find hosts that ignore ping, via ARP (OS ARP table, Scapy scan, getmac).
- Better device names (mDNS, NetBIOS, SSDP).
- Port scanning opt-in.
- JSON/CSV export.
- Optionally remember known devices between runs (SQLite, JSON file).
- Demo GIF in the README (vhs, asciinema).
- Drop the "alpha" label and tag a release.

## Phase 4: Web dashboard

- Serve a live dashboard page for running on an always-on box (Pi, NAS, home server).
- Web server (aiohttp, Starlette, Flask, built-in http.server).
- Live updates (server-sent events, WebSockets, polling).
- Page (plain HTML/JS, htmx or Alpine, a full framework).
- Secure by default: local only unless chosen, token required for LAN access.
- History over hours or days (SQLite, in memory only).
- Run as a service (systemd, Docker).
