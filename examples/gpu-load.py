#!/usr/bin/env python3
"""Colour the Sound Blaster GS5's strip by GPU load: green below 70 %, yellow from 70 %, red from 90 %.

Reads AMD's /sys/class/drm/card*/device/gpu_busy_percent once a second and sends a colour frame when the colour
changes (and once a minute anyway, in case the soundbar lost it). Reconnects if the GS5 is unplugged.
For NVIDIA, replace gpu_busy() with e.g. `nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits`.
"""
import glob
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]   # installed next to gs5rgb.py, or run from the repo's examples/
from gs5rgb import GS5, GS5Error  # noqa: E402

LEVELS = [(90, "ff0000"), (70, "e8d000"), (0, "4f8a10")]   # (from %, colour)
REFRESH = 60


def gpu_busy():
    for f in sorted(glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")):
        try:
            return int(open(f).read())
        except (OSError, ValueError):
            pass
    return None


def colour_for(load):
    return next(c for at, c in LEVELS if load >= at)


def main():
    running = True

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    dev, cur, last = None, None, 0.0
    while running:
        if dev is None:
            try:
                dev = GS5.open()
                dev.static("4f8a10")       # sets up static mode once
                dev.brightness(100)
                cur, last = "4f8a10", time.monotonic()
                print(f"GS5 connected on {dev.path}", flush=True)
            except (GS5Error, OSError) as e:
                if dev:
                    dev.close()
                dev = None
                print(f"waiting for the GS5: {e}", flush=True)
                time.sleep(5)
                continue
        load = gpu_busy()
        if load is not None:
            c = colour_for(load)
            if c != cur or time.monotonic() - last >= REFRESH:
                try:
                    dev.static(c, setup=False)
                    cur, last = c, time.monotonic()
                except OSError as e:
                    print(f"GS5 lost ({e}); reconnecting", flush=True)
                    dev.close()
                    dev = None
                    continue
        time.sleep(1)
    if dev:
        dev.close()


if __name__ == "__main__":
    main()
