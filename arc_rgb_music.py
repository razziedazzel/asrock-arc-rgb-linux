#!/usr/bin/env python3
"""Drive an ASRock Arc card's LEDs from the PC's audio output (music mode).

Captures the default output's monitor with parec and follows one frequency band with an
FFT. Two styles:
  * Pulse brightness: one colour for the whole card, brightness follows the band, and an
    optional accent colour flashes on hard hits (sent with the all-channel mode 0x11).
  * Card music effect: the firmware's own Music mode (6), fed the level in byte 10.
Only loudness is computed; no audio is stored.

`arc-rgb-music run` is the background service (arc-rgb-music.service, a systemd user unit):
it reads music.json and the saved card lighting, reloads when either changes, and puts the
saved lighting back when it stops.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time

import numpy as np

SAMPLE_RATE = 16000         # enough for bands up to 8 kHz (hi-hats)
MAX_HZ = SAMPLE_RATE // 2
# name -> (FFT window, hop, capture buffer ms). Low: 64 ms window (~16 Hz bins), new value
# every 16 ms; Normal: 128 ms window (~8 Hz bins), every 64 ms.
LATENCY = {"Normal": (2048, 1024, 30), "Low": (1024, 256, 20)}
REFERENCE_HOP_S = 0.064     # the envelope constants below were tuned for 64 ms steps
# (low, high) in Hz; a band can also be any custom (low, high) tuple
BANDS = {
    "Sub-bass": (20, 60),
    "Bass (beat)": (60, 150),
    "Low-mid": (150, 500),
    "Vocals": (500, 2000),
    "Presence (snare)": (2000, 4000),
    "Highs (hi-hats)": (4000, 8000),
}
DEFAULT_BAND = BANDS["Bass (beat)"]
PREFS_PATH = os.path.expanduser("~/.config/arc-rgb/music.json")
STATUS_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "arc-rgb-music.json")
SERVICE = "arc-rgb-music.service"
MUSIC_EFFECT = 6
STYLES = ["Pulse brightness", "Card music effect"]
# Card updates per second by (style, latency). Pulse sends one all-channel packet per update;
# the card effect sends one per channel. All stay well under what hung the RX 9070 XT.
UPDATE_HZ = {("Pulse brightness", "Normal"): 10, ("Pulse brightness", "Low"): 15,
             ("Card music effect", "Normal"): 6, ("Card music effect", "Low"): 8}
MUSIC_MODE = 0x06
ALL_CHANNELS_STATIC = 0x11  # firmware: same colour on every channel, rendered immediately
ACCENT_HOLD = 0.2           # seconds an accent colour stays after a hard hit
SILENCE = 0.05              # band magnitude below which the lights rest


class Envelope:
    """Beat-friendly loudness follower with automatic gain, returning 0-1."""

    def __init__(self, hop_s=REFERENCE_HOP_S):
        self.peak = 1.0
        self.env = 0.0
        # Scale per-step decay so release and auto gain behave the same at any step size
        steps = hop_s / REFERENCE_HOP_S
        self.release = 0.6 ** steps
        self.peak_decay = 0.995 ** steps

    def update(self, value, sensitivity):
        # Fast attack, quick release so beats punch through
        self.env = value if value > self.env else self.env * self.release + value * (1 - self.release)
        # Auto gain: follow the recent peak, decaying slowly so quiet songs still fill the range
        self.peak = max(self.env, self.peak * self.peak_decay, SILENCE)
        return min(1.0, (self.env / self.peak if self.env > SILENCE else 0.0) * sensitivity)


class BandLevel:
    """Background follower for the PC's audio output: one band drives the level, another
    (or the same) detects hard hits."""

    def __init__(self, band=DEFAULT_BAND, hit_band=None, sensitivity=1.0, threshold=0.0, hard_hit=0.9,
                 latency="Normal"):
        self.band = band                # (low_hz, high_hz) driving the level
        self.hit_band = hit_band        # (low_hz, high_hz) for hard hits; None = same as band
        self.sensitivity = sensitivity  # gain on the normalised level
        self.threshold = threshold      # 0-1: below this the output is 0 (lights off)
        self.hard_hit = hard_hit        # 0-1: rising past this counts as a hard hit
        self.level = 0                  # 0-255 after gain and threshold, read by the UI thread
        self.last_hit = 0.0             # time.monotonic() of the latest hard hit
        self._window, self._hop, self._buffer_ms = LATENCY[latency]
        hop_s = self._hop / SAMPLE_RATE
        self._level_env = Envelope(hop_s)
        self._hit_env = Envelope(hop_s)
        self._prev_hit = 0.0
        self._proc = None
        self._freqs = np.fft.rfftfreq(self._window, 1 / SAMPLE_RATE)
        self._hann = np.hanning(self._window).astype(np.float32)

    def start(self):
        self._proc = subprocess.Popen(
            ["parec", "-d", "@DEFAULT_MONITOR@", "--format=s16le", f"--rate={SAMPLE_RATE}",
             "--channels=1", f"--latency-msec={self._buffer_ms}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        self.level = 0

    def hit_active(self):
        return time.monotonic() - self.last_hit < ACCENT_HOLD

    def _run(self):
        hop = self._hop
        buf = np.zeros(self._window, dtype=np.float32)
        stream = self._proc.stdout
        while self._proc:
            chunk = stream.read(hop * 2)
            if not chunk or len(chunk) < hop * 2:
                break
            buf = np.roll(buf, -hop)
            buf[-hop:] = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768
            spectrum = np.abs(np.fft.rfft(buf * self._hann))
            norm = self._level_env.update(self._band_value(spectrum, self.band), self.sensitivity)
            if self.hit_band and self.hit_band != self.band:
                hit = self._hit_env.update(self._band_value(spectrum, self.hit_band), self.sensitivity)
            else:
                hit = norm
            if hit >= self.hard_hit > self._prev_hit:
                self.last_hit = time.monotonic()
            self._prev_hit = hit
            if norm <= self.threshold:
                self.level = 0
            else:
                self.level = int((norm - self.threshold) / (1.0 - self.threshold) * 255)


    def _band_value(self, spectrum, band):
        lo, hi = band
        lo, hi = max(1, min(lo, MAX_HZ - 1)), max(2, min(hi, MAX_HZ))
        if hi <= lo:
            hi = lo + 1
        bins = spectrum[(self._freqs >= lo) & (self._freqs < hi)]
        return float(bins.mean()) if bins.size else 0.0


def send_card_effect(led, channels, settings, level):
    """Firmware Music mode: one packet per channel, keeping its colour/brightness bytes."""
    for ch in channels:
        s = settings[ch]
        led.write(0x10, 0, ch, MUSIC_MODE, *s["rgb"], s["speed"], s.get("brightness", 0xFF), 0, level)


def pulse_rgb(base_rgb, level, accent_rgb=None):
    """The colour for this moment: the accent on a hard hit, else the base scaled by level."""
    return accent_rgb if accent_rgb else tuple(round(c * level / 255) for c in base_rgb)


def send_pulse(led, rgb):
    """One colour on every channel. Static ignores the brightness byte, so callers scale the colour."""
    led.write(0x10, 0, 0, ALL_CHANNELS_STATIC, *rgb, 0x80, 0xFF, 0, 0)


def load_prefs(path=PREFS_PATH):
    """Music settings remembered between runs, or {} if there are none (or the file is bad)."""
    try:
        with open(path) as f:
            prefs = json.load(f)
        return prefs if isinstance(prefs, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(prefs, path=PREFS_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(prefs, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)



# ---- background service ----------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import arc_rgb  # noqa: E402


def band_from_prefs(prefs, name_key, custom_key, default):
    """Resolve a range name (or "Custom") from music.json to (low, high) Hz; None = same as Follow."""
    name = prefs.get(name_key, default)
    if name == "Same as Follow":
        return None
    if name == "Custom":
        lo, hi = prefs.get(custom_key, BANDS[default] if default in BANDS else DEFAULT_BAND)
        return (int(lo), int(hi))
    return BANDS.get(name, DEFAULT_BAND)


def write_status(level, running):
    """Tiny status file so the app can show the live level meter."""
    try:
        tmp = STATUS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"level": level, "running": running, "time": time.time()}, f)
        os.replace(tmp, STATUS_PATH)
    except OSError:
        pass


class MusicEngine:
    """Music mode against the hardware, driven by music.json and the saved card lighting."""

    def __init__(self):
        self.led = arc_rgb.LedCtrl(wait=30)
        self.listener = None
        self.latency = None
        self.prefs = {}
        self.card = {}
        self.mtimes = None

    def files_changed(self):
        mtimes = []
        for path in (PREFS_PATH, arc_rgb.CONFIG_PATH):
            try:
                mtimes.append(os.stat(path).st_mtime_ns)
            except OSError:
                mtimes.append(None)
        changed = mtimes != self.mtimes
        self.mtimes = mtimes
        return changed

    def reload(self):
        self.prefs = load_prefs()
        self.card = arc_rgb.load_config() or {}
        if self.listener:
            if self.prefs.get("latency", "Normal") != self.latency:
                self.stop_listener(restore=False)   # restarted with the new window below
            else:
                self.configure(self.listener)

    def zones(self):
        return [ch for ch, s in self.card.items() if s["mode"] == MUSIC_EFFECT]

    def style(self):
        return self.prefs.get("style", STYLES[0])

    def hz(self):
        return UPDATE_HZ.get((self.style(), self.prefs.get("latency", "Normal")), 10)

    def configure(self, listener):
        p = self.prefs
        listener.band = band_from_prefs(p, "follow", "follow_custom", "Bass (beat)")
        listener.hit_band = band_from_prefs(p, "hit_band", "hit_custom", "Same as Follow")
        listener.sensitivity = p.get("sensitivity", 100) / 100
        listener.threshold = p.get("threshold", 0) / 100
        listener.hard_hit = p.get("hard_hit", 90) / 100

    def start_listener(self):
        self.latency = self.prefs.get("latency", "Normal")
        self.listener = BandLevel(latency=self.latency if self.latency in LATENCY else "Normal")
        self.configure(self.listener)
        self.listener.start()

    def stop_listener(self, restore=True):
        if self.listener:
            self.listener.stop()
            self.listener = None
        if restore:
            self.restore()

    def restore(self):
        """Put the saved card lighting back."""
        # A zone saved as Music has nothing to show without music, so it rests as a steady
        # glow in its own colour (Music packets don't replace the firmware's stored mode)
        resting = {ch: dict(s, mode=1) if s["mode"] == MUSIC_EFFECT else s for ch, s in self.card.items()}
        try:
            self.led._last_set.clear()
            arc_rgb.write_settings(self.led, resting, hardware=False)
        except OSError:
            pass

    def tick(self):
        p = self.prefs
        level = self.listener.level
        pulse = self.style() == "Pulse brightness"
        accent = (tuple(arc_rgb.parse_color(p.get("accent_colour", "ff0000")))
                  if pulse and p.get("accent_on") and self.listener.hit_active() else None)
        rgb = pulse_rgb(arc_rgb.parse_color(p.get("colour", "00ff40")), level, accent)
        try:
            if pulse:
                send_pulse(self.led, rgb)
            else:
                send_card_effect(self.led, self.zones(), self.card, level)
        except OSError:
            pass   # LedCtrl pauses itself after a failed write
        return level



def run():
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    engine = MusicEngine()
    try:
        while not stop.is_set():
            if engine.files_changed():
                engine.reload()
            active = bool(engine.zones())
            if active and not engine.listener:
                engine.start_listener()
            elif not active and engine.listener:
                engine.stop_listener()
            if engine.listener:
                write_status(engine.tick(), True)
                stop.wait(1 / engine.hz())
            else:
                write_status(0, False)
                stop.wait(0.5)
    finally:
        engine.stop_listener()
        write_status(0, False)


def main():
    ap = argparse.ArgumentParser(prog="arc-rgb-music", description="Arc RGB music mode")
    ap.add_argument("command", choices=["run"], help="run the background music engine")
    ap.parse_args()
    try:
        run()
    except arc_rgb.NoCardError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
