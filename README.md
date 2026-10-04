# Pingthing

Summary of your local network (htop style).  
In Alpha release ATM.

![Screenshot](ping_thing_redacted.png "Screenshot")

This utility was created because I needed to inspect my local network, and most of the tools to do so were 
inadequate or overly cumbersome.

#### Design goals:  
  - No configuration files and a simple install.
  - At a glance view of current, best, worst, average ping.
  - Reliably just work.
  - Port scan only the 1% of ports 99% of people care about.
  - Decide what information is useful, not "shop for stuff to show".

#### Installation
Needs Python 3.11 or newer. Works on Linux, macOS and Windows, and does not need root / admin.

    # with uv
    uv tool install pingthing

    # or with pipx
    pipx install pingthing

    # or the latest from GitHub
    uv tool install git+https://github.com/busyDuckman/pingthing

Then run `pingthing`. Press F1 for help, q to quit.

#### Usage
It finds your local network by itself, or you can give it one.

    pingthing [--range RANGE] [--time_out SECONDS] [--interval SECONDS] [--view COLUMNS] [--internet ADDRESS]
              [--no-ports] [--bw]

      --range RANGE         network range, eg: 192.168.0.0/24 (default: detect the local network)
      --time_out SECONDS    time out for ping (default: 1)
      --interval SECONDS    seconds between pings to each host (default: 2)
      --view COLUMNS        columns to show, defaults to:
                            flag,ip,ping,mean,best,worst,sd,up-time,last-outage,name,services,mac,manufacturer
      --internet ADDRESS    address to ping as a measure of internet latency, 'off' to skip (default: 1.1.1.1)
      --no-ports            don't scan hosts for common services
      --bw                  black/white mode (colour blind safe)

Ping times are in milliseconds. In the first column, G marks your gateway and * marks this machine.

Keys work like htop: F3 search, F4 filter, F5 pause, F6 sort (or click a column heading), F10 quit.
Select a host and press Enter (or click it) for its latency graph and histogram, a full port scan and traceroute.

Ping times are accurate to about 1ms as they are measured in Python rather than by the OS. 

#### Development
Uses [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just).

    just run      # run from source
    just check    # lint and test
    just --list   # everything else

#### Licence 
Licenced under the MIT License, see LICENSE for details.


#### Consciences Cognizance, Cataloging Contributions:

  - MAC data from https://standards-oui.ieee.org/oui/oui.csv  
  - [Textual](https://github.com/Textualize/textual) [MIT]  
  - [icmplib](https://github.com/ValentinBELYN/icmplib) [LGPL-3.0]  
  - [psutil](https://github.com/giampaolo/psutil) [BSD-3-Clause]  
  - [get-mac](https://github.com/GhostofGoes/getmac) [MIT]  
