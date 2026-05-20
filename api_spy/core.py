import sys
import os
import time
import threading

# Reentrant lock for terminal output serialization
_console_lock = threading.RLock()

# Thread-local storage to track if the current thread is painting the dashboard
_local = threading.local()

# Tracks whether the dashboard is currently visible on the terminal screen
_dashboard_visible = False

# Original system streams
_original_stdout = None
_original_stderr = None

# Fixed height layout height is exactly 9 lines

def _get_stdout():
    return _original_stdout or sys.__stdout__ or sys.stdout

def _get_stderr():
    return _original_stderr or sys.__stderr__ or sys.stderr

class TelemetryState:
    def __init__(self):
        self.lock = threading.Lock()
        self.a = 0  # Active requests
        self.t = 0  # Total requests
        self.c = [0, 0, 0, 0, 0, 0]  # Status classes [0, 1xx, 2xx, 3xx, 4xx, 5xx]
        self.s = []  # Top 5 slowest routes: list of dicts with {"m", "p", "l", "h"}
        self.enabled = False

    def record_request_start(self):
        with self.lock:
            self.a += 1
        paint_dashboard()

    def record_request_end(self, method, path, status, latency_ms):
        with self.lock:
            if self.a > 0:
                self.a -= 1
            self.t += 1
            
            # Index maps to status // 100
            idx = status // 100
            if idx < 0:
                idx = 0
            elif idx > 5:
                idx = 5
            self.c[idx] += 1
            
            # Update slowest routes cache
            found = False
            latency_int = int(latency_ms)
            for entry in self.s:
                if entry["m"] == method and entry["p"] == path:
                    entry["h"] += 1
                    entry["l"] = max(entry["l"], latency_int)
                    found = True
                    break
            
            if not found:
                self.s.append({
                    "m": method,
                    "p": path,
                    "l": latency_int,
                    "h": 1
                })
            
            # Keep top 5 slowest (highest latency first)
            self.s.sort(key=lambda x: x["l"], reverse=True)
            self.s = self.s[:5]
            
        paint_dashboard()

# Global telemetry state singleton
spy_state = TelemetryState()

def get_ram_usage():
    """Returns resident set size (RAM) in megabytes, using standard library functions."""
    # Windows fallback via ctypes
    try:
        import ctypes
        from ctypes import wintypes
        
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        
        GetProcessMemoryInfo = ctypes.windll.psapi.GetProcessMemoryInfo
        GetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
        
        # Explicitly set argtypes and restypes for 64-bit safety on Windows
        GetCurrentProcess.argtypes = []
        GetCurrentProcess.restype = wintypes.HANDLE
        GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        GetProcessMemoryInfo.restype = wintypes.BOOL
        
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if GetProcessMemoryInfo(GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / (1024.0 * 1024.0)
    except Exception:
        pass

    # Linux fallback via /proc/self/status
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    return float(parts[1]) / 1024.0  # VmRSS is in KB
    except Exception:
        pass

    # macOS and Unix fallback via resource module
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return usage / (1024.0 * 1024.0)  # macOS ru_maxrss is in bytes
        else:
            return usage / 1024.0  # Linux/Unix ru_maxrss is in KB
    except Exception:
        pass

    return 0.0

def is_painting():
    return getattr(_local, "painting", False)

def paint_dashboard():
    global _dashboard_visible
    if not spy_state.enabled:
        return
        
    with spy_state.lock:
        active = spy_state.a
        total = spy_state.t
        slow_routes = list(spy_state.s)
        
    ram = get_ram_usage()
    ram_str = f"{ram:.1f} MB"
    
    # 1. Base header rows (Fixed)
    lines = [
        "┌" + "─" * 70 + "┐",
        f"│ Active: {active:<12} │ Total: {total:<14} │ RAM: {ram_str:<16} │",
        "├" + "─" * 70 + "┤",
        "│ Slowest Routes (Top 5)                                               │"
    ]
    
    # 2. Always generate exactly 5 detail rows unconditionally
    for i in range(5):
        if i < len(slow_routes):
            r = slow_routes[i]
            is_slow = r['l'] > 200
            status = "\x1b[31m[!]\x1b[0m" if is_slow else "\x1b[32m[✓]\x1b[0m"
            method = r['m'].ljust(6)
            path = r['p']
            stats = f"{int(r['l'])}ms (x{r['h']})"
            
            # Truncate path if too long to maintain deterministic W=72 layout
            max_path_len = 50 - len(stats)
            if len(path) > max_path_len:
                if max_path_len > 3:
                    path_disp = path[:max_path_len - 3] + "..."
                else:
                    path_disp = path[:max_path_len]
            else:
                path_disp = path
                
            plain_len = 2 + 4 + 1 + len(method) + 1 + len(path_disp) + 1 + len(stats) + 2
            dots_count = 72 - plain_len
            dots = "." * max(1, dots_count)
            
            lines.append(f"│ {status} {method} {path_disp} \x1b[90m{dots}\x1b[0m {stats} │")
        else:
            # Consistent placeholder spacing for empty rows
            placeholder = "- "
            lines.append(f"│ {placeholder.ljust(68)} │")
            
    # 3. Footer row (Fixed)
    lines.append("└" + "─" * 70 + "┘")
    
    dashboard_str = "\n".join(lines) + "\n"
    
    _local.painting = True
    try:
        out = _get_stdout()
        with _console_lock:
            # Force global flush before drawing
            out.flush()
            
            # Erase previous dashboard (always 10 lines)
            if _dashboard_visible:
                out.write("\x1b[10A\x1b[J")
                out.flush()
            out.write(dashboard_str)
            out.flush()
            _dashboard_visible = True
    except Exception:
        pass
    finally:
        _local.painting = False

class StreamInterceptor:
    def __init__(self, original, is_stderr=False):
        self.original = original
        self.is_stderr = is_stderr
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, data):
        if not data:
            return 0
            
        # Ignore chunks containing border characters or package keywords
        is_dashboard_chunk = False
        for token in ("api-spy", "┌", "└", "─", "│", "├", "┐", "┘", "┬", "┴"):
            if token in data:
                is_dashboard_chunk = True
                break
                
        if is_painting() or is_dashboard_chunk:
            return self.original.write(data)
            
        with self._lock:
            self._buffer += data
            if "\n" in self._buffer:
                # Extract all full lines up to the last newline
                last_nl_idx = self._buffer.rfind("\n")
                to_write = self._buffer[:last_nl_idx + 1]
                self._buffer = self._buffer[last_nl_idx + 1:]
                
                global _dashboard_visible
                out = _get_stdout()
                with _console_lock:
                    if _dashboard_visible:
                        out.write("\x1b[10A\x1b[J")
                        out.flush()
                        _dashboard_visible = False
                        
                    self.original.write(to_write)
                    self.original.flush()
                    
                paint_dashboard()
                return len(data)
            else:
                return len(data)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        with self._lock:
            if self._buffer:
                to_write = self._buffer
                if not to_write.endswith("\n"):
                    to_write += "\n"
                self._buffer = ""
                
                global _dashboard_visible
                out = _get_stdout()
                with _console_lock:
                    if _dashboard_visible:
                        out.write("\x1b[10A\x1b[J")
                        out.flush()
                        _dashboard_visible = False
                    self.original.write(to_write)
                    self.original.flush()
                paint_dashboard()
        self.original.flush()

    def __getattr__(self, attr):
        return getattr(self.original, attr)

