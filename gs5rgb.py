#!/usr/bin/env python3
"""Control the RGB lighting of a Creative Sound Blaster GS5 soundbar on Linux.

The GS5 (USB 041e:329d) has a vendor HID interface (usage page 0xFF01, report ID 5) that Creative App uses to set
the lighting. This module speaks that protocol directly through /dev/hidraw*. No dependencies beyond the Python
standard library. See PROTOCOL.md for the reverse-engineered command reference.

Library use:

    from gs5rgb import GS5
    with GS5.open() as gs5:
        gs5.static("ff0000")                 # whole strip red
        gs5.static(["ff0000"] * 3 + ["000000"] * 4)
        gs5.brightness(50)
        gs5.effect("chasers", colours=["00ff00"], speed=375, direction="bounce")

Command line: run `gs5rgb.py --help`.
"""
import argparse
import glob
import os
import select
import sys
import time

__version__ = "0.1.0"

VIDPID = "0000041E:0000329D"
SEGMENTS = 7
SLOTS = 6

# mode ids as named in Creative App, and how many colours each one takes
MODES = {"static": 0x03, "cycle": 0x01, "wave": 0x04, "aurora": 0x08, "glowing": 0x09, "peak": 0x0A, "chasers": 0x0B}
MODE_NAMES = {v: k for k, v in MODES.items()}
MODE_COLOURS = {"static": 7, "wave": 7, "glowing": 7, "peak": 5, "chasers": 1, "aurora": 0, "cycle": 0}
HAS_SPEED = {"cycle", "wave", "aurora", "glowing", "chasers"}
HAS_DIRECTION = {"wave", "chasers"}
DIRECTIONS = {"ltr": (0x01, 0x00), "rtl": (0x02, 0x00), "bounce": (0x01, 0x01)}
# the GS5 snaps speeds (ms per cycle) to the app's slider steps
SPEEDS = (250, 375, 750, 1333, 2500, 4000, 6000)

OP_LIGHTING = 0x3A
ACK = bytes([0x05, 0x6A, 0x02, 0x03, 0x00, OP_LIGHTING, 0x00])   # + <cmd> <status>
GET = bytes([0x05, 0x6A, OP_LIGHTING])                          # + <len lo> <len hi> <cmd> <value...>


class GS5Error(OSError):
    pass


def find_device():
    """Path of the GS5's vendor hidraw node, or None."""
    for h in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            if VIDPID not in open(os.path.join(h, "device/uevent")).read().upper():
                continue
            # the vendor interface's report descriptor starts with Usage Page (0xFF01)
            if open(os.path.join(h, "device/report_descriptor"), "rb").read(3) == b"\x06\x01\xff":
                return "/dev/" + os.path.basename(h)
        except OSError:
            pass
    return None


def parse_colour(c):
    c = c.strip().lstrip("#")
    if len(c) != 6:
        raise ValueError(f"colour must be RRGGBB, got {c!r}")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _colour_bytes(colours):
    out = []
    for c in colours:
        r, g, b = parse_colour(c) if isinstance(c, str) else c
        out += [0xFF, b, g, r]           # each colour is sent as ff B G R
    return out


