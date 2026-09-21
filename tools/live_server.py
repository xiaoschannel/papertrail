"""The live app, as the "papertrail-live" scheduled task runs it: the main checkout's API and web app.

Anything a Claude session starts lives in that session's job object and dies with it, so a live app a
session started goes down whenever that session restarts or is archived — usually right after the merge
it was deploying. The task runs outside every session, so live stays up while sessions come and go.

tools/deploy.py copies this file to <main checkout>/.live/ and registers the task to run that copy, so
the task doesn't depend on this file having reached main yet; don't run it by hand. It must stay
self-contained for the same reason: no imports from the repo.

Each server's output goes to <checkout>/.live/<name>.log. If either server exits, the other is stopped
and so is this script, so the task never shows as running while half the app is down.
"""
import argparse
import ctypes
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW: the task runs in the user's session; no consoles popping up
CREATE_SUSPENDED = 0x00000004
LIVE_API, LIVE_WEB = 8000, 5173


class _Job:
    """A Windows job object holding one server and everything it starts.

    Stopping a server means stopping its whole tree: npm starts Vite through cmd, and stopping npm alone
    leaves Vite serving. taskkill /T did that by walking the process table from a separate program, which
    on a busy machine could take seconds while the survivor went on serving, and its failures went
    unnoticed. A job is one kernel call that can't miss a descendant, and since it kills everything in it
    when its last handle closes, the servers go down with this script even when it is killed outright.
    """

    class _Limits(ctypes.Structure):   # JOBOBJECT_EXTENDED_LIMIT_INFORMATION; only LimitFlags is set
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD), ("IoCounters", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _Accounting(ctypes.Structure):   # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    def __init__(self):
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll")
        for function, args in [(self.kernel32.CreateJobObjectW, [ctypes.c_void_p, wintypes.LPCWSTR]),
                               (self.kernel32.SetInformationJobObject,
                                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]),
                               (self.kernel32.QueryInformationJobObject,
                                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]),
                               (self.kernel32.AssignProcessToJobObject, [wintypes.HANDLE, wintypes.HANDLE]),
                               (self.kernel32.TerminateJobObject, [wintypes.HANDLE, wintypes.UINT]),
                               (self.kernel32.CloseHandle, [wintypes.HANDLE])]:
            function.argtypes, function.restype = args, wintypes.BOOL
        self.kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self.ntdll.NtResumeProcess.argtypes, self.ntdll.NtResumeProcess.restype = [wintypes.HANDLE], ctypes.c_long

        self.handle = self._check(self.kernel32.CreateJobObjectW(None, None), "CreateJobObject")
        limits = self._Limits(LimitFlags=0x2000)   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self._check(self.kernel32.SetInformationJobObject(self.handle, 9, ctypes.byref(limits),
                                                          ctypes.sizeof(limits)), "SetInformationJobObject")

    def _check(self, result, what):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error(), f"{what} failed")
        return result

    def start(self, command: list[str], **popen) -> subprocess.Popen:
        """Start it suspended and resume it only once it's in the job, so nothing it starts can escape."""
        process = subprocess.Popen(command, creationflags=popen.pop("creationflags", 0) | CREATE_SUSPENDED,
                                   **popen)
        try:
            self._check(self.kernel32.AssignProcessToJobObject(self.handle, int(process._handle)),
                        "AssignProcessToJobObject")
            if self.ntdll.NtResumeProcess(int(process._handle)) != 0:
                raise OSError(f"could not resume {command[0]}")
        except BaseException:
            process.kill()
            raise
        return process

    def stop(self, timeout: float = 10) -> None:
        """Kill everything in the job and return once it is all gone, so its ports are free again."""
        self._check(self.kernel32.TerminateJobObject(self.handle, 1), "TerminateJobObject")
        accounting = self._Accounting()
        deadline = time.monotonic() + timeout
        while True:
            self._check(self.kernel32.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                                ctypes.sizeof(accounting), None),
                        "QueryInformationJobObject")
            if accounting.ActiveProcesses == 0 or time.monotonic() > deadline:
                return
            time.sleep(0.01)

    def close(self) -> None:
        self.kernel32.CloseHandle(self.handle)


def run(servers: dict[str, list[str]], root: Path, logs: Path) -> None:
    """Start every server, wait until one of them stops, then stop the rest.

    The commands are an argument so a test can hand it two harmless ones and check the survivor really is
    stopped — what keeps the task from looking healthy while half the app is down.
    """
    running: list[tuple[subprocess.Popen, _Job]] = []
    try:
        for name, command in servers.items():
            log = open(logs / f"{name}.log", "ab")
            log.write(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
            log.flush()
            job = _Job()
            running.append((job.start(command, cwd=root, stdin=subprocess.DEVNULL, stdout=log,
                                      stderr=subprocess.STDOUT, creationflags=NO_WINDOW), job))
        while all(process.poll() is None for process, _ in running):
            time.sleep(0.05)
    finally:
        # Every job, the stopped server's too: npm exiting can leave the Vite it started still serving.
        for process, job in running:
            job.stop()
            job.close()
            process.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", required=True, type=Path, help="the main checkout to serve")
    root = parser.parse_args().checkout
    logs = root / ".live"
    logs.mkdir(exist_ok=True)

    npm = shutil.which("npm")
    if npm is None:
        (logs / "web.log").write_bytes(b"npm is not on the PATH the task sees; install Node.js for all users\n")
        return 1
    run({"api": [str(root / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "api.main:app",
                 "--host", "127.0.0.1", "--port", str(LIVE_API)],
         "web": [npm, "--prefix", "frontend", "run", "dev"]}, root, logs)
    return 1


if __name__ == "__main__":
    sys.exit(main())
