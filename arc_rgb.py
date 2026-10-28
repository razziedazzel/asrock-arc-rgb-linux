#!/usr/bin/env python3
"""Control the RGB LEDs on an ASRock Intel Arc card (B580 Steel Legend / A770) from Linux.

Talks to the card's LED microcontroller at I2C address 0x36 on the GPU's own
DesignWare I2C bus (exposed by the xe driver). Protocol notes: PROTOCOL.md.
Normal changes live in the controller's RAM only; `apply` re-sends the saved
config (used at boot and after resume by arc-rgb.service), and `store` writes
the current lighting into the controller's flash.
"""
import argparse
import ctypes
import errno
import fcntl
import glob
import json
import os
import pwd
import subprocess
import sys
import time

__version__ = "0.2.0"

ADDR = 0x36
I2C_RDWR = 0x0707
I2C_M_RD = 0x0001
SUPPORTED_SUBSYS = {(0x1849, 0x6001), (0x1849, 0x6010), (0x1849, 0x6020)}
CONFIG_PATH = os.path.expanduser("~/.config/arc-rgb/config.json")
APP_ID = "io.github.razziedazzel.ArcRGB"

MODES = [
    "off", "static", "breathing", "strobe", "cycling", "random", "music", "wave",
    "spring", "stack", "cram", "scan", "neon", "water", "rainbow", "rainbow2",
    "glowstream", "luminoustide",
]
# Polychrome only sends a speed for these modes. Speed is a frame divider: higher = slower.
SPEED_MODES = set(range(2, 6)) | set(range(7, 16))
FACTORY = dict(mode=0x0F, rgb=(0x00, 0x00, 0xFF), speed=0x80, brightness=0xFF)
# Writing too fast can make the controller stop answering until a full power-off
# (seen on ASRock's Radeon cards, OpenRGB !3735; Polychrome waits up to 200 ms).
MIN_WRITE_GAP = 0.03
# Identified on the B580 Steel Legend by lighting each channel separately
ZONE_NAMES = {3: "ARGB header", 6: "Logo", 7: "Fans"}
# LED counts the firmware ships with. Raising a count past this could overrun the
# controller's per-channel LED buffer, so counts may only be lowered.
FACTORY_LED_COUNTS = {3: 80, 6: 26, 7: 26}


class I2cMsg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16),
                ("len", ctypes.c_uint16), ("buf", ctypes.POINTER(ctypes.c_uint8))]


class I2cRdwrData(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(I2cMsg)), ("nmsgs", ctypes.c_uint32)]


class NoCardError(Exception):
    pass


def find_bus(wait=0.0):
    """Return /dev/i2c-N for the DesignWare adapter that belongs to a supported card.

    With wait > 0, keep looking for that many seconds (the adapter appears only
    after xe and i2c-dev have loaded during boot).
    """
    deadline = time.monotonic() + wait
    while True:
        for adapter in sorted(glob.glob("/sys/bus/i2c/devices/i2c-*")):
            path = os.path.realpath(adapter)
            if "i2c_designware" not in path:
                continue
            pci = path.split("/i2c_designware")[0]
            try:
                vendor = int(open(f"{pci}/subsystem_vendor").read(), 16)
                device = int(open(f"{pci}/subsystem_device").read(), 16)
            except OSError:
                continue
            dev = "/dev/" + os.path.basename(adapter)
            if (vendor, device) in SUPPORTED_SUBSYS and os.path.exists(dev):
                return dev
        if time.monotonic() >= deadline:
            raise NoCardError(
                "No supported ASRock Arc card with an xe I2C adapter found. Check the card is "
                "listed in the README, that it uses the xe driver, that your kernel is recent "
                "enough to expose its I2C adapter (look for 'Synopsys DesignWare I2C adapter' "
                "in `i2cdetect -l`), and that the i2c-dev module is loaded.")
        time.sleep(0.5)


