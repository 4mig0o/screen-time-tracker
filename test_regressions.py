import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import screen_time_tracker as tracker


class RegressionTests(unittest.TestCase):
    def test_migration_preserves_totals_in_both_orders(self):
        for keys in (("github", "github.com"), ("github.com", "github")):
            with self.subTest(keys=keys), tempfile.TemporaryDirectory() as folder:
                db = tracker.UsageDatabase(Path(folder))
                for key, seconds in zip(keys, (60, 120)):
                    db.add_sample(datetime(2026, 10, 22, 12), "website:" + key, key, seconds)
                db.close()
                for _ in range(3):
                    db = tracker.UsageDatabase(Path(folder))
                    self.assertEqual(db.total_for_day("2026-10-22"), 180)
                    self.assertEqual(sum(db.timeline_for_day("2026-10-22").values()), 180)
                    self.assertEqual(len(db.apps_for_day("2026-10-22")), 1)
                    db.close()

    def test_existing_domain_is_not_reclassified_by_keyword(self):
        with tempfile.TemporaryDirectory() as folder:
            db = tracker.UsageDatabase(Path(folder))
            db.add_sample(datetime(2026, 10, 22), "website:github.example.org", "Example", 60)
            db.close()
            db = tracker.UsageDatabase(Path(folder))
            self.assertEqual(db.apps_for_day("2026-10-22")[0]["app_key"], "website:github.example.org")
            db.close()

    def test_same_title_can_change_site_and_browser(self):
        resolver = tracker.WebsiteResolver()
        with patch.object(resolver, "_query_history_domain", side_effect=[
            ("one.example.com", "Dashboard"),
            ("two.example.com", "Dashboard"),
            ("three.example.com", "Dashboard"),
        ]):
            results = [resolver.resolve_site(browser, "Dashboard")[0]
                       for browser in ("chrome.exe", "msedge.exe", "chrome.exe")]
        self.assertEqual(results, ["website:one.example.com", "website:two.example.com", "website:three.example.com"])

    def test_zero_duration(self):
        self.assertEqual(tracker.format_duration(0), "0 分钟")
        self.assertEqual(tracker.format_duration(5), "不足 1 分钟")

    def make_app(self, enabled=True):
        return SimpleNamespace(closing=False, collector_enabled=enabled,
                               status_var=Mock(), status_dot_label=Mock(),
                               activity=Mock(), after=Mock(), refresh_status=Mock(), refresh_all=Mock(), data_dir=Path("unused"))

    def test_missing_collector_is_not_reported_as_running(self):
        app = self.make_app()
        with patch.object(tracker, "collector_is_running", return_value=False):
            tracker.ScreenTimeTrackerApp.refresh_status(app)
        self.assertIn("未运行", app.status_var.set.call_args.args[0])
        app.activity.active_app_details.assert_not_called()

    def test_refresh_does_not_hide_start_failure(self):
        app = self.make_app()
        with patch.object(tracker, "start_background_collector"), patch.object(tracker, "collector_is_running", return_value=False):
            tracker.ScreenTimeTrackerApp.manual_refresh(app)
        self.assertIn("采集未运行", app.status_var.set.call_args.args[0])

    def test_disabled_collector(self):
        app = self.make_app(False)
        tracker.ScreenTimeTrackerApp.refresh_status(app)
        app.status_var.set.assert_called_with("采集未启用")

    def test_running_and_locked_collector_states(self):
        for locked in (True, False):
            with self.subTest(locked=locked):
                app = self.make_app()
                app.activity.is_workstation_locked.return_value = locked
                app.activity.active_app_details.return_value = ("code.exe", "Code", "")
                with patch.object(tracker, "collector_is_running", return_value=True):
                    tracker.ScreenTimeTrackerApp.refresh_status(app)
                message = app.status_var.set.call_args.args[0]
                self.assertIn("暂停记录" if locked else "当前应用：Code", message)


if __name__ == "__main__":
    unittest.main()
