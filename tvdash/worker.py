"""Background polling thread. Emits a metrics dict on every tick."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from .adb import AdbClient
from .collectors import StateTracker, parse_bt, parse_processes


class MonitorWorker(QThread):
    sample = Signal(dict)

    def __init__(self, serial: str, interval_ms: int = 2000):
        super().__init__()
        self.client = AdbClient(serial)
        self.interval_ms = interval_ms
        self.tracker = StateTracker()
        self._running = True
        self._info = None
        self._tick = 0
        self._bt = None

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            data = {"connected": False, "serial": self.client.serial}
            try:
                if not self.client.is_connected():
                    self.client.connect()
                if self.client.is_connected():
                    data["connected"] = True
                    if self._info is None:
                        self._info = self.client.device_info()
                    data["info"] = self._info
                    data.update(self.tracker.update(self.client.snapshot_raw()))
                    data["processes"] = parse_processes(self.client.procs_raw())
                    # Bluetooth state changes rarely; refresh every 3rd tick.
                    self._tick += 1
                    if self._bt is None or self._tick % 3 == 0:
                        self._bt = parse_bt(self.client.bt_state_raw())
                    data["bt"] = self._bt
                else:
                    # lost device: force re-fetch of static info on reconnect
                    self._info = None
                    self._bt = None
                    self.tracker.reset()
            except Exception as e:  # never let the thread die
                data["error"] = str(e)
            self.sample.emit(data)

            slept = 0
            while self._running and slept < self.interval_ms:
                self.msleep(100)
                slept += 100