class LedCtrl:
    def __init__(self, dev=None, wait=0.0):
        self.dev = dev or find_bus(wait)
        self.fd = os.open(self.dev, os.O_RDWR)
        self._present = None
        self._last_write = 0.0
        self._last_set = {}       # channel -> last 0x10 packet sent, to skip repeats
        self._failures = 0
        self._paused_until = 0.0

    def close(self):
        os.close(self.fd)

    def _xfer(self, data, flags):
        if time.monotonic() < self._paused_until:
            raise OSError(errno.EBUSY, "LED controller not answering; pausing before retrying")
        buf = (ctypes.c_uint8 * 12)(*data)
        msg = I2cMsg(ADDR, flags, 12, buf)
        try:
            fcntl.ioctl(self.fd, I2C_RDWR, I2cRdwrData(ctypes.pointer(msg), 1))
        except OSError:
            # Back off instead of hammering a controller that stopped acknowledging
            self._failures += 1
            self._paused_until = time.monotonic() + (5 if self._failures >= 3 else 1)
            raise
        self._failures = 0
        return bytes(buf)

    def write(self, *data):
        wait = self._last_write + MIN_WRITE_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            self._xfer(list(data) + [0] * (12 - len(data)), 0)
        finally:
            self._last_write = time.monotonic()

    def query(self, *data):
        self.write(*data)
        time.sleep(0.03)
        return self._xfer([0] * 12, I2C_M_RD)

    def _mask(self, sub):
        r = self.query(0x14, 0, sub)
        return r[4] | r[5] << 8

    def present_channels(self):
        if self._present is None:
            mask = self._mask(1)
            self._present = [ch for ch in range(10) if mask >> ch & 1]
        return self._present

    def enabled_mask(self):
        return self._mask(3)

    def swap_mask(self):
        """Bit n set = channel n swaps red and green (for GRB-wired LEDs)."""
        return self._mask(7)

    def set_swap_mask(self, mask):
        self.write(0x15, 0, 7, mask & 0xFF, mask >> 8 & 0xFF)

    def led_counts(self):
        """LED count for channels 0..7."""
        return list(self.query(0x14, 0, 2)[4:12])

    def set_led_count(self, ch, n):
        limit = FACTORY_LED_COUNTS.get(ch)
        if ch > 7 or limit is None or not 1 <= n <= limit:
            raise ValueError(f"LED count for channel {ch} must be 1..{limit}")
        counts = self.led_counts()
        counts[ch] = n
        self.write(0x15, 0, 2, *counts)

    def get(self, ch):
        r = self.query(0x11, 0, ch)
        return dict(mode=r[4], rgb=(r[5], r[6], r[7]), speed=r[8], brightness=r[9])

    def set(self, ch, mode, rgb, speed, brightness=0xFF):
        # Byte 9 is the firmware's "sync all channels" flag; we always address one channel
        packet = (0x10, 0, ch, mode, *rgb, speed, brightness, 0, 0)
        if self._last_set.get(ch) == packet:
            return
        self.write(*packet)
        self._last_set[ch] = packet

    def store(self):
        """Write the controller's current settings to its flash (Polychrome's save)."""
        self.write(0x12)
        time.sleep(0.5)


def hex_color(rgb):
    return "".join(f"{b:02x}" for b in rgb)


def parse_color(s):
    s = s.lstrip("#")
    if len(s) != 6:
        raise argparse.ArgumentTypeError("colour must be RRGGBB hex")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def parse_mode(s):
    if str(s).isdigit():
        return int(s)
    try:
        return MODES.index(s.lower())
    except ValueError:
        raise argparse.ArgumentTypeError(f"unknown mode; choose from: {', '.join(MODES)}")


def mode_name(m):
    return MODES[m] if m < len(MODES) else f"0x{m:02x}"


def zone_name(ch):
    return ZONE_NAMES.get(ch, f"Zone {ch}")


def parse_zone(s):
    if s.isdigit():
        return int(s)
    for ch, name in ZONE_NAMES.items():
        if name.lower().replace(" ", "") == s.lower().replace(" ", "").replace("-", ""):
            return ch
    raise argparse.ArgumentTypeError(
        f"unknown zone; use a number or one of: {', '.join(n.lower().replace(' ', '') for n in ZONE_NAMES.values())}")


def percent_to_byte(p):
    return round(max(0, min(100, p)) * 255 / 100)


def byte_to_percent(b):
    return round(b * 100 / 255)


def read_settings(led):
    """Snapshot every present channel: effect fields plus LED count / R-G swap where known."""
    swap = led.swap_mask()
    counts = led.led_counts()
    out = {}
    for ch in led.present_channels():
        s = led.get(ch)
        s["swap_rg"] = bool(swap >> ch & 1)
        if ch in FACTORY_LED_COUNTS:
            s["leds"] = counts[ch]
        out[ch] = s
    return out


