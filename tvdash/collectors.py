"""Parsing of raw ADB output into metrics, plus rate/percentage computation.

StateTracker keeps the previous CPU jiffies and network byte counters so it can
turn cumulative kernel counters into instantaneous CPU% and KB/s.
"""
from __future__ import annotations

import re
import time


def split_sections(raw: str) -> dict:
    sections, cur, buf = {}, None, []
    for line in raw.splitlines():
        if line.startswith("@@"):
            if cur is not None:
                sections[cur] = "\n".join(buf)
            cur, buf = line[2:].strip(), []
        else:
            buf.append(line)
    if cur is not None:
        sections[cur] = "\n".join(buf)
    return sections


def parse_meminfo(s: str) -> dict:
    d = {}
    for line in s.splitlines():
        m = re.match(r"(\w+):\s+(\d+)", line)
        if m:
            d[m.group(1)] = int(m.group(2))  # kB
    return d


def parse_stat(s: str) -> dict:
    """Return {label: (total_jiffies, idle_jiffies)} for 'cpu' and each 'cpuN'."""
    res = {}
    for line in s.splitlines():
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        nums = [int(x) for x in parts[1:] if x.isdigit()]
        if len(nums) >= 5:
            total = sum(nums)
            idle = nums[3] + nums[4]  # idle + iowait
            res[parts[0]] = (total, idle)
    return res


def parse_net(s: str):
    """Pick the most relevant interface; return (name, rx_bytes, tx_bytes)."""
    ifaces = {}
    for line in s.splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        if name == "lo":
            continue
        parts = rest.split()
        if len(parts) < 16:
            continue
        try:
            ifaces[name] = (int(parts[0]), int(parts[8]))
        except ValueError:
            continue
    for pref in ("wlan0", "eth0"):
        if pref in ifaces:
            return (pref, *ifaces[pref])
    if ifaces:
        name = max(ifaces, key=lambda k: sum(ifaces[k]))
        return (name, *ifaces[name])
    return ("?", 0, 0)


def parse_thermal(s: str):
    out = []
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            name = parts[0].strip() or "zone"
            val = int(parts[1])
            c = val / 1000.0 if abs(val) > 1000 else float(val)
            out.append((name, round(c, 1)))
    return out


def parse_gpu(s: str):
    """Best-effort GPU load %. Returns int 0-100 or None if not exposed."""
    for line in s.splitlines():
        if "=" not in line:
            continue
        _, val = line.split("=", 1)
        m = re.search(r"(\d+)", val)
        if m:
            v = int(m.group(1))
            if 0 <= v <= 100:
                return v
    return None


def parse_screen(s: str) -> str:
    m = re.search(r"mWakefulness=(\w+)", s)
    return m.group(1) if m else "Unknown"


def parse_foreground(s: str) -> str:
    m = re.search(r"u0\s+([\w.]+/[\w.$]+)", s)
    if m:
        return m.group(1)
    m = re.search(r"([\w.]+/[\w.$]+)", s)
    return m.group(1) if m else "?"


def parse_uptime(s: str) -> float:
    try:
        return float(s.split()[0])
    except (ValueError, IndexError):
        return 0.0


def parse_loadavg(s: str):
    try:
        p = s.split()
        return (float(p[0]), float(p[1]), float(p[2]))
    except (ValueError, IndexError):
        return (0.0, 0.0, 0.0)


def parse_processes(raw: str, limit: int = 40):
    """Top processes by RSS from `ps -A -o RSS=,PID=,NAME=`.

    Each line is: <rss_kb> <pid> <name>. Kernel threads (RSS 0) are dropped.
    """
    procs = []
    for line in raw.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        rss = int(parts[0])
        if rss <= 0:
            continue
        procs.append({"name": parts[2].strip(), "pid": int(parts[1]), "rss_kb": rss})
    procs.sort(key=lambda p: p["rss_kb"], reverse=True)
    return procs[:limit]


def _freq_to_channel(freq):
    if not freq:
        return None
    if 2412 <= freq <= 2484:
        return 14 if freq == 2484 else (freq - 2407) // 5
    if 5000 <= freq <= 5900:
        return (freq - 5000) // 5
    if 5955 <= freq <= 7115:  # 6 GHz
        return (freq - 5950) // 5
    return None


def _band(freq):
    if not freq:
        return "?"
    if freq < 2500:
        return "2.4 GHz"
    if freq < 5955:
        return "5 GHz"
    return "6 GHz"


def parse_wifi(s: str) -> dict:
    """Parse the WifiInfo line from `cmd wifi status`."""
    d = {"rssi": None, "link_mbps": None, "tx_mbps": None, "freq": None,
         "ssid": None, "bssid": None, "channel": None, "band": "?"}
    m = re.search(r"RSSI:\s*(-?\d+)", s)
    if m:
        d["rssi"] = int(m.group(1))
    m = re.search(r"Tx Link speed:\s*(\d+)", s)
    if m:
        d["tx_mbps"] = int(m.group(1))
    m = re.search(r"Link speed:\s*(\d+)", s)
    if m:
        d["link_mbps"] = int(m.group(1))
    m = re.search(r"Frequency:\s*(\d+)", s)
    if m:
        d["freq"] = int(m.group(1))
        d["channel"] = _freq_to_channel(d["freq"])
        d["band"] = _band(d["freq"])
    m = re.search(r"BSSID:\s*([0-9a-fA-F:]{17})", s)
    if m:
        d["bssid"] = m.group(1)
    m = re.search(r'SSID:\s*"?([^",]+)"?', s)
    if m:
        d["ssid"] = m.group(1).strip()
    return d


