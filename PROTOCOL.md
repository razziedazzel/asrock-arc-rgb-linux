# ASRock Arc LED controller — protocol reference

Everything needed to drive the RGB controller on ASRock Intel Arc cards from your own
software (another language, OpenRGB, a different OS). `arc_rgb.py` is a complete,
dependency-free reference implementation of everything marked **tested** below.

Legend: **tested** = confirmed on a B580 Steel Legend · **firmware** = read from the
controller firmware, not yet exercised on hardware.

> The controller sits on an I2C bus **inside the card**, driven by the GPU. There is no
> external connector for it; you reach it through the GPU's I2C adapter from the host OS.

---

## 1. Finding the controller

| Item | Value |
|---|---|
| Controller | ARM Cortex-M microcontroller (very likely Nuvoton NuMicro; exact part unconfirmed) |
| I2C address | **`0x36`** (7-bit) |
| Bus | The GPU's own DesignWare I2C adapter |
| Card match | PCI subsystem vendor `0x1849` (ASRock) and subsystem device below |

| Subsystem device | Card | Status |
|---|---|---|
| `0x6020` | Arc B580 Steel Legend | tested |
| `0x6001`, `0x6010` | Arc A770 variants | listed by ASRock software; untested |

**Linux** (`xe` driver): the card exposes an adapter named *Synopsys DesignWare I2C adapter*
whose sysfs path runs through the GPU, e.g.
`/sys/devices/…/0000:07:00.0/i2c_designware.1792/i2c-12`. Open `/dev/i2c-N` (needs the
`i2c-dev` module) and use `I2C_RDWR`. No kernel driver binds to `0x36`.

**Windows**: ASRock's driver exposes the controller as the SPB device
`{5CDD8C40-2EC8-4363-9629-C43D92197B68}\ADR_0X0036` ("ASRock SPB Device (NF I2C)").
`WriteFile` on it is a raw I2C write and `ReadFile` a raw I2C read, so the packets below
apply unchanged.

## 2. Transactions

- **Every command is one 12-byte I2C write**, zero-padded. The controller's receiver
  completes a command after exactly 12 bytes and treats any further bytes in the same
  write as the start of a **new** command, so never write more than 12 bytes at once.
  (**firmware**, consistent with the §4.3 test)
- **Queries** are a 12-byte write followed by a *separate* 12-byte read (stop between them,
  not a repeated-start combined transfer). **tested**
- Reply layout: bytes 0–2 are `00`, **byte 3 echoes the argument** (channel or sub-command),
  **data starts at byte 4**. The reply buffer is not cleared between queries, so bytes past
  the documented length can hold stale data. **tested**
- Timing that works reliably: wait **≥ 20 ms after each write** and a further **~30 ms**
  before reading a reply. **tested**
- Bytes 0–1 of every packet are a little-endian 16-bit command, so **byte 1 must be `00`**.

## 3. Channels (zones)

Ten channel slots exist (0–9). A card reports which ones are wired (§4.4, sub 1).

| Channel | B580 Steel Legend | LEDs |
|---|---|---|
| 3 | ARGB header on the card (for an external strip) | 80 |
| 6 | "Steel Legend" logo | 26 |
| 7 | Fans | 26 |

LEDs are WS2812-style and individually driven by the firmware, but the protocol only sets
values **per channel**; per-LED patterns come from the built-in effects.

## 4. Commands

### 4.1 Set channel — `0x10`  **tested**

```
10 00 CH MODE R G B SPEED BRIGHT SYNC MUSIC 00
```

| Byte | Field | Values |
|---|---|---|
| 2 | `CH` | channel 0–15 |
| 3 | `MODE` | see §5 |
| 4–6 | `R G B` | 0–255 each |
| 7 | `SPEED` | 0–255, **higher = slower** (effect steps every `SPEED/10 + 5` frames). Factory `0x80` |
| 8 | `BRIGHT` | 0–255, scales every LED's colour. ASRock's app always sends `0xFF` |
| 9 | `SYNC` | `0` = this channel only. `1` = firmware applies the effect to all channels (**firmware**) |
| 10 | `MUSIC` | audio level for mode 6 (Music) only; host must keep sending it (**firmware**, meaning of scale unknown) |

Takes effect immediately; lost on power-off unless stored (§4.5).

### 4.2 Get channel — `0x11`  **tested**

```
write: 11 00 CH 00 00 00 00 00 00 00 00 00
read : 00 00 00 CH MODE R G B SPEED BRIGHT SYNC ..
```

### 4.3 Special modes on `0x10`  **firmware**

| MODE | Meaning |
|---|---|
| `0xE1` (also `0x11`) | Same colour/speed/brightness on **all** channels at once (bytes 4–8). Rendered as static. |
| `0xE2` | Intended as per-channel colours (`10 00 00 E2` + 16 × `R G B`), but **not usable over I2C**: the handler reads 52 bytes from a 12-byte receive buffer, so only channels 0–1 get real data and the rest get stale memory. **Tested**: on a B580 the logo showed a dim green from leftover bytes and the fans went dark. |

### 4.4 Get / set settings — `0x14` / `0x15`

```
get: 14 00 SUB 00 … (then read 12)        set: 15 00 SUB DATA…
```

