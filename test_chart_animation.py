"""Deterministic animation checks: no desktop, database, or wall-clock sleeps."""
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop_ui import ChartEntrance, DashboardMixin, bar_coordinates, chart_is_visible, clockwise_arc
import screen_time_tracker as core


class NavigationBase:
    """Use the real navigation methods, replacing only the actual UI/database."""
    change_day = core.LegacyScreenTimeTrackerApp.change_day
    go_today = core.LegacyScreenTimeTrackerApp.go_today
    change_week_detail = core.LegacyScreenTimeTrackerApp.change_week_detail
    manual_refresh = core.LegacyScreenTimeTrackerApp.manual_refresh


class NavigationApp(DashboardMixin, NavigationBase):
    def __init__(self):
        self.closing = False
        self.selected_day = self.week_selected_day = date.today()
        self.day_date_var = Mock()
        self.refresh_day = Mock()
        self.refresh_week_detail = Mock()
        self._entrance = Mock()
        self._scrolls = {}
        self._hide_tooltip = Mock()
        self.notebook = SimpleNamespace(selected=0)
        self.notebook.select = lambda index:setattr(self.notebook, "selected", index)
        self.nav_buttons = [Mock(), Mock()]
        self.page_title = Mock()
        self.collector_enabled = False
        self.refresh_all = Mock()
        self.status_var = Mock()
        self.status_dot_label = Mock()