class GS5:
    """One open GS5. Every command waits for the soundbar's reply before returning: the GS5 silently ignores
    commands written back to back."""

    def __init__(self, path, timeout=0.3):
        self.path = path
        self.timeout = timeout
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self._drain(0.05)
        self._raw(0x09, [0x00], expect=bytes([0x05, 0x6A, 0x09]))   # hello
        self._raw(0x2C, [0x01], expect=bytes([0x05, 0x6A, 0x2C]))   # Creative App sends this on connect

    @classmethod
    def open(cls, path=None, **kw):
        path = path or find_device()
        if not path:
            raise GS5Error("no Sound Blaster GS5 found (is it plugged in by USB, and can you read /dev/hidraw*?)")
        return cls(path, **kw)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- transport -----------------------------------------------------------------------------------------
    def _read(self, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        return os.read(self.fd, 64) if r else None

    def _drain(self, secs):
        end = time.monotonic() + secs
        while (left := end - time.monotonic()) > 0 and self._read(left) is not None:
            pass

    def _raw(self, op, data, expect=None, timeout=None):
        """Send 05 6a <op> <len16le> <data> and return the first reply starting with `expect` (None = any)."""
        frame = bytes([0x05, 0x6A, op, len(data) & 0xFF, len(data) >> 8]) + bytes(data)
        if len(frame) > 64:
            raise ValueError("report too long")
        os.write(self.fd, frame.ljust(64, b"\0"))
        end = time.monotonic() + (timeout or self.timeout)
        while (left := end - time.monotonic()) > 0:
            rep = self._read(left)
            if rep is None:
                break
            if expect is None or rep.startswith(expect):
                return rep
        return None

    def _set(self, cmd, args=()):
        """Lighting set command; waits for its ACK (one retry)."""
        for _ in range(2):
            rep = self._raw(OP_LIGHTING, [cmd, *args], expect=ACK + bytes([cmd]))
            if rep is not None:
                if rep[8] != 0:
                    raise GS5Error(f"GS5 rejected command {cmd:02x} (status {rep[8]:02x})")
                return
        raise GS5Error(f"no reply to command {cmd:02x}")

    def _get(self, cmd, args=()):
        """Lighting get command; returns the value bytes after <cmd>."""
        for _ in range(2):
            rep = self._raw(OP_LIGHTING, [cmd, *args], expect=GET)
            # skip unsolicited pushes of other values
            while rep is not None and rep[5] != cmd:
                rep = self._raw_wait(GET)
            if rep is not None:
                n = rep[3] | rep[4] << 8
                return rep[6:5 + n]
        raise GS5Error(f"no reply to query {cmd:02x}")

    def _raw_wait(self, expect):
        end = time.monotonic() + self.timeout
        while (left := end - time.monotonic()) > 0:
            rep = self._read(left)
            if rep is None:
                return None
            if rep.startswith(expect):
                return rep
        return None

    # --- global lighting -----------------------------------------------------------------------------------
    def on(self):
        self._set(0x25, [1])

    def off(self):
        self._set(0x25, [0])

    def is_on(self):
        return self._get(0x26)[0] == 1

    def brightness(self, percent=None):
        """Set brightness 0-100 % (if given) and return the current value in %."""
        if percent is not None:
            self._set(0x27, [round(max(0, min(100, percent)) * 255 / 100)])
        return round(self._get(0x28)[0] * 100 / 255)

    # --- slots and modes -----------------------------------------------------------------------------------
    def active_slot(self):
        return self._get(0x2E)[0]

    def slot_mode(self, slot):
        m = self._get(0x2A, [slot])[1]
        return MODE_NAMES.get(m, f"0x{m:02x}")

    def set_mode(self, slot, mode):
        """Put `mode` in `slot`. This resets the slot's colours/speed/direction to the mode's defaults.
        A mode change also resets the global brightness (to 0xb0, 69 %), so it is put back afterwards."""
        mid = MODES[mode]
        level = self._get(0x28)[0]
        for _ in range(3):
            self._set(0x29, [slot, mid])
            # The GS5 now pushes the slot's settings; commands sent during this window don't stick.
            self._drain(0.3)
            if self._get(0x2A, [slot])[1] == mid:
                if self._get(0x28)[0] != level:
                    self._set(0x27, [level])
                return
        raise GS5Error(f"slot {slot} would not switch to {mode}")

    def activate(self, slot):
        self._set(0x2D, [slot])
        self._drain(0.3)          # another settings push follows a slot switch

    def set_colours(self, slot, colours):
        self._set(0x2B, [slot, 0x01, 0x01] + _colour_bytes(colours))

    def set_speed(self, slot, ms):
        ms = min(SPEEDS, key=lambda s: abs(s - ms))
        self._set(0x2B, [slot, 0x03, ms & 0xFF, ms >> 8])

    def set_direction(self, slot, direction):
        self._set(0x2B, [slot, 0x04, *DIRECTIONS[direction]])

    # --- high level ----------------------------------------------------------------------------------------
    def static(self, colours, slot=1, setup=True):
        """Solid colour(s): one RRGGBB for the whole strip, or 7 (segment 1 = leftmost); "000000" = segment off.
        With setup=False only the colour frame is sent (fast, for animations once static is set up)."""
        if isinstance(colours, str):
            colours = [colours] * SEGMENTS
        if len(colours) != SEGMENTS:
            raise ValueError(f"static needs 1 or {SEGMENTS} colours")
        if setup:
            if self.slot_mode(slot) != "static":
                self.set_mode(slot, "static")
            if self.active_slot() != slot:
                self.activate(slot)
            if not self.is_on():
                self.on()
        self.set_colours(slot, colours)

    def effect(self, mode, slot=1, colours=None, speed=None, direction=None):
        """Run one of the GS5's built-in effects (it animates on its own; nothing has to keep running)."""
        self.set_mode(slot, mode)
        if colours:
            n = MODE_COLOURS[mode]
            if n == 0:
                raise ValueError(f"{mode} takes no colours")
            if len(colours) == 1 and n > 1:
                colours = colours * n
            if len(colours) != n:
                raise ValueError(f"{mode} takes {n} colours")
            self.set_colours(slot, colours)
        if speed is not None:
            if mode not in HAS_SPEED:
                raise ValueError(f"{mode} has no speed")
            self.set_speed(slot, speed)
        if direction is not None:
            if mode not in HAS_DIRECTION:
                raise ValueError(f"{mode} has no direction")
            self.set_direction(slot, direction)
        self.activate(slot)
        self.on()

    def status(self):
        return {
            "on": self.is_on(),
            "brightness": self.brightness(),
            "active_slot": self.active_slot(),
            "slots": {s: self.slot_mode(s) for s in range(1, SLOTS + 1)},
        }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gs5rgb", description="Sound Blaster GS5 lighting control")
    ap.add_argument("--device", help="hidraw node (default: auto-detect)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show on/off, brightness, active slot and each slot's mode")
    sub.add_parser("on")
    sub.add_parser("off")
    b = sub.add_parser("brightness", help="set brightness 0-100")
    b.add_argument("percent", type=int)
    c = sub.add_parser("colour", aliases=["color"], help="solid colour: 1 RRGGBB for the strip, or 7 (left to right)")
    c.add_argument("colours", nargs="+")
    c.add_argument("--slot", type=int, default=1)
    e = sub.add_parser("effect", help="built-in effect: " + ", ".join(m for m in MODES if m != "static"))
    e.add_argument("mode", choices=[m for m in MODES if m != "static"])
    e.add_argument("--colours", "--colors", nargs="+", help="RRGGBB values (count depends on the effect)")
    e.add_argument("--speed", type=int, help="ms per cycle (250-6000, snapped to the app's steps)")
    e.add_argument("--direction", choices=list(DIRECTIONS))
    e.add_argument("--slot", type=int, default=1)
    a = ap.parse_args(argv)

    try:
        with GS5.open(a.device) as g:
            if a.cmd == "status":
                s = g.status()
                print(f"lighting:   {'on' if s['on'] else 'off'}, brightness {s['brightness']}%")
                print(f"active slot: {s['active_slot']}")
                for slot, mode in s["slots"].items():
                    print(f"  slot {slot}: {mode}{'  <- active' if slot == s['active_slot'] else ''}")
            elif a.cmd == "on":
                g.on()
            elif a.cmd == "off":
                g.off()
            elif a.cmd == "brightness":
                print(f"brightness {g.brightness(a.percent)}%")
            elif a.cmd in ("colour", "color"):
                g.static(a.colours[0] if len(a.colours) == 1 else a.colours, slot=a.slot)
            elif a.cmd == "effect":
                g.effect(a.mode, slot=a.slot, colours=a.colours, speed=a.speed, direction=a.direction)
    except (GS5Error, ValueError, PermissionError) as err:
        print(f"gs5rgb: {err}", file=sys.stderr)
        if isinstance(err, PermissionError):
            print("gs5rgb: install udev/70-gs5-hidraw.rules (see README) or run as root", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
