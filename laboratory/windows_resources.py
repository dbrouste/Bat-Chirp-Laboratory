"""Windows process-tree confinement and exclusive campaign ownership."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import subprocess
import time


class BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("reads", "writes", "others", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class ProcessMemory(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ("peak_working", "working", "peak_paged", "paged",
        "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]


def private_commit_bytes():
    if os.name != "nt":
        raise RuntimeError("Windows private commit accounting is required")
    api = ctypes.WinDLL("psapi", use_last_error=True)
    api.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    api.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = ProcessMemory()
    counters.size = ctypes.sizeof(counters)
    if not api.GetProcessMemoryInfo(wintypes.HANDLE(-1), ctypes.byref(counters), counters.size):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.private


class WindowsJob:
    def __init__(self, memory_bytes):
        if os.name != "nt":
            raise RuntimeError("Windows Job Objects are required; process-tree limits cannot be guaranteed")
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000 | 0x0200
        limits.job_memory = memory_bytes
        try:
            self._check(self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            actual = self.information()
            if actual.job_memory != memory_bytes or actual.basic.flags & 0x2200 != 0x2200:
                raise RuntimeError("Windows did not apply the requested job memory/termination limits")
        except BaseException:
            self.close()
            raise

    def _check(self, result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def information(self):
        result = ExtendedLimits()
        self._check(self.api.QueryInformationJobObject(self.handle, 9, ctypes.byref(result), ctypes.sizeof(result), None))
        return result

    def assign(self, process):
        handle = self.api.OpenProcess(0x0100 | 0x0001, False, process.pid)
        self._check(handle)
        try:
            self._check(self.api.AssignProcessToJobObject(self.handle, handle))
        finally:
            self.api.CloseHandle(handle)

    def terminate(self):
        self._check(self.api.TerminateJobObject(self.handle, 1))

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@contextmanager
def campaign_lock(folder: Path, name="campaign.lock"):
    if os.name != "nt":
        raise RuntimeError("Windows file locking is required")
    import msvcrt

    with (folder / name).open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("A supervisor already owns this campaign") from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def run_bounded(command, *, folder, gate, log, seconds, memory_bytes, min_free_bytes, stop_requested):
    started = time.monotonic()
    process = None
    assigned = False
    status, error = "failed", None
    environment = dict(os.environ)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        environment[name] = "1"
    with WindowsJob(memory_bytes) as job, Path(log).open("w", encoding="utf-8") as stream:
        try:
            if shutil.disk_usage(folder).free < min_free_bytes:
                raise RuntimeError("Disk free-space guard reached before launch")
            process = subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[1]),
                                       env=environment, stdout=stream, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            job.assign(process)
            assigned = True
            Path(gate).write_text("assigned", encoding="utf-8")
            while process.poll() is None:
                if stop_requested():
                    status, error = "interrupted", "Stop requested"
                elif time.monotonic() - started >= seconds:
                    status, error = "timed_out", "Wall-time watchdog reached"
                elif shutil.disk_usage(folder).free < min_free_bytes:
                    status, error = "disk_guard", "Disk free-space guard reached"
                else:
                    time.sleep(0.1)
                    continue
                job.terminate()
                break
            process.wait(timeout=10)
            if error is None:
                status = "complete" if process.returncode == 0 else "failed"
                if status == "failed":
                    error = f"Worker exited with {process.returncode}; see execution.log (job commit limit enforced)"
        except KeyboardInterrupt:
            status, error = "interrupted", "Keyboard interrupt"
            job.terminate()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if process is not None:
                job.terminate()
                if process.poll() is None:
                    process.kill()
        finally:
            if process is not None and process.poll() is None:
                job.terminate()
                process.wait(timeout=10)
        peak = job.information().peak_job_memory
    return {"status": status, "error": error, "elapsed_s": time.monotonic() - started,
            "peak_job_commit_bytes": peak, "memory_limit_bytes": memory_bytes,
            "job_memory_limit_verified": True, "process_tree_assigned": assigned,
            "peak_counter_scope": "raw Windows PeakJobMemoryUsed; rejected commits may affect its historical peak"}
