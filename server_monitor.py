import argparse
import ctypes
import os
import shutil
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oraplay.settings")

STILL_ACTIVE = 259
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def filetime_number(value):
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


class ResourceMonitor:
    """Sample machine and workbench process utilization without extra packages."""
    def __init__(self, pid):
        self.pid = pid
        self.latest = {}
        self.peaks = {}
        self.previous_system = None
        self.previous_process = None
        self.previous_sample_at = None
        self.handle = None
        self.kernel = None
        self.psapi = None
        if os.name == "nt":
            from ctypes import wintypes

            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            self.kernel.GetExitCodeProcess.restype = wintypes.BOOL
            self.kernel.GetSystemTimes.argtypes = [ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
            self.kernel.GetSystemTimes.restype = wintypes.BOOL
            self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
            self.kernel.GetProcessTimes.restype = wintypes.BOOL
            self.kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
            self.kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
            self.handle = self.kernel.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
            self.psapi = ctypes.WinDLL("psapi", use_last_error=True)
            self.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD]
            self.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    def exit_code(self):
        if os.name == "nt":
            if not self.handle:
                return 1
            from ctypes import wintypes

            code = wintypes.DWORD()
            if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
                return 1
            return None if code.value == STILL_ACTIVE else int(code.value)
        try:
            os.kill(self.pid, 0)
            return None
        except OSError:
            return 1

    def sample(self):
        now = time.monotonic()
        snapshot = {"cpu_count": os.cpu_count() or 1}
        if os.name == "nt":
            idle, kernel_time, user_time = FILETIME(), FILETIME(), FILETIME()
            if self.kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                idle_value = filetime_number(idle)
                total_value = filetime_number(kernel_time) + filetime_number(user_time)
                if self.previous_system:
                    total_delta = total_value - self.previous_system[1]
                    idle_delta = idle_value - self.previous_system[0]
                    if total_delta > 0:
                        snapshot["system_cpu"] = max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta))
                self.previous_system = (idle_value, total_value)

            memory = MEMORYSTATUSEX()
            memory.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if self.kernel.GlobalMemoryStatusEx(ctypes.byref(memory)):
                used = memory.ullTotalPhys - memory.ullAvailPhys
                snapshot["system_memory_percent"] = float(memory.dwMemoryLoad)
                snapshot["system_memory_used"] = used
                snapshot["system_memory_total"] = memory.ullTotalPhys
                snapshot["system_memory_available"] = memory.ullAvailPhys

            if self.handle:
                creation, exited, kernel_proc, user_proc = FILETIME(), FILETIME(), FILETIME(), FILETIME()
                if self.kernel.GetProcessTimes(self.handle, ctypes.byref(creation), ctypes.byref(exited), ctypes.byref(kernel_proc), ctypes.byref(user_proc)):
                    process_time = filetime_number(kernel_proc) + filetime_number(user_proc)
                    if self.previous_process is not None and self.previous_sample_at is not None:
                        elapsed = max(0.001, now - self.previous_sample_at)
                        snapshot["process_cpu_percent"] = max(0.0, 100.0 * (process_time - self.previous_process) / 10_000_000 / elapsed / snapshot["cpu_count"])
                    self.previous_process = process_time
                    counters = PROCESS_MEMORY_COUNTERS_EX()
                    counters.cb = ctypes.sizeof(counters)
                    if self.psapi.GetProcessMemoryInfo(self.handle, ctypes.byref(counters), counters.cb):
                        snapshot["process_memory"] = counters.WorkingSetSize
                        snapshot["process_peak_memory"] = counters.PeakWorkingSetSize
            self.previous_sample_at = now

        try:
            disk = shutil.disk_usage(Path.cwd())
            snapshot["disk_free"] = disk.free
            snapshot["disk_total"] = disk.total
        except OSError:
            pass

        self.latest.update(snapshot)
        for key, value in snapshot.items():
            if key.endswith("_percent") or key in {"system_cpu", "process_cpu_percent", "process_memory", "system_memory_used"}:
                if isinstance(value, (int, float)):
                    self.peaks[key] = max(self.peaks.get(key, value), value)
        return snapshot

    def summary(self):
        latest, peaks = self.latest, self.peaks

        def percent(label, key):
            if key not in latest:
                return f"{label}: unavailable"
            return f"{label}: {latest[key]:.1f}% latest, {peaks.get(key, latest[key]):.1f}% peak"

        def gib(value):
            return f"{value / (1024 ** 3):.2f} GiB"

        lines = [f"Logical CPU cores: {latest.get('cpu_count', os.cpu_count() or 1)}"]
        lines.append(percent("System CPU", "system_cpu"))
        lines.append(percent("System RAM used", "system_memory_percent"))
        if "system_memory_used" in latest:
            lines[-1] += (
                f" ({gib(latest['system_memory_used'])} used, "
                f"{gib(latest['system_memory_available'])} available, "
                f"{gib(latest['system_memory_total'])} total)"
            )
        lines.append(percent("Workbench process CPU", "process_cpu_percent"))
        if "process_memory" in latest:
            process_peak = max(peaks.get("process_memory", 0), latest.get("process_peak_memory", 0))
            lines.append(f"Workbench process memory: {gib(latest['process_memory'])} latest; {gib(process_peak)} peak")
        else:
            lines.append("Workbench process memory: unavailable")
        if "disk_free" in latest:
            lines.append(f"Disk free at server working directory: {gib(latest['disk_free'])} of {gib(latest['disk_total'])}")
        else:
            lines.append("Disk free space: unavailable")
        return "\n".join(lines)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def monitor_log(path, message):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def health_check(url, host_header):
    request = urllib.request.Request(url, headers={"Host": host_header, "Connection": "close"})
    try:
        import ssl

        context = ssl._create_unverified_context() if url.lower().startswith("https:") else None
        with urllib.request.urlopen(request, timeout=4, context=context) as response:
            return response.status == 200, f"HTTP {response.status}"
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        return False, f"{type(error).__name__}: {error}"