class ChartAnimationTests(unittest.TestCase):
    def setUp(self):
        self.now = 20.0
        self.visible = False
        self.owner = SimpleNamespace(closing=False, after=Mock(return_value="animation-timer"), after_cancel=Mock())
        self.player = ChartEntrance(self.owner, clock=lambda: self.now, visible=lambda canvas, bounds: self.visible)
        self.canvas = object()
        self.paint = Mock()

    def register(self, key="day_timeline", canvas=None, paint=None):
        self.player.register(key, self.canvas if canvas is None else canvas, (0, 0, 300, 200), self.paint if paint is None else paint)

    def start(self):
        self.visible = True
        self.register()
        self.player.tick()

    def test_bars_grow_up_from_baseline_with_ease_out(self):
        final = (10, 25, 30, 125)
        self.assertEqual(bar_coordinates(final, 0), (10, 125, 30, 125))
        self.assertEqual(bar_coordinates(final, .5), (10, 37.5, 30, 125))
        self.assertEqual(bar_coordinates(final, 1), final)
        self.assertEqual(bar_coordinates(final, 1.5), final)

    def test_circle_starts_at_top_and_moves_clockwise_sequentially(self):
        self.assertEqual(clockwise_arc(0, 180, 0), (90, 0))
        self.assertEqual(clockwise_arc(0, 180, .25), (90, -90))
        self.assertEqual(clockwise_arc(180, 180, .25), (-90, 0))
        self.assertEqual(clockwise_arc(180, 180, .75), (-90, -90))
        self.assertEqual(clockwise_arc(180, 180, 1), (-90, -180))

    def test_single_slice_avoids_tk_full_circle_degeneracy(self):
        self.assertEqual(clockwise_arc(0, 360, 1), (90, -359.999))
        self.assertEqual(clockwise_arc(30, 0, 1), (60, 0))

    def test_hidden_chart_waits_without_consuming_time(self):
        self.register()
        self.now += 4
        self.player.tick()
        self.assertEqual(self.player.charts["day_timeline"].state, "waiting")
        self.assertIsNone(self.player.charts["day_timeline"].started)
        self.paint.assert_called_with(0)
        self.visible = True
        self.player.tick()
        self.assertEqual(self.player.charts["day_timeline"].started, self.now)

    def test_initial_layout_and_panel_paint_finish_before_animation_clock_starts(self):
        self.visible = True
        self.register()
        self.owner._panel_draws = {"large-rounded-card"}
        self.now += 2
        self.player.tick()
        self.assertEqual(self.player.charts["day_timeline"].state, "waiting")
        self.owner._panel_draws.clear()
        self.owner._draw_timers = {"chart":"resize-timer"}
        self.player.tick()
        self.assertEqual(self.player.charts["day_timeline"].state, "waiting")
        self.owner._draw_timers.clear()
        self.player.tick()
        self.assertEqual(self.player.charts["day_timeline"].started, self.now)

    def test_finishes_after_one_second_not_frame_count(self):
        self.start()
        self.now += .48
        self.player.tick()
        self.assertAlmostEqual(self.paint.call_args.args[0], .48)
        self.now += .57
        self.player.tick()
        self.assertEqual(self.player.charts["day_timeline"].state, "done")
        self.paint.assert_called_with(1)

    def test_automatic_refresh_and_resize_preserve_start_and_progress(self):
        self.start()
        started = self.player.charts["day_timeline"].started
        self.now += .9
        self.player.detach(self.canvas)
        redrawn = Mock()
        self.register(paint=redrawn)
        self.assertEqual(self.player.charts["day_timeline"].started, started)
        self.assertAlmostEqual(redrawn.call_args.args[0], .9)
        self.now += .11
        self.player.tick()
        redrawn.assert_called_with(1)

    def test_completed_chart_does_not_replay_without_explicit_rearm(self):
        self.start()
        self.now += 1.1
        self.player.tick()
        self.visible = False
        self.register()
        self.visible = True
        self.player.tick()
        self.paint.assert_called_with(1)
        self.assertEqual(self.player.charts["day_timeline"].state, "done")

    def test_scroll_out_does_not_pause_or_restart_animation(self):
        self.start()
        started = self.player.charts["day_timeline"].started
        self.visible = False
        self.now += 1.1
        self.player.tick()
        self.visible = True
        self.player.tick()
        self.assertEqual(self.player.charts["day_timeline"].started, started)
        self.assertEqual(self.player.charts["day_timeline"].state, "done")

    def test_manual_refresh_finishes_only_playing_charts(self):
        self.start()
        self.visible = False
        waiting = Mock()
        self.register("week_chart", object(), waiting)
        self.player.finish_playing()
        self.paint.assert_called_with(1)
        self.assertEqual(self.player.charts["week_chart"].state, "waiting")
        self.visible = True
        self.player.tick()
        self.assertEqual(self.player.charts["week_chart"].state, "playing")

    def test_targeted_finish_does_not_consume_unrelated_charts(self):
        self.start()
        self.register("week_chart", object(), Mock())
        self.player.tick()
        self.player.finish_playing(("day_timeline", "day_pie"))
        self.assertEqual(self.player.charts["day_timeline"].state, "done")
        self.assertEqual(self.player.charts["week_chart"].state, "playing")

    def test_all_charts_share_one_timer(self):
        for key in ("day_timeline", "day_pie", "week_chart", "week_pie"):
            self.register(key)
        self.owner.after.assert_called_once()

    def test_no_data_never_registers_or_schedules_animation(self):
        self.player.tick()
        self.assertFalse(self.player.charts)
        self.owner.after.assert_not_called()
        self.register()
        self.visible = True
        self.player.tick()
        self.assertEqual(self.player.charts["day_timeline"].state, "playing")

    def test_dpi_rebuild_preserves_completed_state_and_waiting_charts(self):
        self.start()
        self.visible = False
        self.register("day_pie", object(), Mock())
        self.player.suspend()
        self.owner.after_cancel.assert_called_once()
        self.assertIsNone(self.player.timer)
        self.assertEqual(self.player.charts["day_timeline"].state, "done")
        self.assertEqual(self.player.charts["day_pie"].state, "waiting")
        self.register()
        self.paint.assert_called_with(1)

    def test_closed_player_cancels_callbacks_and_ignores_results(self):
        self.start()
        self.player.close()
        self.owner.after_cancel.assert_called_once()
        self.paint.reset_mock()
        self.player.tick()
        self.register()
        self.paint.assert_not_called()
        self.assertIsNone(self.player.timer)

    def test_detached_canvas_is_not_updated_by_old_callback(self):
        self.start()
        self.player.detach(self.canvas)
        self.paint.reset_mock()
        self.now += .5
        self.player.tick()
        self.paint.assert_not_called()

    def test_rearm_waits_for_new_data_and_never_calls_stale_paint(self):
        self.start()
        old = self.player.charts["day_timeline"]
        self.now += .4
        self.player.rearm(("day_timeline", "day_pie"))
        self.paint.reset_mock()
        self.player.tick()
        fresh = self.player.charts["day_timeline"]
        self.assertIsNot(fresh, old)
        self.assertIsNone(fresh.canvas)
        self.assertIsNone(fresh.started)
        self.assertEqual(fresh.state, "waiting")
        self.paint.assert_not_called()
        paint = Mock()
        self.register(paint=paint)
        self.player.tick()
        self.assertEqual(fresh.started, self.now)
        paint.assert_called_with(0)

    def test_rearm_replays_a_completed_chart_from_zero(self):
        self.start()
        self.now += 1.1
        self.player.tick()
        self.player.rearm(("day_timeline",))
        self.register()
        self.paint.assert_called_with(0)
        self.player.tick()
        self.now += .25
        self.player.tick()
        self.assertAlmostEqual(self.paint.call_args.args[0], .25)

    def test_rearm_preserves_unrelated_chart_progress(self):
        self.start()
        self.register("week_chart", object(), Mock())
        self.player.tick()
        week = self.player.charts["week_chart"]
        self.now += .2
        self.player.rearm(("day_timeline", "day_pie"))
        self.assertIs(self.player.charts["week_chart"], week)
        self.assertEqual(week.state, "playing")
        self.assertAlmostEqual(self.player.progress(week), .2)

    def test_rapid_rearms_do_not_queue_timers_or_old_frames(self):
        self.start()
        first = self.player.charts["day_timeline"]
        scheduled = self.owner.after.call_count
        for _ in range(20):
            self.player.rearm(("day_timeline", "day_pie"))
            self.register()
        self.assertEqual(self.owner.after.call_count, scheduled)
        final = self.player.charts["day_timeline"]
        self.assertIsNot(final, first)
        self.now += .5
        self.player.tick()
        self.assertEqual(final.started, self.now)
        self.assertEqual(final.state, "playing")

    def test_rearmed_offscreen_chart_still_waits_for_visibility(self):
        self.start()
        self.player.finish_playing()
        self.player.rearm(("day_timeline",))
        self.visible = False
        self.register()
        self.now += 2
        self.player.tick()
        self.assertEqual(self.player.charts["day_timeline"].state, "waiting")
        self.visible = True
        self.player.tick()
        self.assertEqual(self.player.charts["day_timeline"].started, self.now)

    def test_closed_controller_ignores_rearm(self):
        self.player.close()
        self.player.rearm(("day_timeline",))
        self.assertFalse(self.player.charts)

    def test_first_visibility_clips_graph_not_legend_to_scroll_viewport(self):
        def widget(x, y, w, h, master=None):
            return SimpleNamespace(master=master, winfo_rootx=lambda:x, winfo_rooty=lambda:y,
                                   winfo_width=lambda:w, winfo_height=lambda:h)
        root = widget(0, 0, 800, 600)
        root.state = lambda:"normal"
        root.winfo_screenwidth, root.winfo_screenheight = lambda:1920, lambda:1080
        viewport = widget(200, 200, 500, 300, root)
        canvas = widget(200, 480, 300, 440, viewport)
        canvas.winfo_exists = canvas.winfo_viewable = lambda:True
        canvas.canvasx = canvas.canvasy = lambda n:n
        with patch("desktop_ui.os.name", "posix"):
            self.assertFalse(chart_is_visible(root, canvas, (0, 30, 300, 190)))
            canvas.winfo_rooty = lambda:460
            self.assertTrue(chart_is_visible(root, canvas, (0, 30, 300, 190)))
            canvas.winfo_viewable = lambda:False
            self.assertFalse(chart_is_visible(root, canvas, (0, 30, 300, 190)))


