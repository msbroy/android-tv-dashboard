"""Main window: live health dashboard + scrcpy + admin/navigation/Bluetooth controls."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .adb import DEFAULT_SERIAL, AdbClient
from .widgets import Sparkline
from .worker import MonitorWorker

SHOTS_DIR = os.path.join(os.path.dirname(__file__), "shots")

STYLE = """
QWidget { background:#0c0f14; color:#e6edf3;
          font-family:'Segoe UI','DejaVu Sans',sans-serif; font-size:12px; }
QFrame#Card { background:#11161f; border:1px solid #1d2530; border-radius:12px; }
QLabel#Title { color:#8aa0b6; font-size:10px; font-weight:700; letter-spacing:1.5px; }
QLabel#Big { font-size:24px; font-weight:800; }
QLabel#Sub { color:#8aa0b6; font-size:11px; }
QLabel#Dot { font-size:15px; }
QGroupBox { border:1px solid #1d2530; border-radius:12px; margin-top:10px; padding:8px;
            background:#11161f; }
QGroupBox::title { subcontrol-origin: margin; left:12px; color:#8aa0b6;
                   font-weight:700; letter-spacing:1px; }
QPushButton { background:#1b2430; border:1px solid #2a3645; border-radius:8px;
              padding:8px 10px; font-weight:600; }
QPushButton:hover { background:#26344a; }
QPushButton#accent { background:#13476f; border-color:#1d5e92; }
QPushButton#accent:hover { background:#1a5d92; }
QPushButton#danger { background:#3a1414; border-color:#5a1d1d; }
QPushButton#danger:hover { background:#551c1c; }
QTableWidget, QListWidget { background:#0e131a; gridline-color:#1a212b;
               border:1px solid #1d2530; border-radius:8px; }
QHeaderView::section { background:#151c26; color:#8aa0b6; border:none; padding:6px; }
QProgressBar { background:#0e131a; border:1px solid #1d2530; border-radius:6px;
               text-align:center; height:16px; color:#cdd9e5; }
QProgressBar::chunk { background:#4ea1ff; border-radius:6px; }
QLineEdit, QSpinBox, QComboBox { background:#0e131a; border:1px solid #2a3645;
                      border-radius:6px; padding:5px; }
QScrollArea { border:none; }
"""


def fmt_rate(bps: float) -> str:
    kb = bps / 1024.0
    return f"{kb:,.0f} KB/s" if kb < 1024 else f"{kb / 1024:,.1f} MB/s"


def fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d}d {h}h {m}m"
    return f"{h}h {m}m" if h else f"{m}m"


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        os.makedirs(SHOTS_DIR, exist_ok=True)
        self.serial = DEFAULT_SERIAL
        self.ctl = AdbClient(self.serial)
        self.core_bars = []
        self._apps_loaded = False
        self.setWindowTitle("TV Dashboard")
        self.resize(1240, 860)
        self.setStyleSheet(STYLE)
        self._build_ui()
        self._start_worker()

    # ------------------------------------------------------------------ UI
    def _card(self, title: str):
        frame = QFrame()
        frame.setObjectName("Card")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        t = QLabel(title)
        t.setObjectName("Title")
        lay.addWidget(t)
        return frame, lay

    def _btn(self, text, slot, obj=None):
        b = QPushButton(text)
        if obj:
            b.setObjectName(obj)
        b.clicked.connect(slot)
        return b

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        # ---- header ---------------------------------------------------
        header = QFrame()
        header.setObjectName("Card")
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 12, 16, 12)
        self.dot = QLabel("●")
        self.dot.setObjectName("Dot")
        self.dot.setStyleSheet("color:#e0524d;")
        self.conn_lbl = QLabel("Connecting…")
        self.conn_lbl.setStyleSheet("font-weight:700;")
        self.device_lbl = QLabel("—")
        self.device_lbl.setObjectName("Sub")
        h.addWidget(self.dot)
        h.addWidget(self.conn_lbl)
        h.addSpacing(14)
        h.addWidget(self.device_lbl)
        h.addStretch(1)
        self.screen_lbl = QLabel("Screen: —")
        self.screen_lbl.setObjectName("Sub")
        self.fg_lbl = QLabel("Foreground: —")
        self.fg_lbl.setObjectName("Sub")
        self.uptime_lbl = QLabel("Uptime: —")
        self.uptime_lbl.setObjectName("Sub")
        for w in (self.screen_lbl, self.fg_lbl, self.uptime_lbl):
            h.addWidget(w)
            h.addSpacing(12)
        h.addWidget(self._btn("Reconnect", self._reconnect))
        root.addWidget(header)

        # ---- metric cards --------------------------------------------
        grid = QGridLayout()
        grid.setSpacing(12)

        cpu_card, cpu_lay = self._card("CPU")
        self.cpu_value = QLabel("—")
        self.cpu_value.setObjectName("Big")
        cpu_lay.addWidget(self.cpu_value)
        self.core_box = QHBoxLayout()
        self.core_box.setSpacing(4)
        cpu_lay.addLayout(self.core_box)
        self.cpu_spark = Sparkline("#4ea1ff", fixed_max=100)
        cpu_lay.addWidget(self.cpu_spark)
        self.load_lbl = QLabel("load: —")
        self.load_lbl.setObjectName("Sub")
        self.load_lbl.setToolTip(
            "Linux load average. On this MediaTek TV it reads very high because the "
            "SoC's driver threads sit in uninterruptible-sleep; it does NOT mean the "
            "CPU is busy. Trust the CPU% above instead."
        )
        cpu_lay.addWidget(self.load_lbl)
        grid.addWidget(cpu_card, 0, 0)

        ram_card, ram_lay = self._card("MEMORY")
        self.ram_value = QLabel("—")
        self.ram_value.setObjectName("Big")
        ram_lay.addWidget(self.ram_value)
        self.ram_bar = QProgressBar()
        self.ram_bar.setMaximum(100)
        ram_lay.addWidget(self.ram_bar)
        self.swap_lbl = QLabel("swap: —")
        self.swap_lbl.setObjectName("Sub")
        ram_lay.addWidget(self.swap_lbl)
        self.ram_spark = Sparkline("#7ee787", fixed_max=100)
        ram_lay.addWidget(self.ram_spark)
        grid.addWidget(ram_card, 0, 1)

        net_card, net_lay = self._card("NETWORK")
        self.net_value = QLabel("↓ —   ↑ —")
        self.net_value.setObjectName("Big")
        net_lay.addWidget(self.net_value)
        self.net_iface = QLabel("iface: —")
        self.net_iface.setObjectName("Sub")
        net_lay.addWidget(self.net_iface)
        self.net_spark = Sparkline("#ffa657")
        net_lay.addWidget(self.net_spark)
        grid.addWidget(net_card, 0, 2)

        wifi_card, wifi_lay = self._card("WI-FI SIGNAL")
        self.wifi_value = QLabel("—")
        self.wifi_value.setObjectName("Big")
        wifi_lay.addWidget(self.wifi_value)
        self.wifi_sub = QLabel("—")
        self.wifi_sub.setObjectName("Sub")
        self.wifi_sub.setWordWrap(True)
        wifi_lay.addWidget(self.wifi_sub)
        self.wifi_spark = Sparkline("#56d4dd", fixed_max=100)
        wifi_lay.addWidget(self.wifi_spark)
        grid.addWidget(wifi_card, 1, 0)

        dns_card, dns_lay = self._card("DNS & NETWORK")
        self.dns_table = QTableWidget(0, 2)
        self.dns_table.horizontalHeader().setVisible(False)
        self.dns_table.verticalHeader().setVisible(False)
        self.dns_table.setColumnWidth(0, 96)
        self.dns_table.horizontalHeader().setStretchLastSection(True)
        self.dns_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.dns_table.setSelectionMode(QTableWidget.NoSelection)
        self.dns_table.setShowGrid(False)
        dns_lay.addWidget(self.dns_table)
        grid.addWidget(dns_card, 1, 1)
        grid.addWidget(QWidget(), 1, 2)  # spacer
        for c in range(3):
            grid.setColumnStretch(c, 1)
        root.addLayout(grid)

        # ---- lower: processes (left) + control column (right) --------
        lower = QHBoxLayout()
        lower.setSpacing(12)

        proc_card, proc_lay = self._card("TOP PROCESSES (by RAM)")
        self.proc_table = QTableWidget(0, 3)
        self.proc_table.setHorizontalHeaderLabels(["Process", "PID", "RAM (MB)"])
        self.proc_table.setColumnWidth(0, 420)
        self.proc_table.setColumnWidth(1, 80)
        self.proc_table.setColumnWidth(2, 100)
        self.proc_table.verticalHeader().setVisible(False)
        self.proc_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.proc_table.setEditTriggers(QTableWidget.NoEditTriggers)
        proc_lay.addWidget(self.proc_table)
        self.fs_btn = self._btn("Force-stop selected app", self._force_stop_selected, "danger")
        proc_lay.addWidget(self.fs_btn)
        lower.addWidget(proc_card, 3)

        lower.addWidget(self._control_column(), 2)
        root.addLayout(lower, 1)

        # ---- footer ---------------------------------------------------
        footer = QHBoxLayout()
        footer.addWidget(QLabel("Device:"))
        self.serial_edit = QLineEdit(self.serial)
        self.serial_edit.setPlaceholderText("e.g. 192.168.1.50:5555 or USB serial")
        self.serial_edit.setFixedWidth(170)
        footer.addWidget(self.serial_edit)
        footer.addWidget(QLabel("Poll (ms):"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(500, 10000)
        self.interval_spin.setSingleStep(250)
        self.interval_spin.setValue(2000)
        footer.addWidget(self.interval_spin)
        footer.addWidget(self._btn("Apply", self._apply_settings))
        footer.addSpacing(16)
        self.autofree_chk = QCheckBox("Auto-free RAM every")
        self.autofree_chk.toggled.connect(self._toggle_autofree)
        footer.addWidget(self.autofree_chk)
        self.autofree_min = QSpinBox()
        self.autofree_min.setRange(5, 240)
        self.autofree_min.setValue(30)
        self.autofree_min.setSuffix(" min")
        self.autofree_min.valueChanged.connect(self._reschedule_autofree)
        footer.addWidget(self.autofree_min)
        footer.addStretch(1)
        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("Sub")
        footer.addWidget(self.status_lbl)
        root.addLayout(footer)

    def _control_column(self) -> QScrollArea:
        col = QWidget()
        cv = QVBoxLayout(col)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(10)

        # DEVICE
        dev = QGroupBox("DEVICE")
        dg = QGridLayout(dev)
        dg.setSpacing(8)
        self.scrcpy_btn = self._btn("▶  Remote (scrcpy)", self._launch_scrcpy, "accent")
        dg.addWidget(self.scrcpy_btn, 0, 0, 1, 2)
        dg.addWidget(self._btn("Screenshot", self._screenshot), 1, 0)
        dg.addWidget(self._btn("Settings", lambda: self._ctl("Settings", self.ctl.open_settings)), 1, 1)
        dg.addWidget(self._btn("Wake", lambda: self._ctl("Wake", self.ctl.wake)), 2, 0)
        dg.addWidget(self._btn("Standby", lambda: self._ctl("Standby", self.ctl.standby)), 2, 1)
        dg.addWidget(self._btn("🧹  Free Memory", self._free_memory, "accent"), 3, 0, 1, 2)
        dg.addWidget(self._btn("Reboot", self._reboot, "danger"), 4, 0)
        dg.addWidget(self._btn("Power Off", self._power_off, "danger"), 4, 1)
        cv.addWidget(dev)

        # NAVIGATION & MEDIA
        nav = QGroupBox("NAVIGATION & MEDIA")
        ng = QGridLayout(nav)
        ng.setSpacing(6)
        ng.addWidget(self._btn("▲", lambda: self.ctl.dpad("up")), 0, 1)
        ng.addWidget(self._btn("◀", lambda: self.ctl.dpad("left")), 1, 0)
        ng.addWidget(self._btn("OK", lambda: self.ctl.dpad("ok"), "accent"), 1, 1)
        ng.addWidget(self._btn("▶", lambda: self.ctl.dpad("right")), 1, 2)
        ng.addWidget(self._btn("▼", lambda: self.ctl.dpad("down")), 2, 1)
        ng.addWidget(self._btn("Back", self.ctl.back), 0, 0)
        ng.addWidget(self._btn("Home", self.ctl.home), 0, 2)
        ng.addWidget(self._btn("Vol −", self.ctl.volume_down), 3, 0)
        ng.addWidget(self._btn("Mute", self.ctl.mute), 3, 1)
        ng.addWidget(self._btn("Vol +", self.ctl.volume_up), 3, 2)
        ng.addWidget(self._btn("⏮", self.ctl.prev_track), 4, 0)
        ng.addWidget(self._btn("⏯", self.ctl.play_pause), 4, 1)
        ng.addWidget(self._btn("⏭", self.ctl.next_track), 4, 2)
        ng.addWidget(self._btn("📺  Live TV", lambda: self._ctl("Live TV", self.ctl.launch_live_tv)), 5, 0, 1, 3)
        self.app_combo = QComboBox()
        ng.addWidget(self.app_combo, 6, 0, 1, 2)
        ng.addWidget(self._btn("Launch", self._launch_app), 6, 2)
        cv.addWidget(nav)

        # BLUETOOTH
        bt = QGroupBox("BLUETOOTH")
        bg = QVBoxLayout(bt)
        bg.setSpacing(6)
        self.bt_state_lbl = QLabel("—")
        self.bt_state_lbl.setStyleSheet("font-weight:700;")
        bg.addWidget(self.bt_state_lbl)
        self.bt_conn_lbl = QLabel("")
        self.bt_conn_lbl.setObjectName("Sub")
        bg.addWidget(self.bt_conn_lbl)
        self.bt_list = QListWidget()
        self.bt_list.setFixedHeight(80)
        bg.addWidget(self.bt_list)
        row = QHBoxLayout()
        row.addWidget(self._btn("Enable", lambda: self._ctl("BT enable", self.ctl.bt_enable)))
        row.addWidget(self._btn("Disable", lambda: self._ctl("BT disable", self.ctl.bt_disable)))
        bg.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(self._btn("Pair / Settings", lambda: self._ctl("BT settings", self.ctl.open_bt_settings)))
        row2.addWidget(self._btn("Refresh", self._refresh_bt))
        bg.addLayout(row2)
        cv.addWidget(bt)

        cv.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(col)
        scroll.setMinimumWidth(330)
        return scroll

    # -------------------------------------------------------------- worker
    def _start_worker(self):
        self.worker = MonitorWorker(self.serial, self.interval_spin.value())
        self.worker.sample.connect(self._on_sample)
        self.worker.start()
        self.autofree_timer = QTimer(self)
        self.autofree_timer.timeout.connect(self._auto_free_tick)

    def _toggle_autofree(self, on):
        if on:
            self.autofree_timer.start(self.autofree_min.value() * 60_000)
            self.status_lbl.setText(f"Auto-free ON ({self.autofree_min.value()} min)")
        else:
            self.autofree_timer.stop()
            self.status_lbl.setText("Auto-free OFF")

    def _reschedule_autofree(self):
        if self.autofree_chk.isChecked():
            self.autofree_timer.start(self.autofree_min.value() * 60_000)

    def _auto_free_tick(self):
        try:
            before, after = self.ctl.free_memory()
            if before and after:
                self.status_lbl.setText(
                    f"Auto-free {datetime.now():%H:%M}: {(after-before)/1024:+,.0f} MB"
                )
        except Exception:
            pass

    def _restart_worker(self):
        if hasattr(self, "worker"):
            self.worker.stop()
            self.worker.wait(3000)
        self._start_worker()

    def _apply_settings(self):
        self.serial = self.serial_edit.text().strip() or DEFAULT_SERIAL
        self.ctl = AdbClient(self.serial)
        self._apps_loaded = False
        self.app_combo.clear()
        for s in (self.cpu_spark, self.ram_spark, self.wifi_spark, self.net_spark):
            s.clear()
        self.core_bars = []
        while self.core_box.count():
            self.core_box.takeAt(0).widget().deleteLater()
        self._restart_worker()

    # -------------------------------------------------------------- update
    def _on_sample(self, d: dict):
        if not d.get("connected"):
            self.dot.setStyleSheet("color:#e0524d;")
            self.conn_lbl.setText("Disconnected")
            self.status_lbl.setText(d.get("error", "no device") or "no device")
            return

        self.dot.setStyleSheet("color:#3fb950;")
        self.conn_lbl.setText(f"Connected — {d['serial']}")
        info = d.get("info", {})
        if info:
            self.device_lbl.setText(
                f"{info.get('brand','')} {info.get('model','')}  •  "
                f"Android {info.get('android','')} (SDK {info.get('sdk','')})  •  "
                f"patch {info.get('patch','')}"
            )

        if not self._apps_loaded:
            self._apps_loaded = True
            try:
                for pkg, comp in self.ctl.launchable_apps():
                    self.app_combo.addItem(pkg, comp)
            except Exception:
                pass

        # CPU
        cpu = d.get("cpu_pct", {})
        overall = cpu.get("cpu")
        if overall is not None:
            self.cpu_value.setText(f"{overall:0.0f}%")
            self.cpu_spark.push(overall)
        cores = sorted(
            (k for k in cpu if k.startswith("cpu") and k != "cpu"),
            key=lambda k: int(k[3:]),
        )
        if cores and not self.core_bars:
            for _ in cores:
                b = QProgressBar()
                b.setMaximum(100)
                b.setTextVisible(False)
                b.setFixedHeight(10)
                self.core_box.addWidget(b)
                self.core_bars.append(b)
        for b, k in zip(self.core_bars, cores):
            b.setValue(int(cpu.get(k, 0)))
        lo = d.get("loadavg", (0, 0, 0))
        self.load_lbl.setText(f"load: {lo[0]:.1f} / {lo[1]:.1f} / {lo[2]:.1f}  (ⓘ tooltip)")

        # RAM
        mem = d.get("mem", {})
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        if total:
            used = total - avail
            pct = used / total * 100
            self.ram_value.setText(f"{used/1024:,.0f} / {total/1024:,.0f} MB  ({pct:0.0f}%)")
            self.ram_bar.setValue(int(pct))
            self.ram_spark.push(pct)
        swt, swf = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
        self.swap_lbl.setText(
            f"swap: {(swt-swf)/1024:,.0f} / {swt/1024:,.0f} MB used" if swt else "swap: none"
        )

        # Network throughput
        net = d.get("net", {})
        self.net_value.setText(
            f"↓ {fmt_rate(net.get('rx_bps',0))}    ↑ {fmt_rate(net.get('tx_bps',0))}"
        )
        self.net_iface.setText(f"iface: {net.get('iface','?')}")
        self.net_spark.push(net.get("rx_bps", 0) / 1024.0)

        # Wi-Fi signal
        wifi = d.get("wifi", {})
        ni = d.get("netinfo", {})
        rssi = wifi.get("rssi")
        if rssi is None:
            rssi = ni.get("signal")
        if rssi is not None:
            qual = max(0, min(100, 2 * (rssi + 100)))
            label = (
                "Excellent" if rssi >= -55 else
                "Good" if rssi >= -67 else
                "Fair" if rssi >= -75 else "Weak"
            )
            self.wifi_value.setText(f"{rssi} dBm")
            extra = []
            if wifi.get("ssid") or ni.get("ssid"):
                extra.append(wifi.get("ssid") or ni.get("ssid"))
            if wifi.get("band") and wifi["band"] != "?":
                ch = wifi.get("channel")
                extra.append(f"{wifi['band']}" + (f" ch{ch}" if ch else ""))
            if wifi.get("tx_mbps") or wifi.get("link_mbps"):
                extra.append(f"{wifi.get('tx_mbps') or wifi['link_mbps']} Mbps")
            self.wifi_sub.setText(f"{label}   " + "  ·  ".join(extra))
            self.wifi_spark.push(qual)
        else:
            self.wifi_value.setText("—")
            self.wifi_sub.setText("not on Wi-Fi")

        # DNS & network table
        pdns = ni.get("private_dns")
        pdns_txt = (
            "ON — " + ", ".join(ni.get("private_dns_addrs", [])) if pdns else
            "Off" if pdns is not None else "—"
        )
        rows = [
            ("SSID", ni.get("ssid") or wifi.get("ssid") or "—"),
            ("BSSID", wifi.get("bssid") or "—"),
            ("Band / Ch", f"{wifi.get('band','?')}" + (f" · ch{wifi['channel']}" if wifi.get("channel") else "")),
            ("IP", ni.get("ip", "—")),
            ("Gateway", ni.get("gateway", "—")),
            ("DNS", ", ".join(ni.get("dns", [])) or "—"),
            ("Private DNS", pdns_txt),
            ("Link", f"{wifi.get('tx_mbps') or wifi.get('link_mbps') or '—'} Mbps"),
        ]
        self.dns_table.setRowCount(len(rows))
        for r, (k, v) in enumerate(rows):
            ki = QTableWidgetItem(k)
            ki.setForeground(Qt.gray)
            self.dns_table.setItem(r, 0, ki)
            self.dns_table.setItem(r, 1, QTableWidgetItem(str(v)))

        # State
        self.screen_lbl.setText(f"Screen: {d.get('screen','—')}")
        self.fg_lbl.setText(f"Foreground: {d.get('foreground','—')}")
        self.uptime_lbl.setText(f"Uptime: {fmt_uptime(d.get('uptime',0))}")

        # Bluetooth
        self._apply_bt(d.get("bt"))

        # Processes
        procs = d.get("processes", [])
        self.proc_table.setRowCount(len(procs))
        for r, p in enumerate(procs):
            self.proc_table.setItem(r, 0, QTableWidgetItem(p["name"]))
            pid_item = QTableWidgetItem(str(p["pid"]))
            pid_item.setTextAlignment(Qt.AlignCenter)
            self.proc_table.setItem(r, 1, pid_item)
            mb = QTableWidgetItem(f"{p['rss_kb']/1024:,.1f}")
            mb.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.proc_table.setItem(r, 2, mb)

        self.status_lbl.setText("Updated " + datetime.now().strftime("%H:%M:%S"))

    def _apply_bt(self, bt):
        if not bt:
            return
        on = bt.get("enabled")
        self.bt_state_lbl.setText(
            ("● ON" if on else "○ OFF") + f"   {bt.get('name','')}"
        )
        self.bt_state_lbl.setStyleSheet(
            "font-weight:700;color:%s;" % ("#3fb950" if on else "#8aa0b6")
        )
        cs = bt.get("conn_state", "?")
        if cs and cs not in ("?", "DISCONNECTED"):
            codec = bt.get("codec")
            self.bt_conn_lbl.setText(f"🔊 {cs}" + (f" · {codec}" if codec else ""))
            self.bt_conn_lbl.setStyleSheet("color:#3fb950;")
        else:
            self.bt_conn_lbl.setText("no audio device connected")
            self.bt_conn_lbl.setStyleSheet("color:#8aa0b6;")
        self.bt_list.clear()
        bonded = bt.get("bonded", [])
        if bonded:
            for addr, name in bonded:
                self.bt_list.addItem(f"{name or '(unknown)'}   [{addr}]")
        else:
            self.bt_list.addItem("(no paired devices — use Pair / Settings)")

    # ------------------------------------------------------------- actions
    def _ctl(self, name, fn):
        try:
            fn()
            self.status_lbl.setText(f"{name} sent")
        except Exception as e:
            QMessageBox.warning(self, name, str(e))

    def _reconnect(self):
        self.ctl.connect()
        self.status_lbl.setText("Reconnect requested")

    def _refresh_bt(self):
        try:
            from .collectors import parse_bt

            self._apply_bt(parse_bt(self.ctl.bt_state_raw()))
            self.status_lbl.setText("Bluetooth refreshed")
        except Exception as e:
            QMessageBox.warning(self, "Bluetooth", str(e))

    def _launch_app(self):
        comp = self.app_combo.currentData()
        if comp:
            self._ctl(f"Launch {self.app_combo.currentText()}", lambda: self.ctl.launch_component(comp))

    def _free_memory(self):
        try:
            before, after = self.ctl.free_memory()
            if before and after:
                freed = (after - before) / 1024.0
                self.status_lbl.setText(f"Freed {freed:+,.0f} MB")
                QMessageBox.information(
                    self,
                    "Free Memory",
                    "Killed background apps and trimmed file caches.\n\n"
                    f"Available RAM:  {before/1024:,.0f} MB  →  {after/1024:,.0f} MB\n"
                    f"Reclaimed:  {freed:+,.0f} MB\n\n"
                    "(Foreground app, Live TV and system services are left running.)",
                )
            else:
                self.status_lbl.setText("Memory freed")
        except Exception as e:
            QMessageBox.warning(self, "Free Memory", str(e))

    def _reboot(self):
        if QMessageBox.question(self, "Reboot", "Reboot the TV now?") == QMessageBox.Yes:
            self._ctl("Reboot", self.ctl.reboot)

    def _power_off(self):
        if (
            QMessageBox.question(
                self,
                "Power Off",
                "Power off the TV?\n\n(You'll need the physical remote / power "
                "button to turn it back on.)",
            )
            == QMessageBox.Yes
        ):
            self._ctl("Power Off", self.ctl.power_off)

    def _force_stop_selected(self):
        row = self.proc_table.currentRow()
        if row < 0:
            return
        name = self.proc_table.item(row, 0).text()
        pkg = name.split(":")[0]
        if "." not in pkg:
            QMessageBox.information(self, "Force-stop", f"'{name}' is a system process, not an app.")
            return
        if QMessageBox.question(self, "Force-stop", f"Force-stop {pkg}?") == QMessageBox.Yes:
            self._ctl(f"Force-stop {pkg}", lambda: self.ctl.force_stop(pkg))

    def _screenshot(self):
        path = os.path.join(SHOTS_DIR, time.strftime("shot-%Y%m%d-%H%M%S.png"))
        if not self.ctl.screenshot(path) or not os.path.exists(path) or os.path.getsize(path) == 0:
            QMessageBox.information(
                self,
                "Screenshot",
                "Capture failed or was empty.\n\nProtected content (Live TV / HDMI "
                "input, DRM video) cannot be captured.",
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(os.path.basename(path))
        v = QVBoxLayout(dlg)
        lbl = QLabel()
        lbl.setPixmap(QPixmap(path).scaledToWidth(900, Qt.SmoothTransformation))
        v.addWidget(lbl)
        v.addWidget(QLabel(f"Saved to {path}"))
        dlg.exec()

    def _launch_scrcpy(self):
        exe = shutil.which("scrcpy")
        if not exe:
            QMessageBox.warning(
                self, "scrcpy not found",
                "scrcpy is not installed or not in PATH.\nInstall it, then click Remote again.",
            )
            return
        try:
            subprocess.Popen(
                [exe, "-s", self.serial, "--window-title", f"TV — {self.serial}", "--stay-awake"]
            )
            self.status_lbl.setText("scrcpy launched")
        except Exception as e:
            QMessageBox.warning(self, "scrcpy", str(e))

    # --------------------------------------------------------------- close
    def closeEvent(self, e):
        if hasattr(self, "worker"):
            self.worker.stop()
            self.worker.wait(3000)
        super().closeEvent(e)