def send_report(recipient, description, pid, resource_summary, log_path):
    try:
        import django

        django.setup()
        from workbench.server_alerts import send_server_crash_report

        send_server_crash_report(recipient, description, pid, resource_summary)
        monitor_log(log_path, f"Crash report sent to {recipient}: {description}")
        return True
    except Exception as error:
        monitor_log(log_path, f"Could not send crash report: {type(error).__name__}: {error}")
        return False


def crash_description(path, fallback):
    try:
        summary = path.read_text(encoding="utf-8", errors="replace").strip()
        if summary:
            path.unlink(missing_ok=True)
            return f"{fallback} Server error summary: {summary[:400]}"
    except OSError:
        pass
    return fallback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--host-header", required=True)
    parser.add_argument("--stop-marker", type=Path, required=True)
    parser.add_argument("--monitor-log", type=Path, required=True)
    parser.add_argument("--crash-summary", type=Path, required=True)
    args = parser.parse_args()

    failure_count = 0
    outage_reported = False
    resources = ResourceMonitor(args.pid)
    try:
        while True:
            if args.stop_marker.exists():
                try:
                    args.stop_marker.unlink()
                except OSError:
                    pass
                monitor_log(args.monitor_log, f"Intentional shutdown recorded for server PID {args.pid}.")
                return 0

            resources.sample()
            exit_code = resources.exit_code()
            if exit_code is not None:
                description = crash_description(
                    args.crash_summary,
                    f"The server process exited unexpectedly with exit code {exit_code}.",
                )
                for _attempt in range(3):
                    if send_report(args.recipient, description, args.pid, resources.summary(), args.monitor_log):
                        break
                    time.sleep(30)
                return 1

            healthy, detail = health_check(args.health_url, args.host_header)
            if healthy:
                failure_count = 0
                outage_reported = False
            else:
                failure_count += 1
                if failure_count >= 3 and not outage_reported:
                    sent = send_report(
                        args.recipient,
                        "The server process is still running, but the local health endpoint failed three consecutive checks. "
                        f"Latest check: {detail}",
                        args.pid,
                        resources.summary(),
                        args.monitor_log,
                    )
                    outage_reported = sent
            time.sleep(15)
    finally:
        resources.close()


if __name__ == "__main__":
    raise SystemExit(main())
