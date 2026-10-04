# IoT device interfacing

**Status: brainstorming possible future features.** Nothing here is planned or committed to. These are ideas
to come back to, not designs.

## The idea

Many devices pingthing finds speak a simple, well known protocol. When the inspect view recognises one, it
could offer an extra key, **`i` for interface**, that opens a small panel to see what the device is doing,
and maybe poke it.

Devices are recognised from things pingthing already gathers (open ports, MAC manufacturer, name), plus
whatever the device announces about itself.

## Candidates

| Device / protocol | How we'd spot it | What `i` could do |
|---|---|---|
| Tuya smart plugs, bulbs, switches | Tuya MAC, port 6668, UDP 6667 broadcasts | Show device ID and product. On/off, if the user supplies the device key. |
| Google Cast (Chromecast, Nest speakers) | ports 8008/8009, mDNS `_googlecast` | Show the device name and what's playing. Pause, volume. |
| MQTT broker | port 1883 | Watch live topics and messages. Publish a message. |
| Tasmota / Shelly / ESPHome | web UI, ports 80/6053 | Show status. Toggle a relay. |
| WLED light strips | port 80, `/json` | On/off, brightness, preset. |
| Philips Hue bridge | Hue MAC, port 80/443 | List lights. On/off. |
| Sonos | port 1400 | What's playing, volume. |
| Network printers | port 631 (IPP) | Status, ink and paper levels. |

## Ground rules (if it ever happens)

- Look first, control second: start with read-only views.
- Only show `i` when a device is actually recognised.
- Keep it trivial. pingthing is a network monitor, not a home automation app.
