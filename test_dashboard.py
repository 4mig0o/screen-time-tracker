import queue
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from desktop_ui import AsyncIcons, BackgroundJobs, DashboardMixin, SmoothScroll, fit_icon, icon_pixels, unpremultiply


class FakeCanvas:
    def __init__(self, position=0, height=1000, viewport=200):
        self.position, self.height, self.viewport = position, height, viewport
        self.tk = SimpleNamespace(splitlist=lambda value: value.split())

    def cget(self, _key):
        return f"0 0 300 {self.height}"

    def winfo_height(self):
        return self.viewport

    def winfo_exists(self):
        return True

    def yview(self):
        return self.position / self.height, (self.position + self.viewport) / self.height

    def yview_moveto(self, fraction):
        self.position = fraction * self.height


class DashboardTests(unittest.TestCase):
    def owner(self):
        return SimpleNamespace(after=Mock(return_value="timer"), after_cancel=Mock(), closing=False)

    def test_small_wheel_inputs_accumulate(self):
        scroll = SmoothScroll(self.owner(), FakeCanvas())
        for _ in range(10):
            scroll.push(0.4)
        self.assertAlmostEqual(scroll.target, 4)

    def test_reverse_input_responds_from_current_position(self):
        canvas = FakeCanvas(200)
        scroll = SmoothScroll(self.owner(), canvas)
        scroll.push(80)
        scroll.push(-20)
        self.assertEqual(scroll.target, 180)

    def test_subpixel_input_survives_separate_animation_runs(self):
        canvas = FakeCanvas()
        canvas.yview_moveto = lambda fraction:setattr(canvas,"position",round(fraction*canvas.height))
        scroll = SmoothScroll(self.owner(), canvas)
        for _ in range(5):
            scroll.push(.2)
            scroll.tick()
        self.assertEqual(canvas.position, 1)

    def test_child_overflow_passes_to_parent(self):
        child = SmoothScroll(self.owner(), FakeCanvas(790))
        parent = SmoothScroll(self.owner(), FakeCanvas(100))
        parent.push(child.push(80))
        self.assertEqual(child.target, 800)
        self.assertEqual(parent.target, 170)

    def test_scroll_finishes_and_drag_cancels(self):
        owner = self.owner()
        scroll = SmoothScroll(owner, FakeCanvas())
        scroll.push(80)
        for _ in range(20):
            scroll.last = time.perf_counter() - .016
            scroll.tick()
            if scroll.target is None:
                break
        self.assertAlmostEqual(scroll.canvas.position, 80)
        self.assertIsNone(scroll.target)
        scroll.push(30)
        scroll.cancel()
        self.assertIsNone(scroll.target)
        owner.after_cancel.assert_called()

    def test_dpi_physical_icon_sizes(self):
        self.assertEqual([icon_pixels(34, s) for s in (1, 1.25, 1.5, 2)], [34, 43, 51, 68])
        self.assertEqual(icon_pixels(22, 1.5), 33)

    def test_small_icon_is_not_enlarged(self):
        result = fit_icon(Image.new("RGBA", (16, 16), "red"), 68)
        self.assertEqual(result.size, (68, 68))
        self.assertEqual(result.getbbox(), (26, 26, 42, 42))

    def test_transparency_unpremultiplication(self):
        self.assertEqual(unpremultiply(bytes((20, 40, 60, 128))), bytes((40, 80, 120, 128)))
        self.assertEqual(unpremultiply(bytes((0, 0, 0, 0))), bytes((0, 0, 0, 0)))

    def test_cache_key_changes_with_source_and_scale(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "icon.bin"
            path.write_bytes(b"a")
            service = AsyncIcons(SimpleNamespace(icon_manager=Mock(), scale=1))
            row = {"app_key": "test.exe", "app_name": "Test", "icon_path": str(path)}
            key = service.key(row, 34)
            service.owner.scale = 2
            self.assertNotEqual(key, service.key(row, 34))
            service.owner.scale = 1
            import os
            os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
            self.assertNotEqual(key, service.key(row, 34))

    def test_closed_worker_does_not_deliver_results(self):
        jobs = BackgroundJobs(self.owner())
        done = Mock()
        jobs.results.put(("late", done, 42, None))
        jobs.close()
        jobs.drain()
        done.assert_not_called()

    def test_site_dispatch_uses_favicon_instead_of_browser_exe(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "favicons").mkdir()
            Image.new("RGBA", (128,128), "red").save(directory / "favicons" / "example.com.png")
            owner = SimpleNamespace(icon_manager=Mock(), scale=2, preview=True, data_dir=directory, closing=False)
            service = AsyncIcons(owner)
            def immediate(key, work, done):
                done(work(), None)
                return True
            owner._jobs = SimpleNamespace(submit=immediate)
            callback = Mock()
            with patch("desktop_ui.ImageTk.PhotoImage", return_value="photo"):
                service.request({"app_key":"website:example.com", "app_name":"Example", "icon_path":"favicon:example.com"}, 34, callback)
            callback.assert_called_with("photo")
            owner.icon_manager.resolve_icon_path.assert_not_called()
            self.assertEqual(next(iter(service.source_sizes.values())), (128,128))

    def test_icon_result_for_old_canvas_generation_is_ignored(self):
        canvas = Mock()
        canvas.winfo_exists.return_value = True
        owner = SimpleNamespace(px=lambda n:n, font=lambda *a:None, _icons=Mock(),
                                _canvas_generation={canvas:1}, _canvas_photos={canvas:[]}, closing=False)
        row = {"app_key":"test.exe", "app_name":"Test", "icon_path":""}
        DashboardMixin._draw_app_icon(owner, canvas, 0, 0, row)
        deliver = owner._icons.request.call_args.args[2]
        owner._canvas_generation[canvas] = 2
        deliver("late photo")
        canvas.create_image.assert_not_called()

    def test_async_status_reports_missing_collector(self):
        owner = SimpleNamespace(closing=False, preview=False, collector_enabled=True,
                                core=SimpleNamespace(collector_is_running=lambda:False),
                                activity=Mock(), status_var=Mock(), status_dot_label=Mock(),
                                after=Mock(), refresh_status=Mock())
        def immediate(key, work, done):
            done(work(), None)
        owner._jobs = SimpleNamespace(submit=immediate)
        DashboardMixin.refresh_status(owner)
        self.assertIn("未运行", owner.status_var.set.call_args.args[0])
        owner.activity.active_app_details.assert_not_called()

    def test_async_status_failure_is_visible(self):
        owner = SimpleNamespace(closing=False, preview=False, collector_enabled=True,
                                status_var=Mock(), status_dot_label=Mock(), after=Mock(), refresh_status=Mock())
        owner._jobs = SimpleNamespace(submit=lambda key, work, done:done(None, OSError("failure")))
        DashboardMixin.refresh_status(owner)
        self.assertIn("读取失败", owner.status_var.set.call_args.args[0])

    def test_native_source_is_high_res_before_final_resize(self):
        native = Mock()
        native.resolve_icon_path.return_value = "example.exe"
        owner = SimpleNamespace(icon_manager=native, scale=2, preview=True, closing=False)
        owner._jobs = SimpleNamespace(submit=lambda key, work, done:(done(work(),None), True)[1])
        service = AsyncIcons(owner)
        with patch("desktop_ui.shell_icon", return_value=Image.new("RGBA",(128,128),"blue")) as extract, patch("desktop_ui.ImageTk.PhotoImage", return_value="photo") as photo:
            service.request({"app_key":"example.exe","app_name":"Example","icon_path":""},34,Mock())
        extract.assert_called_once_with("example.exe",128)
        self.assertEqual(photo.call_args.args[0].size,(68,68))


if __name__ == "__main__":
    unittest.main()
