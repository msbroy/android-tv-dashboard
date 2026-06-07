"""ADB transport layer.

Wraps the `adb` CLI for a single target device (default: the networked TV at
192.168.1.50:5555). Calls are best-effort: failures return empty strings / False
so the GUI can render a "disconnected" state instead of crashing.

No root is assumed. Anything that needs root (GPU load, thermal on some devices)
simply comes back empty and is shown as N/A by the UI.
"""
from __future__ import annotations

import re
import shutil
import subprocess

DEFAULT_SERIAL = ""  # set your device in the UI, e.g. 192.168.1.50:5555

# A single round-trip that dumps every cheap /proc + sysfs source we need.
# Sections are delimited by @@NAME markers the parser splits on. Doing this in
# one shell invocation keeps latency low over a network ADB link.
SNAPSHOT_CMD = r"""
echo '@@MEM'; cat /proc/meminfo 2>/dev/null
echo '@@STAT'; cat /proc/stat 2>/dev/null
echo '@@NET'; cat /proc/net/dev 2>/dev/null
echo '@@LOAD'; cat /proc/loadavg 2>/dev/null
echo '@@UPTIME'; cat /proc/uptime 2>/dev/null
echo '@@WIFI'; cmd wifi status 2>/dev/null | grep -m1 'WifiInfo:'
echo '@@CONN'; dumpsys connectivity 2>/dev/null | grep -m1 DnsAddresses
echo '@@SCREEN'; dumpsys power 2>/dev/null | grep -m1 'mWakefulness='
echo '@@FG'; dumpsys activity activities 2>/dev/null | grep -m1 'mResumedActivity'
echo '@@END'
"""

# Fast process list: RSS(kB), PID, NAME for every process. `=` suppresses headers.
# ~0.2s vs ~4s for `dumpsys meminfo`, so the CPU-delta loop stays responsive.
PROCS_CMD = "ps -A -o RSS=,PID=,NAME= 2>/dev/null"

INFO_CMD = (
    "getprop ro.product.brand; getprop ro.product.model; "
    "getprop ro.product.manufacturer; getprop ro.build.version.release; "
    "getprop ro.build.version.sdk; getprop ro.serialno; "
    "getprop ro.build.version.security_patch"
)


