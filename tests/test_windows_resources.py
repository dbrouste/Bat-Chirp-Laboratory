"""Real Windows confinement checks with tiny, corpus-free Python subprocesses."""

import os
import ctypes
from ctypes import wintypes
import sys

import pytest

from laboratory.windows_resources import campaign_lock, run_bounded

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects only")


def bounded(tmp_path, payload, *, seconds=5, stop=lambda: False, memory=512 * 1024**2):
    gate = tmp_path / "gate"
    code = "import sys,time; from pathlib import Path; gate=Path(sys.argv[1])\nwhile not gate.exists(): time.sleep(0.01)\n" + payload
    return run_bounded([sys.executable, "-c", code, str(gate)], folder=tmp_path, gate=gate,
                       log=tmp_path / "execution.log", seconds=seconds, memory_bytes=memory,
                       min_free_bytes=0, stop_requested=stop)


def test_job_memory_is_measurable_and_normal_process_completes(tmp_path):
    result = bounded(tmp_path, "print('ready', flush=True)")
    assert result["status"] == "complete"
    assert 0 < result["peak_job_commit_bytes"] <= result["memory_limit_bytes"]


def test_watchdog_terminates_worker(tmp_path):
    result = bounded(tmp_path, "time.sleep(60)", seconds=0.3)
    assert result["status"] == "timed_out"
    assert result["elapsed_s"] < 10


def test_stop_terminates_worker(tmp_path):
    checks = []

    def stop():
        checks.append(True)
        return len(checks) >= 3

    assert bounded(tmp_path, "time.sleep(60)", stop=stop)["status"] == "interrupted"


def test_commit_limit_prevents_large_allocation(tmp_path):
    result = bounded(tmp_path, "payload = bytearray(256 * 1024**2)", memory=128 * 1024**2)
    assert result["status"] == "failed"
    assert "MemoryError" in (tmp_path / "execution.log").read_text(encoding="utf-8")


def test_campaign_lock_rejects_second_supervisor(tmp_path):
    with campaign_lock(tmp_path):
        with pytest.raises(RuntimeError, match="already owns"):
            with campaign_lock(tmp_path):
                pass


def test_disk_guard_fails_explicitly_before_process_launch(tmp_path):
    gate = tmp_path / "gate"
    result = run_bounded([sys.executable, "-c", "raise RuntimeError('must not run')"],
        folder=tmp_path, gate=gate, log=tmp_path / "execution.log", seconds=1,
        memory_bytes=512 * 1024**2, min_free_bytes=2**63, stop_requested=lambda: False)
    assert result["status"] == "failed" and "Disk" in result["error"]
    assert not gate.exists()


def test_timeout_kills_descendants_as_well_as_root(tmp_path):
    pid_path = tmp_path / "child.pid"
    payload = ("import subprocess\n"
               "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
               f"Path({str(pid_path)!r}).write_text(str(child.pid))\n"
               "time.sleep(60)")
    result = bounded(tmp_path, payload, seconds=1)
    assert result["status"] == "timed_out"
    assert pid_path.is_file()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, int(pid_path.read_text()))
    if handle:
        try:
            exit_code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            assert exit_code.value != 259
        finally:
            kernel.CloseHandle(handle)


def test_job_memory_limit_is_shared_by_parent_and_all_children(tmp_path):
    child_code = ("import json,time; from laboratory.windows_resources import private_commit_bytes\n"
                  "before=private_commit_bytes()\n"
                  "try:\n memory=bytearray(200*1024**2); failed=False\n"
                  "except MemoryError:\n failed=True\n"
                  "after=private_commit_bytes()\n"
                  "print(json.dumps({'before':before,'after':after,'failed':failed}),flush=True)\n"
                  "time.sleep(1)")
    payload = ("import subprocess,json\n"
               "from laboratory.windows_resources import private_commit_bytes\n"
               "parent_memory=bytearray(80*1024**2)\n"
               f"code={child_code!r}\n"
               "children=[subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,text=True) for index in range(2)]\n"
               "records=[json.loads(child.stdout.readline()) for child in children]\n"
               "assert any(record['failed'] for record in records),records\n"
               "assert all(record['after']-record['before']<5*1024**2 for record in records if record['failed']),records\n"
               "assert private_commit_bytes()+sum(record['after'] for record in records)<=384*1024**2,records\n"
               "print(json.dumps(records),flush=True)\n"
               "assert all(child.wait()==0 for child in children)\n")
    result = bounded(tmp_path, payload, memory=384*1024**2)
    assert result["status"] == "complete"
    assert result["job_memory_limit_verified"] and result["process_tree_assigned"]
    assert '"failed": true' in (tmp_path / "execution.log").read_text(encoding="utf-8")
