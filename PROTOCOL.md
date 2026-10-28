# ASRock Arc B580 Steel Legend RGB — I2C protocol (from Polychrome RGB v2.0.214)

Source: `AsrPolychromeRGB.exe` class `TAsrIntelVgaLedCtrl` (0x4ff9c4–0x5006ec),
transport `TIntelSpbIO` -> `ASRISP\asrspb.dll` -> `AsrNfDrv000.sys` (SPB device `ADR_0X0036`).

## Card detection
PCI display device (config 0x0B == 0x03) whose subsystem dword (config 0x2C) is one of:
`0x60011849`, `0x60101849` (A770), `0x60201849` (B580 Steel Legend). Ours: 1849:6020.

## Transport
- Linux: `/dev/i2c-12` (xe "Synopsys DesignWare I2C adapter"), 7-bit address **0x36**.
- Every command is a plain 12-byte I2C write (zero-padded).
- Queries: 12-byte write, then a separate 12-byte read (`AsrSpb_Write` then `AsrSpb_Read`,
  not a combined WriteRead). Linux: `i2ctransfer -y 12 w12@0x36 ...` then `i2ctransfer -y 12 r12@0x36`.
- Reply payload starts at byte 4.

## Commands  (`[cmd, 0x00, arg, data...]`, padded to 12)
| Packet | Dir | Meaning |
|---|---|---|
| `10 00 ch m R G B spd 5 6 7` | set | Write channel `ch` record bytes 0–7 |
| `11 00 ch` | get | Read channel `ch` record: reply[4..8]=rec[0..4], reply[10]=rec[6], reply[11]=rec[7] |
| `12` | set | No args. Called once from the "apply/save" path — likely commit to MCU flash. Use sparingly. |
| `14 00 01` | get | Present-channel mask: reply[4] \| reply[5]<<8 (10 bits) |
| `14 00 02` | get | 8 per-channel bytes (rec[8]) at reply[4..11] |
| `14 00 03` | get | Enable mask: reply[4] \| reply[5]<<8 -> rec[9] per channel |
| `15 00 02 b0..b7` | set | Write rec[8] for channels 0–7 |
| `15 00 03 lo hi` | set | Write enable mask (bit n = channel n on) |

## Per-channel record (10 bytes, 10 channels)
| Byte | Meaning |
|---|---|
| 0 | mode (app rewrites mode 0x11 -> 0x0E on load) |
| 1,2,3 | R, G, B |
| 4 | speed: frame divider, step every speed/10+5 frames — **higher = slower** |
| 5 | **brightness** 0–255 (every LED colour scaled by it; app always sends 0xFF). Read back at reply[9] |
| 6 | "sync all" flag; when set the app copies mode/colour/speed to every channel |
| 7 | forced 0x00 by app |
| 8 | per-channel byte via 0x14/0x15 sub 2 (meaning unknown) |
| 9 | channel enabled (0x14/0x15 sub 3 mask) |

App selects channel 6 by default after loading.

## Observed on this card (2026-10-27, Linux, i2c-12)
- Replies echo the arg at byte 3; data from byte 4. Reply buffer is not cleared, so trailing bytes may be stale.
- `14 00 01` -> mask 0x00C8: channels **3, 6, 7** present.
- `14 00 03` -> enable mask 0x01C9 (channels 0,3,6,7,8).
- `14 00 02` -> rec[8] = 01 01 50 50 01 01 1A 1A (ch0..7).
- Factory record on every channel: mode 0x0F, RGB 00 00 FF, speed 0x80, rec5 0xFF.
- `10 00 06 01 FF 00 00 80 FF 00 00` accepted; read-back shows mode 01, RGB FF 00 00.
- Restore factory: `i2ctransfer -y 12 w12@0x36 0x10 0 <ch> 0x0f 0 0 0xff 0x80 0xff 0 0 0` for ch 3,6,7.

## Modes (from Themes/*/theme_v1.xml `ModeItemSel`; modes 1 static and 2 breathing confirmed on hardware)
0 Off, 1 Static, 2 Breathing, 3 Strobe, 4 Cycling, 5 Random, 6 Music, 7 Wave, 8 Spring,
9 Stack, 10 Cram, 11 Scan, 12 Neon, 13 Water, 14 Rainbow, 15 Rainbow2 (factory default),
16 GlowStream, 17 LuminousTide (app remaps 17 -> 14 on this card).

## Controller firmware (APROM_VGA_250924_1B54.bin, 14.5 KB, unencrypted ARM Cortex-M, base 0)
Command handler is `FUN_00002604`; packet word 0 (LE u16) is the command, so byte 1 must be 0.
- `10 00 ch mode R G B spd b8 b9`: ch < 16. Mode 6 (music) also takes byte 10.
  - mode `0xE1`/`0x11`: applies to **all** channels at once.
  - mode `0xE2`: **direct per-zone** — 52-byte packet `10 00 xx E2` + 16×(R,G,B); every channel
    gets its own static colour in one write. Effect engine renders 0x11/0xE1/0xE2 as static.
- `12`: `FUN_00002f30` (save path; not analysed further — treat as flash write).
- `14 00 sub` gets: 1 present mask, 2 LED counts ×8, 3 enable mask, 6 flag bit, 7 R/G-swap mask, 8 dword, 9/10 bytes.
- sub 7 mask: bit n set = channel n swaps R and G (GRB strips). This card: 0x01FF (all onboard zones swapped).
- `12` (`FUN_00002f30`): erases the data-flash page and writes the 323-byte settings block (same size as
  `ARC_B580_SL_250317.dat`), then verifies. Firmware falls back to defaults on a bad block.
- `15 00 sub` sets: 1 present mask, 2 LED counts ×8, 3 enable mask, 7 mask, 8 three bytes,
  10 (byte3≠0 / =0 toggles GPIO 0x800 with 300 ms delays — likely LED power).
- **rec[8] = LED count per channel** (sub-2 store at 0x20000723+ch is the length check in the
  WS2812-style output loop `FUN_000023dc`; LEDs past the count are sent black).
  This card: ch3 = ARGB header (80, nothing attached), ch6 = "Steel Legend" logo (26), ch7 = fans (26).
- **Dangerous — never send:** `20 00 01` (jumps to bootloader, MCU stops responding),
  `0xA1`–`0xD0` (ISP / flash erase / program).
- No per-LED colour command exists; LEDs are addressable in hardware but only the firmware's
  own effects (rainbow etc.) vary colour along a strip.

## Not yet known
- Meaning of `14/15` subs 6, 8, 9, 10; music-mode level byte (mode 6, byte 10).
