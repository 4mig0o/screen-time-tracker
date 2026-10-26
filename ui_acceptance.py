"""Repeatable checks against a real Tk window using disposable data only."""
import argparse
from contextlib import ExitStack
import ctypes
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace

from PIL import Image, ImageGrab
import screen_time_tracker as core


def pump(app, seconds=.35):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        app.update()
        time.sleep(.008)


def snapshot(app, destination):
    if app.state() == "iconic":
        app.deiconify()
        app.geometry(f"{app.winfo_width()}x{app.winfo_height()}-20000-20000")
    pump(app, .15)
    # Capture our HWND rather than screen coordinates: unrelated foreground apps
    # must never enter a preview artifact when the dashboard is occluded.
    user = ctypes.windll.user32
    user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user.GetAncestor.restype = ctypes.c_void_p
    failure = None
    previous_geometry = app.geometry()
    was_topmost = app.attributes("-topmost")
    for attempt in range(4):
        if attempt == 2:
            # Tk Canvas may not receive WM_PAINT when entirely offscreen. Briefly
            # expose only our preview, without capturing any other window.
            app.geometry(f"{app.winfo_width()}x{app.winfo_height()}+0+0")
            app.attributes("-toolwindow", False)
            app.attributes("-topmost", True)
            pump(app, .35)
        for hwnd in (user.GetAncestor(app.winfo_id(), 2), app.winfo_id()):
            try:
                image = ImageGrab.grab(window=hwnd)
                colors = image.getcolors(image.width * image.height)
                assert any(color == (17,19,24) and count > 1000 for count,color in colors), "Dashboard HWND was not captured"
                white = sum(count for count,color in colors if color == (255,255,255))
                assert white < image.width * image.height * .03, "Tk canvases were not painted into the capture"
                image.save(destination)
                if attempt >= 2:
                    app.attributes("-topmost", was_topmost)
                    app.attributes("-toolwindow", True)
                    app.geometry(previous_geometry)
                return
            except (OSError, RuntimeError, AssertionError) as error:
                failure = error
        pump(app, .25)
    raise RuntimeError(f"Preview capture failed: {failure}; state={app.state()}, id={app.winfo_id()}, size={app.winfo_width()}x{app.winfo_height()}")


