"""Exercise real Tk animation with isolated demo data and own-window captures."""
import argparse
from contextlib import ExitStack
import ctypes
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
import time

from PIL import Image, ImageDraw, ImageGrab
import screen_time_tracker as core


def pump(app, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.update()
        time.sleep(.004)


def until(app, condition, timeout=3):
    end = time.perf_counter() + timeout
    while not condition():
        if time.perf_counter() >= end:
            raise AssertionError("Timed out waiting for chart state")
        pump(app, .008)


def reveal(app, key):
    motion = app._entrance.charts[key]
    page = app.day_scroll_canvas if key.startswith("day") else app.week_scroll_canvas
    app._scrolls[page].cancel()
    # Scroll only far enough to expose the graph, not the following chart.
    graph_top = motion.canvas.winfo_rooty() + motion.bounds[1]
    bottom = motion.canvas.winfo_rooty() + motion.bounds[3]
    if graph_top < page.winfo_rooty() + app.px(12):
        top = page.canvasy(0) + graph_top - page.winfo_rooty() - app.px(12)
    else:
        top = page.canvasy(0) + max(0, bottom - page.winfo_rooty() - page.winfo_height() + app.px(12))
    region = [float(v) for v in page.tk.splitlist(page.cget("scrollregion"))]
    page.yview_moveto(max(0, top) / max(1, region[3] - region[1]))
    app.update_idletasks()


def capture_graph(app, key):
    """Capture only this process's window, never the screen/foreground app."""
    user = ctypes.windll.user32
    user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user.GetAncestor.restype = ctypes.c_void_p
    hwnd = user.GetAncestor(app.winfo_id(), 2)
    motion = app._entrance.charts[key]
    canvas, bounds = motion.canvas, motion.bounds
    left = canvas.winfo_rootx() - app.winfo_rootx() + bounds[0]
    top = canvas.winfo_rooty() - app.winfo_rooty() + bounds[1]
    box = (round(left), round(top), round(left + bounds[2]-bounds[0]), round(top + bounds[3]-bounds[1]))
    image = ImageGrab.grab(window=hwnd)
    # The HWND capture contains the Tk client area (without Windows chrome).
    assert image.size == (app.winfo_width(), app.winfo_height()), (image.size, app.geometry())
    assert min(box) >= 0 and box[2] <= image.width and box[3] <= image.height, (key, box, image.size)
    return image.crop(box)


def record(app, key, folder):
    player = app._entrance
    # A page switch posts a virtual event; idle-only layout updates do not run
    # that event, so wait for its fresh data/items before accessing the graph.
    until(app, lambda:player.charts[key].canvas is not None and not app._draw_timers and not app._panel_draws)
    reveal(app, key)
    until(app, lambda:not app._draw_timers and not app._panel_draws)
    reveal(app, key)
    try:
        until(app, lambda:player.charts[key].state == "playing")
    except AssertionError as error:
        motion = player.charts[key]
        page = app.day_scroll_canvas if key.startswith("day") else app.week_scroll_canvas
        raise AssertionError({"key":key,"state":motion.state,"age":None if motion.started is None else time.perf_counter()-motion.started,"window":app.geometry(),
                              "canvas":[motion.canvas.winfo_rootx(),motion.canvas.winfo_rooty(),motion.canvas.winfo_width(),motion.canvas.winfo_height()],
                              "page":[page.winfo_rootx(),page.winfo_rooty(),page.winfo_width(),page.winfo_height()],
                              "region":page.cget("scrollregion"),"position":page.yview(),"bounds":motion.bounds}) from error
    motion = player.charts[key]
    heartbeat_start = time.perf_counter()
    app.after(80, lambda:app._qa_heartbeats.update({key:round((time.perf_counter()-heartbeat_start)*1000, 2)}))
    samples, images = [], []
    for target in (.04, .24, .48, .72, 1.03):
        until(app, lambda:time.perf_counter()-motion.started >= target)
        app.update_idletasks()
        progress = player.progress(motion)
        image = capture_graph(app, key)
        images.append(image)
        samples.append({"progress":round(progress, 4), "elapsed_seconds":round(time.perf_counter()-motion.started, 4)})
    until(app, lambda:motion.state == "done")
    # Images are actual captures at the recorded times; no interpolated frames.
    width = 420
    thumbs = []
    for sample, image in zip(samples, images):
        thumb = image.convert("RGB")
        thumb.thumbnail((width, 250), Image.Resampling.LANCZOS)
        panel = Image.new("RGB", (width+20, 290), "#111318")
        panel.paste(thumb, ((panel.width-thumb.width)//2, 36))
        ImageDraw.Draw(panel).text((12, 10), f"{key}  {sample['progress']*100:.0f}%", fill="#F3F4F6")
        thumbs.append(panel)
    sheet = Image.new("RGB", (len(thumbs)*thumbs[0].width, 290), "#111318")
    for index, panel in enumerate(thumbs):
        sheet.paste(panel, (index*panel.width, 0))
    sheet.save(folder / f"{key}-frames.png")
    for index, image in enumerate(images):
        image.save(folder / f"{key}-{index}.png")
    assert images[0].tobytes() != images[-1].tobytes(), f"{key}: no visual change captured"
    return samples


def run(output, scales):
    output.mkdir(parents=True, exist_ok=True)
    reports = []
    for scale in scales:
        folder = output / f"{scale:g}x"
        folder.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="screen-time-animation-") as temp, ExitStack() as cleanup:
            core.seed_demo_data(Path(temp))
            app = core.ScreenTimeTrackerApp(Path(temp), start_collector=False, preview=True, ui_scale=scale)
            app._qa_heartbeats = {}
            cleanup.callback(app.close)
            app.geometry(f"{min(app.px(1320),app.winfo_screenwidth()-50)}x{min(app.px(520), app.winfo_screenheight()-100)}+20+20")
            calls = {}
            original = app._entrance.register
            def register(key, canvas, bounds, paint):
                def observed(progress):
                    paint(progress)
                    calls.setdefault(key, []).append((time.perf_counter(), progress))
                original(key, canvas, bounds, observed)
            app._entrance.register = register
            pump(app, .025)
            player = app._entrance
            assert player.charts["week_chart"].state == "waiting"
            assert player.charts["week_pie"].state == "waiting"
            assert player.charts["day_pie"].state == "waiting", "Offscreen ring started prematurely"
            frames = {}
            frames["day_timeline"] = record(app, "day_timeline", folder)
            app.day_scroll_canvas.yview_moveto(0)
            pump(app, .08)
            assert player.charts["day_pie"].state == "waiting"
            # The native 900ms refresh has run by now; the timeline still started once.
            assert player.charts["day_timeline"].state == "done"
            frames["day_pie"] = record(app, "day_pie", folder)
            started = {key:m.started for key,m in player.charts.items() if m.started is not None}
            app.select_page(1)
            app.week_scroll_canvas.yview_moveto(0)
            app.update_idletasks()
            frames["week_chart"] = record(app, "week_chart", folder)
            frames["week_pie"] = record(app, "week_pie", folder)
            started.update({key:m.started for key,m in player.charts.items() if m.started is not None})
            app.manual_refresh()
            app.geometry(f"{min(app.px(1100),app.winfo_screenwidth()-50)}x700+20+20")
            pump(app, .2)
            assert all(m.state == "done" and m.started == started[key] for key,m in player.charts.items())
            durations = {}
            for key, motion in player.charts.items():
                completed = next(t for t,p in calls[key] if p >= 1)
                durations[key] = round(completed-motion.started, 4)
                assert .98 <= durations[key] <= 1.25, (key, durations[key])
            # Empty data never starts an entrance; restoring data starts it on visibility.
            app.database.connection.execute("DELETE FROM app_usage")
            app.database.connection.execute("DELETE FROM timeline_usage")
            app.database.connection.commit()
            app.refresh_all()
            pump(app, .08)
            assert not app._tk_errors, app._tk_errors
            reports.append({"scale":scale,"frame_samples":frames,"durations_seconds":durations,
                            "80ms_heartbeat_actual_ms":app._qa_heartbeats,
                            "hidden_charts_waited":True,"refresh_resize_no_replay":True,
                            "empty_data_errors":list(app._tk_errors),"temporary_data_only":True})
    (output/"results.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(reports, ensure_ascii=False))


def run_edges(output):
    """Real-widget checks for empty-to-data, in-flight cancel, scrolling and close."""
    with tempfile.TemporaryDirectory(prefix="screen-time-animation-edges-") as temp, ExitStack() as cleanup:
        app = core.ScreenTimeTrackerApp(Path(temp), start_collector=False, preview=True, ui_scale=1)
        cleanup.callback(app.close)
        app.geometry("1100x600+20+20")
        pump(app, .6)
        assert not app._entrance.charts, "Empty data consumed an entrance"
        app.database.add_sample(datetime.now(), "animation-qa.exe", "Animation QA", 600)
        app.refresh_all()
        app.update_idletasks()
        reveal(app, "day_timeline")
        until(app, lambda:app._entrance.charts["day_timeline"].state == "playing")
        motion = app._entrance.charts["day_timeline"]
        started = motion.started
        heartbeat, clicked = [], []
        begin = time.perf_counter()
        app._jobs.submit("animation-slow-worker", lambda:time.sleep(2), lambda value,error:None)
        app.after(80, lambda:heartbeat.append(time.perf_counter()-begin))
        page_scroll = app._scrolls[app.day_scroll_canvas]
        before = page_scroll.current()
        page_scroll.push(12)
        def click_refresh():
            app.manual_refresh()
            clicked.append(time.perf_counter()-begin)
        app.after(180, click_refresh)
        pump(app, .4)
        assert heartbeat and heartbeat[0] < .3
        assert clicked and clicked[0] < .5
        assert page_scroll.current() > before, "Scrolling did not respond during animation"
        assert motion.state == "done" and motion.started == started
        app.go_today()
        pump(app, .1)
        assert motion.state == "done" and motion.started == started
        assert not app._tk_errors, app._tk_errors
        report = {"empty_then_data_entrance":True,"refresh_during_animation_finishes_without_replay":True,
                  "scroll_during_animation":True,"80ms_heartbeat_actual_ms":round(heartbeat[0]*1000,2),
                  "180ms_refresh_actual_ms":round(clicked[0]*1000,2),"tk_errors":list(app._tk_errors)}
        app.close()
        assert app._entrance.closed and app._entrance.timer is None
        report["close_cancels_timer"] = True
        probe = core.ScreenTimeTrackerApp(Path(temp), start_collector=False, preview=True, ui_scale=1)
        cleanup.callback(probe.close)
        probe.update_idletasks()
        report["pending_panels_at_close"] = len(probe._panel_draws)
        assert probe._panel_draws, "Close-during-layout probe did not queue any card paints"
        probe.close()
        report["close_during_layout"] = True
        (output/"edge-results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False))


def run_replay(output, scales):
    """Exercise navigation on the actual dashboard, not a simulated controller."""
    reports = []
    for scale in scales:
        folder = output / "replay" / f"{scale:g}x"
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="screen-time-replay-") as temp, ExitStack() as cleanup:
            core.seed_demo_data(Path(temp))
            app = core.ScreenTimeTrackerApp(Path(temp), start_collector=False, preview=True, ui_scale=scale)
            cleanup.callback(app.close)
            app._qa_heartbeats = {}
            app.geometry(f"{min(app.px(1320),app.winfo_screenwidth()-50)}x{min(app.px(520),app.winfo_screenheight()-100)}+20+20")
            pump(app, 1.5)
            player = app._entrance
            day_keys, week_keys = ("day_timeline", "day_pie"), ("week_chart", "week_pie")
            frames = {}
            def save(label, key):
                destination = folder / label
                destination.mkdir(exist_ok=True)
                frames[label] = record(app, key, destination)
            def settled(keys):
                return {key:(player.charts[key],player.charts[key].started) for key in keys}
            def unchanged(snapshot):
                assert all(player.charts[key] is motion and motion.started == started for key,(motion,started) in snapshot.items())

            # Actual date change rearms day only, including an offscreen ring.
            week_before = settled(week_keys)
            old_day = player.charts["day_timeline"]
            app.change_day(-1)
            assert player.charts["day_timeline"] is not old_day
            unchanged(week_before)
            assert player.charts["day_pie"].state == "waiting"
            save("date-change-bars", "day_timeline")
            save("date-change-ring", "day_pie")
            historical = settled(day_keys)
            app.day_scroll_canvas.yview_moveto(0)
            pump(app, .08)
            app.day_scroll_canvas.yview_moveto(1)
            pump(app, .08)
            unchanged(historical)
            assert all(player.charts[key].state == "done" for key in day_keys)

            # Return to today rearms, while choosing today again does not.
            app.day_scroll_canvas.yview_moveto(0)
            app.go_today()
            assert all(player.charts[key] is not historical[key][0] for key in day_keys)
            assert app.selected_day == date.today()
            save("return-today-bars", "day_timeline")
            same_day = settled(day_keys)
            app.go_today()
            app.change_day(0)
            app.change_day(1)
            app.select_page(0)
            unchanged(same_day)

            # Entering week rearms week; the detail selector does not.
            app.select_page(1)
            assert all(player.charts[key] is not week_before[key][0] for key in week_keys)
            app.week_scroll_canvas.yview_moveto(0)
            app.update_idletasks()
            save("enter-week-bars", "week_chart")
            save("enter-week-ring", "week_pie")
            weekly = settled(week_keys)
            app.week_detail_var.set(app.week_day_values[-2])
            app.change_week_detail()
            app.select_page(1)
            app.manual_refresh()
            pump(app, .08)
            unchanged(weekly)

            # Every round trip creates fresh target states, never old queued runs.
            app.day_scroll_canvas.yview_moveto(0)
            app.select_page(0)
            assert all(player.charts[key] is not same_day[key][0] for key in day_keys)
            save("return-day-bars", "day_timeline")
            app.week_scroll_canvas.yview_moveto(0)
            app.select_page(1)
            assert all(player.charts[key] is not weekly[key][0] for key in week_keys)
            save("return-week-bars", "week_chart")

            for _ in range(8):
                app.select_page(0)
                app.change_day(-1)
                app.select_page(1)
            assert app.notebook.selected == 1
            until(app, lambda:player.charts["week_chart"].state == "done")
            app.day_scroll_canvas.yview_moveto(0)
            app.select_page(0)
            # Rapid date changes finish with the chosen day, not a stale sample.
            app.change_day(-1)
            app.change_day(1)
            app.go_today()
            assert app.selected_day == date.today()
            until(app, lambda:player.charts["day_timeline"].state == "done")
            assert app.database.total_for_day(app.selected_day) == app.database.total_for_day(date.today())

            # A no-data selection remains waiting until a real visible sample exists.
            app.change_day(-30)
            pump(app, .08)
            assert not app.day_apps_data
            assert all(player.charts[key].state == "waiting" and player.charts[key].canvas is None for key in day_keys)
            app.database.add_sample(datetime.combine(app.selected_day,datetime.min.time()).replace(hour=12), "replay-qa.exe", "Replay QA", 600)
            app.refresh_all()
            reveal(app, "day_timeline")
            until(app, lambda:player.charts["day_timeline"].state == "playing")
            until(app, lambda:player.charts["day_timeline"].state == "done")
            day_done = settled(("day_timeline",))
            app.refresh_all()
            app.geometry(f"{min(app.px(1100),app.winfo_screenwidth()-50)}x{min(app.px(600),app.winfo_screenheight()-100)}+20+20")
            pump(app, .2)
            unchanged(day_done)
            # Exercise the real rebuild path; it must not treat restoration as navigation.
            before_dpi = settled(tuple(player.charts))
            app._rebuild_for_dpi(scale)
            # Keep this QA instance at the selected application-level scale.
            app._forced_scale = scale
            pump(app, .2)
            unchanged(before_dpi)
            assert not app._tk_errors, app._tk_errors
            reports.append({"scale":scale,"date_replay":True,"return_today_replay":True,
                            "day_week_roundtrip_replay":True,"scroll_no_replay":True,
                            "same_date_page_future_no_replay":True,"week_detail_no_replay":True,
                            "rapid_navigation_latest_only":True,"empty_then_data":True,
                            "automatic_refresh_resize_dpi_no_rearm":True,"frames":frames,
                            "80ms_heartbeat_actual_ms":app._qa_heartbeats,"tk_errors":list(app._tk_errors)})
    (output/"replay-results.json").write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"replay_scales":list(scales),"result":"passed"},ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("qa-output/animation"))
    parser.add_argument("--scale", type=float, action="append")
    parser.add_argument("--replay-only", action="store_true")
    options = parser.parse_args()
    if not options.replay_only:
        run(options.output, options.scale or (1, 2))
        run_edges(options.output)
    run_replay(options.output, options.scale or (1, 2))
