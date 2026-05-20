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

# Tracks the number of lines printed in the previous render pass for dynamic erasure
last_printed_lines = 0

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
    global _dashboard_visible, last_printed_lines
    if not spy_state.enabled:
        return
        
    with spy_state.lock:
        active = spy_state.a
        total = spy_state.t
        slow_routes = list(spy_state.s)
        
    ram = get_ram_usage()
    
    # 72-char wide box boundary calculations
    W = 72
    inner_w = W - 4  # 68 characters
    
    lines = []
    
    # Line 1: Top Border
    lines.append("┌" + "─" * 70 + "┐")
    
    # Line 2: Header metrics bar (aligned segments)
    col1 = f"Active: {active}".ljust(20)
    col2 = f"Total: {total}".ljust(20)
    col3 = f"RAM: {ram:.1f} MB".ljust(22)
    lines.append(f"│ {col1} │ {col2} │ {col3} │")
    
    # Line 3: Divider
    lines.append("├" + "─" * 70 + "┤")
    
    # Line 4: Section title
    lines.append(f"│ {'Slowest Routes (Top 5)'.ljust(inner_w)} │")
    
    # Lines 5-9: Top 5 slow routes slots
    for i in range(5):
        if i < len(slow_routes):
            route = slow_routes[i]
            m = route["m"]
            p = route["p"]
            l = route["l"]
            h = route["h"]
            
            fast = l <= 200
            
            # Form stats suffix e.g. " 21ms (x4)"
            stats = f" {l}ms (x{h})"
            
            # Indicator text takes 4 visible chars: "[✓] " or "[!] "
            # Method pad takes 8 visible chars
            # Path + leaders + stats must equal (inner_w - 4 - 8) = 56 chars
            # Dot leaders must bridge the rest
            max_path_len = 56 - len(stats) - 5 # guarantee at least 5 dot-leaders
            
            if len(p) > max_path_len:
                if max_path_len > 3:
                    p_disp = p[:max_path_len - 3] + "..."
                else:
                    p_disp = p[:max_path_len]
            else:
                p_disp = p
                
            num_dots = 56 - len(p_disp) - len(stats)
            dots = "." * num_dots
            
            # ANSI coloring
            ansi_indicator = f"\x1b[32m[✓]\x1b[0m " if fast else f"\x1b[31m[!]\x1b[0m "
            ansi_dots = f"\x1b[90m{dots}\x1b[0m"
            
            lines.append(f"│ {ansi_indicator}{m:<8}{p_disp}{ansi_dots}{stats} │")
        else:
            # Empty slot padding
            lines.append(f"│ {'-'.ljust(inner_w)} │")
            
    # Line 10: Bottom Border
    lines.append("└" + "─" * 70 + "┘")
    
    dashboard_str = "\n".join(lines) + "\n"
    
    _local.painting = True
    try:
        out = _get_stdout()
        with _console_lock:
            # Force global flush before drawing
            out.flush()
            
            # Erase previous dashboard dynamically
            if _dashboard_visible and last_printed_lines > 0:
                out.write(f"\x1b[{last_printed_lines}A")
                out.write("\x1b[J")
                out.flush()
            out.write(dashboard_str)
            out.flush()
            _dashboard_visible = True
            last_printed_lines = len(lines)
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
                
                global _dashboard_visible, last_printed_lines
                out = _get_stdout()
                with _console_lock:
                    if _dashboard_visible and last_printed_lines > 0:
                        out.write(f"\x1b[{last_printed_lines}A")
                        out.write("\x1b[J")
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
                
                global _dashboard_visible, last_printed_lines
                out = _get_stdout()
                with _console_lock:
                    if _dashboard_visible and last_printed_lines > 0:
                        out.write(f"\x1b[{last_printed_lines}A")
                        out.write("\x1b[J")
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