def capture_client(hwnd):
    """WM_PRINT captures only our client area, including when another app covers it."""
    from ctypes import wintypes
    user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetDC.argtypes = [wintypes.HWND]
    user.GetDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
    gdi.SelectObject.restype = ctypes.c_void_p
    gdi.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    rect = wintypes.RECT()
    user.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right, rect.bottom
    if width <= 0 or height <= 0:
        raise RuntimeError("Preview window has no client area")
    source = user.GetDC(hwnd)
    target = gdi.CreateCompatibleDC(source)
    bitmap = gdi.CreateCompatibleBitmap(source, width, height)
    old = gdi.SelectObject(target, bitmap)
    try:
        if not user.PrintWindow(hwnd, target, 3):
            raise RuntimeError("PrintWindow could not render the preview")
        class HEADER(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG), ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD), ("bytes", wintypes.DWORD), ("x", wintypes.LONG), ("y", wintypes.LONG), ("used", wintypes.DWORD), ("important", wintypes.DWORD)]
        header = HEADER(ctypes.sizeof(HEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        data = ctypes.create_string_buffer(width*height*4)
        gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        gdi.SelectObject(target, old)
        if not gdi.GetDIBits(target, bitmap, 0, height, data, ctypes.byref(header), 0):
            raise RuntimeError("Could not read preview pixels")
        return Image.frombytes("RGB", (width,height), data.raw, "raw", "BGRX")
    finally:
        gdi.SelectObject(target, old)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(target)
        user.ReleaseDC(hwnd, source)


def all_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from all_widgets(child)


def run(output, scales=(1, 1.25, 1.5, 2)):
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for scale in scales:
        with tempfile.TemporaryDirectory(prefix="screen-time-qa-") as folder, ExitStack() as cleanup:
            directory = Path(folder)
            core.seed_demo_data(directory)
            app = core.ScreenTimeTrackerApp(directory, start_collector=False, preview=True, ui_scale=scale)
            cleanup.callback(app.close)
            # Render offscreen; don't compete with the user's foreground app.
            app.attributes("-toolwindow", True)
            app.geometry(f"{app.winfo_width() if app.winfo_width()>1 else min(app.px(1320),app.winfo_screenwidth()-50)}x{min(app.px(940),app.winfo_screenheight()-100)}-20000-20000")
            pump(app, .8)
            normal_geometry = app.geometry()
            for page in (0, 1):
                app.select_page(page)
                pump(app, .5)
                snapshot(app, output / f"{'day' if page == 0 else 'week'}-{scale:g}x.png")
                results.append({"scale": scale, "page": page, "size": [app.winfo_width(), app.winfo_height()], "errors": list(app._tk_errors)})
            # Both advertised display sizes, capped at the actual display work area.
            for width, height in ((1366, 768), (1920, 1080)):
                app.geometry(f"{min(width,app.winfo_screenwidth()-40)}x{min(height,app.winfo_screenheight()-100)}-20000-20000")
                pump(app, .2)
                for page in (0, 1):
                    app.select_page(page)
                    pump(app, .2)
                    results.append({"scale": scale, "requested_display": [width,height], "actual_window": [app.winfo_width(),app.winfo_height()], "page":page, "errors":list(app._tk_errors)})
            app.geometry(normal_geometry)
            app.select_page(0)
            # Long title and at least 100 entries, entirely in a temporary database.
            for index in range(110):
                app.database.add_sample(datetime.now(), f"qa-{index}.exe", f"长应用名称-{index}-用于检查名称与时长不重叠-"*3, 300+index)
            app.refresh_all()
            pump(app, .3)
            app.day_scroll_canvas.yview_moveto(1)
            pump(app, .15)
            snapshot(app, output / f"long-list-{scale:g}x.png")
            scroll = app._scrolls[app.day_apps_canvas]
            before = scroll.current()
            for _ in range(8):
                app._wheel(SimpleNamespace(widget=app.day_apps_canvas, delta=-15))
            pump(app, .24)
            assert scroll.current() > before, "Small wheel inputs must move the list"
            elapsed = []
            start = time.perf_counter()
            app._jobs.submit("slow-qa", lambda: time.sleep(2), lambda _value, _error: None)
            app.after(80, lambda: elapsed.append(time.perf_counter()-start))
            scroll.push(80*scale)
            pump(app, .3)
            assert elapsed and elapsed[0] < .3, f"UI heartbeat stalled: {elapsed}"
            app.day_apps_canvas.yview_moveto(1)
            pump(app, .1)
            assert app.day_apps_canvas.yview()[1] > .99
            app.geometry(f"{min(app.px(720), app.winfo_screenwidth()-40)}x{min(app.px(600),app.winfo_screenheight()-100)}-20000-20000")
            app.top_app_var.set("这是用于检查自动换行的超长应用名称" * 4 + " · 2 小时")
            app.status_var.set("采集进程运行中\n当前应用：" + "超长应用名称" * 10)
            pump(app, .3)
            app.day_scroll_canvas.yview_moveto(0)
            snapshot(app, output / f"compact-{scale:g}x.png")
            label_clips = []
            for widget in all_widgets(app):
                if widget.winfo_class() == "Label" and widget.winfo_ismapped():
                    if widget.winfo_reqheight() > widget.winfo_height()+2 or widget.winfo_reqwidth() > widget.winfo_width()+2:
                        label_clips.append({"widget":str(widget),"requested":[widget.winfo_reqwidth(),widget.winfo_reqheight()],"actual":[widget.winfo_width(),widget.winfo_height()]})
            assert not label_clips, f"Clipped label heights: {label_clips}"
            # Empty data keeps layout and scroll regions usable.
            app.database.connection.execute("DELETE FROM app_usage")
            app.database.connection.execute("DELETE FROM timeline_usage")
            app.database.connection.commit()
            app.refresh_all()
            pump(app, .15)
            assert not app._tk_errors, app._tk_errors
            results.append({"scale":scale,"small_wheel":True,"list_bottom":True,"heartbeat_seconds":elapsed[0],"label_height_clips":label_clips,"empty_state":True,
                            "icon_sources":[{"app":key[0],"physical_target":key[3],"source":list(size)} for key,size in app._icons.source_sizes.items()]})
            app.close()
    (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"states":len(results),"result":"passed","output":str(output)},ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("qa-output"))
    parser.add_argument("--scale", type=float)
    args = parser.parse_args()
    run(args.output.resolve(), (args.scale,) if args.scale else (1, 1.25, 1.5, 2))
