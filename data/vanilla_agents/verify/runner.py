"""Run one check in its own process tree with a timeout; the whole tree dies on timeout and when the check ends (Windows Job Object, else a process group).

Output goes to a temp file (no pipe deadlocks), is capped to head+tail, and is decoded as UTF-8 with replacement.
The environment is an allowlist: credentials and the user profile are never passed unless a check names them."""
import os
import subprocess
import tempfile
import threading
import time

IS_NT = os.name == "nt"
BASE_ENV_NT = ("SystemRoot", "PATH", "PATHEXT", "COMSPEC", "WINDIR")
BASE_ENV_POSIX = ("PATH", "LANG", "HOME")
CANCEL = threading.Event()    # set by the app's Pause: the running check's whole tree is killed and later checks do not start

if IS_NT:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount",
                                                      "WriteTransferCount", "OtherTransferCount")]

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class _EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _nt = ctypes.WinDLL("ntdll")
    _nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _nt.NtResumeProcess.restype = ctypes.c_long
    _CREATE_SUSPENDED = 0x4
    _JOB_EXTENDED_LIMIT = 9
    _KILL_ON_JOB_CLOSE = 0x2000


class ProcessTree:
    """Containment for one check: every process it starts is killed when the job is terminated or closed."""

    def __init__(self):
        self.job = None
        self.assigned = False
        if IS_NT:
            self.job = _k32.CreateJobObjectW(None, None)
            if not self.job:
                raise OSError("CreateJobObjectW failed (error %d)" % ctypes.get_last_error())
            info = _EXT()
            info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
            if not _k32.SetInformationJobObject(self.job, _JOB_EXTENDED_LIMIT, ctypes.byref(info), ctypes.sizeof(info)):
                err = ctypes.get_last_error()
                self.close()
                raise OSError("SetInformationJobObject failed (error %d)" % err)

    def attach(self, proc):
        """Windows: the process was created suspended; put it in the job, then resume it. Raises OSError (-> BLOCKED) on any failure,
        so a check never runs uncontained. Elsewhere the process group is the containment."""
        if IS_NT:
            h = wintypes.HANDLE(int(proc._handle))
            if not _k32.AssignProcessToJobObject(self.job, h):
                raise OSError("AssignProcessToJobObject failed (error %d)" % ctypes.get_last_error())
            self.assigned = True
            if _nt.NtResumeProcess(h) != 0:
                raise OSError("NtResumeProcess failed")

    def kill(self, proc):
        if IS_NT:
            if self.job and self.assigned:
                _k32.TerminateJobObject(self.job, 1)
            else:  # fallback: kill the tree by pid
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def close(self):
        if IS_NT and self.job:
            _k32.CloseHandle(self.job)
            self.job = None


def build_env(allow, run_tmp):
    names = list(BASE_ENV_NT if IS_NT else BASE_ENV_POSIX) + list(allow or [])
    env = {k: os.environ[k] for k in names if k in os.environ}
    env.update({"TEMP": run_tmp, "TMP": run_tmp, "TMPDIR": run_tmp, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "CI": "1"})
    return env


def _read_capped(path, cap):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size <= cap * 2:
            data = f.read()
            note = ""
        else:
            head = f.read(cap)
            f.seek(size - cap)
            data = head + b"\n...[%d bytes omitted]...\n" % (size - 2 * cap) + f.read()
            note = "truncated"
    return data.decode("utf-8", "replace"), note


def run_check(argv, cwd, env, timeout, max_output=65536, tmp_dir=None):
    """-> {exit_code, output, timed_out, duration, truncated}. Never raises for a failing or hanging check."""
    if CANCEL.is_set():
        return {"exit_code": None, "output": "cancelled", "timed_out": False, "duration": 0.0, "truncated": "", "cancelled": True}
    tmp_dir = tmp_dir or tempfile.gettempdir()
    out_path = os.path.join(tmp_dir, "out_%d_%s.txt" % (time.time_ns(), os.urandom(3).hex()))
    t0 = time.time()
    timed_out, cancelled, code = False, False, None
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | _CREATE_SUSPENDED) if IS_NT else 0
    try:
        tree = ProcessTree()
    except OSError as e:
        return {"exit_code": None, "output": "could not start: no containment: %s" % e, "timed_out": False, "duration": 0.0, "truncated": "", "start_error": True}
    try:
        with open(out_path, "wb") as out:
            try:
                proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, creationflags=flags,
                                        start_new_session=not IS_NT)
            except OSError as e:
                return {"exit_code": None, "output": "could not start: %s" % e, "timed_out": False, "duration": 0.0, "truncated": "", "start_error": True}
            try:
                tree.attach(proc)
            except OSError as e:
                tree.kill(proc)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                return {"exit_code": None, "output": "could not start: not contained: %s" % e, "timed_out": False, "duration": 0.0, "truncated": "", "start_error": True}
            try:
                deadline = time.time() + timeout
                while True:
                    try:
                        code = proc.wait(timeout=max(0.01, min(1.0, deadline - time.time())))
                        break
                    except subprocess.TimeoutExpired:
                        if CANCEL.is_set():
                            cancelled = True
                            raise
                        if time.time() >= deadline:
                            raise
            except subprocess.TimeoutExpired:
                timed_out = not cancelled
                tree.kill(proc)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            finally:
                tree.kill(proc)           # no orphans, even after a normal exit
    finally:
        tree.close()
    text, note = _read_capped(out_path, max_output)
    try:
        os.remove(out_path)
    except OSError:
        pass
    return {"exit_code": code, "output": text, "timed_out": timed_out, "duration": round(time.time() - t0, 3), "truncated": note, "cancelled": cancelled}
