"""内存复核：起真服务、压一轮并发请求、量常驻内存。

验收清单要求"3 账号并发时 < 260 MB"。真实刷课负载需要账号，因此这里做的是
**可比对的近似**：起服务 → 空载读数 → 并发压一轮（模拟多账号同时请求）→ 再读数。
两个数字都打出来，谁都能判断离阈值还有多少余量。

    uv run python scripts/memory_check.py

读数走系统接口（Windows 用 Win32 API，Linux 读 ``/proc``），**不引入第三方依赖** ——
一个复核脚本不该让项目多一个包。
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 验收阈值（MB）
BUDGET_MB = 260
#: 并发压测的请求数（模拟多账号同时刷新的负载形状）
CONCURRENT_REQUESTS = 60


def free_port() -> int:
    """要一个空闲端口。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _rss_windows(pid: int) -> int:
    """Windows：用 Win32 API 读工作集大小。"""

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ —— 少了前者，psapi 会拿不到数据
    # （用 PROCESS_QUERY_LIMITED_INFORMATION 时 GetProcessMemoryInfo 会失败，
    #  返回值看起来像"很小的内存"，那比报错更危险）
    access = 0x0400 | 0x0010
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        raise OSError(f"打不开进程 {pid}（错误码 {ctypes.get_last_error()}）")
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise OSError(f"读内存失败（错误码 {ctypes.get_last_error()}）")
        return int(counters.WorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def rss_bytes(pid: int) -> int:
    """某进程的常驻内存（字节）；读不到返回 0。"""
    if sys.platform == "win32":
        return _rss_windows(pid)
    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return 0
    for line in status.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return 0


def serve_entry() -> Path:
    """venv 里的 ``cx`` 入口。

    直接用它而不是 ``uv run``：少一层启动器，**被测进程就是服务本身**，
    不必再去猜哪一层才是真正占内存的那个（Windows 上找子进程还得依赖已被移除的 wmic）。
    """
    bin_dir = "Scripts" if sys.platform == "win32" else "bin"
    name = "cx.exe" if sys.platform == "win32" else "cx"
    return ROOT / ".venv" / bin_dir / name


def wait_health(port: int, *, timeout: float = 90.0) -> bool:
    """轮询健康检查。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def hit(port: int, path: str) -> int:
    """打一个请求（返回状态码；失败返回 0）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)  # 401/403 也是"服务在正常应答"
    except (urllib.error.URLError, OSError):
        return 0


def measure_via_os(pid: int) -> float:
    """用系统命令独立读一次（MB）—— 与 Win32 API 双源对照。

    单一路径读数有个阴险的失败模式：读错了会得到一个**看起来很正常的小数字**，
    于是"通过"变成了假证据。所以这里再走一遍系统自带的工具。
    """
    if sys.platform == "win32":
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).WorkingSet64",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        text = result.stdout.strip()
        return float(text) / 1024 / 1024 if text.isdigit() else 0.0
    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return 0.0
    for line in status.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("VmRSS:"):
            return float(line.split()[1]) / 1024
    return 0.0


def pid_on_port(port: int) -> int | None:
    """反查**正在监听该端口**的进程。

    这才是"服务进程"的准确定义。按启动器 pid 去量会量到一个 5 MB 的 shim，
    得出一个"看起来很漂亮"的假结论 —— 比报错更危险。
    """
    if sys.platform == "win32":
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, check=False
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            is_listener = (
                len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING"
            )
            if is_listener and parts[1].endswith(f":{port}"):
                return int(parts[4])
        return None
    result = subprocess.run(
        ["ss", "-lptnH", f"sport = :{port}"], capture_output=True, text=True, check=False
    )
    match = re.search(r"pid=(\d+)", result.stdout)
    return int(match.group(1)) if match else None


def measure(port: int, pids: list[int], label: str) -> float:
    """量一轮内存（MB）：同时给出两个来源的读数。"""
    api_reading = sum(rss_bytes(pid) for pid in pids) / 1024 / 1024
    os_reading = sum(measure_via_os(pid) for pid in pids)
    drift = abs(api_reading - os_reading) / max(os_reading, 1) * 100

    print(f"  {label}：{api_reading:.1f} MB（Win32 API）· {os_reading:.1f} MB（系统工具）")
    if os_reading > 0 and drift > 10:
        print(f"    ⚠ 两个来源差 {drift:.0f}%，读数不可信 —— 不要用它下结论")
    return max(api_reading, os_reading)


def main() -> int:
    """入口。"""
    port = free_port()
    temp_dir = Path(tempfile.mkdtemp(prefix="cx-memory-check-"))
    env = {
        **os.environ,
        "APP__DATA_DIR": str(temp_dir),
        "APP__PORT": str(port),
        "APP__HOST": "127.0.0.1",
        "PYTHONUTF8": "1",
    }

    print(f"端口 {port} · 临时库 {temp_dir}")
    try:
        subprocess.run([str(serve_entry()), "db", "upgrade"], cwd=ROOT, env=env, check=True)
        server = subprocess.Popen(
            [str(serve_entry()), "serve", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            if not wait_health(port):
                print("服务没起来", file=sys.stderr)
                return 2

            # 按**端口反查**真正的服务进程；查不到才退回启动器 pid
            pid = pid_on_port(port) or server.pid
            pids = [pid]
            print(f"服务进程 pid={pid}")
            print("内存读数：")
            empty = measure(port, pids, "空载")

            print(f"并发压测：{CONCURRENT_REQUESTS} 个请求（模拟多账号同时刷新）")
            paths = ["/api/health", "/api/auth/me", "/api/settings", "/api/accounts"]
            with ThreadPoolExecutor(max_workers=12) as pool:
                codes = list(
                    pool.map(lambda i: hit(port, paths[i % len(paths)]), range(CONCURRENT_REQUESTS))
                )
            ok = sum(1 for code in codes if code)
            print(f"  成功应答 {ok}/{CONCURRENT_REQUESTS}")

            loaded = measure(port, pids, "并发后")
            verdict = "通过" if loaded < BUDGET_MB else "**超出预算**"
            print()
            print(f"结论：并发后 {loaded:.1f} MB / 预算 {BUDGET_MB} MB → {verdict}")
            print(
                json.dumps(
                    {"empty_mb": round(empty, 1), "loaded_mb": round(loaded, 1)}, ensure_ascii=False
                )
            )
            return 0 if loaded < BUDGET_MB else 1
        finally:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(server.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