class AdbClient:
    def __init__(self, serial: str = DEFAULT_SERIAL):
        self.serial = serial
        self.adb = shutil.which("adb") or "adb"

    # ---- low level -------------------------------------------------------
    def _run(self, args, timeout=15):
        try:
            r = subprocess.run(
                [self.adb, *args], capture_output=True, text=True, timeout=timeout
            )
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            return 1, "", "timeout"
        except Exception as e:  # adb missing, etc.
            return 1, "", str(e)

    def shell(self, cmd, timeout=20) -> str:
        _, out, _ = self._run(["-s", self.serial, "shell", cmd], timeout=timeout)
        return out

    # ---- connection ------------------------------------------------------
    def connect(self):
        """Reconnect to a network device. No-op for USB serials."""
        if ":" in self.serial:
            self._run(["connect", self.serial], timeout=10)

    def disconnect(self):
        if ":" in self.serial:
            self._run(["disconnect", self.serial], timeout=10)

    def is_connected(self) -> bool:
        _, out, _ = self._run(["devices"], timeout=10)
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[0] == self.serial and parts[1] == "device":
                return True
        return False

    # ---- data ------------------------------------------------------------
    def snapshot_raw(self) -> str:
        return self.shell(SNAPSHOT_CMD, timeout=20)

    def procs_raw(self) -> str:
        return self.shell(PROCS_CMD, timeout=20)

    def device_info(self) -> dict:
        out = self.shell(INFO_CMD, timeout=15)
        lines = [l.strip() for l in out.splitlines()]
        keys = ["brand", "model", "manufacturer", "android", "sdk", "serialno", "patch"]
        vals = (lines + [""] * len(keys))[: len(keys)]
        return dict(zip(keys, vals))

    # ---- control ---------------------------------------------------------
    def keyevent(self, code: str):
        self.shell(f"input keyevent {code}")

    def wake(self):
        self.keyevent("KEYCODE_WAKEUP")

    def standby(self):
        # On a TV, the power key toggles standby (screen off / low power).
        self.keyevent("KEYCODE_POWER")

    def reboot(self):
        self._run(["-s", self.serial, "reboot"], timeout=20)

    def power_off(self):
        # Full power off. Works on most devices over adb without root.
        self._run(["-s", self.serial, "shell", "reboot", "-p"], timeout=20)

    def force_stop(self, package: str):
        self.shell(f"am force-stop {package}")

    def screenshot(self, local_path: str) -> bool:
        remote = "/sdcard/_tvdash_shot.png"
        self.shell(f"screencap -p {remote}")
        rc, _, _ = self._run(["-s", self.serial, "pull", remote, local_path], timeout=20)
        self.shell(f"rm -f {remote}")
        return rc == 0

    # ---- navigation / media ---------------------------------------------
    _DPAD = {
        "up": "KEYCODE_DPAD_UP",
        "down": "KEYCODE_DPAD_DOWN",
        "left": "KEYCODE_DPAD_LEFT",
        "right": "KEYCODE_DPAD_RIGHT",
        "ok": "KEYCODE_DPAD_CENTER",
    }

    def dpad(self, direction: str):
        self.keyevent(self._DPAD[direction])

    def back(self):
        self.keyevent("KEYCODE_BACK")

    def home(self):
        self.keyevent("KEYCODE_HOME")

    def volume_up(self):
        self.keyevent("KEYCODE_VOLUME_UP")

    def volume_down(self):
        self.keyevent("KEYCODE_VOLUME_DOWN")

    def mute(self):
        self.keyevent("KEYCODE_VOLUME_MUTE")

    def play_pause(self):
        self.keyevent("KEYCODE_MEDIA_PLAY_PAUSE")

    def next_track(self):
        self.keyevent("KEYCODE_MEDIA_NEXT")

    def prev_track(self):
        self.keyevent("KEYCODE_MEDIA_PREVIOUS")

    def launch_live_tv(self):
        self.shell("monkey -p com.mediatek.wwtv.tvcenter -c android.intent.category.LAUNCHER 1")

    def open_settings(self):
        self.shell("am start -a android.settings.SETTINGS")

    def launch_component(self, component: str):
        self.shell(f"am start -n {component}")

    def launchable_apps(self):
        """List (package, component) for leanback-launchable apps."""
        out = self.shell(
            "cmd package query-activities --brief -a android.intent.action.MAIN "
            "-c android.intent.category.LEANBACK_LAUNCHER"
        )
        comps, seen = [], set()
        for m in re.finditer(r"([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)", out):
            comp = m.group(1)
            pkg = comp.split("/")[0]
            if pkg not in seen:
                seen.add(pkg)
                comps.append((pkg, comp))
        comps.sort()
        return comps

    # ---- bluetooth (manage the TV's own adapter) -------------------------
    def bt_state_raw(self) -> str:
        return self.shell("dumpsys bluetooth_manager 2>/dev/null")

    def bt_enable(self):
        self.shell("svc bluetooth enable")

    def bt_disable(self):
        self.shell("svc bluetooth disable")

    def open_bt_settings(self):
        self.shell("am start -a android.settings.BLUETOOTH_SETTINGS")

    # ---- maintenance -----------------------------------------------------
    def _mem_available_kb(self):
        m = re.search(r"(\d+)", self.shell("grep MemAvailable /proc/meminfo"))
        return int(m.group(1)) if m else None

    def free_memory(self):
        """Kill cached/background apps and trim file caches. Returns (before_kb, after_kb)."""
        before = self._mem_available_kb()
        self.shell("am kill-all")          # kill safe-to-kill background processes
        self.shell("pm trim-caches 2048M")  # free cached files
        after = self._mem_available_kb()
        return before, after