| SUB | Get reply (from byte 4) | Set data (from byte 3) | Status |
|---|---|---|---|
| 1 | present-channel mask, u16 LE | — (do not write) | get **tested** |
| 2 | LED count for channels 0–7, 8 bytes | 8 bytes, channels 0–7 | **tested** (see warning) |
| 3 | enabled-channel mask, u16 LE | mask, u16 LE | get **tested** |
| 7 | red/green swap mask, u16 LE (bit set = channel's LEDs are GRB) | mask, u16 LE | **tested** |
| 6, 8, 9, 10 | unknown | unknown (sub 10 toggles a GPIO with 300 ms delays, likely LED power) | avoid |

- B580 Steel Legend factory values: present `0x00C8` (ch 3, 6, 7), enabled `0x01C9`,
  LED counts `01 01 50 50 01 01 1A 1A`, swap mask `0x01FF`.
- **Never raise an LED count above its factory value**: the firmware does not bounds-check it,
  and a larger count could overrun its per-channel LED buffer. Lowering is safe (LEDs past
  the count are sent black).

### 4.5 Store to flash — `0x12`  (firmware-verified routine)

```
12 00 00 00 00 00 00 00 00 00 00 00
```

Erases the controller's settings page and writes its current settings (a 323-byte block),
then verifies. The card shows the stored lighting from power-on. If the block is ever
corrupt the firmware falls back to factory settings. Flash wears with use, so don't send
it in a loop.

### 4.6 Never send

| Packet | Effect |
|---|---|
| `20 00 01 …` | Jumps to the bootloader; the controller stops running the LED firmware |
| Commands `0xA1`–`0xD0` | Bootloader / firmware erase and programming |
| `15 00 01 …` | Rewrites the present-channel mask |
| Anything longer than 12 bytes | Split by the controller into several commands (see §2) |

## 5. Modes

| # | Name | Uses speed | # | Name | Uses speed |
|---|---|---|---|---|---|
| 0 | Off | | 9 | Stack | ✓ |
| 1 | Static (**tested**) | | 10 | Cram | ✓ |
| 2 | Breathing (**tested**) | ✓ | 11 | Scan | ✓ |
| 3 | Strobe (**tested**) | ✓ | 12 | Neon | ✓ |
| 4 | Cycling | ✓ | 13 | Water | ✓ |
| 5 | Random | ✓ | 14 | Rainbow | ✓ |
| 6 | Music (host sends level) | | 15 | Rainbow2 (factory default) | ✓ |
| 7 | Wave | ✓ | 16 | GlowStream | |
| 8 | Spring | ✓ | 17 | LuminousTide (ASRock's app substitutes 14 on these cards) | |

## 6. Examples

Linux shell (`i2c-tools`; replace `12` with your bus number):

```sh
# Which channels exist? -> mask in reply bytes 4-5
i2ctransfer -y 12 w12@0x36 0x14 0 1 0 0 0 0 0 0 0 0 0 ; i2ctransfer -y 12 r12@0x36

# Logo (ch 6): static red, full brightness
i2ctransfer -y 12 w12@0x36 0x10 0 6 0x01 0xff 0 0 0x80 0xff 0 0 0

# Fans (ch 7): breathing green at 50 % brightness, faster than default
i2ctransfer -y 12 w12@0x36 0x10 0 7 0x02 0 0xff 0x40 0x40 0x80 0 0 0

# Read channel 7 back
i2ctransfer -y 12 w12@0x36 0x11 0 7 0 0 0 0 0 0 0 0 0 ; i2ctransfer -y 12 r12@0x36

# Restore the factory effect on a channel
i2ctransfer -y 12 w12@0x36 0x10 0 7 0x0f 0 0 0xff 0x80 0xff 0 0 0
```

C (Linux `I2C_RDWR`):

```c
#include <fcntl.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int xfer(int fd, unsigned char *buf, int read) {
    struct i2c_msg m = { .addr = 0x36, .flags = read ? I2C_M_RD : 0, .len = 12, .buf = buf };
    struct i2c_rdwr_ioctl_data d = { .msgs = &m, .nmsgs = 1 };
    int r = ioctl(fd, I2C_RDWR, &d);
    usleep(read ? 0 : 20000);
    return r;
}

int set_channel(int fd, int ch, int mode, int r, int g, int b, int speed, int bright) {
    unsigned char p[12] = { 0x10, 0x00, ch, mode, r, g, b, speed, bright, 0, 0, 0 };
    return xfer(fd, p, 0);
}

int get_channel(int fd, int ch, unsigned char reply[12]) {
    unsigned char q[12] = { 0x11, 0x00, ch };
    if (xfer(fd, q, 0) < 0) return -1;
    usleep(30000);
    memset(reply, 0, 12);
    return xfer(fd, reply, 1);   /* reply[4..10] = mode R G B speed bright sync */
}
```

## 7. Open questions

- Settings sub-commands 6, 8, 9 and 10, and the scale of the Music-mode level byte.
- Whether the A770 boards use the same channel numbers.
- `SYNC = 1` and `0xE1` have not yet been exercised on hardware.

Corrections and reports from other ASRock Arc cards are welcome via issues.