def enable_ansi_support():
    """Enables virtual terminal processing (ANSI escape sequence support) on Windows."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            
            kernel32 = ctypes.windll.kernel32
            STD_OUTPUT_HANDLE = -11
            GetStdHandle = kernel32.GetStdHandle
            GetStdHandle.argtypes = [wintypes.DWORD]
            GetStdHandle.restype = wintypes.HANDLE
            
            GetConsoleMode = kernel32.GetConsoleMode
            GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            GetConsoleMode.restype = wintypes.BOOL
            
            SetConsoleMode = kernel32.SetConsoleMode
            SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            SetConsoleMode.restype = wintypes.BOOL
            
            hOut = GetStdHandle(STD_OUTPUT_HANDLE)
            if hOut != wintypes.HANDLE(-1).value and hOut is not None:
                mode = wintypes.DWORD()
                if GetConsoleMode(hOut, ctypes.byref(mode)):
                    # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                    mode.value |= 0x0004
                    SetConsoleMode(hOut, mode)
        except Exception:
            pass

def start_spy():
    global _original_stdout, _original_stderr
    with spy_state.lock:
        if spy_state.enabled:
            return
            
        # Verify running in a standard TTY before starting stream hooks
        if not sys.stdout.isatty():
            return
            
        enable_ansi_support()
            
        _original_stdout = sys.stdout
        _original_stderr = sys.stderr
        
        sys.stdout = StreamInterceptor(sys.stdout, is_stderr=False)
        sys.stderr = StreamInterceptor(sys.stderr, is_stderr=True)
        spy_state.enabled = True
        
    paint_dashboard()

def stop_spy():
    global _original_stdout, _original_stderr, _dashboard_visible
    with spy_state.lock:
        if not spy_state.enabled:
            return
            
        if _original_stdout:
            sys.stdout = _original_stdout
        if _original_stderr:
            sys.stderr = _original_stderr
            
        spy_state.enabled = False
        
        # Cleanly erase final dashboard from the viewport
        with _console_lock:
            if _dashboard_visible:
                sys.stdout.write("\r\x1b[10A\x1b[J")
                sys.stdout.flush()
                _dashboard_visible = False
