# gs5-rgb

Control the RGB light strip of the **Creative Sound Blaster GS5** soundbar from Linux, over USB.

Creative only offers its own app for the GS5's lighting, and tools like OpenRGB don't support it. This is a small,
dependency-free Python library and command-line tool that speaks the soundbar's lighting protocol directly. The
protocol was reverse-engineered from Creative App's USB traffic; the full reference is in [PROTOCOL.md](PROTOCOL.md).

- Solid colours, for the whole strip or each of the 7 segments
- Brightness and on/off
- The soundbar's built-in effects (cycle, wave, aurora, glowing, peak meter, chasers) with speed, direction and colours
- Status: on/off, brightness, the mode in each of the 6 effect slots
- An example service that colours the strip by GPU load

Tested with GS5 firmware 1.25.251009.1430 on Linux 6.x/7.x. Connect the GS5 by **USB** (lighting control doesn't
go over Bluetooth with this tool).

## Install

Needs Python 3.8+ and nothing else.

```sh
git clone https://github.com/JRiley2025/gs5-rgb.git
cd gs5-rgb
sudo cp udev/70-gs5-hidraw.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

The udev rule lets the logged-in user open the soundbar's control node (`/dev/hidraw*`) without root. Unplug and
replug the GS5 if `gs5rgb.py status` says permission denied.

## Command line

```sh
./gs5rgb.py status
./gs5rgb.py colour ff0000                                         # whole strip red
./gs5rgb.py colour ff0000 ff7f00 ffff00 00ff00 0000ff 4b0082 ffffff  # one colour per segment, left to right
./gs5rgb.py colour 000000 000000 000000 ffffff 000000 000000 000000  # only the centre segment
./gs5rgb.py brightness 40
./gs5rgb.py off
./gs5rgb.py on
./gs5rgb.py effect cycle --speed 750
./gs5rgb.py effect chasers --colours 00ff00 --speed 375 --direction bounce
./gs5rgb.py effect glowing --colours ff0000 --speed 1333
./gs5rgb.py effect peak --colours ff0000 ff7f00 ffff00 00ff00 00ff00   # VU meter that follows the audio
```

`colour` and `color` both work. Speeds are milliseconds per cycle and snap to the soundbar's steps
(250, 375, 750, 1333, 2500, 4000, 6000). Directions are `ltr`, `rtl` and `bounce`.

`status` looks like this:

```
lighting:   on, brightness 100%
active slot: 1
  slot 1: static  <- active
  slot 2: aurora
  ...
```

## Library

```python
from gs5rgb import GS5

with GS5.open() as gs5:                # auto-detects the device; GS5.open("/dev/hidraw4") also works
    gs5.static("ff0000")               # sets slot 1 to static if needed, then the colour
    gs5.static(["00ff00"] * 3 + ["000000"] * 4)
    gs5.brightness(60)
    gs5.effect("wave", speed=2500, direction="rtl")
    print(gs5.status())
```

For animations, call `gs5.static(colours, setup=False)` after the first call: that sends just the colour frame
(one round trip, about 15 ms). Every method waits for the soundbar's reply, which the GS5 requires.

## Example: strip colour follows GPU load

[`examples/gpu-load.py`](examples/gpu-load.py) turns the strip green, yellow or red by GPU load (AMD GPUs via
`gpu_busy_percent`; easy to adapt). It reconnects when the soundbar is unplugged, and a systemd user unit is
included:

```sh
mkdir -p ~/.local/share/gs5-rgb && cp gs5rgb.py examples/gpu-load.py ~/.local/share/gs5-rgb/
cp examples/gs5-gpu-load.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now gs5-gpu-load
```

## Notes

- Close Creative App (or don't run it on the same machine): both would be driving the strip.
- Changing effect mode resets the soundbar's brightness to 69 %; the library puts your brightness back.
- The GS5 stores the colours and effect per slot, so the last setting stays after the tool exits.

## Contributing

Captures from other firmware versions, the unknown mode `07`, and whether settings survive a power cycle are all
open questions; see the end of [PROTOCOL.md](PROTOCOL.md). Issues and pull requests are welcome.

## Disclaimer

Not affiliated with or endorsed by Creative Technology Ltd. "Sound Blaster" is a trademark of Creative Technology
Ltd. This talks to the soundbar with the same commands Creative App sends, but use it at your own risk.

## Licence

MIT, see [LICENSE](LICENSE).