class NavigationReplayTests(unittest.TestCase):
    def setUp(self):
        self.app = NavigationApp()

    def test_date_change_rearms_day_only_before_loading_new_data(self):
        self.app.change_day(-1)
        self.app._entrance.rearm.assert_called_once_with(("day_timeline", "day_pie"))
        self.assertEqual(self.app.selected_day, date.today()-timedelta(days=1))
        self.app.refresh_day.assert_called_once()

    def test_future_date_and_zero_offset_do_not_rearm(self):
        self.app.change_day(1)
        self.app.change_day(0)
        self.app._entrance.rearm.assert_not_called()
        self.assertEqual(self.app.selected_day, date.today())

    def test_back_to_today_rearms_only_when_date_changes(self):
        self.app.go_today()
        self.app._entrance.rearm.assert_not_called()
        self.app.selected_day -= timedelta(days=3)
        self.app.go_today()
        self.app._entrance.rearm.assert_called_once_with(("day_timeline", "day_pie"))
        self.assertEqual(self.app.selected_day, date.today())

    def test_each_actual_page_change_rearms_its_destination(self):
        self.app.select_page(1)
        self.app.select_page(0)
        self.assertEqual(self.app._entrance.rearm.call_args_list, [
            unittest.mock.call(("week_chart", "week_pie")),
            unittest.mock.call(("day_timeline", "day_pie"))])
        self.assertEqual(self.app._entrance.finish_playing.call_count, 2)

    def test_repeated_current_page_click_does_not_rearm_or_finish(self):
        self.app.select_page(0)
        self.app._entrance.rearm.assert_not_called()
        self.app._entrance.finish_playing.assert_not_called()

    def test_initial_layout_and_dpi_restore_do_not_rearm(self):
        self.app.notebook.selected = -1
        self.app.select_page(0, replay=False)
        self.app.select_page(1, replay=False)
        self.app._entrance.rearm.assert_not_called()

    def test_week_detail_date_change_does_not_rearm_summary(self):
        previous = date.today()-timedelta(days=1)
        display = core.day_display(previous)
        self.app.week_detail_var = Mock()
        self.app.week_detail_var.get.return_value = display
        self.app.week_day_values = [display]
        self.app.change_week_detail()
        self.assertEqual(self.app.week_selected_day, previous)
        self.app.refresh_week_detail.assert_called_once()
        self.app._entrance.rearm.assert_not_called()

    def test_manual_refresh_finishes_but_does_not_rearm(self):
        self.app.manual_refresh()
        self.app._entrance.finish_playing.assert_called_once_with()
        self.app._entrance.rearm.assert_not_called()
        self.app.refresh_all.assert_called_once()

    def test_pending_panel_timers_are_cancelled_by_their_own_widgets(self):
        panel = Mock(render_timer="child-timer")
        self.app._panel_draws = {panel}
        self.app._cancel_panel_draws()
        panel.after_cancel.assert_called_once_with("child-timer")
        self.assertIsNone(panel.render_timer)
        self.assertFalse(self.app._panel_draws)


if __name__ == "__main__":
    unittest.main()
