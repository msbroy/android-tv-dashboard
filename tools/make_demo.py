#!/usr/bin/env python3
"""Generate docs/demo.gif by rendering the real dashboard UI driven by SYNTHETIC
data (no device connection, so no personal info ends up in the recording).

Run:  QT_QPA_PLATFORM=offscreen python3 tools/make_demo.py
Requires: PySide6, Pillow.
"""
import math
import os
import sys

from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tvdash.app import MainWindow  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "demo.gif")
FRAMES = 30
WIDTH = 760  # output width (downscaled from the window)

PROCS = [
    ("system_server", 1011, 142000),
    ("com.android.systemui", 1322, 98000),
    ("com.google.android.tvlauncher", 1544, 89000),
    ("com.google.android.youtube.tv", 1808, 82000),
    ("com.android.vending", 1620, 73000),
    ("com.example.streaming", 2102, 56000),
    ("com.example.musicplayer", 2240, 44000),
    ("media.codec", 980, 38000),
    ("com.example.filemanager", 2310, 31000),
    ("surfaceflinger", 760, 22000),
    ("com.android.bluetooth", 1140, 19000),
    ("audioserver", 770, 9000),
]


def frame(i):
    t = i / FRAMES * 2 * math.pi
    cpu = 28 + 18 * (math.sin(t) + 1)            # ~28..64
    cores = {f"cpu{c}": max(4, cpu + 12 * math.sin(t + c)) for c in range(4)}
    avail = 1_050_000 + 180_000 * math.sin(t * 1.3)
    rx = 90_000 + 70_000 * (math.sin(t * 1.7) + 1)
    tx = 30_000 + 40_000 * (math.cos(t * 1.1) + 1)
    rssi = int(-58 + 5 * math.sin(t * 0.9))
    return {
        "connected": True,
        "serial": "192.168.1.50:5555",
        "info": {"brand": "Living Room", "model": "Android TV", "manufacturer": "",
                 "android": "13", "sdk": "33", "patch": "2025-05-01"},
        "cpu_pct": {"cpu": cpu, **cores},
        "mem": {"MemTotal": 2_000_000, "MemAvailable": int(avail),
                "SwapTotal": 1_000_000, "SwapFree": 740_000},
        "net": {"iface": "wlan0", "rx_bps": rx, "tx_bps": tx},
        "wifi": {"rssi": rssi, "link_mbps": 433, "tx_mbps": 433, "freq": 5180,
                 "ssid": "Living Room", "bssid": "AA:BB:CC:11:22:33",
                 "channel": 36, "band": "5 GHz"},
        "netinfo": {"dns": ["1.1.1.1", "9.9.9.9"], "private_dns": True,
                    "private_dns_addrs": ["1.1.1.1"], "gateway": "192.168.1.1",
                    "ssid": "Living Room", "signal": rssi, "ip": "192.168.1.50/24"},
        "screen": "Awake",
        "foreground": "com.google.android.youtube.tv/.MainActivity",
        "uptime": 123456 + i * 2,
        "loadavg": (2.1, 1.8, 1.5),
        "bt": {"enabled": True, "name": "Living Room TV", "state": "ON",
               "conn_state": "CONNECTED", "codec": "AAC",
               "bonded": [("AA:BB:CC:DD:EE:FF", "Headphones")]},
        "processes": [{"name": n, "pid": p, "rss_kb": int(r * (0.9 + 0.1 * math.sin(t + p)))}
                      for n, p, r in PROCS],
    }


def main():
    from PIL import Image

    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1240, 860)
    win.show()
    win.worker.stop()
    win.worker.wait(2000)
    try:
        win.worker.sample.disconnect()
    except Exception:
        pass
    win._apps_loaded = True  # skip the adb app-list call

    tmp = []
    for i in range(FRAMES):
        win._on_sample(frame(i))
        app.processEvents()
        path = f"/tmp/_demo_{i:03d}.png"
        win.grab().save(path, "PNG")
        tmp.append(path)

    imgs = []
    for p in tmp[3:]:  # drop first few while sparklines fill
        im = Image.open(p).convert("RGB")
        h = int(im.height * WIDTH / im.width)
        imgs.append(im.resize((WIDTH, h), Image.LANCZOS).quantize(colors=128, dither=Image.NONE))
    imgs[0].save(OUT, save_all=True, append_images=imgs[1:], duration=110, loop=0, optimize=True)
    for p in tmp:
        os.remove(p)
    print(f"wrote {OUT}  ({len(imgs)} frames, {os.path.getsize(OUT)//1024} KB)")


if __name__ == "__main__":
    main()