def write_settings(led, settings, hardware=True):
    """Send settings to the card. hardware=False skips LED counts and the R/G swap mask."""
    present = led.present_channels()
    for ch, s in settings.items():
        if ch in present:
            led.set(ch, s["mode"], s["rgb"], s["speed"], s.get("brightness", 0xFF))
    if not hardware:
        return
    swap = led.swap_mask()
    new_swap = swap
    for ch, s in settings.items():
        if "swap_rg" in s:
            new_swap = new_swap | 1 << ch if s["swap_rg"] else new_swap & ~(1 << ch)
    if new_swap != swap:
        led.set_swap_mask(new_swap)
    counts = led.led_counts()
    for ch, s in settings.items():
        if "leds" in s and ch < 8 and counts[ch] != s["leds"]:
            led.set_led_count(ch, s["leds"])


def load_config(path=CONFIG_PATH):
    """Return {channel: settings} or None if there is no saved config."""
    try:
        with open(path) as f:
            raw = json.load(f)
    except FileNotFoundError:
        return None
    out = {}
    for ch, v in raw["channels"].items():
        s = dict(mode=parse_mode(v["mode"]), rgb=parse_color(v["color"]), speed=int(v["speed"]),
                 brightness=int(v.get("brightness", 0xFF)))
        for k in ("leds", "swap_rg"):
            if k in v:
                s[k] = v[k]
        out[int(ch)] = s
    return out


def save_config(settings, path=CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    chans = {}
    for ch, s in sorted(settings.items()):
        c = {"mode": mode_name(s["mode"]), "color": hex_color(s["rgb"]), "speed": s["speed"],
             "brightness": s.get("brightness", 0xFF)}
        for k in ("leds", "swap_rg"):
            if k in s:
                c[k] = s[k]
        chans[str(ch)] = c
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"channels": chans}, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def config_path_for(user):
    return os.path.join(pwd.getpwnam(user).pw_dir, ".config/arc-rgb/config.json")


def invoking_user():
    """The human behind this process, even when run through sudo or pkexec."""
    if os.environ.get("PKEXEC_UID"):
        return pwd.getpwuid(int(os.environ["PKEXEC_UID"])).pw_name
    if os.environ.get("SUDO_USER"):
        return os.environ["SUDO_USER"]
    return pwd.getpwuid(os.getuid()).pw_name


def service_unit(user):
    return f"arc-rgb@{user}.service"


def service_installed():
    return any(os.path.exists(f"{d}/arc-rgb@.service")
               for d in ("/etc/systemd/system", "/usr/local/lib/systemd/system", "/usr/lib/systemd/system"))


def service_enabled(user):
    return subprocess.run(["systemctl", "is-enabled", "--quiet", service_unit(user)]).returncode == 0


def service_command(action, user):
    """enable / disable / status of the boot+resume service. enable/disable need root."""
    pwd.getpwnam(user)  # raises KeyError for unknown users
    unit = service_unit(user)
    if action == "status":
        state = "enabled" if service_enabled(user) else "disabled"
        print(f"{unit}: {state}{'' if service_installed() else ' (service not installed)'}")
        return 0
    if os.geteuid() != 0:
        sys.exit(f"'{action}' needs root: sudo arc-rgb service {action}")
    if not service_installed():
        sys.exit("arc-rgb@.service is not installed (install the package or run install.sh).")
    if action == "enable":
        subprocess.run(["systemctl", "enable", unit], check=True)
        if os.path.exists(config_path_for(user)):
            subprocess.run(["systemctl", "start", unit], check=False)
        else:
            print(f"Note: nothing saved yet for {user}; save a setting (app 'Save' or 'set --save').")
    else:
        subprocess.run(["systemctl", "disable", unit], check=True)
    return 0


