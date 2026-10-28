#!/usr/bin/env python3
"""GTK 4 front end for arc_rgb: live-edit the card's lighting and save it for boot."""
import getpass
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import arc_rgb  # noqa: E402

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

        swatches = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=8,
                               min_children_per_line=4, homogeneous=True,
                               margin_top=6, margin_bottom=6)
        for name, hexc in PRESETS:
            b = Gtk.Button(tooltip_text=name, width_request=36, height_request=36)
            b.add_css_class("swatch")
            b.add_css_class(f"swatch-{hexc}")
            b.connect("clicked", self.on_preset, hexc)
            swatches.append(b)
        group.add(swatches)

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

    def action_row(self, title, handler, subtitle=None):
        # Plain activatable row rather than Adw.ButtonRow, which needs libadwaita 1.6
        row = Adw.ActionRow(title=title, activatable=True)
        if subtitle:
            row.set_subtitle(subtitle)
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", handler)
        return row

    def slider_row(self, title, lo, hi, unit):
        row = Adw.ActionRow(title=title)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        scale.set_hexpand(True)
        scale.set_size_request(200, -1)
        if unit:
            scale.set_draw_value(True)
            scale.set_format_value_func(lambda _s, v: f"{v:.0f}{unit}")
        else:
            scale.set_draw_value(False)
        scale.connect("value-changed", self.on_edit)
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
        self.mode_row.set_selected(min(s["mode"], len(arc_rgb.MODES) - 1))
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

    def update_speed_sensitivity(self):
        self.speed_row.set_sensitive(self.mode_row.get_selected() in arc_rgb.SPEED_MODES)

    def update_dirty(self):
        dirty = self.saved is None or self.saved != self.settings
        self.save_btn.set_sensitive(dirty)
        self.revert_btn.set_sensitive(self.saved is not None and dirty)
        if self.saved is None:
            self.banner.set_title("Nothing saved yet — the card will use its own effect at boot")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def push(self, settings, hardware=False):
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
        values = dict(mode=self.mode_row.get_selected(),
                      rgb=rgb_from_rgba(self.color_btn.get_rgba()),
                      speed=255 - int(self.speed.get_value()),
                      brightness=arc_rgb.percent_to_byte(self.brightness.get_value()))
        chans = self.selected_channels()
        for ch in chans:
            self.settings[ch].update(values)
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
        if self.mode_row.get_selected() == 0:
            self.mode_row.set_selected(1)
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
        arc_rgb.save_config(self.settings)
        self.saved = copy_settings(self.settings)
        self.update_dirty()
        self.toasts.add_toast(Adw.Toast(title="Saved"))

    def on_revert(self, *_):
        self.settings = {ch: dict(s) for ch, s in copy_settings(self.saved).items() if ch in self.settings}
        self.push(self.settings, hardware=True)
        self.load_controls()

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