def parse_netinfo(s: str) -> dict:
    """Parse DNS / network config from a connectivity NetworkAgentInfo line."""
    d = {}
    m = re.search(r"DnsAddresses:\s*\[([^\]]*)\]", s)
    if m:
        d["dns"] = [x.strip().lstrip("/") for x in m.group(1).split(",") if x.strip()]
    m = re.search(r"UsePrivateDns:\s*(\w+)", s)
    d["private_dns"] = (m.group(1) == "true") if m else None
    m = re.search(r"ValidatedPrivateDnsAddresses:\s*\[([^\]]*)\]", s)
    if m:
        d["private_dns_addrs"] = [x.strip() for x in m.group(1).split(",") if x.strip()]
    m = re.search(r"ServerAddress:\s*/?([\d.]+)", s)
    if m:
        d["gateway"] = m.group(1)
    m = re.search(r'SSID:\s*"([^"]+)"', s)
    if m:
        d["ssid"] = m.group(1)
    m = re.search(r"SignalStrength:\s*(-?\d+)", s)
    if m:
        d["signal"] = int(m.group(1))
    m = re.search(r"LinkAddresses:\s*\[([^\]]*)\]", s)
    if m:
        for a in m.group(1).split(","):
            a = a.strip()
            if re.match(r"\d+\.\d+\.\d+\.\d+", a):
                d["ip"] = a
                break
    return d


def parse_bt(raw: str) -> dict:
    """Parse `dumpsys bluetooth_manager` into adapter state + paired devices."""
    d = {"enabled": False, "name": "?", "address": "?", "state": "?",
         "conn_state": "?", "codec": None, "bonded": []}
    for line in raw.splitlines():
        s = line.strip()
        m = re.match(r"enabled:\s*(\w+)", s)
        if m:
            d["enabled"] = m.group(1).lower() == "true"
        m = re.match(r"state:\s*(\w+)", s, re.I)
        if m and d["state"] == "?":
            d["state"] = m.group(1)
        m = re.match(r"ConnectionState:\s*STATE_(\w+)", s)
        if m and d["conn_state"] == "?":
            d["conn_state"] = m.group(1)
        m = re.search(r"codecType\W+(\w+)|Current Codec[^:]*:\s*(\w+)", s)
        if m and not d["codec"]:
            d["codec"] = m.group(1) or m.group(2)
        m = re.match(r"name:\s*(.+)", s)
        if m and d["name"] == "?":
            d["name"] = m.group(1).strip()
        m = re.match(r"address:\s*([0-9A-Fa-f:]{17})", s)
        if m and d["address"] == "?":
            d["address"] = m.group(1)

    capture = False
    for line in raw.splitlines():
        if "Bonded devices" in line:
            capture = True
            continue
        if capture:
            mm = re.search(r"([0-9A-Fa-f:]{17})\s*(.*)", line.strip())
            if mm:
                d["bonded"].append((mm.group(1), mm.group(2).strip()))
            elif line.strip() and not re.search(r"[0-9A-Fa-f:]{17}", line):
                break
    return d


class StateTracker:
    def __init__(self):
        self.reset()

    def reset(self):
        self._prev_cpu = None
        self._prev_net = None
        self._prev_t = None

    def update(self, raw: str) -> dict:
        secs = split_sections(raw)
        now = time.monotonic()
        out = {"mem": parse_meminfo(secs.get("MEM", ""))}

        # CPU% via jiffy deltas
        cpu = parse_stat(secs.get("STAT", ""))
        cpu_pct = {}
        if self._prev_cpu:
            for k, (tot, idle) in cpu.items():
                p = self._prev_cpu.get(k)
                if p and tot - p[0] > 0:
                    dt, di = tot - p[0], idle - p[1]
                    cpu_pct[k] = max(0.0, min(100.0, 100.0 * (dt - di) / dt))
        self._prev_cpu = cpu
        out["cpu_pct"] = cpu_pct

        # Network rates via byte deltas
        name, rx, tx = parse_net(secs.get("NET", ""))
        rates = {"iface": name, "rx_bps": 0.0, "tx_bps": 0.0}
        if self._prev_net and self._prev_t and now - self._prev_t > 0:
            dt = now - self._prev_t
            rates["rx_bps"] = max(0.0, (rx - self._prev_net[0]) / dt)
            rates["tx_bps"] = max(0.0, (tx - self._prev_net[1]) / dt)
        self._prev_net, self._prev_t = (rx, tx), now
        out["net"] = rates

        out["wifi"] = parse_wifi(secs.get("WIFI", ""))
        out["netinfo"] = parse_netinfo(secs.get("CONN", ""))
        out["screen"] = parse_screen(secs.get("SCREEN", ""))
        out["foreground"] = parse_foreground(secs.get("FG", ""))
        out["uptime"] = parse_uptime(secs.get("UPTIME", ""))
        out["loadavg"] = parse_loadavg(secs.get("LOAD", ""))
        return out