def main():
    ap = argparse.ArgumentParser(prog="arc-rgb", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--bus", help="I2C device, e.g. /dev/i2c-12 (default: auto-detect)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show zones and their current settings")
    s = sub.add_parser("set", help="change zone settings (unspecified fields are kept)")
    s.add_argument("-m", "--mode", type=parse_mode)
    s.add_argument("-c", "--color", type=parse_color, help="RRGGBB")
    s.add_argument("-s", "--speed", type=lambda x: int(x, 0), help="0-255, higher = slower (factory 0x80)")
    s.add_argument("-b", "--brightness", type=int, help="0-100 %%")
    s.add_argument("--leds", type=int, help="shorten the zone to this many LEDs (up to its factory count)")
    s.add_argument("--swap-rg", choices=["on", "off"], help="swap red/green (for GRB-wired strips)")
    s.add_argument("--channel", "-z", "--zone", type=parse_zone, action="append", dest="channel",
                   help="zone: logo, fans, argbheader or a channel number (repeatable; default: all)")
    s.add_argument("--save", action="store_true", help="also save as the boot/resume setting")
    sub.add_parser("off", help="turn all LEDs off")
    sub.add_parser("factory", help="restore the factory effect (Rainbow2, speed 0x80)")
    sub.add_parser("modes", help="list mode names")
    a = sub.add_parser("apply", help="send the saved config to the card (used at boot)")
    a.add_argument("--config", help=f"config file (default: {CONFIG_PATH})")
    a.add_argument("--user", help="use this user's saved config (for the boot service)")
    a.add_argument("--wait", type=float, default=0, help="seconds to wait for the card to appear")
    sv = sub.add_parser("service", help="enable/disable re-applying the saved setting at boot and resume")
    sv.add_argument("action", choices=["enable", "disable", "status"])
    sv.add_argument("--user", help="whose saved setting to apply (default: the invoking user)")
    st = sub.add_parser("store", help="write the current lighting into the controller's flash")
    st.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    args = ap.parse_args()

    if args.cmd == "service":
        try:
            return service_command(args.action, args.user or invoking_user())
        except KeyError:
            sys.exit(f"unknown user: {args.user}")
        except subprocess.CalledProcessError as e:
            sys.exit(f"systemctl failed: {e}")

    if args.cmd == "modes":
        for i, n in enumerate(MODES):
            print(f"{i:2d}  {n}{'  (speed)' if i in SPEED_MODES else ''}")
        return

    try:
        led = LedCtrl(args.bus, getattr(args, "wait", 0))
    except NoCardError as e:
        sys.exit(str(e))
    present = led.present_channels()

    if args.cmd == "status":
        en = led.enabled_mask()
        print(f"bus {led.dev}, address 0x{ADDR:02x}")
        for ch, r in read_settings(led).items():
            extra = f"  {r['leds']} LEDs" if "leds" in r else ""
            print(f"  {zone_name(ch):<12} (ch{ch}): {mode_name(r['mode']):<12} #{hex_color(r['rgb'])}"
                  f"  speed 0x{r['speed']:02x}  brightness {byte_to_percent(r['brightness'])}%"
                  f"{extra}{'  R/G swapped' if r['swap_rg'] else ''}"
                  f"{'' if en >> ch & 1 else '  disabled'}")
        return

    if args.cmd == "apply":
        path = args.config or (config_path_for(args.user) if args.user else CONFIG_PATH)
        cfg = load_config(path)
        if cfg is None:
            sys.exit(f"No saved config at {path}")
        write_settings(led, cfg)
        for ch, s in cfg.items():
            print(f"{zone_name(ch)}: {mode_name(s['mode'])} #{hex_color(s['rgb'])}")
        return

    if args.cmd == "store":
        if not args.yes:
            ans = input("Write the current lighting into the LED controller's flash? [y/N] ")
            if ans.strip().lower() not in ("y", "yes"):
                sys.exit("Cancelled.")
        led.store()
        print("Stored on the card.")
        return

    channels = getattr(args, "channel", None) or present
    for ch in channels:
        cur = led.get(ch)
        if args.cmd == "off":
            led.set(ch, 0, cur["rgb"], cur["speed"], cur["brightness"])
        elif args.cmd == "factory":
            led.set(ch, FACTORY["mode"], FACTORY["rgb"], FACTORY["speed"], FACTORY["brightness"])
        else:
            led.set(ch,
                    cur["mode"] if args.mode is None else args.mode,
                    cur["rgb"] if args.color is None else args.color,
                    cur["speed"] if args.speed is None else args.speed,
                    cur["brightness"] if args.brightness is None else percent_to_byte(args.brightness))
            try:
                if args.leds is not None:
                    led.set_led_count(ch, args.leds)
            except ValueError as e:
                sys.exit(str(e))
            if args.swap_rg is not None:
                m = led.swap_mask()
                led.set_swap_mask(m | 1 << ch if args.swap_rg == "on" else m & ~(1 << ch))
        r = led.get(ch)
        print(f"{zone_name(ch)}: {mode_name(r['mode'])} #{hex_color(r['rgb'])} speed 0x{r['speed']:02x}"
              f" brightness {byte_to_percent(r['brightness'])}%")

    if getattr(args, "save", False):
        save_config(read_settings(led))
        print(f"saved to {CONFIG_PATH}")


if __name__ == "__main__":
    sys.exit(main())
