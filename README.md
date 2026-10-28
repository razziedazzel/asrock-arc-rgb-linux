# asrock-arc-rgb-linux

Control the RGB lighting on **ASRock Intel Arc** graphics cards from Linux — no Windows,
no Polychrome Sync, no extra drivers. Includes a command-line tool, a GTK 4 app, and an
optional service that re-applies your lighting at boot and after resume.

> Unofficial project. Not affiliated with or endorsed by ASRock or Intel.
> Use at your own risk.

## Supported cards

| Card | PCI subsystem | Status |
|---|---|---|
| ASRock Arc B580 Steel Legend | `1849:6020` | Tested |
| ASRock Arc A770 (two variants) | `1849:6001`, `1849:6010` | Same controller per Polychrome; **untested** |

Check yours with `lspci -nnv -d 8086: | grep -i subsystem`.

## Requirements

- The `xe` kernel driver (it exposes the card's own I2C adapter; recent kernels).
- Python 3 (standard library only for the CLI).
- For the app: GTK 4 and libadwaita ≥ 1.6 with PyGObject
  (Fedora: `python3-gobject gtk4 libadwaita`; Debian/Ubuntu: `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`).

## Install

```sh
git clone https://github.com/razziedazzel/asrock-arc-rgb-linux.git
cd asrock-arc-rgb-linux
sudo ./install.sh            # add --no-service to skip the boot/resume service
```

This installs `arc-rgb` and `arc-rgb-gui` to `/usr/local/bin`, an **Arc RGB** menu entry,
a udev rule so your user can reach the controller without root, and
`arc-rgb@<you>.service`. Log out and back in so the udev permission applies. `sudo ./uninstall.sh` removes everything except your saved settings.

You can also run straight from the checkout: `./arc_rgb.py status`, `./arc_rgb_gui.py`.

## Usage

```sh
arc-rgb status                              # zones, effect, colour, speed, brightness
arc-rgb set -m static -c ff0000             # all zones solid red
arc-rgb set -z logo -m breathing -c 00ff40 -b 60
arc-rgb set -z fans -m rainbow -s 0x40      # speed: lower = faster
arc-rgb set -z argbheader --leds 30         # shorten a strip on the ARGB header
arc-rgb set ... --save                      # also save as the boot/resume setting
arc-rgb apply                               # re-send the saved setting
arc-rgb store                               # write current lighting to the card's flash
arc-rgb off | factory | modes
```

Zones on the B580 Steel Legend: **logo** (the "Steel Legend" lettering, 26 LEDs),
**fans** (26 LEDs) and **argbheader** (onboard ARGB header, up to 80 LEDs).

Effects: off, static, breathing, strobe, cycling, random, music, wave, spring, stack,
cram, scan, neon, water, rainbow, rainbow2, glowstream, luminoustide.

### The app

Pick a zone (or all), effect, colour, brightness and speed — changes show on the card
immediately. **Save** stores the setting for the boot/resume service; **Store on card**
writes it into the LED controller's flash so the card shows it from power-on.

## How it works

The LED controller is a small Nuvoton-family microcontroller at I2C address `0x36` on the
GPU's own DesignWare I2C bus. Every command is a 12-byte write; queries are followed by a
12-byte read. The full protocol — commands, record layout, firmware notes, and the
commands you must **never** send (bootloader/ISP) — is in [PROTOCOL.md](PROTOCOL.md).

It was worked out for interoperability by studying ASRock Polychrome Sync and the LED
controller firmware it ships. No ASRock software or firmware is included in this repository.

## Limitations

- Lighting is per zone. The LEDs are individually addressable in hardware, but the
  controller firmware offers no per-LED command; per-LED patterns come from its built-in
  effects.
- Music mode needs the host to stream an audio level; not implemented yet.
- Fan control is not done by this controller (it's in the GPU firmware), so it isn't here.

## License

MIT — see [LICENSE](LICENSE).
