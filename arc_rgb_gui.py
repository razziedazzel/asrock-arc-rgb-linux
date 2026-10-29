#!/usr/bin/env python3
"""GTK 4 front end for arc_rgb: live-edit the card's lighting and save it for boot."""
import getpass
import json
import os
import shutil
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import arc_rgb  # noqa: E402

try:  # music mode is optional: it needs numpy and parec
    import arc_rgb_music
except ImportError:
    arc_rgb_music = None
if arc_rgb_music and not shutil.which("parec"):
    arc_rgb_music = None   # no audio capture tool (pulseaudio-utils), so no music mode

PRESETS = [
    ("Red", "ff0000"), ("Orange", "ff6000"), ("Yellow", "ffff00"), ("Green", "00ff00"),
    ("Cyan", "00ffff"), ("Blue", "0000ff"), ("Purple", "8000ff"), ("White", "ffffff"),
]
EFFECT_FIELDS = ("mode", "rgb", "speed", "brightness")
CLI = os.path.realpath(arc_rgb.__file__)


def rgba_from_rgb(rgb):
    c = Gdk.RGBA()
    c.parse("#" + arc_rgb.hex_color(rgb))
    return c


def rgb_from_rgba(c):
    return tuple(round(v * 255) for v in (c.red, c.green, c.blue))


