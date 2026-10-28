# Sound Blaster GS5 lighting protocol

Reverse-engineered from USB captures of Creative App 1.24.5.0 (Windows) talking to a GS5 on firmware
1.25.251009.1430, then confirmed by sending the commands from Windows (HID API) and Linux (hidraw).
Unofficial; Creative may change it in a future firmware.

## Device

| | |
|---|---|
| USB ID | `041e:329d` (Creative Technology Ltd, "Sound Blaster GS5"), bcdDevice 25.00 |
| Interface | HID, vendor usage page `0xFF01`, **report ID 5**, 63-byte IN/OUT reports (EP 0x82 IN, EP 0x03 OUT) |
| Same interface | report ID 2 = consumer-control keys (volume/media buttons on the bar) |
| Linux | one `/dev/hidraw*` node; its report descriptor starts `06 01 ff` |

The rest of the GS5 (USB audio class) needs no driver beyond the kernel's `snd-usb-audio`.

## Framing

Every report is 64 bytes including the report ID, zero-padded:

```
05 6a <op> <len lo> <len hi> <data ...>
```

- `<len>` is the length of `<data>`, uint16 little-endian.
- Lighting uses **op `3a`**, with `data = <cmd> <args...>`.
- A **set** is acknowledged with `05 6a 02 03 00 3a 00 <cmd> <status>`, where status `00` = OK.
- A **get** is answered with `05 6a 3a <len lo> <len hi> <cmd> <value...>`.
- After `29` (set mode) and `2d` (activate slot) the GS5 also pushes unsolicited `2a`/`2c` reports with the slot's mode
  and settings, before or after the ACK. Skip any report that isn't the reply you're waiting for.

Other frames seen:

| Frame | Meaning |
|---|---|
| `05 6a 09 01 00 00` | hello; the reply contains the VID/PID (`1e 04 9d 32`) |
| `05 6a 2c 01 00 01` | sent by the app on connect and repeatedly while connected; purpose unknown, harmless |
| `05 6a 42 ..`, `05 6a 4a ..`, `05 5a 6c ..` | app polling of other settings; not needed for lighting |

## Pacing: wait for every reply

Send one command, **wait for its reply**, then send the next. Commands written back to back are accepted by the OS
but ignored by the GS5 (once that left the strip dark while on/brightness/mode still read back fine).

Measured over ~600 replied-to commands: reply in **17 ms on average, 113 ms at worst**. Animating at 25 frames/s
(one `2b` colour frame every 40 ms) is smooth; ~50 frames/s is the ceiling.

**After `29` (mode change) wait ~300 ms** and read away the settings it pushes. Commands sent inside that window can
be lost; in testing a slot was left in mode `00` and only the centre of the strip lit. Read the slot's mode back
(`2a`) to confirm.

## Global lighting

| Command | Meaning |
|---|---|
| `25 <0/1>` | lighting off / on |
| `26` (get) | reply `26 01` = on |
| `27 <v>` | brightness, `00`-`ff` (`80` = 50 %) |
| `28` (get) | reply `28 <v>` |

**A mode change (`29`) resets the brightness to `b0` (69 %).** Set it again afterwards if you want something else.

## Effect slots

The GS5 keeps **6 effect slots** (1-6). Each holds one mode with its own settings, and one slot is active.

| Command | Meaning |
|---|---|
| `29 <slot> <mode>` | put a mode in a slot; **resets the slot's colours/speed/direction to the mode's defaults** |
| `2a <slot>` (get) | reply `2a <slot> <mode>` |
| `2d <slot>` | activate a slot |
| `2e` (get) | reply `2e <slot>` (the active slot) |
| `2b <slot> <param> <value...>` | set a slot parameter (below); stored in the slot |
| `2c <slot> <param>` (get) | read a slot parameter; same layout as `2b` |
| `21` (get) | list of supported mode ids (not needed) |
| `22 <mode>` (get) | a mode's parameter schema (not needed) |

### Modes

Names are the ones Creative App shows.

| Mode | Id | Colours | Speed | Direction | Default after `29` |
|---|---|---|---|---|---|
| Static | `03` | 7 (one per segment) | | | 7-colour rainbow |
| Cycle | `01` | | yes | | |
| Wave | `04` | 7 | yes | yes | `FF0000 FF00FF 0000FF 00A0FF 00FFFF 00FF00 FFFF00`, 4000 ms, left→right |
| Aurora | `08` | | yes | | 4000 ms |
| Glowing | `09` | 7 | yes | | 7 × `00E5E5`, 4000 ms |
| Peak Meter | `0a` | 5 | | | `FE0000 F76600 FEF600 5CFE1B 00FEFE`; follows the audio level |
| Chasers | `0b` | 1 | yes | yes | `00E5E5`, 4000 ms, bouncing |

Id `07` appears in the supported-modes list but not in the app; untested.
The GS5 runs every effect itself, so nothing has to keep sending frames.

### Slot parameters (`2b` / `2c`)

| Param | Value |
|---|---|
| `01` colours | `01`, then N × `ff BB GG RR` (**blue, green, red order**). N depends on the mode (table above). For static, the 7 colours are segments 1-7 and **segment 1 is the leftmost**. `ff 00 00 00` turns a segment off. |
| `03` speed | uint16 LE, milliseconds per cycle. Snaps to the app's steps **250 / 375 / 750 / 1333 / 2500 / 4000 / 6000** (250 = fastest). |
| `04` direction | `01 00` left→right, `02 00` right→left, `01 01` bouncing |

## Examples

Whole strip red (static, slot 1):

```
05 6a 3a 20 00 2b 01 01 01  ff 00 00 ff  ff 00 00 ff  ff 00 00 ff  ff 00 00 ff  ff 00 00 ff  ff 00 00 ff  ff 00 00 ff
```

A reliable start-up sequence (each step waits for its reply):

```
05 6a 09 01 00 00            hello
05 6a 2c 01 00 01            (what the app sends on connect)
05 6a 3a 03 00 29 01 03      slot 1 = static          then wait 300 ms, check 2a 01 reads 03
05 6a 3a 02 00 2d 01         activate slot 1          then wait 300 ms
05 6a 3a 02 00 25 01         lighting on
05 6a 3a 02 00 27 ff         brightness 100 %
05 6a 3a 20 00 2b 01 01 01 + 7 × ff B G R            colours
```

After that, changing the colour is a single `2b` frame.

## Open questions

- Whether colours survive a power cycle of the GS5 (they survive the host reconnecting).
- Mode `07`; the purpose of the `2c 01` frame and of the `42`/`4a` polling.
- Peak Meter: which of the 5 colours maps to which level (the default order suggests colour 1 = loudest).
