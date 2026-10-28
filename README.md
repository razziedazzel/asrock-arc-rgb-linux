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

### Only ASRock's LED controller — not other brands

This tool talks to the **RGB controller ASRock puts on its own Arc boards**, not to anything
built into the Intel GPU. Other vendors' Arc cards (Gigabyte, Sparkle, Acer, Intel Limited
Edition, …) use different RGB hardware and **will not work**, even if they are also B580s.

What the ASRock controller is:

- a small **ARM Cortex-M microcontroller** (very likely a Nuvoton NuMicro part, going by its
  APROM/LDROM firmware layout; exact model unconfirmed),
- at I2C address **`0x36`** on the GPU's own I2C bus (shown as
  "Synopsys DesignWare I2C adapter" under the `xe` driver),
- identified by PCI subsystem vendor **`1849`** (ASRock).

`arc-rgb` only opens that bus on a card whose subsystem ID is in the table above, so it
does nothing on unsupported cards. If you have another ASRock Arc card with RGB, please
open an issue with the output of `lspci -nnv -d 8086:` and `i2cdetect -l`.

## Requirements

- The `xe` kernel driver with I2C support for the card. Only recent kernels create the card's
  I2C adapter; check with `i2cdetect -l` (from `i2c-tools`) for a
  "Synopsys DesignWare I2C adapter". Tested on kernel 7.2. On Linux Mint / Ubuntu LTS you will
  likely need an HWE or mainline kernel.
- Python 3 (standard library only for the CLI).
- For the app: GTK 4.12+ and libadwaita 1.5+ with PyGObject (Ubuntu 24.04 / Mint 22 or newer)
  (Fedora: `python3-gobject gtk4 libadwaita`; Debian/Ubuntu: `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`).

## Install

**Linux Mint / Ubuntu / Debian:** download `asrock-arc-rgb_<version>_all.deb` from the
[Releases](https://github.com/razziedazzel/asrock-arc-rgb-linux/releases) page and open it,
or run `sudo apt install ./asrock-arc-rgb_<version>_all.deb`.

**Fedora:** download `asrock-arc-rgb-<version>-1.noarch.rpm` and run
`sudo dnf install ./asrock-arc-rgb-<version>-1.noarch.rpm`.

Either package gives you:

- the **Arc RGB** app in your applications menu, and the `arc-rgb` command,
- a udev rule so your user can reach the LED controller without root,
- the `i2c-dev` module loaded at boot,
- an optional boot/resume service, **off by default** (see below).

Log out and back in once after installing so the new permission applies.

**From source:** `git clone` this repo and run `sudo ./install.sh` (installs to `/usr/local`;
`sudo ./uninstall.sh` removes it). You can also run `./arc_rgb.py` / `./arc_rgb_gui.py`
straight from the checkout.

### Keeping your lighting

Two options; use either or both:

- **Store on card** (app) / `arc-rgb store` — writes the lighting into the LED controller's
  flash, so the card shows it from power-on with no software running.
- **Re-apply at boot and after resume** (app switch, asks for your password) /
  `sudo arc-rgb service enable` — a systemd service re-sends your saved setting
  (`~/.config/arc-rgb/config.json`) at boot and after waking from sleep.
  `sudo arc-rgb service disable` turns it off; `arc-rgb service status` shows it.

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
immediately. **Save** keeps the setting for the boot/resume service; the
**Keep my lighting** section has the service switch and **Store on card**.

## How it works

The LED controller is a small Nuvoton-family microcontroller at I2C address `0x36` on the
GPU's own DesignWare I2C bus. Every command is a 12-byte write; queries are followed by a
12-byte read. **[PROTOCOL.md](PROTOCOL.md)** is a standalone reference for driving the
controller from your own software: how to find it, every command and reply byte, timing,
worked `i2ctransfer` and C examples, and the commands you must **never** send.

It was worked out for interoperability by studying ASRock Polychrome Sync and the LED
controller firmware it ships. No ASRock software or firmware is included in this repository.

## Limitations

- Lighting is per zone. The LEDs are individually addressable in hardware, but the
  controller firmware offers no per-LED command; per-LED patterns come from its built-in
  effects.
- Music mode needs the host to stream an audio level; not implemented yet.
- Fan control is not done by this controller (it's in the GPU firmware), so it isn't here.

## Building packages

`packaging/build-deb.sh VERSION` and `packaging/build-rpm.sh VERSION` write to `dist/`
(needs `dpkg-deb` / `rpmbuild`). Pushing a `vX.Y.Z` tag that matches `__version__` in
`arc_rgb.py` makes GitHub Actions build both and publish a release.

## License

MIT — see [LICENSE](LICENSE).
