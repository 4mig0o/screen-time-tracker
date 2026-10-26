"""Check the packaged self-test and open/close its isolated preview window."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time


def related_pids(parent):
    command = "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'ScreenTimeTracker.exe' } | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"
    raw = subprocess.check_output(["powershell.exe","-NoProfile","-Command",command], creationflags=subprocess.CREATE_NO_WINDOW)
    records = json.loads(raw.decode("utf-8-sig")) if raw.strip() else []
    if isinstance(records, dict):
        records = [records]
    found = {parent}
    for _ in range(4):
        found.update(row["ProcessId"] for row in records if row["ParentProcessId"] in found)
    return found


def preview_windows(pids):
    user = ctypes.windll.user32
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    matches = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def callback(hwnd, _data):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            text = ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(hwnd, text, 512)
            if "演示预览" in text.value:
                matches.append((hwnd,text.value))
        return True
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows(callback, 0)
    return matches


def run(executable):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    code = subprocess.run([str(executable),"--self-test"],startupinfo=startup,timeout=40).returncode
    assert code == 0, f"Packaged self-test exited {code}"
    process = subprocess.Popen([str(executable),"--preview"],startupinfo=startup)
    matched = []
    try:
        for _ in range(5):
            time.sleep(2)
            matched = preview_windows(related_pids(process.pid))
            if matched:
                break
            if process.poll() is not None:
                break
        assert matched, "No packaged dashboard preview window was created"
        time.sleep(1)
        user = ctypes.windll.user32
        user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        for hwnd, _title in matched:
            user.PostMessageW(hwnd,0x0010,0,0)
        assert process.wait(timeout=20) == 0, "Preview did not close cleanly"
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill.exe","/PID",str(process.pid),"/T","/F"],creationflags=subprocess.CREATE_NO_WINDOW,capture_output=True)
    report = {"self_test_exit":code,"preview_title":matched[0][1],"preview_exit":process.returncode}
    Path("qa-output/packaged-results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))


if __name__ == "__main__":
    run(Path(sys.argv[1] if len(sys.argv)>1 else "dist/ScreenTimeTracker.exe").resolve())
