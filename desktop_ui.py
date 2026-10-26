"""Tk dashboard presentation. Worker threads never access Tk objects."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from datetime import date, timedelta
from dataclasses import dataclass
from functools import lru_cache
import math
import os
from pathlib import Path
import queue
import sys
import time
import tkinter as tk
from tkinter import ttk, font as tkfont
import uuid

from PIL import Image, ImageDraw, ImageTk


THEME = {
    "background": "#111318", "surface": "#1C2029", "surface_alt": "#252B36",
    "text": "#F3F4F6", "muted": "#A8B0BF", "border": "#343D4D",
    "grid": "#303746", "blue": "#7B9CFF", "teal": "#50D9C0",
    "purple": "#B49AFF", "orange": "#FFBC7A", "red": "#FF8C9C",
    "cyan": "#79D7FF",
}
PALETTE = [THEME[k] for k in ("blue", "teal", "purple", "orange", "cyan", "red")]


def icon_pixels(logical_size, scale):
    return max(1, math.ceil(logical_size * scale))


def fit_icon(image, target):
    """Shrink once; never manufacture detail by enlarging a small source."""
    image = image.convert("RGBA").copy()
    image.thumbnail((target, target), Image.Resampling.LANCZOS)
    output = Image.new("RGBA", (target, target))
    output.alpha_composite(image, ((target - image.width) // 2, (target - image.height) // 2))
    return output


def unpremultiply(raw):
    pixels = bytearray(raw)
    for offset in range(0, len(pixels), 4):
        alpha = pixels[offset + 3]
        if 0 < alpha < 255:
            for channel in range(3):
                pixels[offset + channel] = min(255, round(pixels[offset + channel] * 255 / alpha))
    return bytes(pixels)


def shell_icon(path, size):
    """IShellItemImageFactory, with correctly scoped COM/GDI ownership."""
    if os.name != "nt" or not path:
        return None

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("type", wintypes.LONG), ("width", wintypes.LONG),
                    ("height", wintypes.LONG), ("stride", wintypes.LONG),
                    ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("data", ctypes.c_void_p)]

    class HEADER(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG),
                    ("height", wintypes.LONG), ("planes", wintypes.WORD),
                    ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                    ("bytes", wintypes.DWORD), ("x", wintypes.LONG), ("y", wintypes.LONG),
                    ("used", wintypes.DWORD), ("important", wintypes.DWORD)]

    ole = ctypes.OleDLL("ole32")
    shell = ctypes.WinDLL("shell32")
    gdi = ctypes.WinDLL("gdi32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 2) in (0, 1)
    interface = ctypes.c_void_p()
    bitmap = wintypes.HBITMAP()
    dc = None
    try:
        iid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID("bcc18b79-ba16-442f-80c4-8a59c30c463b").bytes_le)
        shell.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        shell.SHCreateItemFromParsingName.restype = ctypes.c_long
        if shell.SHCreateItemFromParsingName(str(path), None, iid, ctypes.byref(interface)) < 0:
            return None
        table = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        get_image = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, SIZE, wintypes.UINT, ctypes.POINTER(wintypes.HBITMAP))(table[3])
        # ICONONLY | BIGGERSIZEOK; deliberately omit SCALEUP.
        if get_image(interface, SIZE(size, size), 5, ctypes.byref(bitmap)) < 0:
            return None
        gdi.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        info = BITMAP()
        if not gdi.GetObjectW(bitmap, ctypes.sizeof(info), ctypes.byref(info)):
            return None
        width, height = info.width, abs(info.height)
        if not (0 < width <= 4096 and 0 < height <= 4096):
            return None
        header = HEADER(ctypes.sizeof(HEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        data = ctypes.create_string_buffer(width * height * 4)
        gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi.CreateCompatibleDC.restype = wintypes.HDC
        dc = gdi.CreateCompatibleDC(None)
        gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        if not gdi.GetDIBits(dc, bitmap, 0, height, data, ctypes.byref(header), 0):
            return None
        image = Image.frombytes("RGBA", (width, height), unpremultiply(data.raw), "raw", "BGRA")
        return image if image.getextrema()[3][1] else None
    except (OSError, ValueError, ctypes.ArgumentError):
        return None
    finally:
        if dc:
            gdi.DeleteDC.argtypes = [wintypes.HDC]
            gdi.DeleteDC(dc)
        if bitmap:
            gdi.DeleteObject.argtypes = [ctypes.c_void_p]
            gdi.DeleteObject(bitmap)
        if interface:
            table = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(table[2])(interface)
        if initialized:
            ole.CoUninitialize()


class BackgroundJobs:
    def __init__(self, owner):
        self.owner = owner
        self.pool = ThreadPoolExecutor(2, thread_name_prefix="icons")
        self.status_pool = ThreadPoolExecutor(1, thread_name_prefix="activity")
        self.pending = set()
        self.results = queue.Queue()
        self.closed = False
        self.timer = owner.after(25, self.drain)

    def submit(self, key, work, done):
        if self.closed or key in self.pending or len(self.pending) >= 256:
            return False
        self.pending.add(key)
        pool = self.status_pool if key == "activity" else self.pool
        future = pool.submit(work)
        def complete(f):
            try:
                value, error = f.result(), None
            except Exception as exc:
                value, error = None, exc
            self.results.put((key, done, value, error))
        future.add_done_callback(complete)
        return True

    def drain(self):
        if self.closed:
            return
        started = time.perf_counter()
        while time.perf_counter() - started < 0.008:
            try:
                key, done, value, error = self.results.get_nowait()
            except queue.Empty:
                break
            self.pending.discard(key)
            done(value, error)
        self.timer = self.owner.after(25, self.drain)

    def close(self):
        self.closed = True
        self.owner.after_cancel(self.timer)
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.status_pool.shutdown(wait=False, cancel_futures=True)


class AsyncIcons:
    def __init__(self, owner):
        self.owner = owner
        self.native = owner.icon_manager
        self.cache = OrderedDict()
        self.waiters = {}
        self.source_sizes = {}

    def key(self, row, logical):
        path = str(row["icon_path"] or "")
        try:
            stamp = Path(path).stat().st_mtime_ns if path and not path.startswith("favicon:") else 0
        except OSError:
            stamp = 0
        return (str(row["app_key"]), path, stamp, icon_pixels(logical, self.owner.scale), self.owner.scale)

    def request(self, row, logical, callback):
        key = self.key(row, logical)
        if key in self.cache:
            self.cache.move_to_end(key)
            callback(self.cache[key])
            return
        if key in self.waiters:
            self.waiters[key].append(callback)
            return
        self.waiters[key] = [callback]
        record = dict(row)
        def load():
            app_key, path, _, target, _scale = key
            if app_key.startswith(("website:", "site:")) or path.startswith("favicon:"):
                domain = path[8:] if path.startswith("favicon:") else app_key.split(":", 1)[1]
                # Local cache and browser source only. No network request is needed by the UI.
                disk = self.owner.data_dir / "favicons" / (domain + ".png")
                image = None
                if disk.is_file():
                    try:
                        with Image.open(disk) as source:
                            image = source.convert("RGBA")
                    except OSError:
                        pass
                if (image is None or max(image.size) < target) and not self.owner.preview:
                    better = self.native.favicon_manager._find_browser_favicon(domain)
                    if better is not None and (image is None or max(better.size) > max(image.size)):
                        image = better
            else:
                resolved = self.native.resolve_icon_path(app_key, path)
                # Request a high-resolution source explicitly: the Shell otherwise
                # stretches its result to the exact requested nonstandard size.
                source_size = min(512, max(128, 1 << (target - 1).bit_length()))
                image = shell_icon(resolved, source_size)
                if image is None:
                    native_size = self.native.user32.GetSystemMetrics(11) if self.native.available else 32
                    image = self.native._render_icon(resolved, size=min(target, max(1, native_size)))
            if image is None:
                return None, None
            return fit_icon(image, target), image.size
        def finish(result, error):
            if self.owner.closing:
                return
            image, source_size = result if result is not None else (None, None)
            # Do not negatively cache failures; allow later refresh to find newly available icons.
            photo = ImageTk.PhotoImage(image, master=self.owner) if image is not None else None
            if photo is not None:
                self.cache[key] = photo
                self.source_sizes[key] = source_size
                while len(self.cache) > 512:
                    old, _ = self.cache.popitem(last=False)
                    self.source_sizes.pop(old, None)
            for waiter in self.waiters.pop(key, []):
                waiter(photo)
        if not self.owner._jobs.submit(("icon", key), load, finish):
            self.waiters.pop(key, None)
            callback(None)


class SmoothScroll:
    def __init__(self, owner, canvas):
        self.owner, self.canvas = owner, canvas
        self.target = None
        self.timer = None
        self.last = 0.0
        self.residual = 0.0

    def bounds(self):
        region = self.canvas.cget("scrollregion")
        if not region:
            return 0.0, 0.0
        parts = [float(x) for x in self.canvas.tk.splitlist(region)]
        height = max(0.0, parts[3] - parts[1])
        return height, max(0.0, height - self.canvas.winfo_height())

    def current(self):
        height, _ = self.bounds()
        return self.canvas.yview()[0] * height

    def push(self, pixels):
        height, limit = self.bounds()
        if not height or limit <= 0:
            return pixels
        current = self.current()
        origin = current + self.residual if self.target is None else self.target
        self.residual = 0.0
        if (origin - current) * pixels < 0:
            origin = current
        wanted = origin + pixels
        self.target = min(limit, max(0.0, wanted))
        remaining = wanted - self.target
        if self.timer is None:
            self.last = time.perf_counter()
            self.timer = self.owner.after(0, self.tick)
        return remaining

    def tick(self):
        self.timer = None
        if self.owner.closing or not self.canvas.winfo_exists() or self.target is None:
            return
        height, limit = self.bounds()
        if height <= 0:
            self.cancel()
            return
        self.target = min(limit, max(0, self.target))
        now = time.perf_counter()
        fraction = min(1.0, (now - self.last) / 0.035)
        self.last = now
        current = self.current()
        value = current + (self.target - current) * fraction
        if abs(self.target - current) < 0.8:
            self.canvas.yview_moveto(self.target / height)
            # Tk rounds to device pixels. Retain the fraction across separate
            # animation runs so slow precision-wheel input is never discarded.
            self.residual = self.target - self.current()
            self.target = None
        else:
            self.canvas.yview_moveto(value / height)
            self.timer = self.owner.after(16, self.tick)

    def cancel(self):
        if self.timer is not None:
            self.owner.after_cancel(self.timer)
        self.timer, self.target = None, None
        self.residual = 0.0

    def direct(self, *args):
        self.cancel()
        self.canvas.yview(*args)


def bar_coordinates(final, progress):
    """Grow from the fixed baseline, with a cubic ease-out and exact endpoints."""
    left, top, right, bottom = final
    eased = 1 - (1 - min(1.0, max(0.0, progress))) ** 3
    return left, bottom - (bottom - top) * eased, right, bottom


def clockwise_arc(offset, extent, progress):
    """Tk angles run anticlockwise; negative extents reveal a clockwise sweep."""
    visible = max(0.0, min(extent, 360 * min(1.0, max(0.0, progress)) - offset))
    return 90 - offset, -min(359.999, visible)


def chart_is_visible(owner, canvas, bounds):
    """Intersect the graph (not its legend) with every clipping ancestor."""
    if not canvas.winfo_exists() or not canvas.winfo_viewable() or owner.state() in ("iconic", "withdrawn"):
        return False
    if canvas.winfo_width() < 40 or canvas.winfo_height() < 40:
        return False  # Ignore provisional 1px geometry during initial layout.
    x, y = canvas.winfo_rootx() - canvas.canvasx(0), canvas.winfo_rooty() - canvas.canvasy(0)
    left, top, right, bottom = x + bounds[0], y + bounds[1], x + bounds[2], y + bounds[3]
    widget = canvas
    while widget is not None:
        left, top = max(left, widget.winfo_rootx()), max(top, widget.winfo_rooty())
        right = min(right, widget.winfo_rootx() + widget.winfo_width())
        bottom = min(bottom, widget.winfo_rooty() + widget.winfo_height())
        if right <= left or bottom <= top:
            return False
        if widget == owner:
            break
        widget = widget.master
    # Virtual screen includes secondary monitors with negative coordinates.
    if os.name == "nt":
        user = ctypes.windll.user32
        sx, sy = user.GetSystemMetrics(76), user.GetSystemMetrics(77)
        sw, sh = user.GetSystemMetrics(78), user.GetSystemMetrics(79)
    else:
        sx, sy, sw, sh = 0, 0, owner.winfo_screenwidth(), owner.winfo_screenheight()
    return min(right, sx + sw) > max(left, sx) and min(bottom, sy + sh) > max(top, sy)


@dataclass
class ChartMotion:
    state: str = "waiting"
    started: float | None = None
    canvas: object = None
    bounds: tuple | None = None
    paint: object = None


class ChartEntrance:
    """One shared clock; explicit navigation rearms only the affected charts."""
    duration = 1.0

    def __init__(self, owner, *, clock=None, visible=None):
        self.owner = owner
        self.clock = clock or time.perf_counter
        self.visible = visible or (lambda canvas, bounds: chart_is_visible(owner, canvas, bounds))
        self.charts = {}
        self.timer = None
        self.closed = False

    def progress(self, motion):
        if motion.state == "done":
            return 1.0
        if motion.started is None:
            return 0.0
        return min(1.0, max(0.0, (self.clock() - motion.started) / self.duration))

    def register(self, key, canvas, bounds, paint):
        if self.closed:
            return
        motion = self.charts.setdefault(key, ChartMotion())
        motion.canvas, motion.bounds, motion.paint = canvas, bounds, paint
        # Redraws bind new canvas items but never reset the original start time.
        paint(self.progress(motion))
        self._schedule()

    def detach(self, canvas):
        for motion in self.charts.values():
            if motion.canvas is canvas:
                motion.canvas = motion.bounds = motion.paint = None

    def rearm(self, keys):
        """Wait for freshly drawn data; never animate a stale canvas callback."""
        if self.closed or self.owner.closing:
            return
        for key in keys:
            self.charts[key] = ChartMotion()

    def _schedule(self):
        if self.closed or self.owner.closing or self.timer is not None:
            return
        pending = [m for m in self.charts.values() if m.state != "done" and (m.canvas is not None or m.state == "playing")]
        if pending:
            self.timer = self.owner.after(16 if any(m.state == "playing" for m in pending) else 50, self.tick)

    def tick(self):
        self.timer = None
        if self.closed or self.owner.closing:
            return
        for motion in self.charts.values():
            layout_ready = not getattr(self.owner, "_draw_timers", None) and not getattr(self.owner, "_panel_draws", None)
            if motion.state == "waiting" and layout_ready and motion.canvas is not None and self.visible(motion.canvas, motion.bounds):
                motion.started, motion.state = self.clock(), "playing"
            if motion.state == "playing":
                progress = self.progress(motion)
                if motion.paint is not None:
                    motion.paint(progress)
                if progress >= 1:
                    motion.state = "done"
        self._schedule()

    def finish_playing(self, keys=None):
        for key, motion in self.charts.items():
            if motion.state == "playing" and (keys is None or key in keys):
                motion.state = "done"
                if motion.paint is not None:
                    motion.paint(1.0)

    def suspend(self):
        """Dispose old widgets on a DPI rebuild without rearming any chart."""
        if self.timer is not None:
            self.owner.after_cancel(self.timer)
            self.timer = None
        for motion in self.charts.values():
            if motion.state == "playing":
                motion.state = "done"
            motion.canvas = motion.bounds = motion.paint = None

    def close(self):
        self.closed = True
        self.suspend()


@lru_cache(maxsize=24)
def rounded_image(width, height, radius, color, border):
    factor = 2
    image = Image.new("RGBA", (width * factor, height * factor))
    ImageDraw.Draw(image).rounded_rectangle((0, 0, width * factor - 1, height * factor - 1),
        radius=radius * factor, fill=color, outline=border, width=factor)
    return image.resize((width, height), Image.Resampling.LANCZOS)


class RoundedPanel(tk.Canvas):
    def __init__(self, parent, app, padding=20, **kwargs):
        self.app = app
        self.pad = app.px(padding)
        super().__init__(parent, bg=THEME["background"], highlightthickness=0, height=app.px(100), **kwargs)
        self.inner = tk.Frame(self, bg=THEME["surface"])
        self.background_id = self.create_image(0, 0, anchor="nw")
        self.window_id = self.create_window(self.pad, self.pad, window=self.inner, anchor="nw")
        self.render_timer = None
        self.bind("<Configure>", self.resize)
        self.inner.bind("<Configure>", self.content_size)

    def content_size(self, _event=None):
        requested = self.inner.winfo_reqheight() + self.pad * 2
        if int(self.cget("height")) != requested:
            self.configure(height=requested)

    def resize(self, event):
        self.app._panel_draws.add(self)
        self.itemconfigure(self.window_id, width=max(1, event.width - 2 * self.pad))
        if self.render_timer:
            self.after_cancel(self.render_timer)
        self.render_timer = self.after(35, self.render)

    def render(self):
        self.render_timer = None
        try:
            if not self.winfo_exists():
                return
            width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
            self.photo = ImageTk.PhotoImage(rounded_image(width, height, self.app.px(16), THEME["surface"], THEME["border"]), master=self)
            self.itemconfigure(self.background_id, image=self.photo)
        finally:
            self.app._panel_draws.discard(self)


class ResponsiveRow(tk.Frame):
    def __init__(self, parent, app, ratios=None):
        super().__init__(parent, bg=THEME["background"])
        self.app, self.children_list, self.ratios = app, [], ratios
        self.columns = 0
        self.bind("<Configure>", self.reflow)

    def add(self, widget):
        self.children_list.append(widget)
        self.columns = 0
        self.reflow()

    def reflow(self, event=None):
        width = (event.width if event else self.winfo_width()) / self.app.scale
        count = len(self.children_list)
        columns = (count if width >= 900 else 1) if self.ratios else min(count, 3 if width >= 900 else 2 if width >= 600 else 1)
        if columns == self.columns or not count:
            return
        self.columns = columns
        for index in range(max(count, 3)):
            self.grid_columnconfigure(index, weight=0, uniform="")
        for index, child in enumerate(self.children_list):
            child.grid_forget()
            column = index % columns
            weight = self.ratios[index] if self.ratios and columns > 1 else 1
            self.grid_columnconfigure(column, weight=weight, uniform="parts")
            child.grid(row=index // columns, column=column, sticky="nsew", padx=(0 if column == 0 else self.app.px(8), 0 if column == columns - 1 else self.app.px(8)), pady=(0, self.app.px(16)))


class PageStack(tk.Frame):
    """Borderless stack retaining the small Notebook interface the app uses."""
    def __init__(self, parent):
        super().__init__(parent, bg=THEME["background"])
        self.pages = []
        self.selected = -1

    def add(self, frame, text=""):
        self.pages.append(frame)

    def select(self, index=None):
        if index is None:
            return str(self.pages[self.selected]) if self.selected >= 0 else ""
        if index == self.selected:
            return
        for page in self.pages:
            page.pack_forget()
        self.selected = int(index)
        self.pages[self.selected].pack(fill="both", expand=True)
        self.event_generate("<<NotebookTabChanged>>", when="tail")


class SlimScrollbar(tk.Canvas):
    def __init__(self, parent, app, command):
        super().__init__(parent, width=app.px(10), bg=parent.cget("bg"), highlightthickness=0, takefocus=True)
        self.app, self.command = app, command
        self.first, self.last, self.grab_offset = 0.0, 1.0, 0.0
        self.bind("<Configure>", self.draw)
        self.bind("<Button-1>", self.press)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<Up>", lambda _e: self.command("scroll", -1, "units"))
        self.bind("<Down>", lambda _e: self.command("scroll", 1, "units"))
        self.bind("<Prior>", lambda _e: self.command("scroll", -1, "pages"))
        self.bind("<Next>", lambda _e: self.command("scroll", 1, "pages"))

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self.draw()

    def draw(self, _event=None):
        self.delete("all")
        if self.last - self.first >= .999:
            return
        height, width = self.winfo_height(), self.winfo_width()
        radius = max(1, width / 2)
        y1 = max(radius, height * self.first)
        y2 = min(height - radius, max(y1 + self.app.px(20), height * self.last))
        self.create_line(width / 2, y1, width / 2, max(y1, y2), fill="#526078", width=max(1, width-4), capstyle="round")

    def press(self, event):
        if self.first <= event.y / max(1, self.winfo_height()) <= self.last:
            self.grab_offset = event.y - self.first * self.winfo_height()
        else:
            self.grab_offset = (self.last-self.first) * self.winfo_height() / 2
            self.drag(event)

    def drag(self, event):
        value = (event.y - self.grab_offset) / max(1, self.winfo_height())
        self.command("moveto", max(0, min(1-(self.last-self.first), value)))


class DashboardMixin:
    def __init__(self, *args, preview=False, ui_scale=None, **kwargs):
        self.preview = preview
        self._forced_scale = ui_scale
        self.scale = 1.0
        self._scrolls = {}
        self._canvas_generation = {}
        self._canvas_photos = {}
        self._draw_timers = {}
        self._panel_draws = set()
        self._entrance = ChartEntrance(self)
        self._tooltip = None
        self._tk_errors = []
        self.core = sys.modules[type(self).__module__]
        if os.name == "nt":
            try:
                ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            except (AttributeError, OSError):
                pass
        super().__init__(*args, **kwargs)
        self.report_callback_exception = self._report_error
        if preview:
            self.title(self.core.APP_TITLE + " · 演示预览")
        self.status_var.set("演示预览 · 不记录实际使用" if preview else "正在检查采集状态")

    def _report_error(self, kind, value, trace):
        self._tk_errors.append(str(value))
        import traceback
        traceback.print_exception(kind, value, trace)

    def px(self, value):
        return max(1, round(value * self.scale))

    def font(self, size=14, bold=False):
        return ("Microsoft YaHei UI", -self.px(size), "bold" if bold else "normal")

    def label(self, parent, text=None, variable=None, size=14, bold=False, color=None, wrap=True):
        widget = tk.Label(parent, text=text, textvariable=variable, bg=THEME["surface"], fg=color or THEME["text"],
                          font=self.font(size, bold), anchor="w", justify="left", borderwidth=0)
        if wrap:
            parent.bind("<Configure>", lambda event, w=widget: w.configure(wraplength=max(self.px(30), event.width - self.px(4))), add="+")
        return widget

    def _setup_styles(self):
        self.scale = self._forced_scale or self.winfo_fpixels("1i") / 96.0
        self.core.COLORS.update(THEME)
        self.configure(bg=THEME["background"])
        style = ttk.Style(self)
        style.theme_use("clam")
        style.layout("Hidden.TNotebook.Tab", [])
        style.configure("Hidden.TNotebook", background=THEME["background"], borderwidth=0, tabmargins=0)
        style.configure("Dark.Vertical.TScrollbar", background=THEME["border"], troughcolor=THEME["surface"], bordercolor=THEME["surface"], arrowcolor=THEME["muted"], width=self.px(10))
        style.configure("Dark.TCombobox", fieldbackground=THEME["surface_alt"], background=THEME["surface_alt"], foreground=THEME["text"], arrowcolor=THEME["text"], padding=self.px(8))
        style.map("Dark.TCombobox", fieldbackground=[("readonly", THEME["surface_alt"])], foreground=[("readonly", THEME["text"])], selectbackground=[("readonly", THEME["surface_alt"])], selectforeground=[("readonly", THEME["text"])])
        self.option_add("*TCombobox*Listbox.background", THEME["surface_alt"])
        self.option_add("*TCombobox*Listbox.foreground", THEME["text"])
        width, height = self.winfo_screenwidth(), self.winfo_screenheight()
        if os.name == "nt":
            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                width, height = rect.right - rect.left, rect.bottom - rect.top
        w, h = min(self.px(1320), int(width * .94)), min(self.px(940), int(height * .90))
        self.geometry(f"{w}x{h}+{max(0, (width-w)//2)}+{max(0, (height-h)//2)}")
        self.minsize(min(self.px(680), w), min(self.px(460), h))

    def _build_layout(self):
        self._jobs = BackgroundJobs(self)
        self._icons = AsyncIcons(self)
        sidebar = tk.Frame(self, bg=THEME["surface"], width=self.px(208))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = self.label(sidebar, "屏幕使用时长", size=20, bold=True)
        brand.pack(fill="x", padx=self.px(22), pady=(self.px(30), self.px(8)))
        self.label(sidebar, "SCREEN TIME", size=10, color=THEME["muted"]).pack(fill="x", padx=self.px(24))
        tk.Frame(sidebar, bg=THEME["border"], height=1).pack(fill="x", padx=self.px(24), pady=self.px(28))
        self.nav_buttons = []
        for index, title in enumerate(("日视图", "最近 7 天")):
            button = self._modern_button(sidebar, title, lambda i=index: self.select_page(i))
            button.pack(fill="x", padx=self.px(16), pady=(0, self.px(8)))
            self.nav_buttons.append(button)
        bottom = tk.Frame(sidebar, bg=THEME["surface"])
        bottom.pack(side="bottom", fill="x", padx=self.px(22), pady=self.px(26))
        self.status_dot_label = self.label(bottom, "●  采集状态", size=12, color=THEME["teal"])
        self.status_dot_label.pack(fill="x", pady=(0, self.px(10)))
        self.label(bottom, variable=self.status_var, size=12, color=THEME["muted"]).pack(fill="x")
        self.label(bottom, "记录保存在本机", size=11, color=THEME["muted"]).pack(fill="x", pady=(self.px(20), 0))
        main = tk.Frame(self, bg=THEME["background"])
        main.pack(side="left", fill="both", expand=True, padx=self.px(24), pady=self.px(24))
        header = tk.Frame(main, bg=THEME["background"])
        header.pack(fill="x", pady=(0, self.px(24)))
        self.page_title = tk.Label(header, text="使用概览", bg=THEME["background"], fg=THEME["text"], font=self.font(28, True), anchor="w")
        self.page_title.pack(fill="x")
        self.page_subtitle = tk.Label(header, text="查看每天的电脑使用时长与应用分布", bg=THEME["background"], fg=THEME["muted"], font=self.font(13), anchor="w", justify="left")
        self.page_subtitle.pack(fill="x", pady=(self.px(8), 0))
        header.bind("<Configure>", lambda event: self.page_subtitle.configure(wraplength=max(1, event.width)))
        self.notebook = PageStack(main)
        self.notebook.pack(fill="both", expand=True)
        self.day_tab = tk.Frame(self.notebook, bg=THEME["background"])
        self.week_tab = tk.Frame(self.notebook, bg=THEME["background"])
        self.notebook.add(self.day_tab, text="日视图")
        self.notebook.add(self.week_tab, text="最近 7 天")
        self.notebook.bind("<<NotebookTabChanged>>", self._page_changed)
        self.day_page, self.day_scroll_canvas, self.day_scrollbar = self._create_scrollable_page(self.day_tab)
        self.week_page, self.week_scroll_canvas, self.week_scrollbar = self._create_scrollable_page(self.week_tab)
        self._build_day_tab()
        self._build_week_tab()
        self.bind_all("<MouseWheel>", self._wheel)
        self.bind("<Configure>", self._window_changed, add="+")
        self.select_page(0, replay=False)

    def _modern_button(self, parent, text, command, *, primary=False, compact=False):
        # A cached Pillow pill provides real rounded corners without extra UI dependencies.
        button = tk.Button(parent, text=text, command=command, compound="center", font=self.font(14, primary),
            fg=THEME["background"] if primary else THEME["text"], bg=parent.cget("bg"),
            activeforeground=THEME["background"] if primary else THEME["text"], activebackground=parent.cget("bg"),
            relief="flat", borderwidth=0, highlightthickness=2, highlightbackground=parent.cget("bg"), highlightcolor=THEME["blue"], cursor="hand2", padx=0, pady=0)
        width = self.px(46 if compact else max(96, len(text) * 15 + 34))
        height = self.px(40)
        def paint(state=0):
            color = ("#5E7EDB" if state == 2 else "#96AFFF" if state else THEME["blue"]) if primary else ("#465572" if state == 2 else THEME["border"] if state else THEME["surface_alt"])
            photo = ImageTk.PhotoImage(rounded_image(width, height, self.px(10), color, color), master=self)
            button._pill = photo
            button.configure(image=photo)
        paint()
        button.bind("<Enter>", lambda _e: paint(1))
        button.bind("<Leave>", lambda _e: paint(0))
        button.bind("<ButtonPress-1>", lambda _e: paint(2))
        button.bind("<ButtonRelease-1>", lambda _e: paint(1))
        return button

    def select_page(self, index, *, replay=True):
        previous = self.notebook.selected
        if previous != index:
            self._entrance.finish_playing()
            if replay and previous >= 0:
                self._entrance.rearm(("day_timeline", "day_pie") if index == 0 else ("week_chart", "week_pie"))
        for scroll in self._scrolls.values():
            scroll.cancel()
        self._hide_tooltip()
        self.notebook.select(index)
        for i, button in enumerate(self.nav_buttons):
            button.configure(fg=THEME["blue"] if i == index else THEME["muted"])
        self.page_title.configure(text="使用概览" if index == 0 else "最近 7 天")

    def _page_changed(self, _event=None):
        if hasattr(self, "day_apps_canvas"):
            self.refresh_all()

    def manual_refresh(self):
        if not hasattr(self, "_entrance"):
            # The collector regression suite intentionally uses a headless owner.
            import screen_time_tracker as core
            return core.LegacyScreenTimeTrackerApp.manual_refresh(self)
        self._entrance.finish_playing()
        super().manual_refresh()

    def change_day(self, offset):
        candidate = self.selected_day + timedelta(days=offset)
        if candidate <= date.today() and candidate != self.selected_day:
            self._entrance.rearm(("day_timeline", "day_pie"))
        super().change_day(offset)

    def go_today(self):
        if self.selected_day != date.today():
            self._entrance.rearm(("day_timeline", "day_pie"))
        super().go_today()

    def refresh_day(self):
        self.core.LegacyScreenTimeTrackerApp.refresh_day(self)
        self.next_day_button.configure(state="disabled" if self.selected_day >= date.today() else "normal", disabledforeground="#657089")
        if not self.day_apps_data:
            self.top_app_var.set("当天暂无应用使用记录")

    def refresh_week(self):
        self.core.LegacyScreenTimeTrackerApp.refresh_week(self)
        if not any(seconds for _day, seconds in self.week_totals_data):
            self.week_highest_var.set("最近 7 天暂无使用时长")

    def _window_changed(self, event):
        if event.widget != self or self._forced_scale:
            return
        if os.name != "nt" or getattr(self, "_dpi_timer", None):
            return
        try:
            user = ctypes.windll.user32
            user.GetDpiForWindow.argtypes = [wintypes.HWND]
            user.GetDpiForWindow.restype = wintypes.UINT
            value = user.GetDpiForWindow(self.winfo_id()) / 96.0
            if value and abs(value - self.scale) > .02:
                self._dpi_timer = self.after(150, lambda: self._rebuild_for_dpi(value))
        except (OSError, AttributeError):
            pass

    def _rebuild_for_dpi(self, scale):
        if self.closing:
            return
        selected = self.notebook.selected
        self._entrance.suspend()
        self._hide_tooltip()
        for scroll in self._scrolls.values():
            scroll.cancel()
        self._jobs.close()
        self._cancel_panel_draws()
        for timer in self.tk.splitlist(self.tk.call("after", "info")):
            self.after_cancel(timer)
        for child in self.winfo_children():
            child.destroy()
        self._scrolls.clear()
        self._canvas_generation.clear()
        self._canvas_photos.clear()
        self._draw_timers.clear()
        self._panel_draws.clear()
        self.scroll_targets.clear()
        self._forced_scale = scale
        self._setup_styles()
        self._build_layout()
        self._bind_resize_events()
        self._forced_scale = None
        self._dpi_timer = None
        self.select_page(selected, replay=False)
        self.refresh_all()
        self.after(300, self.refresh_status)
        if self.collector_enabled:
            self.after(10000, self.ensure_collector)

    def _create_scrollable_page(self, parent):
        canvas = tk.Canvas(parent, bg=THEME["background"], highlightthickness=0, yscrollincrement=1)
        canvas.pack(side="left", fill="both", expand=True)
        scroll = SmoothScroll(self, canvas)
        self._scrolls[canvas] = scroll
        bar = SlimScrollbar(parent, self, scroll.direct)
        bar.pack(side="right", fill="y", padx=(self.px(6), 0))
        canvas.configure(yscrollcommand=bar.set)
        page = tk.Frame(canvas, bg=THEME["background"])
        window = canvas.create_window(0, 0, window=page, anchor="nw")
        page.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        self.scroll_targets.append((canvas, page))
        return page, canvas, bar

    def _wheel(self, event):
        widget = event.widget
        if widget.winfo_toplevel() != self:
            return None
        self._hide_tooltip()
        pixels = -float(event.delta) / 120 * self.px(80)
        chain = []
        while widget is not None:
            chain.append(widget)
            widget = getattr(widget, "master", None)
        handled = False
        for widget in chain:
            if widget in self._scrolls:
                pixels = self._scrolls[widget].push(pixels)
                handled = True
                if abs(pixels) < .01:
                    break
        return "break" if handled else None

    def _card(self, parent, title, variable, accent):
        panel = RoundedPanel(parent, self)
        self.label(panel.inner, title, size=12, color=THEME["muted"]).pack(fill="x")
        self.label(panel.inner, variable=variable, size=25 if variable in (self.day_total_var, self.week_total_var) else 19, bold=True, color=accent).pack(fill="x", pady=(self.px(14), self.px(6)))
        return panel

    def _stats(self, parent, entries):
        row = ResponsiveRow(parent, self)
        row.pack(fill="x")
        for title, variable, accent in entries:
            row.add(self._card(row, title, variable, THEME[accent]))

    def _heading(self, panel, title, subtitle):
        self.label(panel.inner, title, size=17, bold=True).pack(fill="x")
        if subtitle:
            self.label(panel.inner, subtitle, size=12, color=THEME["muted"]).pack(fill="x", pady=(self.px(6), self.px(16)))

    def _section(self, parent, title, subtitle):
        panel = RoundedPanel(parent, self)
        self._heading(panel, title, subtitle)
        canvas = tk.Canvas(panel.inner, bg=THEME["surface"], highlightthickness=0, height=self.px(250))
        canvas.pack(fill="x", pady=(self.px(8), 0))
        return panel, canvas

    def _split_app_section(self, parent, title, subtitle):
        row = ResponsiveRow(parent, self, ratios=(1, 2))
        pie_panel = RoundedPanel(row, self)
        self._heading(pie_panel, "应用占比", "使用时长占比 · 前 6 项及其他")
        pie = tk.Canvas(pie_panel.inner, bg=THEME["surface"], highlightthickness=0, height=self.px(440))
        pie.pack(fill="x")
        list_panel = RoundedPanel(row, self)
        self._heading(list_panel, title, subtitle)
        list_body = tk.Frame(list_panel.inner, bg=THEME["surface"])
        list_body.pack(fill="x")
        canvas = tk.Canvas(list_body, bg=THEME["surface"], highlightthickness=0, height=self.px(440), yscrollincrement=1, takefocus=True)
        canvas.pack(side="left", fill="both", expand=True)
        scroll = SmoothScroll(self, canvas)
        self._scrolls[canvas] = scroll
        bar = SlimScrollbar(list_body, self, scroll.direct)
        bar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=bar.set)
        row.add(pie_panel)
        row.add(list_panel)
        return row, pie, canvas, bar

    def _toolbar(self, parent, day=False):
        toolbar = tk.Frame(parent, bg=THEME["background"])
        toolbar.pack(fill="x", pady=(0, self.px(20)))
        controls = tk.Frame(toolbar, bg=THEME["background"])
        controls.pack(anchor="w")
        if day:
            self._modern_button(controls, "‹", lambda: self.change_day(-1), compact=True).pack(side="left")
            tk.Label(controls, textvariable=self.day_date_var, font=self.font(14, True), fg=THEME["text"], bg=THEME["background"]).pack(side="left", padx=self.px(12))
            self.next_day_button = self._modern_button(controls, "›", lambda: self.change_day(1), compact=True)
            self.next_day_button.pack(side="left")
        else:
            tk.Label(controls, text="统计今天及此前 6 天的使用时长。", font=self.font(13), fg=THEME["muted"], bg=THEME["background"], wraplength=self.px(310), justify="left").pack(side="left")
        actions = tk.Frame(toolbar, bg=THEME["background"])
        actions.pack(anchor="w", pady=(self.px(12), 0))
        if day:
            self._modern_button(actions, "回到今天", self.go_today).pack(side="left", padx=(0, self.px(8)))
        self._modern_button(actions, "刷新", self.manual_refresh, primary=True).pack(side="left")

    def _build_day_tab(self):
        self._toolbar(self.day_page, True)
        self._stats(self.day_page, [("屏幕使用总时长", self.day_total_var, "blue"), ("与上一日相比", self.day_delta_var, "orange"), ("使用时长最多的应用", self.top_app_var, "teal")])
        panel, self.day_timeline_canvas = self._section(self.day_page, "当天使用时长分布", "每根柱子显示一个 15 分钟时段内的使用时长。")
        panel.pack(fill="x", pady=(0, self.px(20)))
        row, self.day_apps_pie_canvas, self.day_apps_canvas, self.day_apps_scrollbar = self._split_app_section(self.day_page, "应用使用排行", "各应用使用时长 · 从高到低排列")
        row.pack(fill="x")

    def _build_week_tab(self):
        self._toolbar(self.week_page)
        self._stats(self.week_page, [("最近 7 天总时长", self.week_total_var, "blue"), ("最近 7 天日均时长", self.week_average_var, "purple"), ("使用时长最多的一天", self.week_highest_var, "orange")])
        panel, self.week_chart_canvas = self._section(self.week_page, "最近 7 天屏幕使用时长", "每根柱子显示一天的屏幕使用时长。")
        panel.pack(fill="x", pady=(0, self.px(20)))
        row, self.week_apps_pie_canvas, self.week_apps_canvas, self.week_apps_scrollbar = self._split_app_section(self.week_page, "最近 7 天应用使用情况", "按应用汇总最近 7 天的使用时长。")
        row.pack(fill="x")
        panel = RoundedPanel(self.week_page, self)
        panel.pack(fill="x", pady=(self.px(4), self.px(20)))
        self._heading(panel, "单日应用使用明细", "选择日期，查看当天各应用的使用时长。")
        self.week_detail_combo = ttk.Combobox(panel.inner, textvariable=self.week_detail_var, state="readonly", width=24, style="Dark.TCombobox", font=self.font(13))
        self.week_detail_combo.pack(anchor="w", pady=(0, self.px(16)))
        self.week_detail_combo.bind("<<ComboboxSelected>>", self.change_week_detail)
        body = tk.Frame(panel.inner, bg=THEME["surface"])
        body.pack(fill="x")
        self.week_detail_canvas = tk.Canvas(body, bg=THEME["surface"], height=self.px(300), highlightthickness=0, yscrollincrement=1, takefocus=True)
        self.week_detail_canvas.pack(side="left", fill="both", expand=True)
        scroll = SmoothScroll(self, self.week_detail_canvas)
        self._scrolls[self.week_detail_canvas] = scroll
        bar = SlimScrollbar(body, self, scroll.direct)
        bar.pack(side="right", fill="y")
        self.week_detail_canvas.configure(yscrollcommand=bar.set)

    def _bind_resize_events(self):
        for canvas, function in [(self.day_timeline_canvas, self.draw_timeline), (self.day_apps_canvas, self.draw_day_apps),
                (self.day_apps_pie_canvas, self.draw_day_apps_pie), (self.week_chart_canvas, self.draw_week_chart),
                (self.week_apps_canvas, self.draw_week_apps), (self.week_apps_pie_canvas, self.draw_week_apps_pie), (self.week_detail_canvas, self.draw_week_detail_apps)]:
            canvas.bind("<Configure>", lambda _event, c=canvas, f=function: self._schedule_draw(c, f))

    def _schedule_draw(self, canvas, function):
        old = self._draw_timers.pop(canvas, None)
        if old:
            self.after_cancel(old)
        def draw():
            self._draw_timers.pop(canvas, None)
            if not self.closing:
                function()
        self._draw_timers[canvas] = self.after(50, draw)

    def refresh_status(self):
        if not hasattr(self, "_jobs"):
            # Preserve the existing headless regression contract.
            import screen_time_tracker as core
            return core.LegacyScreenTimeTrackerApp.refresh_status(self)
        if self.closing:
            return
        if self.preview:
            self.status_var.set("演示预览 · 不记录实际使用")
            return
        def work():
            if not self.collector_enabled:
                return "采集未启用", "muted"
            if not self.core.collector_is_running():
                return "采集未运行，请点击刷新重试", "red"
            if self.activity.is_workstation_locked():
                return "桌面已锁定，暂停记录", "orange"
            key, name, _path = self.activity.active_app_details()
            return ("采集进程运行中\n统计窗口不计入" if key == "__ignored__" else "采集进程运行中\n当前应用：" + name), "teal"
        def done(result, error):
            if self.closing:
                return
            text, color = result if error is None else ("状态读取失败，请稍后刷新", "red")
            self.status_var.set(text)
            self.status_dot_label.configure(fg=THEME[color])
        self._jobs.submit("activity", work, done)
        self.after(1000, self.refresh_status)

    def _begin_canvas(self, canvas):
        self._entrance.detach(canvas)
        canvas.delete("all")
        self._canvas_generation[canvas] = self._canvas_generation.get(canvas, 0) + 1
        self._canvas_photos[canvas] = []

    def _draw_app_icon(self, canvas, x, y, row, size=34):
        physical = self.px(size)
        label = str(row["app_name"]) if row is not None else "其他应用"
        generation = self._canvas_generation.get(canvas, 0)
        badge = canvas.create_oval(x, y, x + physical, y + physical, fill=PALETTE[sum(map(ord, label)) % len(PALETTE)], outline="")
        letter = next((ch for ch in label if ch.isalnum()), "?")
        text_id = canvas.create_text(x + physical / 2, y + physical / 2, text=letter.upper(), font=self.font(max(10, size // 2), True), fill=THEME["background"])
        if row is None:
            return
        def ready(photo):
            if self.closing or not canvas.winfo_exists() or self._canvas_generation.get(canvas) != generation:
                return
            if photo is not None:
                canvas.delete(badge, text_id)
                canvas.create_image(x + physical / 2, y + physical / 2, image=photo)
                self._canvas_photos[canvas].append(photo)
        self._icons.request(row, size, ready)

    def _draw_empty(self, canvas, width, height, text):
        text = text.replace("这一天", "当天")
        canvas.create_text(width / 2, height / 2, text=text, width=max(1, width - self.px(30)), font=self.font(14), fill=THEME["muted"], justify="center")

    def _shorten(self, text, available, font):
        if font.measure(text) <= available:
            return text
        while text and font.measure(text + "…") > available:
            text = text[:-1]
        return text + "…" if text else "…"

    def _hide_tooltip(self, _event=None):
        if self._tooltip is not None:
            self._tooltip.destroy()
            self._tooltip = None

    def _show_tooltip(self, canvas, name, y=None):
        self._hide_tooltip()
        tooltip = tk.Toplevel(self)
        tooltip.overrideredirect(True)
        tooltip.attributes("-topmost", True)
        tk.Label(tooltip, text=name, bg=THEME["surface_alt"], fg=THEME["text"], font=self.font(13), wraplength=self.px(400), justify="left", padx=self.px(12), pady=self.px(8)).pack()
        tooltip.update_idletasks()
        x = min(canvas.winfo_rootx() + self.px(12), self.winfo_screenwidth() - tooltip.winfo_reqwidth() - 8)
        top = canvas.winfo_rooty() + (self.px(36) if y is None else y)
        top = min(top, self.winfo_screenheight() - tooltip.winfo_reqheight() - 8)
        tooltip.geometry(f"+{max(0, x)}+{max(0, top)}")
        self._tooltip = tooltip

    def _draw_horizontal_bars(self, canvas, rows, color, empty_text):
        self._begin_canvas(canvas)
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        row_height, top = self.px(66), self.px(8)
        canvas.configure(scrollregion=(0, 0, width, max(height, len(rows) * row_height + top)))
        if not rows:
            self._draw_empty(canvas, width, height, empty_text.replace("还没有", "暂无"))
            return
        font = tkfont.Font(self, font=self.font(13))
        maximum = max(1, max(int(row["seconds"]) for row in rows))
        for index, row in enumerate(rows):
            y = top + index * row_height
            self._draw_app_icon(canvas, 0, y + self.px(3), row)
            duration = self.core.format_short_duration(row["seconds"])
            duration_width = font.measure(duration)
            left, right = self.px(46), width - self.px(6)
            name = self._shorten(str(row["app_name"]), max(1, right - left - duration_width - self.px(20)), font)
            canvas.create_text(left, y + self.px(10), text=name, font=self.font(13), fill=THEME["text"], anchor="w")
            canvas.create_text(right, y + self.px(10), text=duration, font=self.font(12), fill=THEME["muted"], anchor="e")
            track_y = y + self.px(32)
            canvas.create_line(left, track_y, right, track_y, fill=THEME["grid"], width=self.px(6), capstyle="round")
            if row["seconds"]:
                canvas.create_line(left, track_y, left + max(1, right - left) * row["seconds"] / maximum, track_y, fill=color, width=self.px(6), capstyle="round")
        state = {"index": 0, "hover": -1}
        def motion(event):
            index = int((canvas.canvasy(event.y) - top) // row_height)
            if index != state["hover"]:
                state["hover"] = index
                if 0 <= index < len(rows):
                    self._show_tooltip(canvas, str(rows[index]["app_name"]), event.y + self.px(24))
                else:
                    self._hide_tooltip()
        def keyboard(event):
            state["index"] = max(0, min(len(rows) - 1, state["index"] + (-1 if event.keysym == "Up" else 1 if event.keysym == "Down" else 0)))
            canvas.yview_moveto(max(0, state["index"] * row_height - height / 2) / max(height, len(rows) * row_height + top))
            self._show_tooltip(canvas, str(rows[state["index"]]["app_name"]))
            return "break"
        canvas.bind("<Motion>", motion)
        canvas.bind("<Leave>", self._hide_tooltip)
        canvas.bind("<FocusOut>", self._hide_tooltip)
        canvas.bind("<FocusIn>", lambda _e: self._show_tooltip(canvas, str(rows[state["index"]]["app_name"])))
        canvas.bind("<Up>", keyboard)
        canvas.bind("<Down>", keyboard)
        canvas.bind("<Button-1>", lambda _e: canvas.focus_set())

    def _draw_pie(self, canvas, rows, empty_text):
        self._begin_canvas(canvas)
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        if not rows or not any(int(row["seconds"]) > 0 for row in rows):
            self._draw_empty(canvas, width, height, empty_text.replace("还没有", "暂无"))
            return
        slices = [(str(row["app_name"]), int(row["seconds"]), row) for row in rows[:6]]
        other = sum(int(row["seconds"]) for row in rows[6:])
        if other:
            slices.append(("其他应用", other, None))
        total = max(1, sum(seconds for _, seconds, _ in slices))
        radius = max(self.px(24), min(self.px(86), width * .36))
        cx, cy = width / 2, self.px(100)
        offset = 0.0
        arcs = []
        for index, (_name, seconds, _row) in enumerate(slices):
            extent = seconds / total * 360
            item = canvas.create_arc(cx-radius, cy-radius, cx+radius, cy+radius, start=90-offset, extent=0,
                fill=PALETTE[index % len(PALETTE)], outline=THEME["surface"], width=self.px(3), style="pieslice", state="hidden")
            arcs.append((item, offset, extent))
            offset += extent
        inner = radius * .77
        canvas.create_oval(cx-inner, cy-inner, cx+inner, cy+inner, fill=THEME["surface"], outline="")
        canvas.create_text(cx, cy-self.px(10), text="总时长", font=self.font(12), fill=THEME["muted"])
        canvas.create_text(cx, cy+self.px(14), text=self.core.format_short_duration(total), font=self.font(16, True), fill=THEME["text"])
        font = tkfont.Font(self, font=self.font(12))
        for index, (name, seconds, row) in enumerate(slices):
            y = self.px(220 + index * 30)
            self._draw_app_icon(canvas, 0, y - self.px(11), row, size=22)
            percent = f"{seconds / total * 100:.0f}%"
            label = self._shorten(name, max(1, width-self.px(40)-font.measure(percent)), font)
            canvas.create_text(self.px(32), y, text=label, font=self.font(12), fill=THEME["text"], anchor="w")
            canvas.create_text(width-self.px(2), y, text=percent, font=self.font(12), fill=THEME["muted"], anchor="e")
        def paint(progress):
            for item, offset, extent in arcs:
                start, sweep = clockwise_arc(offset, extent, progress)
                canvas.itemconfigure(item, start=start, extent=sweep, state="normal" if sweep < 0 else "hidden")
        key = "day_pie" if canvas is self.day_apps_pie_canvas else "week_pie"
        if any(seconds > 0 for _, seconds, _ in slices):
            self._entrance.register(key, canvas, (cx-radius, cy-radius, cx+radius, cy+radius), paint)

    def draw_timeline(self):
        self._draw_chart(self.day_timeline_canvas, [self.day_timeline_data.get(i*15, 0) for i in range(96)], False)

    def draw_week_chart(self):
        self._draw_chart(self.week_chart_canvas, [seconds for _, seconds in self.week_totals_data], True)

    def _draw_chart(self, canvas, values, weekly):
        self._begin_canvas(canvas)
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        if not any(values):
            self._draw_empty(canvas, width, height, "最近 7 天暂无屏幕使用记录" if weekly else "当天暂无屏幕使用记录")
            return
        left, right, top, bottom = self.px(58), self.px(18), self.px(22), self.px(34)
        cw, ch = max(1, width-left-right), max(1, height-top-bottom)
        maximum = max(max(values), 3600 if weekly else 60)
        for index in range(5):
            ratio = index/4
            y = top+ch*(1-ratio)
            canvas.create_line(left, y, width-right, y, fill=THEME["grid"], dash=(2, 4))
            canvas.create_text(left-self.px(9), y, text=self.core.format_short_duration(maximum*ratio), font=self.font(10), fill=THEME["muted"], anchor="e")
        slot = cw / len(values)
        bars = []
        for index, seconds in enumerate(values):
            center = left + slot*(index+.5)
            bar = min(self.px(54), slot*.6) if weekly else max(1, slot*.65)
            y = top+ch*(1-seconds/maximum)
            color = THEME["purple"] if weekly else THEME["blue"]
            final = (center-bar/2, y, center+bar/2, top+ch)
            item = canvas.create_rectangle(*final, fill=color, outline="", state="hidden")
            bars.append((item, final, seconds))
            if weekly:
                day = self.week_totals_data[index][0]
                canvas.create_text(center, max(self.px(8), y-self.px(10)), text=self.core.format_short_duration(seconds), font=self.font(10), fill=THEME["text"])
                canvas.create_text(center, height-self.px(12), text=f"{day.month}/{day.day}", font=self.font(11), fill=THEME["muted"])
        if not weekly:
            for hour in range(0, 25, 3):
                canvas.create_text(left+cw*hour/24, height-self.px(12), text=f"{hour:02d}:00", font=self.font(10), fill=THEME["muted"])
        def paint(progress):
            for item, final, seconds in bars:
                canvas.coords(item, *bar_coordinates(final, progress))
                canvas.itemconfigure(item, state="normal" if progress > 0 and seconds > 0 else "hidden")
        self._entrance.register("week_chart" if weekly else "day_timeline", canvas, (left, top, width-right, top+ch), paint)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self._entrance.close()
        self._hide_tooltip()
        for scroll in self._scrolls.values():
            scroll.cancel()
        for timer in self._draw_timers.values():
            self.after_cancel(timer)
        self._jobs.close()
        self._cancel_panel_draws()
        for timer in self.tk.splitlist(self.tk.call("after", "info")):
            self.after_cancel(timer)
        self.database.close()
        self.destroy()

    def _cancel_panel_draws(self):
        # Each child owns its Tcl callback command. Cancelling its timer through
        # the root would leave a stale command name in the child's destroy list.
        for panel in tuple(self._panel_draws):
            if panel.render_timer is not None:
                panel.after_cancel(panel.render_timer)
                panel.render_timer = None
        self._panel_draws.clear()