def copy_settings(settings):
    return {ch: dict(s) for ch, s in settings.items()}


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Arc RGB", default_width=460, default_height=760)
        self.led = None
        self.settings = {}      # channel -> settings dict, mirrors what is on the card
        self.saved = None       # last saved config
        self.loading = False
        self.pending = None
        self.music_running = False  # the background music service is active
        self.music_timer = None
        self.music_react_pref = False   # the user's last React-to-music choice, remembered
        self.prefs_loading = False

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.set_tooltip_text("Save this lighting (used by the boot service)")
        self.save_btn.connect("clicked", self.on_save)
        self.revert_btn = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Revert to saved")
        self.revert_btn.connect("clicked", self.on_revert)
        header.pack_end(self.save_btn)
        header.pack_start(self.revert_btn)
        view.add_top_bar(header)

        self.banner = Adw.Banner()
        view.add_top_bar(self.banner)

        page = Adw.PreferencesPage()

        # ---- effect ----
        group = Adw.PreferencesGroup(title="Lighting")
        page.add(group)
        self.channel_row = Adw.ComboRow(title="Zone")
        self.channel_row.connect("notify::selected", self.on_channel_changed)
        group.add(self.channel_row)

        self.mode_ids = list(range(len(arc_rgb.MODES)))   # Effect list position -> mode number
        self.mode_row = Adw.ComboRow(title="Effect",
                                     model=Gtk.StringList.new([m.capitalize() for m in arc_rgb.MODES]))
        self.mode_row.connect("notify::selected", self.on_edit)
        group.add(self.mode_row)

        color_row = Adw.ActionRow(title="Colour")
        self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                               valign=Gtk.Align.CENTER)
        self.color_btn.connect("notify::rgba", self.on_edit)
        color_row.add_suffix(self.color_btn)
        group.add(color_row)

        group.add(self.swatches(self.on_preset))

        self.brightness_row, self.brightness = self.slider_row("Brightness", 0, 100, "%")
        group.add(self.brightness_row)
        # Firmware speed is a frame divider (higher = slower), so the slider runs 255 - speed
        self.speed_row, self.speed = self.slider_row("Speed", 0, 255, None)
        self.speed.add_mark(255 - 0x80, Gtk.PositionType.BOTTOM, None)
        group.add(self.speed_row)

        # ---- zone hardware ----
        self.hw_group = Adw.PreferencesGroup(
            title="Zone hardware",
            description="Applies to the selected zone. Useful for a strip on the ARGB header.")
        page.add(self.hw_group)
        self.leds_row = Adw.SpinRow.new_with_range(1, 80, 1)
        self.leds_row.set_title("LED count")
        self.leds_row.set_subtitle("Can be shortened, not lengthened past the factory count")
        self.leds_row.connect("notify::value", self.on_hw_edit)
        self.hw_group.add(self.leds_row)
        self.swap_row = Adw.SwitchRow(title="Swap red and green",
                                      subtitle="Turn on if red and green appear exchanged")
        self.swap_row.connect("notify::active", self.on_hw_edit)
        self.hw_group.add(self.swap_row)

        # ---- music ----
        if arc_rgb_music:
            music = Adw.PreferencesGroup(title="Music")
            self.music_group = music
            page.add(music)
            self.music_row = Adw.SwitchRow(title="React to music")
            self.music_row.connect("notify::active", self.on_music_toggled)
            music.add(self.music_row)
            self.style_row = Adw.ComboRow(title="Style", model=Gtk.StringList.new(arc_rgb_music.STYLES))
            self.style_row.connect("notify::selected", self.on_music_settings)
            music.add(self.style_row)
            self.latency_row = Adw.ComboRow(title="Latency", subtitle="Low reacts sooner; sub-bass is a bit rougher",
                                            model=Gtk.StringList.new(list(arc_rgb_music.LATENCY)))
            self.latency_row.connect("notify::selected", self.on_latency_changed)
            music.add(self.latency_row)
            band_names = list(arc_rgb_music.BANDS) + ["Custom"]
            self.band_row = Adw.ComboRow(title="Follow", subtitle="Frequency range for the brightness",
                                         model=Gtk.StringList.new(band_names))
            self.band_row.set_selected(band_names.index("Bass (beat)"))
            self.band_row.connect("notify::selected", self.on_music_settings)
            music.add(self.band_row)
            self.band_lo, self.band_hi = self.hz_rows(music, (60, 150))
            self.pulse_color_row = Adw.ActionRow(title="Colour", subtitle="Whole card; brightness follows the music")
            self.pulse_color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                                     valign=Gtk.Align.CENTER, rgba=rgba_from_rgb((0, 255, 64)))
            self.pulse_color_row.add_suffix(self.pulse_color)
            music.add(self.pulse_color_row)
            self.accent_row = Adw.ActionRow(title="Accent colour", subtitle="Flashes on hard hits")
            self.accent_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            self.accent_color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                                      valign=Gtk.Align.CENTER, rgba=rgba_from_rgb((255, 0, 0)))
            self.accent_switch.connect("notify::active", self.on_music_settings)
            self.accent_row.add_suffix(self.accent_color)
            self.accent_row.add_suffix(self.accent_switch)
            music.add(self.accent_row)
            self.hit_band_row = Adw.ComboRow(title="Hard hits listen to",
                                             model=Gtk.StringList.new(["Same as Follow"] + band_names))
            self.hit_band_row.connect("notify::selected", self.on_music_settings)
            music.add(self.hit_band_row)
            self.hit_lo, self.hit_hi = self.hz_rows(music, (4000, 8000))
            self.sens_row, self.sens = self.slider_row("Sensitivity", 25, 400, "%", self.on_music_settings)
            self.sens.set_value(100)
            music.add(self.sens_row)
            self.threshold_row, self.threshold = self.slider_row("Threshold", 0, 100, "%", self.on_music_settings)
            self.threshold_row.set_subtitle("Lights stay off below this")
            music.add(self.threshold_row)
            self.hit_row, self.hit = self.slider_row("Hard hit", 50, 100, "%", self.on_music_settings)
            self.hit.set_value(90)
            music.add(self.hit_row)
            level_row = Adw.ActionRow(title="Level")
            self.level_bar = Gtk.LevelBar(min_value=0, max_value=255, hexpand=True, valign=Gtk.Align.CENTER)
            self.level_bar.set_size_request(200, -1)
            level_row.add_suffix(self.level_bar)
            music.add(level_row)
            self.pulse_color.connect("notify::rgba", self.on_music_settings)
            self.accent_color.connect("notify::rgba", self.on_music_settings)
            self.apply_music_prefs()
            self.update_music_sensitivity()
            self.connect("close-request", lambda *_: self.stop_music() or False)

        # ---- keep my lighting ----
        keep = Adw.PreferencesGroup(title="Keep my lighting")
        page.add(keep)
        self.service_row = Adw.SwitchRow(title="Re-apply at boot and after resume",
                                         subtitle="Uses your saved setting. Asks for your password.")
        self.service_row.connect("notify::active", self.on_service_toggled)
        keep.add(self.service_row)
        keep.add(self.action_row("Store on card…", self.on_store,
                                 "Saves into the card itself, so it shows from power-on"))

        # ---- actions ----
        actions = Adw.PreferencesGroup()
        page.add(actions)
        actions.add(self.action_row("Turn off", lambda *_: self.set_mode_everywhere(0)))
        actions.add(self.action_row("Factory effect", self.on_factory))

        view.set_content(page)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        css = Gtk.CssProvider()
        css.load_from_string("".join(
            f".swatch-{h} {{ background: #{h}; border-radius: 50%; min-width: 0; padding: 0; }}"
            for _, h in PRESETS))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.connect_card()
        if arc_rgb_music:
            self.refresh_music_running()       # the switch mirrors the background service

    def swatches(self, handler):
        box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=8,
                          min_children_per_line=4, homogeneous=True, margin_top=6, margin_bottom=6)
        for name, hexc in PRESETS:
            b = Gtk.Button(tooltip_text=name, width_request=36, height_request=36)
            b.add_css_class("swatch")
            b.add_css_class(f"swatch-{hexc}")
            b.connect("clicked", handler, hexc)
            box.append(b)
        return box

    def action_row(self, title, handler, subtitle=None):
        # Plain activatable row rather than Adw.ButtonRow, which needs libadwaita 1.6
        row = Adw.ActionRow(title=title, activatable=True)
        if subtitle:
            row.set_subtitle(subtitle)
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", handler)
        return row

    def slider_row(self, title, lo, hi, unit, handler=None):
        row = Adw.ActionRow(title=title)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        scale.set_hexpand(True)
        scale.set_size_request(200, -1)
        if unit:
            scale.set_draw_value(True)
            scale.set_format_value_func(lambda _s, v: f"{v:.0f}{unit}")
        else:
            scale.set_draw_value(False)
        scale.connect("value-changed", handler or self.on_edit)
        row.add_suffix(scale)
        return row, scale

    # ---- card / model -------------------------------------------------
    def connect_card(self):
        try:
            self.led = arc_rgb.LedCtrl()
            self.settings = arc_rgb.read_settings(self.led)
        except (arc_rgb.NoCardError, OSError) as e:
            self.banner.set_title(f"Card not available: {e}")
            self.banner.set_revealed(True)
            self.get_content().set_sensitive(False)
            return
        self.saved = arc_rgb.load_config()
        labels = ["All zones"] + [arc_rgb.zone_name(ch) for ch in self.settings]
        self.channel_row.set_model(Gtk.StringList.new(labels))
        self.channel_row.set_selected(0)
        self.load_controls()
        self.update_dirty()
        self.refresh_service_state()

    def refresh_service_state(self):
        self.loading = True
        if arc_rgb.service_installed():
            self.service_row.set_active(arc_rgb.service_enabled(getpass.getuser()))
        else:
            self.service_row.set_active(False)
            self.service_row.set_sensitive(False)
            self.service_row.set_subtitle("Not available: install the package or run install.sh")
        self.loading = False

    def selected_channels(self):
        idx = self.channel_row.get_selected()
        chans = list(self.settings)
        return chans if idx in (0, Gtk.INVALID_LIST_POSITION) else [chans[idx - 1]]

    def single_channel(self):
        chans = self.selected_channels()
        return chans[0] if len(chans) == 1 else None

    def load_controls(self):
        chans = self.selected_channels()
        # "All zones" shows the first lit zone so the controls don't open on the dark header
        ch = next((c for c in chans if self.settings[c]["mode"] != 0), chans[0])
        s = self.settings[ch]
        self.loading = True
        self.refresh_mode_list(s["mode"])
        self.select_mode(min(s["mode"], len(arc_rgb.MODES) - 1))
        self.color_btn.set_rgba(rgba_from_rgb(s["rgb"]))
        self.speed.set_value(255 - s["speed"])
        self.brightness.set_value(arc_rgb.byte_to_percent(s["brightness"]))
        single = self.single_channel()
        hw = single is not None and "leds" in self.settings[single]
        self.hw_group.set_visible(hw)
        if hw:
            hs = self.settings[single]
            self.leds_row.set_range(1, arc_rgb.FACTORY_LED_COUNTS[single])
            self.leds_row.set_value(hs["leds"])
            self.swap_row.set_active(hs["swap_rg"])
        self.loading = False
        self.update_speed_sensitivity()
        self.update_music_availability()

    def music_zones(self):
        """Zones whose Effect is Music; the Music section only applies to these."""
        return [ch for ch, s in self.settings.items() if s["mode"] == arc_rgb.MODES.index("music")]

    def update_music_availability(self):
        if not arc_rgb_music or not hasattr(self, "music_group"):
            return
        available = bool(self.music_zones())
        self.music_group.set_sensitive(available)
        self.music_group.set_description(
            "Lights follow what your PC is playing, in the background, also after you close this app."
            if available else "Choose the Music effect above to use this.")

    def selected_mode(self):
        idx = self.mode_row.get_selected()
        return self.mode_ids[idx] if idx < len(self.mode_ids) else 0

    def select_mode(self, mode):
        self.mode_row.set_selected(self.mode_ids.index(mode) if mode in self.mode_ids else 0)

    def refresh_mode_list(self, current_mode):
        """Pulse brightness can only drive the whole card (per-zone streaming flickers), so
        don't offer Music for a single zone in that style unless the zone already uses it."""
        music = arc_rgb.MODES.index("music")
        ids = list(range(len(arc_rgb.MODES)))
        if (arc_rgb_music and hasattr(self, "style_row") and self.music_style() == "Pulse brightness"
                and self.single_channel() is not None and current_mode != music):
            ids.remove(music)
        if ids != self.mode_ids:
            self.mode_ids = ids
            self.mode_row.set_model(Gtk.StringList.new([arc_rgb.MODES[m].capitalize() for m in ids]))

    def update_speed_sensitivity(self):
        mode = self.selected_mode()
        self.speed_row.set_sensitive(mode in arc_rgb.SPEED_MODES)
        no_brightness = mode in arc_rgb.NO_BRIGHTNESS_MODES
        self.brightness_row.set_sensitive(not no_brightness)
        self.brightness_row.set_subtitle(arc_rgb.STATIC_BRIGHTNESS_NOTE if mode == 1 else "")

    def update_dirty(self):
        card_dirty = bool(self.settings) and (self.saved is None or self.saved != self.settings)
        dirty = card_dirty
        self.save_btn.set_sensitive(dirty)
        self.revert_btn.set_sensitive(dirty)
        if getattr(self, "music_running", False):
            self.banner.set_title("Music is running in the background: card changes apply when you press Save")
            self.banner.set_revealed(True)
        elif self.settings and self.saved is None:
            self.banner.set_title("Nothing saved yet — the card will use its own effect at boot")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def push(self, settings, hardware=False):
        if self.music_running and not hardware:
            # The background music service owns the card; edits apply when saved (it reloads)
            self.update_dirty()
            return
        try:
            arc_rgb.write_settings(self.led, settings, hardware)
        except (OSError, ValueError) as e:
            self.toasts.add_toast(Adw.Toast(title=f"Write failed: {e}"))
        self.update_dirty()

    # ---- handlers ----------------------------------------------------
    def on_channel_changed(self, *_):
        if self.settings:
            self.load_controls()

    def on_edit(self, *_):
        if self.loading or not self.settings:
            return
        self.update_speed_sensitivity()
        values = dict(mode=self.selected_mode(),
                      rgb=rgb_from_rgba(self.color_btn.get_rgba()),
                      speed=255 - int(self.speed.get_value()),
                      brightness=arc_rgb.percent_to_byte(self.brightness.get_value()))
        chans = self.selected_channels()
        for ch in chans:
            self.settings[ch].update(values)
        self.update_music_availability()
        self.schedule({ch: self.settings[ch] for ch in chans}, hardware=False)

    def on_hw_edit(self, *_):
        ch = self.single_channel()
        if self.loading or ch is None:
            return
        self.settings[ch]["leds"] = int(self.leds_row.get_value())
        self.settings[ch]["swap_rg"] = self.swap_row.get_active()
        self.schedule({ch: self.settings[ch]}, hardware=True)

    def schedule(self, settings, hardware):
        # Coalesce rapid slider/colour drags into one write burst
        if self.pending:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add(150, self.flush, settings, hardware)

    def flush(self, settings, hardware):
        self.pending = None
        self.push(settings, hardware)
        return GLib.SOURCE_REMOVE

    def on_preset(self, _btn, hexc):
        if self.selected_mode() == 0:
            self.select_mode(1)
        self.color_btn.set_rgba(rgba_from_rgb(arc_rgb.parse_color(hexc)))

    def set_mode_everywhere(self, mode):
        for s in self.settings.values():
            s["mode"] = mode
        self.push(self.settings)
        self.load_controls()

    def on_factory(self, *_):
        for s in self.settings.values():
            s.update({k: arc_rgb.FACTORY[k] for k in EFFECT_FIELDS})
        self.push(self.settings)
        self.load_controls()

    def on_save(self, *_):
        if self.settings:
            arc_rgb.save_config(self.settings)
            self.saved = copy_settings(self.settings)
        self.update_dirty()
        self.toasts.add_toast(Adw.Toast(title="Saved"))

    def on_revert(self, *_):
        if self.saved is not None and self.settings:
            self.settings = {ch: dict(s) for ch, s in copy_settings(self.saved).items() if ch in self.settings}
            self.push(self.settings, hardware=True)
            self.load_controls()
        self.update_dirty()

    # ---- music ----
    def music_style(self):
        return self.style_row.get_selected_item().get_string()

    def hz_rows(self, group, default):
        """From/To spin rows for a custom frequency range, shown only when 'Custom' is picked."""
        rows = []
        for title, value in zip(("From (Hz)", "To (Hz)"), default):
            row = Adw.SpinRow.new_with_range(20, arc_rgb_music.MAX_HZ, 10)
            row.set_title(title)
            row.set_value(value)
            row.set_visible(False)
            row.connect("notify::value", self.on_music_settings)
            group.add(row)
            rows.append(row)
        return rows

    def selected_band(self, combo, lo_row, hi_row):
        name = combo.get_selected_item().get_string()
        if name == "Same as Follow":
            return None
        if name == "Custom":
            return (int(lo_row.get_value()), int(hi_row.get_value()))
        return arc_rgb_music.BANDS[name]

    def update_music_sensitivity(self):
        pulse = self.music_style() == "Pulse brightness"
        accent = pulse and self.accent_switch.get_active()
        self.pulse_color_row.set_sensitive(pulse)
        self.accent_row.set_sensitive(pulse)
        self.accent_color.set_sensitive(self.accent_switch.get_active())
        self.hit_row.set_sensitive(accent)
        self.hit_band_row.set_sensitive(accent)
        custom = self.band_row.get_selected_item().get_string() == "Custom"
        hit_custom = self.hit_band_row.get_selected_item().get_string() == "Custom"
        for row in (self.band_lo, self.band_hi):
            row.set_visible(custom)
        for row in (self.hit_lo, self.hit_hi):
            row.set_visible(hit_custom)
            row.set_sensitive(accent)

    def on_music_toggled(self, row, _pspec):
        """React to music = run the background service now and at every login."""
        if self.prefs_loading:
            return
        self.music_react_pref = row.get_active()
        self.save_music_prefs()
        if row.get_active():
            self.save_card_for_music()
        action = ["enable", "--now"] if row.get_active() else ["disable", "--now"]
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "--user", *action, arc_rgb_music.SERVICE],
                           check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as e:
            self.toasts.add_toast(Adw.Toast(title=f"Could not {action[0]} the music service: {e}"))
        self.refresh_music_running()

    def music_latency(self):
        return self.latency_row.get_selected_item().get_string()

    def refresh_music_running(self):
        """Mirror the background service: switch state, card ownership and the level meter."""
        active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", arc_rgb_music.SERVICE],
                                check=False).returncode == 0
        self.music_running = active
        self.prefs_loading = True
        self.music_row.set_active(active)
        self.prefs_loading = False
        if active and not self.music_timer:
            self.music_timer = GLib.timeout_add(100, self.music_tick)
        self.banner_music()

    def banner_music(self):
        self.update_dirty()

    def save_card_for_music(self):
        """The service reads the saved lighting, so save what's on screen first."""
        if self.settings and (self.saved is None or self.saved != self.settings):
            self.on_save()

    def on_latency_changed(self, *_):
        self.save_music_prefs()   # the service reloads music.json

    def music_tick(self):
        """Show the service's live level; stop polling once it has stopped."""
        try:
            with open(arc_rgb_music.STATUS_PATH) as f:
                status = json.load(f)
        except (OSError, ValueError):
            status = {}
        fresh = status.get("running") and time.time() - status.get("time", 0) < 2
        self.level_bar.set_value(status.get("level", 0) if fresh else 0)
        self.stale_ticks = 0 if fresh else getattr(self, "stale_ticks", 0) + 1
        if self.stale_ticks == 30:
            self.refresh_music_running()   # stopped elsewhere, or idle with no Music zones?
        if not self.music_running:
            self.music_timer = None
            self.level_bar.set_value(0)
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def combo_select(row, text):
        model = row.get_model()
        for i in range(model.get_n_items()):
            if model.get_string(i) == text:
                row.set_selected(i)
                return

    def apply_music_prefs(self):
        """Restore the Music section as it was last left."""
        p = arc_rgb_music.load_prefs()
        self.prefs_loading = True
        try:
            for row, key in ((self.style_row, "style"), (self.latency_row, "latency"),
                             (self.band_row, "follow"), (self.hit_band_row, "hit_band")):
                if key in p:
                    self.combo_select(row, p[key])
            for rows, key in (((self.band_lo, self.band_hi), "follow_custom"),
                              ((self.hit_lo, self.hit_hi), "hit_custom")):
                if isinstance(p.get(key), list) and len(p[key]) == 2:
                    for row, value in zip(rows, p[key]):
                        row.set_value(value)
            for button, key in ((self.pulse_color, "colour"), (self.accent_color, "accent_colour")):
                if key in p:
                    button.set_rgba(rgba_from_rgb(arc_rgb.parse_color(p[key])))
            for scale, key in ((self.sens, "sensitivity"), (self.threshold, "threshold"), (self.hit, "hard_hit")):
                if key in p:
                    scale.set_value(p[key])
            self.accent_switch.set_active(bool(p.get("accent_on", False)))
            self.music_react_pref = bool(p.get("react", False))
        except (TypeError, ValueError):
            pass   # a damaged entry just keeps its default
        finally:
            self.prefs_loading = False

    def save_music_prefs(self):
        if self.prefs_loading or not hasattr(self, "level_bar"):
            return
        text = lambda row: row.get_selected_item().get_string()
        prefs = {
            "react": self.music_react_pref,
            "style": text(self.style_row),
            "latency": text(self.latency_row),
            "follow": text(self.band_row),
            "follow_custom": [int(self.band_lo.get_value()), int(self.band_hi.get_value())],
            "hit_band": text(self.hit_band_row),
            "hit_custom": [int(self.hit_lo.get_value()), int(self.hit_hi.get_value())],
            "colour": arc_rgb.hex_color(rgb_from_rgba(self.pulse_color.get_rgba())),
            "accent_on": self.accent_switch.get_active(),
            "accent_colour": arc_rgb.hex_color(rgb_from_rgba(self.accent_color.get_rgba())),
            "sensitivity": int(self.sens.get_value()),
            "threshold": int(self.threshold.get_value()),
            "hard_hit": int(self.hit.get_value()),
        }
        try:
            arc_rgb_music.save_prefs(prefs)
        except OSError as e:
            self.toasts.add_toast(Adw.Toast(title=f"Could not save music settings: {e}"))

    def on_music_settings(self, *_):
        if not hasattr(self, "level_bar") or self.prefs_loading:
            return  # still building or restoring the Music section
        self.save_music_prefs()
        self.update_music_sensitivity()
        style_changed = getattr(self, "_music_style", None) not in (None, self.music_style())
        self._music_style = self.music_style()
        if style_changed and self.settings:
            self.load_controls()   # the Effect list depends on the style
        self._music_style = self.music_style()

    def stop_music(self):
        """Stop the level meter (the background service keeps running on its own)."""
        if self.music_timer:
            GLib.source_remove(self.music_timer)
            self.music_timer = None
        self.level_bar.set_value(0)

    def on_service_toggled(self, row, _pspec):
        if self.loading:
            return
        enable = row.get_active()
        if enable and (self.saved is None or self.saved != self.settings):
            self.on_save()  # the service applies the saved setting, so save what's on screen
        row.set_sensitive(False)
        argv = ["pkexec", CLI, "service", "enable" if enable else "disable",
                "--user", getpass.getuser()]
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as e:
            self.toasts.add_toast(Adw.Toast(title=f"Could not run pkexec: {e.message}"))
            row.set_sensitive(True)
            self.refresh_service_state()
            return
        proc.communicate_utf8_async(None, None, self.on_service_done, enable)

    def on_service_done(self, proc, result, enable):
        _ok, _out, err = proc.communicate_utf8_finish(result)
        self.service_row.set_sensitive(True)
        self.refresh_service_state()
        if proc.get_exit_status() == 0:
            msg = "Lighting will be re-applied at boot" if enable else "Boot service turned off"
        elif proc.get_exit_status() in (126, 127):  # pkexec: dialog dismissed / not authorised
            msg = "Cancelled"
        else:
            msg = f"Failed: {(err or '').strip().splitlines()[-1] if err else 'unknown error'}"
        self.toasts.add_toast(Adw.Toast(title=msg))

    def on_store(self, *_):
        dlg = Adw.AlertDialog(
            heading="Store on the card?",
            body="Writes the current lighting into the LED controller's flash, the same way "
                 "Polychrome saves. The card will then show it from power-on, even without "
                 "this app or the boot service. Avoid doing this many times in a row.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("store", "Store")
        dlg.set_response_appearance("store", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", self.on_store_response)
        dlg.present(self)

    def on_store_response(self, _dlg, response):
        if response != "store":
            return
        try:
            self.led.store()
        except OSError as e:
            self.toasts.add_toast(Adw.Toast(title=f"Store failed: {e}"))
            return
        self.toasts.add_toast(Adw.Toast(title="Stored on the card"))


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=arc_rgb.APP_ID)

    def do_activate(self):
        win = self.get_active_window() or Window(self)
        win.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
