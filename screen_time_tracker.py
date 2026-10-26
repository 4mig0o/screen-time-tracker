from __future__ import annotations

import ctypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
import io
import threading
import urllib.request
from urllib.parse import urlparse

import tkinter as tk
from tkinter import messagebox, ttk

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageTk = None

APP_TITLE = "屏幕使用时长"
SAMPLE_SECONDS = 5
TIMELINE_BUCKET_MINUTES = 15
LEGACY_UNKNOWN_APP_KEY = "legacy:unknown-app"
LEGACY_UNKNOWN_APP_NAME = "历史未知应用（无法追溯）"
COLLECTOR_MUTEX_NAME = "Local\\ScreenTimeTrackerCollector"
ERROR_ALREADY_EXISTS = 183
IGNORED_APP_KEYS = {
    "__ignored__",
    "screentimetracker.exe",
    "screen_time_tracker.py",
}

COLORS = {
    "background": "#F5F7FB",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",
    "text": "#152033",
    "muted": "#738094",
    "border": "#E3E8F0",
    "grid": "#EDF1F6",
    "blue": "#4C6FFF",
    "teal": "#19A974",
    "orange": "#F0A44B",
    "red": "#E35D6A",
    "purple": "#8A6CFF",
    "cyan": "#4BB3C8",
}

PIE_COLORS = (
    COLORS["blue"],
    COLORS["teal"],
    COLORS["orange"],
    COLORS["purple"],
    COLORS["red"],
    COLORS["cyan"],
)

APP_NAME_MAP = {
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Mozilla Firefox",
    "wechat.exe": "微信",
    "weixin.exe": "微信",
    "qq.exe": "QQ",
    "winword.exe": "Microsoft Word",
    "excel.exe": "Microsoft Excel",
    "powerpnt.exe": "Microsoft PowerPoint",
    "outlook.exe": "Microsoft Outlook",
    "code.exe": "Visual Studio Code",
    "notepad.exe": "记事本",
    "explorer.exe": "文件资源管理器",
    "teams.exe": "Microsoft Teams",
    "discord.exe": "Discord",
    "slack.exe": "Slack",
    "steam.exe": "Steam",
    "spotify.exe": "Spotify",
    "python.exe": "Python",
    "pythonw.exe": "Python",
    "devenv.exe": "Visual Studio",
    "powershell.exe": "PowerShell",
    "cmd.exe": "命令提示符",
    "chatgpt.exe": "ChatGPT",
    "cc-switch.exe": "CC Switch",
    "searchhost.exe": "Windows 搜索",
    "startmenuexperiencehost.exe": "开始菜单",
    "applicationframehost.exe": "Windows 应用",
    "textinputhost.exe": "文字输入",
    "shellexperiencehost.exe": "Windows 桌面",
    "lockapp.exe": "锁屏界面",
    "taskmgr.exe": "任务管理器",
    "mmc.exe": "Windows 管理控制台",
    "nrc-win64-shipping.exe": "洛克王国：世界",
    "nrc_launcher.exe": "洛克王国：世界启动器",
    "snowshot.exe": "Snow Shot",
    "dwm.exe": "桌面窗口管理器",
    "runtimebroker.exe": "运行时代理",
    "ctfmon.exe": "文字输入服务",
    "sihost.exe": "Windows 外壳体验",
    "securityhealthsystray.exe": "Windows 安全中心",
    "searchapp.exe": "Windows 搜索",
    "widgets.exe": "Windows 小组件",
    "taskhostw.exe": "Windows 任务主机",
    "audiodg.exe": "Windows 音频设备图形隔离",
}

BROWSER_PROCESS_NAMES = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "arc.exe",
}

WEBSITE_TITLE_RULES = (
    ("bilibili", "B站"),
    ("哔哩哔哩", "B站"),
    ("b站", "B站"),
    ("chatgpt", "ChatGPT"),
    ("openai", "ChatGPT"),
    ("github", "GitHub"),
    ("youtube", "YouTube"),
    ("知乎", "知乎"),
    ("zhihu", "知乎"),
    ("抖音", "抖音"),
    ("douyin", "抖音"),
    ("小红书", "小红书"),
    ("xiaohongshu", "小红书"),
    ("微博", "微博"),
    ("weibo", "微博"),
    ("百度", "百度"),
    ("baidu", "百度"),
    ("淘宝", "淘宝"),
    ("taobao", "淘宝"),
    ("京东", "京东"),
    ("jd.com", "京东"),
)

MEDIA_WEBSITE_LABELS = {"B站", "YouTube", "抖音"}

APP_ICON_STYLES = {
    "Google Chrome": ("C", "#F2B544"),
    "Microsoft Edge": ("e", "#1D9BF0"),
    "Mozilla Firefox": ("f", "#E66B2E"),
    "B站（网页）": ("B", "#F06A9B"),
    "ChatGPT（网页）": ("AI", "#111827"),
    "ChatGPT": ("AI", "#111827"),
    "GitHub（网页）": ("G", "#24292F"),
    "YouTube（网页）": ("Y", "#E53935"),
    "微信": ("微", "#18A957"),
    "Microsoft Word": ("W", "#2B78C5"),
    "Microsoft Excel": ("X", "#1F9D55"),
    "Microsoft PowerPoint": ("P", "#D65A3A"),
    "Visual Studio Code": ("<", "#2778C8"),
    "文件资源管理器": ("▣", "#E6A23C"),
    "记事本": ("文", "#6C8CD5"),
    "PowerShell": (">_", "#2C5D8A"),
    LEGACY_UNKNOWN_APP_NAME: ("?", "#64748B"),
}

APP_ICON_COLORS = (
    "#4C6FFF",
    "#19A974",
    "#F0A44B",
    "#8A6CFF",
    "#E35D6A",
    "#4BB3C8",
)


def default_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ScreenTimeTracker"
    return Path.home() / ".screen_time_tracker"


def iso_day(value: date | datetime) -> str:
    return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def chinese_weekday(value: date) -> str:
    return ("一", "二", "三", "四", "五", "六", "日")[value.weekday()]


def day_display(value: date) -> str:
    return f"{value.isoformat()} 星期{chinese_weekday(value)}"


def format_duration(seconds: int | float) -> str:
    if seconds <= 0:
        return "0 分钟"
    total_minutes = max(0, int(round(seconds / 60)))
    if total_minutes == 0:
        return "不足 1 分钟"
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def format_short_duration(seconds: int | float) -> str:
    total_minutes = max(0, int(round(seconds / 60)))
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}小时{minutes}分" if minutes else f"{hours}小时"
    return f"{minutes}分钟"


def format_delta(seconds: int) -> str:
    if seconds > 0:
        return f"较上一日增加 {format_duration(seconds)}"
    if seconds < 0:
        return f"较上一日减少 {format_duration(abs(seconds))}"
    return "与上一日相同"


class UsageDatabase:
    """本地 SQLite 数据库，所有统计数据都保存在用户电脑上。"""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir or default_data_dir())
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "screen_time.db"
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_usage (
                day TEXT NOT NULL,
                app_key TEXT NOT NULL,
                app_name TEXT NOT NULL,
                icon_path TEXT NOT NULL DEFAULT '',
                seconds INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (day, app_key)
            );

            CREATE TABLE IF NOT EXISTS timeline_usage (
                day TEXT NOT NULL,
                bucket_minute INTEGER NOT NULL,
                seconds INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, bucket_minute)
            );

            CREATE INDEX IF NOT EXISTS idx_app_usage_day
                ON app_usage(day);
            CREATE INDEX IF NOT EXISTS idx_timeline_usage_day
                ON timeline_usage(day);
            """
        )
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(app_usage)")
        }
        if "icon_path" not in columns:
            self.connection.execute(
                "ALTER TABLE app_usage ADD COLUMN icon_path TEXT NOT NULL DEFAULT ''"
            )
        self._repair_legacy_website_records()
        self._upgrade_legacy_app_names()
        self.connection.commit()

    def _repair_legacy_website_records(self) -> None:
        """\u4fee\u590d\u5386\u53f2\u6570\u636e\u5e93\u4e2d\u7f51\u7ad9\u8bb0\u5f55\uff0c\u5c06 msedge \u540d\u79f0\u8fd8\u539f\u4e3a\u771f\u5b9e\u7f51\u7ad9\u540d\u79f0\uff0c\u5e76\u8bbe\u7f6e favicon \u56fe\u6807\u8def\u5f84\u3002"""
        rows = list(self.connection.execute(
            """
            SELECT day, app_key, app_name, icon_path, seconds, updated_at
            FROM app_usage
            WHERE app_key LIKE 'website:%' OR app_key LIKE 'site:%' OR app_name = 'msedge'
            """
        ))
        if not rows:
            return

        def parse_legacy_website(key: str, cur_name: str) -> tuple[str, str, str]:
            domain = key.removeprefix("website:")
            if key.startswith("website:") and re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:\:[0-9]+)?", domain):
                return key, cur_name, f"favicon:{domain}"
            k = key.lower()
            if "b\u7ad9" in k or "bilibili" in k or "bv" in k:
                return "website:bilibili.com", "\u54d4\u54e9\u54d4\u54e9", "favicon:bilibili.com"
            if "github" in k or "repository" in k or "4mig0o" in k or "focus-time-tracker" in k:
                return "website:github.com", "GitHub", "favicon:github.com"
            if "chatgpt" in k or "openai" in k:
                return "website:chatgpt.com", "ChatGPT", "favicon:chatgpt.com"
            if "render" in k:
                return "website:render.com", "Render", "favicon:render.com"
            if "blast" in k:
                return "website:blast.tv", "Blast \u8d5b\u4e8b", "favicon:blast.tv"
            if "zlibrary" in k or "zlib" in k or "\u9ad8\u7b49\u4ee3\u6570" in k or "\u6570\u5b66\u5206\u6790" in k:
                return "website:z-lib.id", "Z-Library", "favicon:z-lib.id"
            if "rufus" in k:
                return "website:rufus.ie", "Rufus \u5b98\u7f51", "favicon:rufus.ie"
            if "\u7f8e\u98df\u5730\u56fe" in k:
                return "website:127.0.0.1:4173", "\u7f8e\u98df\u5730\u56fe", "favicon:127.0.0.1:4173"
            if "siliconflow" in k:
                return "website:siliconflow.cn", "\u7845\u57fa\u6d41\u52a8", "favicon:siliconflow.cn"
            if "bing" in k:
                return "website:bing.com", "\u5fae\u8f6f\u5fc5\u5e94", "favicon:bing.com"
            if "msn" in k:
                return "website:msn.cn", "MSN \u8d44\u8baf", "favicon:msn.cn"
            if "\u65b0\u6807\u7b7e\u9875" in k or "\u65b0\u9009\u9879\u5361" in k:
                return "browser:newtab", "\u6d4f\u89c8\u5668\u8d77\u59cb\u9875", "msedge.exe"
            if "259" in k or "\u4e1d\u53e3" in k or "taobao" in k:
                return "website:taobao.com", "\u6dd8\u5b9d\u7f51", "favicon:taobao.com"
            if "scau" in k or "\u8bfe\u8868" in k or "\u6559\u52a1" in k:
                return "website:scau.edu.cn", "\u534e\u5357\u519c\u4e1a\u5927\u5b66", "favicon:scau.edu.cn"
            if "windows-10" in k or "mediacreation" in k or "win10" in k or "win11" in k:
                return "website:microsoft.com", "\u5fae\u8f6f\u5b98\u7f51", "favicon:microsoft.com"
            if "douyin" in k:
                return "website:douyin.com", "\u6296\u97f3", "favicon:douyin.com"
            if "zhihu" in k:
                return "website:zhihu.com", "\u77e5\u4e4e", "favicon:zhihu.com"
            if "baidu" in k:
                return "website:baidu.com", "\u767e\u5ea6", "favicon:baidu.com"
            if "weibo" in k:
                return "website:weibo.com", "\u5fae\u535a", "favicon:weibo.com"
            if "csdn" in k:
                return "website:csdn.net", "CSDN", "favicon:csdn.net"
            if "v2ex" in k:
                return "website:v2ex.com", "V2EX", "favicon:v2ex.com"
            if "gitee" in k:
                return "website:gitee.com", "Gitee", "favicon:gitee.com"
            if "steam" in k:
                return "website:steampowered.com", "Steam \u793e\u533a", "favicon:steampowered.com"

            clean = key[8:] if key.startswith("website:") else key
            clean = clean.split("-\u548c\u53e6\u5916")[0].split("-2-")[0].split("-1-")[0].strip("-")
            if not clean or clean == "msedge":
                clean = "\u7f51\u9875\u6d4f\u89c8"
            domain = clean[:30]
            name = clean[:24]
            return f"website:{domain}", name, f"favicon:{domain}"

        with self.connection:
            for r in rows:
                self.connection.execute(
                    "DELETE FROM app_usage WHERE day = ? AND app_key = ?",
                    (r["day"], r["app_key"]),
                )
            for r in rows:
                new_key, new_name, new_icon = parse_legacy_website(r["app_key"], r["app_name"])
                self.connection.execute(
                    """
                    INSERT INTO app_usage (day, app_key, app_name, icon_path, seconds, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(day, app_key) DO UPDATE SET
                        app_name = excluded.app_name,
                        icon_path = excluded.icon_path,
                        seconds = app_usage.seconds + excluded.seconds,
                        updated_at = excluded.updated_at
                    """,
                    (r["day"], new_key, new_name, new_icon, r["seconds"], r["updated_at"]),
                )

    def _upgrade_legacy_app_names(self) -> None:
        rows = list(
            self.connection.execute(
                """
                SELECT day, app_key, app_name, icon_path, seconds
                FROM app_usage
                """
            )
        )
        legacy_rows: list[sqlite3.Row] = []
        for row in rows:
            app_key = str(row["app_key"])
            current_name = str(row["app_name"])
            icon_path = str(row["icon_path"] or "")
            if app_key.startswith("website:") or app_key.startswith("site:") or app_key.startswith("browser:"):
                continue
            replacement = APP_NAME_MAP.get(app_key)
            if not replacement and icon_path:
                replacement = Path(icon_path).stem
            if not replacement and app_key.startswith("window:"):
                replacement = f"窗口：{app_key[7:][:28]}"
            if (
                app_key.startswith("pid-")
                or current_name.startswith("进程 ")
                or app_key == "legacy:other-software"
            ):
                legacy_rows.append(row)
                continue
            if not replacement and current_name in {"未知应用", "系统组件"}:
                replacement = f"应用：{app_key[:28]}"
            if replacement and replacement != current_name:
                self.connection.execute(
                    """
                    UPDATE app_usage
                    SET app_name = ?
                    WHERE day = ? AND app_key = ?
                    """,
                    (replacement, row["day"], row["app_key"]),
                )

        for row in legacy_rows:
            canonical_key = LEGACY_UNKNOWN_APP_KEY
            existing = self.connection.execute(
                """
                SELECT seconds, icon_path
                FROM app_usage
                WHERE day = ? AND app_key = ?
                """,
                (row["day"], canonical_key),
            ).fetchone()
            if existing:
                self.connection.execute(
                    """
                    UPDATE app_usage
                    SET seconds = seconds + ?,
                        icon_path = CASE
                            WHEN icon_path = '' THEN ?
                            ELSE icon_path
                        END
                    WHERE day = ? AND app_key = ?
                    """,
                    (
                        row["seconds"],
                        row["icon_path"] or "",
                        row["day"],
                        canonical_key,
                    ),
                )
                self.connection.execute(
                    "DELETE FROM app_usage WHERE day = ? AND app_key = ?",
                    (row["day"], row["app_key"]),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE app_usage
                    SET app_key = ?, app_name = ?
                    WHERE day = ? AND app_key = ?
                    """,
                    (
                        canonical_key,
                        LEGACY_UNKNOWN_APP_NAME,
                        row["day"],
                        row["app_key"],
                    ),
                )

    def add_sample(
        self,
        sample_time: datetime,
        app_key: str,
        app_name: str,
        seconds: int,
        icon_path: str = "",
    ) -> None:
        if seconds <= 0 or app_key in IGNORED_APP_KEYS:
            return
        day = iso_day(sample_time)
        bucket = (
            (sample_time.hour * 60 + sample_time.minute)
            // TIMELINE_BUCKET_MINUTES
        ) * TIMELINE_BUCKET_MINUTES
        timestamp = sample_time.isoformat(timespec="seconds")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO app_usage(
                    day, app_key, app_name, icon_path, seconds, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(day, app_key) DO UPDATE SET
                    app_name = excluded.app_name,
                    icon_path = CASE
                        WHEN excluded.icon_path <> '' THEN excluded.icon_path
                        ELSE app_usage.icon_path
                    END,
                    seconds = app_usage.seconds + excluded.seconds,
                    updated_at = excluded.updated_at
                """,
                (day, app_key, app_name, icon_path, seconds, timestamp),
            )
            self.connection.execute(
                """
                INSERT INTO timeline_usage(day, bucket_minute, seconds)
                VALUES (?, ?, ?)
                ON CONFLICT(day, bucket_minute) DO UPDATE SET
                    seconds = timeline_usage.seconds + excluded.seconds
                """,
                (day, bucket, seconds),
            )

    def total_for_day(self, value: date | str) -> int:
        day = value if isinstance(value, str) else value.isoformat()
        row = self.connection.execute(
            """
            SELECT COALESCE(SUM(seconds), 0) AS total
            FROM app_usage
            WHERE day = ?
              AND app_key NOT IN (
                  '__ignored__',
                  'screentimetracker.exe',
                  'screen_time_tracker.py'
              )
            """,
            (day,),
        ).fetchone()
        return int(row["total"]) if row else 0

    def apps_for_day(self, value: date | str) -> list[sqlite3.Row]:
        day = value if isinstance(value, str) else value.isoformat()
        return list(
            self.connection.execute(
                """
                SELECT app_key, app_name, icon_path, seconds
                FROM app_usage
                WHERE day = ?
                  AND app_key NOT IN (
                      '__ignored__',
                      'screentimetracker.exe',
                      'screen_time_tracker.py'
                  )
                ORDER BY seconds DESC, app_name ASC
                """,
                (day,),
            )
        )

    def timeline_for_day(self, value: date | str) -> dict[int, int]:
        day = value if isinstance(value, str) else value.isoformat()
        rows = self.connection.execute(
            """
            SELECT bucket_minute, seconds
            FROM timeline_usage
            WHERE day = ?
            ORDER BY bucket_minute
            """,
            (day,),
        )
        return {int(row["bucket_minute"]): int(row["seconds"]) for row in rows}

    def totals_for_days(self, days: Iterable[date]) -> list[tuple[date, int]]:
        day_list = list(days)
        if not day_list:
            return []
        placeholders = ",".join("?" for _ in day_list)
        values = [item.isoformat() for item in day_list]
        rows = self.connection.execute(
            f"""
            SELECT day, COALESCE(SUM(seconds), 0) AS total
            FROM app_usage
            WHERE day IN ({placeholders})
              AND app_key NOT IN (
                  '__ignored__',
                  'screentimetracker.exe',
                  'screen_time_tracker.py'
              )
            GROUP BY day
            """,
            values,
        )
        totals = {row["day"]: int(row["total"]) for row in rows}
        return [(item, totals.get(item.isoformat(), 0)) for item in day_list]

    def apps_for_days(self, days: Iterable[date]) -> list[sqlite3.Row]:
        day_list = list(days)
        if not day_list:
            return []
        placeholders = ",".join("?" for _ in day_list)
        values = [item.isoformat() for item in day_list]
        return list(
            self.connection.execute(
                f"""
                SELECT
                    app_key,
                    MAX(app_name) AS app_name,
                    MAX(icon_path) AS icon_path,
                    SUM(seconds) AS seconds
                FROM app_usage
                WHERE day IN ({placeholders})
                  AND app_key NOT IN (
                      '__ignored__',
                      'screentimetracker.exe',
                      'screen_time_tracker.py'
                  )
                GROUP BY app_key
                ORDER BY seconds DESC, app_name ASC
                """,
                values,
            )
        )

    def close(self) -> None:
        self.connection.close()



DEFAULT_FAVICON_DOMAINS = {
    "bilibili.com": ("\u54d4\u54e9\u54d4\u54e9", "#FB7299", "B"),
    "b23.tv": ("\u54d4\u54e9\u54d4\u54e9", "#FB7299", "B"),
    "github.com": ("GitHub", "#24292F", "Git"),
    "chatgpt.com": ("ChatGPT", "#10A37F", "AI"),
    "openai.com": ("ChatGPT", "#10A37F", "AI"),
    "youtube.com": ("YouTube", "#FF0000", "\u25b6"),
    "zhihu.com": ("\u77e5\u4e4e", "#0084FF", "\u77e5"),
    "baidu.com": ("\u767e\u5ea6", "#2932E1", "\u767e"),
    "taobao.com": ("\u6dd8\u5b9d\u7f51", "#FF5000", "\u6dd8"),
    "jd.com": ("\u4eac\u4e1c", "#E1251B", "\u4eac"),
    "douyin.com": ("\u6296\u97f3", "#161823", "\u6296"),
    "xiaohongshu.com": ("\u5c0f\u7ea2\u4e66", "#FE2C55", "\u7ea2"),
    "weibo.com": ("\u5fae\u535a", "#E6162D", "\u5fae"),
    "render.com": ("Render", "#141824", "R"),
    "csdn.net": ("CSDN", "#FC5531", "C"),
    "v2ex.com": ("V2EX", "#1F1F1F", "V"),
    "gitee.com": ("Gitee", "#C71D23", "G"),
    "bing.com": ("\u5fae\u8f6f\u5fc5\u5e94", "#008373", "b"),
    "google.com": ("Google", "#4285F4", "G"),
    "steampowered.com": ("Steam \u793e\u533a", "#171A21", "\u2668"),
    "steamcommunity.com": ("Steam \u793e\u533a", "#171A21", "\u2668"),
    "notion.so": ("Notion", "#000000", "N"),
    "figma.com": ("Figma", "#F24E1E", "F"),
    "stackoverflow.com": ("Stack Overflow", "#F48024", "SO"),
    "siliconflow.cn": ("\u7845\u57fa\u6d41\u52a8", "#2563EB", "Si"),
    "rufus.ie": ("Rufus \u5b98\u7f51", "#0078D7", "R"),
    "z-lib.id": ("Z-Library", "#00A389", "Z"),
    "scau.edu.cn": ("\u534e\u5357\u519c\u4e1a\u5927\u5b66", "#006633", "\u519c"),
    "127.0.0.1": ("\u672c\u5730\u7f51\u9875", "#4F46E5", "</>"),
    "localhost": ("\u672c\u5730\u7f51\u9875", "#4F46E5", "</>"),
    "msn.com": ("MSN \u8d44\u8baf", "#0078D7", "M"),
    "msn.cn": ("MSN \u8d44\u8baf", "#0078D7", "M"),
    "qq.com": ("\u817e\u8baf\u7f51", "#12B7F5", "\u4f01"),
    "microsoft.com": ("\u5fae\u8f6f\u5b98\u7f51", "#00A4EF", "\u7530"),
}

KNOWN_SITE_RULES = [
    (["bilibili", "\u54d4\u54e9\u54d4\u54e9", "b\u7ad9"], "bilibili.com", "\u54d4\u54e9\u54d4\u54e9"),
    (["github"], "github.com", "GitHub"),
    (["chatgpt", "openai"], "chatgpt.com", "ChatGPT"),
    (["youtube"], "youtube.com", "YouTube"),
    (["zhihu", "\u77e5\u4e4e"], "zhihu.com", "\u77e5\u4e4e"),
    (["baidu", "\u767e\u5ea6"], "baidu.com", "\u767e\u5ea6"),
    (["taobao", "\u6dd8\u5b9d"], "taobao.com", "\u6dd8\u5b9d\u7f51"),
    (["jd.com", "\u4eac\u4e1c"], "jd.com", "\u4eac\u4e1c"),
    (["douyin", "\u6296\u97f3"], "douyin.com", "\u6296\u97f3"),
    (["xiaohongshu", "\u5c0f\u7ea2\u4e66"], "xiaohongshu.com", "\u5c0f\u7ea2\u4e66"),
    (["weibo", "\u5fae\u535a"], "weibo.com", "\u5fae\u535a"),
    (["render"], "render.com", "Render"),
    (["csdn"], "csdn.net", "CSDN"),
    (["v2ex"], "v2ex.com", "V2EX"),
    (["gitee"], "gitee.com", "Gitee"),
    (["bing", "\u5fc5\u5e94"], "bing.com", "\u5fae\u8f6f\u5fc5\u5e94"),
    (["steamcommunity", "steampowered"], "steampowered.com", "Steam \u793e\u533a"),
    (["qq.com", "\u817e\u8baf\u7f51"], "qq.com", "\u817e\u8baf\u7f51"),
    (["notion"], "notion.so", "Notion"),
    (["figma"], "figma.com", "Figma"),
    (["stackoverflow"], "stackoverflow.com", "Stack Overflow"),
    (["google", "\u8c37\u6b4c"], "google.com", "Google"),
    (["siliconflow", "\u7845\u57fa\u6d41\u52a8"], "siliconflow.cn", "\u7845\u57fa\u6d41\u52a8"),
    (["rufus"], "rufus.ie", "Rufus \u5b98\u7f51"),
    (["z-lib", "zlibrary"], "z-lib.id", "Z-Library"),
    (["scau", "\u534e\u5357\u519c\u4e1a\u5927\u5b66"], "scau.edu.cn", "\u534e\u5357\u519c\u4e1a\u5927\u5b66"),
    (["127.0.0.1", "localhost"], "127.0.0.1", "\u672c\u5730\u7f51\u9875"),
    (["\u7f8e\u98df\u5730\u56fe"], "127.0.0.1:4173", "\u7f8e\u98df\u5730\u56fe"),
]


class FaviconManager:
    """\u7ba1\u7406\u5e76\u63d0\u53d6\u7f51\u7ad9\u56fe\u6807\uff08Favicon\uff09\u3002
    \u4f18\u5148\u4ece Edge / Chrome \u672c\u5730 Favicons \u6570\u636e\u5e93\u63d0\u53d6\u7528\u6237\u5b9e\u9645\u8bbf\u95ee\u7f51\u7ad9\u7684\u9ad8\u6e05\u56fe\u6807\uff1b
    \u5185\u7f6e\u5e38\u89c1\u70ed\u95e8\u7ad9\u70b9\u9ad8\u6e05\u56fe\u6807\u515c\u5e95\uff1b
    \u652f\u6301\u540e\u53f0\u5f02\u6b65\u6293\u53d6\u65b0\u7f51\u7ad9 favicon \u5e76\u6301\u4e45\u5316\u7f13\u5b58\u5230\u672c\u5730\u3002
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = Path(cache_dir or (default_data_dir() / "favicons"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: dict[tuple[str, int], object] = {}
        self._fetching: set[str] = set()
        self._browser_extracted: bool = False

    def _find_browser_favicon(self, domain: str):
        if Image is None:
            return None
        fav_bases = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data",
        ]
        cand_out = self.cache_dir / f"{domain}.png"
        for base in fav_bases:
            if not base.exists():
                continue
            for ff in base.glob("**/Favicons"):
                if not ff.is_file():
                    continue
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                    tmp_name = tmp.name
                try:
                    shutil.copy2(ff, tmp_name)
                    conn = sqlite3.connect(f"file:{tmp_name}?mode=ro", uri=True)
                    row = conn.execute("""
                        SELECT fb.image_data
                        FROM icon_mapping im
                        JOIN favicon_bitmaps fb ON im.icon_id = fb.icon_id
                        WHERE im.page_url LIKE ? AND fb.image_data IS NOT NULL
                        ORDER BY fb.width DESC LIMIT 1
                    """, (f"%{domain}%",)).fetchone()
                    if row and row[0]:
                        img = Image.open(io.BytesIO(row[0]))
                        img.save(cand_out, "PNG")
                        conn.close()
                        return img
                    conn.close()
                except Exception:
                    pass
                finally:
                    try:
                        os.remove(tmp_name)
                    except OSError:
                        pass
        return None

    def _fetch_remote_favicon(self, domain: str) -> None:
        if domain in self._fetching or "." not in domain or "localhost" in domain or "127.0.0.1" in domain:
            return
        self._fetching.add(domain)

        def _worker():
            try:
                cand_out = self.cache_dir / f"{domain}.png"
                if cand_out.exists():
                    return
                url = f"https://icons.duckduckgo.com/ip3/{domain}.ico"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.status == 200:
                        data = resp.read()
                        img = Image.open(io.BytesIO(data))
                        img.save(cand_out, "PNG")
            except Exception:
                pass
            finally:
                self._fetching.discard(domain)

        threading.Thread(target=_worker, daemon=True).start()

    def get_fallback_brand_icon(self, label: str, size: int = 34):
        if Image is None:
            return None
        scale = 2
        canvas_size = size * scale
        img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r = int(canvas_size * 0.25)
        color = "#4C6FFF"
        text = label[:2].upper() if label else "W"
        for d, (name, bg, t) in DEFAULT_FAVICON_DOMAINS.items():
            if d in label.lower() or name in label:
                color = bg
                text = t
                break
        draw.rounded_rectangle((0, 0, canvas_size - 1, canvas_size - 1), radius=r, fill=color)
        font = None
        for fn in ("msyh.ttc", "simhei.ttf", "arial.ttf"):
            try:
                font = ImageFont.truetype(fn, int(canvas_size * 0.45))
                break
            except Exception:
                pass
        if not font:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = (canvas_size - w) / 2 - bbox[0]
        y = (canvas_size - h) / 2 - bbox[1]
        draw.text((x, y), text, font=font, fill="#FFFFFF")
        return img.resize((size, size), Image.Resampling.LANCZOS)

    def get_icon_image(self, domain: str, size: int = 34):
        if Image is None:
            return None
        clean_domain = domain.lower().strip()
        cache_key = (clean_domain, size)
        if cache_key in self._memory_cache:
            res = self._memory_cache[cache_key]
            if res is not None:
                return res

        # 1. \u68c0\u67e5\u672c\u5730\u78c1\u76d8\u7f13\u5b58
        disk_file = self.cache_dir / f"{clean_domain}.png"
        if not disk_file.is_file():
            parts = clean_domain.split(".")
            if len(parts) > 2:
                root_file = self.cache_dir / f"{parts[-2]}.{parts[-1]}.png"
                if root_file.is_file():
                    disk_file = root_file

        if disk_file.is_file():
            try:
                img = Image.open(disk_file).convert("RGBA")
                resized = img.resize((size, size), Image.Resampling.LANCZOS)
                self._memory_cache[cache_key] = resized
                return resized
            except Exception:
                pass

        # 2. \u4ece\u6d4f\u89c8\u5668 Favicons \u6570\u636e\u5e93\u63d0\u53d6
        img = self._find_browser_favicon(clean_domain)
        if img is not None:
            try:
                img = img.convert("RGBA")
                resized = img.resize((size, size), Image.Resampling.LANCZOS)
                self._memory_cache[cache_key] = resized
                return resized
            except Exception:
                pass

        # 3. \u5e38\u7528\u77e5\u540d\u7ad9\u70b9\u9ad8\u6e05\u5185\u7f6e\u54c1\u724c\u56fe\u6807
        for d, (name, bg, t) in DEFAULT_FAVICON_DOMAINS.items():
            if d in clean_domain or clean_domain in d:
                fb = self.get_fallback_brand_icon(d, size)
                self._memory_cache[cache_key] = fb
                self._fetch_remote_favicon(clean_domain)
                return fb

        # 4. \u540e\u53f0\u5f02\u6b65\u7f51\u7edc\u8865\u5145\u5e76\u8fd4\u56de\u9ad8\u6e05\u901a\u7528\u5fbd\u7ae0
        self._fetch_remote_favicon(clean_domain)
        fallback = self.get_fallback_brand_icon(clean_domain, size)
        self._memory_cache[cache_key] = fallback
        return fallback


class WebsiteResolver:
    """\u4ece\u524d\u53f0\u6d4f\u89c8\u5668\u7a97\u53e3\u4e2d\u7cbe\u786e\u8bc6\u522b\u7f51\u7ad9\u57df\u540d\u548c\u53cb\u597d\u4e2d\u6587\u540d\u79f0\u3002
    \u7ed3\u5408\u7a97\u53e3\u6807\u9898\u6e05\u6d17\u3001Chrome/Edge \u672c\u5730\u5386\u53f2\u5e93\u6781\u901f\u5339\u914d\u4e0e\u5173\u952e\u8bcd\u89c4\u5219\u3002
    """

    def __init__(self) -> None:
        self._zero_width = set("\u200b\u200c\u200d\ufeff")
        self._last_db_query: float = 0.0

    def clean_title(self, title: str) -> str:
        t = "".join(ch for ch in (title or "") if ch not in self._zero_width).strip()
        t = re.sub(r"\s*(?:\u548c\u53e6\u5916|\u4ee5\u53ca\u53e6\u5916|and)\s*\d+\s*(?:\u4e2a?\u9875\u9762|\u4e2a?\u6807\u7b7e\u9875|other tabs|more pages).*?$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*\u548c\u53e6\u5916\s*$", "", t)
        t = re.sub(r"\s*[-—–|·]\s*(?:\u4e2a\u4eba\s*)?(?:\u8c37\u6b4c ?chrome|google chrome|microsoft ?edge|edge|chrome|firefox|mozilla firefox|brave|opera|vivaldi|360\u5b89\u5168\u6d4f\u89c8\u5668|360\u6781\u901f\u6d4f\u89c8\u5668)\s*$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*[-—–|·]\s*(?:\u4e2a\u4eba|\u5de5\u4f5c|Default|Profile \d+)\s*$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*[-—–|·]\s*(?:\u8c37\u6b4c ?chrome|google chrome|microsoft ?edge|edge|chrome|firefox|mozilla firefox|brave|opera|vivaldi|360\u5b89\u5168\u6d4f\u89c8\u5668|360\u6781\u901f\u6d4f\u89c8\u5668)\s*$", "", t, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", t).strip(" -—–|·")

    def _query_history_domain(self, cleaned_title: str) -> tuple[str, str] | None:
        if not cleaned_title:
            return None
        now = time.monotonic()
        if now - self._last_db_query < 1.5:
            return None
        self._last_db_query = now

        fav_bases = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data",
        ]
        want = cleaned_title.lower()
        for base in fav_bases:
            if not base.exists():
                continue
            for h in base.glob("**/History"):
                if not h.is_file():
                    continue
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                    tmp_name = tmp.name
                try:
                    shutil.copy2(h, tmp_name)
                    conn = sqlite3.connect(f"file:{tmp_name}?mode=ro", uri=True)
                    rows = conn.execute("""
                        SELECT u.url, u.title
                        FROM urls u
                        JOIN visits v ON v.url = u.id
                        ORDER BY v.visit_time DESC LIMIT 150
                    """).fetchall()
                    conn.close()
                    for url, page_title in rows:
                        if not url or url.startswith(("chrome://", "edge://", "about:", "view-source:")):
                            continue
                        p_clean = self.clean_title(page_title or "").lower()
                        if p_clean and (p_clean == want or want in p_clean or p_clean in want):
                            host = urlparse(url).netloc.lower()
                            if host.startswith("www."):
                                host = host[4:]
                            if host:
                                return host, (page_title or cleaned_title)
                except Exception:
                    pass
                finally:
                    try:
                        os.remove(tmp_name)
                    except OSError:
                        pass
        return None

    def resolve_site(self, executable: str, raw_title: str, executable_path: str = "") -> tuple[str, str, str]:
        if executable not in BROWSER_PROCESS_NAMES:
            return "", "", ""
        cleaned = self.clean_title(raw_title)
        if not cleaned or cleaned in {"\u65b0\u6807\u7b7e\u9875", "\u65b0\u9009\u9879\u5361", "New Tab", "\u65b0\u6807\u7b7e", "Start Page", "\u5de5\u4f5c", "\u4e2a\u4eba"}:
            return "browser:newtab", "\u6d4f\u89c8\u5668\u8d77\u59cb\u9875", executable_path

        domain = ""
        site_name = ""

        # 1. \u5c1d\u8bd5\u4ece\u6d4f\u89c8\u5668\u5386\u53f2\u5e93\u5339\u914d\u771f\u5b9e\u8bbf\u95ee URL
        hist = self._query_history_domain(cleaned)
        if hist:
            domain, page_title = hist

        # 2. \u5173\u952e\u8bcd\u4e0e\u89c4\u5219\u515c\u5e95
        if not domain:
            lower_title = cleaned.lower()
            for keywords, d, name in KNOWN_SITE_RULES:
                if any(kw in lower_title for kw in keywords):
                    domain = d
                    site_name = name
                    break

        # 3. \u57df\u540d\u6b63\u5219\u515c\u5e95
        if not domain:
            m = re.search(r"([a-zA-Z0-9][-a-zA-Z0-9]*\.(?:com|cn|org|net|edu|gov|top|cc|me|io|so|dev|ai|xyz|app|tv|co)(?:\.[a-zA-Z]{2})?)", cleaned)
            if m:
                domain = m.group(1).lower()

        # 4. \u4ecd\u65e0\u6cd5\u8bc6\u522b\u57df\u540d\u65f6\uff0c\u4f7f\u7528\u6e05\u6670\u7684\u9875\u9762\u540d\u79f0
        if not domain:
            slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", cleaned).strip("-").lower()[:40] or "webpage"
            domain = slug
            site_name = cleaned[:24]

        if not site_name:
            for d, (name, _color, _t) in DEFAULT_FAVICON_DOMAINS.items():
                if d in domain or domain in d:
                    site_name = name
                    break
            if not site_name:
                parts = domain.split(".")
                if len(parts) >= 2 and parts[-2] not in ("com", "edu", "gov", "org", "net"):
                    site_name = parts[-2].capitalize()
                else:
                    site_name = domain

        res = (f"website:{domain}", site_name, f"favicon:{domain}")
        return res


class WindowsActivity:
    """使用 Windows API 获取前台程序和最近输入时间。"""

    def __init__(self) -> None:
        self.available = os.name == "nt"
        self.user32 = None
        self.kernel32 = None
        self.psapi = None
        self.current_pid = os.getpid()
        self.current_executable = Path(sys.executable).name.lower()
        self.website_resolver = WebsiteResolver()
        if not self.available:
            return

        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.psapi = ctypes.windll.psapi
        self.version = ctypes.windll.version
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetLastInputInfo.argtypes = [ctypes.c_void_p]
        self.user32.GetLastInputInfo.restype = wintypes.BOOL
        self.user32.OpenInputDesktop.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.user32.OpenInputDesktop.restype = wintypes.HANDLE
        self.user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        self.user32.CloseDesktop.restype = wintypes.BOOL
        self.kernel32.GetTickCount.restype = wintypes.DWORD
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.psapi.GetModuleFileNameExW.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        self.psapi.GetModuleFileNameExW.restype = wintypes.DWORD
        self.version.GetFileVersionInfoSizeW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        self.version.GetFileVersionInfoW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self.version.GetFileVersionInfoW.restype = wintypes.BOOL
        self.version.VerQueryValueW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.UINT),
        ]
        self.version.VerQueryValueW.restype = wintypes.BOOL

    def idle_seconds(self) -> int:
        if not self.available:
            return 0

        class LastInputInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("dwTime", wintypes.DWORD),
            ]

        info = LastInputInfo()
        info.cbSize = ctypes.sizeof(LastInputInfo)
        if not self.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0
        tick_count = int(self.kernel32.GetTickCount())
        return max(0, (tick_count - int(info.dwTime)) // 1000)

    def is_workstation_locked(self) -> bool:
        """只在 Windows 桌面确实不可访问时暂停，不把无操作误判成未使用。"""
        if not self.available:
            return False
        desktop_switch = 0x0100
        desktop = self.user32.OpenInputDesktop(0, False, desktop_switch)
        if desktop:
            self.user32.CloseDesktop(desktop)
            return False
        # 无法确定锁屏状态时继续计时，避免少记屏幕使用时长。
        return False

    def window_title(self, hwnd: wintypes.HWND) -> str:
        if not self.available or not hwnd:
            return ""
        buffer = ctypes.create_unicode_buffer(2048)
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value.replace("\u200b", "").strip()

    @staticmethod
    def clean_browser_title(title: str) -> str:
        cleaned = title.replace("\u200b", "").strip()
        cleaned = re.sub(
            r"\s*[-|]\s*(?:Google Chrome|Microsoft Edge|Mozilla Firefox|"
            r"Brave|Opera|Vivaldi|Arc)\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\s*[-|]\s*(?:个人|Person)\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned.strip(" -|")

    @classmethod
    def website_identity(
        cls,
        executable: str,
        title: str,
    ) -> tuple[str, str] | None:
        if executable not in BROWSER_PROCESS_NAMES:
            return None

        normalized = title.casefold()
        for keyword, label in WEBSITE_TITLE_RULES:
            if keyword.casefold() in normalized:
                return f"website:{label}", f"{label}（网页）"

        cleaned = cls.clean_browser_title(title)
        if not cleaned or cleaned in {"新标签页", "新选项卡", "New Tab"}:
            return None
        slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", cleaned)
        slug = slug.strip("-").lower()[:80] or executable
        return f"website:{slug}", f"网页：{cleaned[:28]}"

    @classmethod
    def fallback_window_identity(
        cls,
        title: str,
        process_id: int,
    ) -> tuple[str, str]:
        normalized = title.casefold()
        for keyword, label in WEBSITE_TITLE_RULES:
            if keyword.casefold() in normalized:
                return f"website:{label}", f"{label}（网页）"

        cleaned = cls.clean_browser_title(title)
        if cleaned:
            slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", cleaned)
            slug = slug.strip("-").lower()[:80] or f"window-{process_id}"
            return f"window:{slug}", f"窗口：{cleaned[:28]}"
        return "system:unknown", "Windows 系统组件"

    def query_process_path(self, process_id: int) -> str:
        if not self.available or not process_id:
            return ""

        access_modes = (
            0x1000,
            0x0400 | 0x0010,
            0x0400,
        )
        for access in access_modes:
            handle = self.kernel32.OpenProcess(access, False, process_id)
            if not handle:
                continue
            try:
                buffer = ctypes.create_unicode_buffer(2048)
                length = wintypes.DWORD(len(buffer))
                success = self.kernel32.QueryFullProcessImageNameW(
                    handle,
                    0,
                    buffer,
                    ctypes.byref(length),
                )
                if success and buffer.value:
                    return buffer.value

                module_length = self.psapi.GetModuleFileNameExW(
                    handle,
                    None,
                    buffer,
                    len(buffer),
                )
                if module_length and buffer.value:
                    return buffer.value
            finally:
                self.kernel32.CloseHandle(handle)
        return ""

    def file_version_name(self, executable_path: str) -> str:
        if not self.available or not executable_path or not Path(executable_path).is_file():
            return ""
        handle = wintypes.DWORD()
        size = self.version.GetFileVersionInfoSizeW(
            executable_path,
            ctypes.byref(handle),
        )
        if not size:
            return ""
        data = ctypes.create_string_buffer(size)
        if not self.version.GetFileVersionInfoW(
            executable_path,
            0,
            size,
            data,
        ):
            return ""

        translation_pointer = ctypes.c_void_p()
        translation_length = wintypes.UINT()
        translations = []
        if self.version.VerQueryValueW(
            data,
            r"\VarFileInfo\Translation",
            ctypes.byref(translation_pointer),
            ctypes.byref(translation_length),
        ):
            word_count = int(translation_length.value) // ctypes.sizeof(wintypes.WORD)
            if word_count:
                translation_array = (wintypes.WORD * word_count).from_address(
                    translation_pointer.value
                )
                for index in range(0, word_count - 1, 2):
                    translations.append(
                        f"{translation_array[index]:04x}{translation_array[index + 1]:04x}"
                    )
        translations.extend(["040904b0", "080404b0"])

        for translation in dict.fromkeys(translations):
            for field in ("FileDescription", "ProductName"):
                value_pointer = ctypes.c_void_p()
                value_length = wintypes.UINT()
                sub_block = rf"\StringFileInfo\{translation}\{field}"
                if self.version.VerQueryValueW(
                    data,
                    sub_block,
                    ctypes.byref(value_pointer),
                    ctypes.byref(value_length),
                ) and value_pointer.value:
                    value = ctypes.wstring_at(value_pointer.value)
                    if value and value.strip():
                        return value.strip()
        return ""

    def active_app_details(self) -> tuple[str, str, str]:
        if not self.available:
            return "unknown", "未知应用", ""

        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return "unknown", "未知应用", ""

        title = self.window_title(hwnd)
        process_id = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return "unknown", "未知应用", ""

        executable_path = self.query_process_path(process_id.value)
        if not executable_path:
            key, name = self.fallback_window_identity(title, process_id.value)
            return key, name, ""

        executable = Path(executable_path).name
        key = executable.lower() or "system:unknown"
        if process_id.value == self.current_pid or key in IGNORED_APP_KEYS:
            return "__ignored__", "统计程序（不计入）", executable_path

        if key in BROWSER_PROCESS_NAMES:
            site_info = self.website_resolver.resolve_site(key, title, executable_path)
            if site_info and site_info[0]:
                return site_info[0], site_info[1], site_info[2]

        display_name = APP_NAME_MAP.get(key)
        if not display_name:
            title_name = title.replace("\u200b", "").strip()
            generic_titles = {
                "",
                "Program Manager",
                "Default IME",
                "MSCTFIME UI",
            }
            if title_name not in generic_titles and len(title_name) <= 80:
                display_name = title_name
            else:
                display_name = self.file_version_name(executable_path)
                if not display_name:
                    display_name = Path(executable).stem.replace("_", " ").strip()
            display_name = display_name or "Windows 系统组件"
        return key, display_name, executable_path

    def active_app(self) -> tuple[str, str]:
        key, name, _icon_path = self.active_app_details()
        return key, name


class NativeIconManager:
    """读取 Windows 程序自身的图标，并转换成 Tk 可显示的高清图片。"""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.available = os.name == "nt" and Image is not None and ImageTk is not None
        self.favicon_manager = FaviconManager(cache_dir)
        self.cache: dict[tuple[str, str, str], object] = {}
        if not self.available:
            self.shell32 = None
            self.user32 = None
            self.gdi32 = None
            return

        self.shell32 = ctypes.windll.shell32
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        self.shell32.ExtractIconExW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.INT,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.UINT,
        ]
        self.shell32.ExtractIconExW.restype = wintypes.UINT
        self.user32.DrawIconEx.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.HBRUSH,
            wintypes.UINT,
        ]
        self.user32.DrawIconEx.restype = wintypes.BOOL
        self.user32.DestroyIcon.argtypes = [ctypes.c_void_p]
        self.user32.DestroyIcon.restype = wintypes.BOOL
        self.gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi32.DeleteDC.restype = wintypes.BOOL
        self.gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self.gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self.gdi32.SelectObject.restype = wintypes.HGDIOBJ
        self.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.gdi32.DeleteObject.restype = wintypes.BOOL

    def _candidate_paths(self, app_key: str) -> list[Path]:
        key = app_key.lower()
        if key.startswith("website:"):
            key = "msedge.exe"

        program_files = os.environ.get("ProgramFiles", "")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "")
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        candidates: list[Path] = []
        roots = [
            Path(program_files) if program_files else None,
            Path(program_files_x86) if program_files_x86 else None,
            Path(local_app_data) if local_app_data else None,
        ]
        relative_paths = {
            "chrome.exe": [
                "Google/Chrome/Application/chrome.exe",
            ],
            "msedge.exe": [
                "Microsoft/Edge/Application/msedge.exe",
            ],
            "firefox.exe": [
                "Mozilla Firefox/firefox.exe",
            ],
            "brave.exe": [
                "BraveSoftware/Brave-Browser/Application/brave.exe",
            ],
            "code.exe": [
                "Programs/Microsoft VS Code/Code.exe",
                "Microsoft VS Code/Code.exe",
            ],
            "wechat.exe": [
                "Tencent/WeChat/WeChat.exe",
            ],
            "winword.exe": [
                "Microsoft Office/root/Office16/WINWORD.EXE",
            ],
            "excel.exe": [
                "Microsoft Office/root/Office16/EXCEL.EXE",
            ],
            "powerpnt.exe": [
                "Microsoft Office/root/Office16/POWERPNT.EXE",
            ],
        }
        for root in roots:
            if root is None:
                continue
            candidates.extend(root / relative for relative in relative_paths.get(key, []))
        which_path = shutil.which(key)
        if which_path:
            candidates.append(Path(which_path))
        return candidates

    def resolve_icon_path(self, app_key: str, icon_path: str = "") -> str:
        if icon_path and Path(icon_path).is_file():
            return icon_path
        for candidate in self._candidate_paths(app_key):
            if candidate.is_file():
                return str(candidate)
        return ""

    def _render_icon(self, executable_path: str, size: int = 64):
        if not self.available or not executable_path:
            return None

        large_icon = ctypes.c_void_p()
        small_icon = ctypes.c_void_p()
        count = self.shell32.ExtractIconExW(
            executable_path,
            0,
            ctypes.byref(large_icon),
            ctypes.byref(small_icon),
            1,
        )
        if count == 0:
            return None
        icon = large_icon.value or small_icon.value
        if not icon:
            return None

        class BitmapInfoHeader(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BitmapInfo(ctypes.Structure):
            _fields_ = [
                ("bmiHeader", BitmapInfoHeader),
                ("bmiColors", wintypes.DWORD * 3),
            ]

        bitmap_info = BitmapInfo()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        bitmap_info.bmiHeader.biWidth = size
        bitmap_info.bmiHeader.biHeight = -size
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = 0

        device_context = self.gdi32.CreateCompatibleDC(None)
        pixel_buffer = ctypes.c_void_p()
        bitmap = self.gdi32.CreateDIBSection(
            device_context,
            ctypes.byref(bitmap_info),
            0,
            ctypes.byref(pixel_buffer),
            None,
            0,
        )
        if not device_context or not bitmap or not pixel_buffer:
            if bitmap:
                self.gdi32.DeleteObject(bitmap)
            if device_context:
                self.gdi32.DeleteDC(device_context)
            if large_icon.value:
                self.user32.DestroyIcon(large_icon)
            if small_icon.value:
                self.user32.DestroyIcon(small_icon)
            return None

        previous_object = self.gdi32.SelectObject(device_context, bitmap)
        try:
            ctypes.memset(pixel_buffer, 0, size * size * 4)
            drawn = self.user32.DrawIconEx(
                device_context,
                0,
                0,
                icon,
                size,
                size,
                0,
                None,
                0x0003,
            )
            if not drawn:
                return None
            raw = ctypes.string_at(pixel_buffer, size * size * 4)
            return Image.frombuffer(
                "RGBA",
                (size, size),
                raw,
                "raw",
                "BGRA",
                0,
                1,
            ).copy()
        finally:
            self.gdi32.SelectObject(device_context, previous_object)
            self.gdi32.DeleteObject(bitmap)
            self.gdi32.DeleteDC(device_context)
            if large_icon.value:
                self.user32.DestroyIcon(large_icon)
            if small_icon.value:
                self.user32.DestroyIcon(small_icon)

    def get_photo(
        self,
        app_key: str,
        app_name: str,
        icon_path: str = "",
        size: int = 34,
    ):
        cache_key = (app_key, icon_path, str(size))
        if cache_key in self.cache:
            return self.cache[cache_key]
        resolved_path = self.resolve_icon_path(app_key, icon_path)
        image = self._render_icon(resolved_path)
        if image is None:
            self.cache[cache_key] = None
            return None
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image)
        self.cache[cache_key] = photo
        return photo


class CollectorMutex:
    def __init__(self) -> None:
        self.handle = None
        self.kernel32 = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        self.kernel32 = ctypes.windll.kernel32
        self.kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.GetLastError.restype = wintypes.DWORD
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel32.CreateMutexW(
            None,
            False,
            COLLECTOR_MUTEX_NAME,
        )
        if not self.handle:
            return False
        if self.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None
            return False
        return True

    def close(self) -> None:
        if self.handle and self.kernel32:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


def collector_is_running() -> bool:
    if os.name != "nt":
        return False
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenMutexW.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    synchronize = 0x00100000
    handle = kernel32.OpenMutexW(
        synchronize,
        False,
        COLLECTOR_MUTEX_NAME,
    )
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def start_background_collector(data_dir: Path) -> None:
    if collector_is_running():
        return
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--collector"]
        working_directory = str(Path(sys.executable).parent)
    else:
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--collector",
        ]
        working_directory = str(Path(__file__).resolve().parent)
    command.extend(["--data-dir", str(data_dir)])
    try:
        subprocess.Popen(
            command,
            cwd=working_directory,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0,
            close_fds=True,
        )
    except OSError:
        # 界面仍可打开，后续 ensure_collector 会继续尝试启动后台采集器。
        return


def run_collector(data_dir: Path) -> None:
    mutex = CollectorMutex()
    if not mutex.acquire():
        return
    database = UsageDatabase(data_dir)
    activity = WindowsActivity()
    last_sample_at = datetime.now()
    try:
        while True:
            time.sleep(SAMPLE_SECONDS)
            now = datetime.now()
            elapsed = int((now - last_sample_at).total_seconds())
            last_sample_at = now
            elapsed = min(max(elapsed, 0), SAMPLE_SECONDS * 3)
            if activity.is_workstation_locked():
                continue
            app_key, app_name, icon_path = activity.active_app_details()
            if app_key in IGNORED_APP_KEYS or app_key == "__ignored__":
                continue
            database.add_sample(
                now,
                app_key,
                app_name,
                elapsed,
                icon_path=icon_path,
            )
    except KeyboardInterrupt:
        return
    finally:
        database.close()
        mutex.close()


class LegacyScreenTimeTrackerApp(tk.Tk):
    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        start_collector: bool = True,
    ) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x960")
        self.minsize(1080, 800)
        self.configure(bg=COLORS["background"])

        self.data_dir = Path(data_dir or default_data_dir())
        self.collector_enabled = start_collector
        self.database = UsageDatabase(self.data_dir)
        self.activity = WindowsActivity()
        self.icon_manager = NativeIconManager(self.data_dir / "favicons")
        self.icon_photo_cache: dict[str, object] = {}
        self.selected_day = date.today()
        self.week_selected_day = date.today()
        self.closing = False
        self.scroll_targets: list[tuple[tk.Canvas, tk.Misc]] = []

        self.day_total_var = tk.StringVar(value="不足 1 分钟")
        self.day_delta_var = tk.StringVar(value="正在计算")
        self.top_app_var = tk.StringVar(value="暂时没有记录")
        self.status_var = tk.StringVar(value="正在准备采集")
        self.status_dot_var = tk.StringVar(value="●")
        self.day_date_var = tk.StringVar(value=day_display(self.selected_day))
        self.week_total_var = tk.StringVar(value="不足 1 分钟")
        self.week_average_var = tk.StringVar(value="不足 1 分钟/天")
        self.week_highest_var = tk.StringVar(value="暂时没有记录")
        self.week_detail_var = tk.StringVar(value=day_display(self.week_selected_day))
        self.week_day_values: list[str] = []

        self.day_timeline_data: dict[int, int] = {}
        self.day_apps_data: list[sqlite3.Row] = []
        self.week_totals_data: list[tuple[date, int]] = []
        self.week_apps_data: list[sqlite3.Row] = []
        self.week_detail_apps_data: list[sqlite3.Row] = []

        self._setup_styles()
        self._build_layout()
        self._bind_resize_events()
        self.protocol("WM_DELETE_WINDOW", self.close)
        if self.collector_enabled:
            start_background_collector(self.data_dir)
        self.after(300, self.refresh_status)
        if self.collector_enabled:
            self.after(10000, self.ensure_collector)
        self.after_idle(self.refresh_all)
        self.after(900, self.refresh_all)

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "App.TNotebook",
            background=COLORS["background"],
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "App.TNotebook.Tab",
            padding=(24, 12),
            background=COLORS["background"],
            foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 11),
        )
        style.map(
            "App.TNotebook.Tab",
            background=[("selected", COLORS["surface"])],
            foreground=[("selected", COLORS["blue"])],
        )
        style.configure(
            "App.TButton",
            padding=(12, 7),
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "Primary.TButton",
            padding=(12, 7),
            foreground="white",
            background=COLORS["blue"],
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#2359B8"), ("pressed", "#2359B8")],
        )
        style.configure(
            "App.TCombobox",
            padding=6,
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "App.Vertical.TScrollbar",
            background=COLORS["surface_alt"],
            troughcolor=COLORS["background"],
            bordercolor=COLORS["background"],
            arrowcolor=COLORS["muted"],
            width=10,
        )
        style.configure(
            "App.Treeview",
            rowheight=32,
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "App.Treeview.Heading",
            font=("Microsoft YaHei UI", 10, "bold"),
        )

    def _build_layout(self) -> None:
        header = tk.Frame(
            self,
            bg=COLORS["surface"],
            height=88,
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        header.pack(fill="x")
        header.pack_propagate(False)

        title_block = tk.Frame(header, bg=COLORS["surface"])
        title_block.pack(side="left", padx=32, pady=16)
        tk.Label(
            title_block,
            text="屏幕使用时长",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_block,
            text="本地记录 · 看清每天的电脑使用节奏",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(3, 0))

        status_block = tk.Frame(
            header,
            bg=COLORS["surface_alt"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        status_block.pack(side="right", padx=32, pady=22)
        self.status_dot_label = tk.Label(
            status_block,
            textvariable=self.status_dot_var,
            bg=COLORS["surface_alt"],
            fg=COLORS["teal"],
            font=("Microsoft YaHei UI", 12),
        )
        self.status_dot_label.pack(side="left", padx=(12, 4), pady=7)
        tk.Label(
            status_block,
            textvariable=self.status_var,
            bg=COLORS["surface_alt"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(side="left", padx=(0, 12), pady=7)
        self._modern_button(
            status_block,
            "刷新",
            self.manual_refresh,
            primary=True,
        ).pack(side="left", padx=(0, 8), pady=6)

        content = tk.Frame(self, bg=COLORS["background"])
        content.pack(fill="both", expand=True, padx=32, pady=(20, 0))

        self.notebook = ttk.Notebook(content, style="App.TNotebook")
        self.notebook.pack(fill="both", expand=True)
        self.day_tab = tk.Frame(self.notebook, bg=COLORS["background"])
        self.week_tab = tk.Frame(self.notebook, bg=COLORS["background"])
        self.notebook.add(self.day_tab, text="日视图")
        self.notebook.add(self.week_tab, text="周视图")
        self.notebook.bind("<<NotebookTabChanged>>", lambda _event: self.refresh_all())
        self.day_page, self.day_scroll_canvas, self.day_scrollbar = (
            self._create_scrollable_page(self.day_tab)
        )
        self.week_page, self.week_scroll_canvas, self.week_scrollbar = (
            self._create_scrollable_page(self.week_tab)
        )

        self._build_day_tab()
        self._build_week_tab()

    def _create_scrollable_page(
        self,
        parent: tk.Misc,
    ) -> tuple[tk.Frame, tk.Canvas, ttk.Scrollbar]:
        viewport = tk.Frame(parent, bg=COLORS["background"])
        viewport.pack(fill="both", expand=True)
        viewport.grid_columnconfigure(0, weight=1)
        viewport.grid_rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            viewport,
            bg=COLORS["background"],
            highlightthickness=0,
            yscrollincrement=2,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            viewport,
            orient="vertical",
            command=canvas.yview,
            style="App.Vertical.TScrollbar",
        )
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        canvas.configure(yscrollcommand=scrollbar.set)

        page = tk.Frame(canvas, bg=COLORS["background"])
        window_id = canvas.create_window((0, 0), window=page, anchor="nw")

        def update_scrollregion(_event: object | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_page_width(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        page.bind("<Configure>", update_scrollregion)
        canvas.bind("<Configure>", fit_page_width)

        def on_mousewheel(event: tk.Event) -> str | None:
            widget = event.widget
            while widget is not None:
                if widget == page:
                    if getattr(event, "delta", 0):
                        canvas.yview_scroll(
                            -int(event.delta / 120),
                            "units",
                        )
                    return "break"
                widget = getattr(widget, "master", None)
            return None

        canvas.bind_all("<MouseWheel>", on_mousewheel, add="+")
        self.scroll_targets.append((canvas, page))
        return page, canvas, scrollbar

    def _card(
        self,
        parent: tk.Misc,
        title: str,
        value_var: tk.StringVar,
        accent: str,
    ) -> tk.Frame:
        card = tk.Frame(
            parent,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        left_line = tk.Frame(card, bg=accent, width=5)
        left_line.pack(side="left", fill="y")
        inner = tk.Frame(card, bg=COLORS["surface"])
        inner.pack(fill="both", expand=True, padx=18, pady=15)
        tk.Label(
            inner,
            text=title,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w")
        tk.Label(
            inner,
            textvariable=value_var,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 17, "bold"),
            wraplength=260,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))
        return card

    def _modern_button(
        self,
        parent: tk.Misc,
        text: str,
        command,
        *,
        primary: bool = False,
        compact: bool = False,
    ) -> tk.Button:
        normal_bg = COLORS["blue"] if primary else COLORS["surface"]
        hover_bg = "#3858E8" if primary else "#EEF2FF"
        normal_fg = "#FFFFFF" if primary else COLORS["text"]
        button = tk.Button(
            parent,
            text=text,
            command=command,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            bg=normal_bg,
            fg=normal_fg,
            activebackground=hover_bg,
            activeforeground=normal_fg,
            font=(
                "Microsoft YaHei UI",
                14 if compact else 10,
                "bold" if primary or compact else "normal",
            ),
            cursor="hand2",
            padx=9 if compact else 16,
            pady=5 if compact else 8,
        )
        if compact:
            button.configure(width=3)

        def on_enter(_event: object) -> None:
            button.configure(bg=hover_bg)

        def on_leave(_event: object) -> None:
            button.configure(bg=normal_bg)

        button.bind("<Enter>", on_enter)
        button.bind("<Leave>", on_leave)
        return button

    def _section(
        self,
        parent: tk.Misc,
        title: str,
        subtitle: str,
    ) -> tuple[tk.Frame, tk.Canvas]:
        frame = tk.Frame(
            parent,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        heading = tk.Frame(frame, bg=COLORS["surface"])
        heading.pack(fill="x", padx=20, pady=(16, 0))
        tk.Label(
            heading,
            text=title,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            heading,
            text=subtitle,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(3, 0))
        canvas = tk.Canvas(
            frame,
            bg=COLORS["surface"],
            highlightthickness=0,
            height=280,
        )
        canvas.pack(fill="both", expand=True, padx=12, pady=(10, 16))
        return frame, canvas

    def _split_app_section(
        self,
        parent: tk.Misc,
        title: str,
        subtitle: str,
    ) -> tuple[tk.Frame, tk.Canvas, tk.Canvas, ttk.Scrollbar]:
        frame = tk.Frame(
            parent,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        heading = tk.Frame(frame, bg=COLORS["surface"])
        heading.pack(fill="x", padx=20, pady=(16, 0))
        tk.Label(
            heading,
            text=title,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            heading,
            text=subtitle,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(3, 0))

        body = tk.Frame(frame, bg=COLORS["surface"])
        body.pack(fill="both", expand=True, padx=12, pady=(8, 14))
        body.grid_columnconfigure(0, weight=5, uniform="app-chart")
        body.grid_columnconfigure(1, weight=7, uniform="app-chart")
        body.grid_rowconfigure(0, weight=1)

        pie_canvas = tk.Canvas(
            body,
            bg=COLORS["surface"],
            highlightthickness=0,
            height=300,
        )
        pie_canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        bars_frame = tk.Frame(body, bg=COLORS["surface"])
        bars_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        bars_frame.grid_columnconfigure(0, weight=1)
        bars_frame.grid_rowconfigure(0, weight=1)
        bars_canvas = tk.Canvas(
            bars_frame,
            bg=COLORS["surface"],
            highlightthickness=0,
            height=300,
        )
        bars_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            bars_frame,
            orient="vertical",
            command=bars_canvas.yview,
            style="App.Vertical.TScrollbar",
        )
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        bars_canvas.configure(yscrollcommand=scrollbar.set)

        def scroll_with_mouse(event: tk.Event) -> str:
            if event.delta:
                bars_canvas.yview_scroll(
                    -max(1, abs(event.delta) // 120) * (1 if event.delta > 0 else -1),
                    "units",
                )
            return "break"

        bars_canvas.bind("<MouseWheel>", scroll_with_mouse)
        return frame, pie_canvas, bars_canvas, scrollbar

    def _build_day_tab(self) -> None:
        toolbar = tk.Frame(self.day_page, bg=COLORS["background"])
        toolbar.pack(fill="x", pady=(0, 14))
        tk.Label(
            toolbar,
            text="查看日期",
            bg=COLORS["background"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        self._modern_button(
            toolbar,
            "‹",
            lambda: self.change_day(-1),
            compact=True,
        ).pack(side="left", padx=(10, 4))
        tk.Label(
            toolbar,
            textvariable=self.day_date_var,
            bg=COLORS["background"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left", padx=8)
        self._modern_button(
            toolbar,
            "›",
            lambda: self.change_day(1),
            compact=True,
        ).pack(side="left", padx=4)
        self._modern_button(
            toolbar,
            "回到今天",
            self.go_today,
            primary=True,
        ).pack(side="left", padx=(10, 0))

        cards = tk.Frame(self.day_page, bg=COLORS["background"])
        cards.pack(fill="x", pady=(0, 14))
        for column in range(3):
            cards.grid_columnconfigure(column, weight=1, uniform="day-card")
        self._card(cards, "屏幕使用总时长", self.day_total_var, COLORS["blue"]).grid(
            row=0, column=0, sticky="nsew", padx=(0, 7)
        )
        self._card(cards, "和上一日相比", self.day_delta_var, COLORS["orange"]).grid(
            row=0, column=1, sticky="nsew", padx=7
        )
        self._card(cards, "使用时间最多的应用", self.top_app_var, COLORS["teal"]).grid(
            row=0, column=2, sticky="nsew", padx=(7, 0)
        )

        charts = tk.Frame(self.day_page, bg=COLORS["background"])
        charts.pack(fill="both", expand=True)
        charts.grid_columnconfigure(0, weight=1)
        charts.grid_rowconfigure(0, weight=2, minsize=260)
        charts.grid_rowconfigure(1, weight=3, minsize=300)
        timeline_section, self.day_timeline_canvas = self._section(
            charts,
            "当天使用时长分布",
            "每根柱子代表 15 分钟，横轴是当天的具体时刻",
        )
        timeline_section.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        (
            apps_section,
            self.day_apps_pie_canvas,
            self.day_apps_canvas,
            self.day_apps_scrollbar,
        ) = self._split_app_section(
            charts,
            "应用使用情况",
            "饼图看占比，右侧条形图看每个软件的具体时长",
        )
        apps_section.grid(row=1, column=0, sticky="nsew")

    def _build_week_tab(self) -> None:
        toolbar = tk.Frame(self.week_page, bg=COLORS["background"])
        toolbar.pack(fill="x", pady=(0, 14))
        tk.Label(
            toolbar,
            text="最近 7 天",
            bg=COLORS["background"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")
        tk.Label(
            toolbar,
            text="从最早的一天到今天统计",
            bg=COLORS["background"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(12, 0))

        cards = tk.Frame(self.week_page, bg=COLORS["background"])
        cards.pack(fill="x", pady=(0, 14))
        for column in range(3):
            cards.grid_columnconfigure(column, weight=1, uniform="week-card")
        self._card(cards, "近 7 天总时长", self.week_total_var, COLORS["blue"]).grid(
            row=0, column=0, sticky="nsew", padx=(0, 7)
        )
        self._card(cards, "每天平均", self.week_average_var, COLORS["purple"]).grid(
            row=0, column=1, sticky="nsew", padx=7
        )
        self._card(cards, "使用时间最多的一天", self.week_highest_var, COLORS["orange"]).grid(
            row=0, column=2, sticky="nsew", padx=(7, 0)
        )

        week_chart_section, self.week_chart_canvas = self._section(
            self.week_page,
            "近 7 天屏幕使用时长",
            "柱子的高度表示每天的屏幕使用时长",
        )
        week_chart_section.pack(fill="both", expand=True, pady=(0, 14))

        detail_row = tk.Frame(self.week_page, bg=COLORS["background"])
        detail_row.pack(fill="both", expand=True)
        detail_row.grid_columnconfigure(0, weight=1)
        detail_row.grid_columnconfigure(1, weight=1)
        detail_row.grid_rowconfigure(0, weight=1)
        (
            weekly_apps_section,
            self.week_apps_pie_canvas,
            self.week_apps_canvas,
            self.week_apps_scrollbar,
        ) = self._split_app_section(
            detail_row,
            "最近 7 天应用使用情况",
            "把最近 7 天中同一个软件的使用时长合并统计",
        )
        weekly_apps_section.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        selected_day_section = tk.Frame(
            detail_row,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        selected_day_section.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        selected_heading = tk.Frame(selected_day_section, bg=COLORS["surface"])
        selected_heading.pack(fill="x", padx=20, pady=(16, 0))
        tk.Label(
            selected_heading,
            text="查看某一天的应用明细",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")
        self.week_detail_combo = ttk.Combobox(
            selected_heading,
            textvariable=self.week_detail_var,
            state="readonly",
            width=20,
            style="App.TCombobox",
        )
        self.week_detail_combo.pack(side="right")
        self.week_detail_combo.bind("<<ComboboxSelected>>", self.change_week_detail)
        tk.Label(
            selected_day_section,
            text="选择日期后，下面会显示当天每个软件的使用时长",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=16, pady=(4, 0))
        self.week_detail_canvas = tk.Canvas(
            selected_day_section,
            bg=COLORS["surface"],
            highlightthickness=0,
            height=280,
        )
        self.week_detail_canvas.pack(fill="both", expand=True, padx=12, pady=(10, 16))

    def _bind_resize_events(self) -> None:
        self.day_timeline_canvas.bind(
            "<Configure>", lambda _event: self.draw_timeline()
        )
        self.day_apps_pie_canvas.bind(
            "<Configure>", lambda _event: self.draw_day_apps_pie()
        )
        self.day_apps_canvas.bind("<Configure>", lambda _event: self.draw_day_apps())
        self.week_chart_canvas.bind("<Configure>", lambda _event: self.draw_week_chart())
        self.week_apps_pie_canvas.bind(
            "<Configure>", lambda _event: self.draw_week_apps_pie()
        )
        self.week_apps_canvas.bind("<Configure>", lambda _event: self.draw_week_apps())
        self.week_detail_canvas.bind(
            "<Configure>", lambda _event: self.draw_week_detail_apps()
        )

    def refresh_status(self) -> None:
        if self.closing:
            return
        if not self.collector_enabled:
            self.status_var.set("采集未启用")
            self.status_dot_label.configure(fg=COLORS["muted"])
        elif not collector_is_running():
            self.status_var.set("采集未运行，请点击刷新重试")
            self.status_dot_label.configure(fg=COLORS["red"])
        elif self.activity.is_workstation_locked():
            self.status_var.set("桌面已锁定，暂停记录")
            self.status_dot_label.configure(fg=COLORS["orange"])
        else:
            app_key, app_name, _icon_path = self.activity.active_app_details()
            if app_key == "__ignored__":
                self.status_var.set("采集进程运行中 · 统计窗口不计入")
                self.status_dot_label.configure(fg=COLORS["muted"])
            else:
                self.status_var.set(f"采集进程运行中 · 当前应用：{app_name}")
                self.status_dot_label.configure(fg=COLORS["teal"])
        self.after(1000, self.refresh_status)

    def ensure_collector(self) -> None:
        if self.closing or not self.collector_enabled:
            return
        if not collector_is_running():
            start_background_collector(self.data_dir)
        self.after(10000, self.ensure_collector)

    def manual_refresh(self) -> None:
        if self.closing:
            return
        if self.collector_enabled:
            start_background_collector(self.data_dir)
        self.refresh_all()
        if self.collector_enabled and not collector_is_running():
            self.status_var.set("统计已刷新 · 采集未运行，请稍后重试")
            self.status_dot_label.configure(fg=COLORS["red"])
        else:
            self.status_var.set(f"已刷新 · {datetime.now().strftime('%H:%M:%S')}")
            self.status_dot_label.configure(fg=COLORS["blue"])

    def change_day(self, offset: int) -> None:
        candidate = self.selected_day + timedelta(days=offset)
        if candidate > date.today():
            return
        self.selected_day = candidate
        self.day_date_var.set(day_display(candidate))
        self.refresh_day()

    def go_today(self) -> None:
        self.selected_day = date.today()
        self.day_date_var.set(day_display(self.selected_day))
        self.refresh_day()

    def change_week_detail(self, _event: object | None = None) -> None:
        current = self.week_detail_var.get()
        for value in self.week_day_values:
            if value == current:
                self.week_selected_day = parse_day(value[:10])
                break
        self.refresh_week_detail()

    def refresh_all(self) -> None:
        if self.closing:
            return
        self.refresh_day()
        self.refresh_week()

    def refresh_day(self) -> None:
        day = self.selected_day
        total = self.database.total_for_day(day)
        previous_total = self.database.total_for_day(day - timedelta(days=1))
        apps = self.database.apps_for_day(day)
        self.day_total_var.set(format_duration(total))
        self.day_delta_var.set(format_delta(total - previous_total))
        self.top_app_var.set(
            f"{apps[0]['app_name']} · {format_duration(apps[0]['seconds'])}"
            if apps
            else "暂时没有记录"
        )
        self.day_timeline_data = self.database.timeline_for_day(day)
        self.day_apps_data = apps
        self.draw_timeline()
        self.draw_day_apps()

    def refresh_week(self) -> None:
        today = date.today()
        days = [today - timedelta(days=6 - index) for index in range(7)]
        totals = self.database.totals_for_days(days)
        self.week_totals_data = totals
        total_seconds = sum(seconds for _day, seconds in totals)
        average_seconds = total_seconds // 7
        highest = max(totals, key=lambda item: item[1]) if totals else (today, 0)
        self.week_total_var.set(format_duration(total_seconds))
        self.week_average_var.set(f"{format_short_duration(average_seconds)}/天")
        self.week_highest_var.set(
            f"{highest[0].strftime('%m月%d日')} · {format_duration(highest[1])}"
            if highest[1]
            else "暂时没有记录"
        )
        self.week_apps_data = self.database.apps_for_days(days)
        self.week_day_values = [day_display(item[0]) for item in totals]
        self.week_detail_combo["values"] = self.week_day_values
        if self.week_selected_day not in days:
            self.week_selected_day = today
        self.week_detail_var.set(day_display(self.week_selected_day))
        self.week_detail_apps_data = self.database.apps_for_day(self.week_selected_day)
        self.draw_week_chart()
        self.draw_week_apps()
        self.draw_week_detail_apps()

    def refresh_week_detail(self) -> None:
        self.week_detail_apps_data = self.database.apps_for_day(self.week_selected_day)
        self.draw_week_detail_apps()

    def draw_timeline(self) -> None:
        canvas = self.day_timeline_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        left, right, top, bottom = 62, 22, 18, 42
        chart_width = width - left - right
        chart_height = max(40, height - top - bottom)
        canvas.configure(scrollregion=(0, 0, width, height))
        values = [
            self.day_timeline_data.get(index * TIMELINE_BUCKET_MINUTES, 0)
            for index in range(96)
        ]
        if not any(values):
            self._draw_empty(canvas, width, height, "当天还没有屏幕使用记录")
            return
        maximum = max(max(values), 60)
        for step in range(5):
            ratio = step / 4
            y = top + chart_height * (1 - ratio)
            canvas.create_line(left, y, width - right, y, fill=COLORS["grid"])
            label = format_short_duration(maximum * ratio)
            canvas.create_text(
                left - 8,
                y,
                text=label,
                anchor="e",
                fill=COLORS["muted"],
                font=("Microsoft YaHei UI", 9),
            )
        slot_width = chart_width / 96
        bar_width = max(2, slot_width - 1)
        for index, seconds in enumerate(values):
            bar_height = chart_height * (seconds / maximum)
            x1 = left + index * slot_width + (slot_width - bar_width) / 2
            x2 = x1 + bar_width
            y1 = top + chart_height - bar_height
            y2 = top + chart_height
            canvas.create_rectangle(
                x1,
                y1,
                x2,
                y2,
                fill=COLORS["blue"],
                outline="",
            )
        canvas.create_line(
            left,
            top + chart_height,
            width - right,
            top + chart_height,
            fill=COLORS["border"],
        )
        for hour in range(0, 25, 3):
            x = left + chart_width * (hour / 24)
            canvas.create_line(
                x,
                top + chart_height,
                x,
                top + chart_height + 5,
                fill=COLORS["border"],
            )
            canvas.create_text(
                x,
                height - 18,
                text=f"{hour:02d}:00",
                anchor="center",
                fill=COLORS["muted"],
                font=("Microsoft YaHei UI", 9),
            )

    def draw_day_apps(self) -> None:
        self._draw_horizontal_bars(
            self.day_apps_canvas,
            self.day_apps_data,
            COLORS["teal"],
            empty_text="当天还没有应用使用记录",
        )
        self.draw_day_apps_pie()

    def draw_day_apps_pie(self) -> None:
        self._draw_pie(
            self.day_apps_pie_canvas,
            self.day_apps_data,
            empty_text="当天还没有应用使用记录",
        )

    def draw_week_chart(self) -> None:
        canvas = self.week_chart_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 600)
        height = max(canvas.winfo_height(), 230)
        left, right, top, bottom = 54, 24, 18, 36
        chart_width = width - left - right
        chart_height = height - top - bottom
        values = [seconds for _day, seconds in self.week_totals_data]
        if not any(values):
            self._draw_empty(canvas, width, height, "最近 7 天还没有屏幕使用记录")
            return
        maximum = max(max(values), 3600)
        for step in range(5):
            ratio = step / 4
            y = top + chart_height * (1 - ratio)
            canvas.create_line(left, y, width - right, y, fill=COLORS["grid"])
            canvas.create_text(
                left - 8,
                y,
                text=format_short_duration(maximum * ratio),
                anchor="e",
                fill=COLORS["muted"],
                font=("Microsoft YaHei UI", 9),
            )
        slot_width = chart_width / max(1, len(self.week_totals_data))
        bar_width = min(70, slot_width * 0.55)
        for index, (day, seconds) in enumerate(self.week_totals_data):
            bar_height = chart_height * (seconds / maximum)
            center_x = left + slot_width * (index + 0.5)
            x1 = center_x - bar_width / 2
            x2 = center_x + bar_width / 2
            y1 = top + chart_height - bar_height
            y2 = top + chart_height
            fill = COLORS["orange"] if day == date.today() else COLORS["blue"]
            canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline="")
            canvas.create_text(
                center_x,
                max(top + 10, y1 - 10),
                text=format_short_duration(seconds),
                fill=COLORS["text"],
                font=("Microsoft YaHei UI", 9),
            )
            canvas.create_text(
                center_x,
                height - 16,
                text=f"{day.month}/{day.day}\n周{chinese_weekday(day)}",
                anchor="center",
                fill=COLORS["muted"],
                font=("Microsoft YaHei UI", 9),
            )

    def draw_week_apps(self) -> None:
        self._draw_horizontal_bars(
            self.week_apps_canvas,
            self.week_apps_data,
            COLORS["purple"],
            empty_text="最近 7 天还没有应用使用记录",
        )
        self.draw_week_apps_pie()

    def draw_week_apps_pie(self) -> None:
        self._draw_pie(
            self.week_apps_pie_canvas,
            self.week_apps_data,
            empty_text="最近 7 天还没有应用使用记录",
        )

    def draw_week_detail_apps(self) -> None:
        self._draw_horizontal_bars(
            self.week_detail_canvas,
            self.week_detail_apps_data,
            COLORS["teal"],
            empty_text="这一天还没有应用使用记录",
        )

    @staticmethod
    def _icon_style(app_name: str) -> tuple[str, str]:
        for known_name, style in APP_ICON_STYLES.items():
            if app_name.startswith(known_name):
                return style

        clean_name = app_name.removeprefix("网页：").strip()
        first_character = next(
            (
                character
                for character in clean_name
                if character.isalnum() or "\u4e00" <= character <= "\u9fff"
            ),
            "?",
        )
        if first_character.isascii():
            first_character = first_character.upper()
        color_index = sum(ord(character) for character in app_name) % len(
            APP_ICON_COLORS
        )
        return first_character, APP_ICON_COLORS[color_index]

    @classmethod
    def _draw_icon_badge(
        cls,
        canvas: tk.Canvas,
        x: float,
        y: float,
        app_name: str,
        size: int = 26,
    ) -> None:
        letter, color = cls._icon_style(app_name)
        canvas.create_oval(
            x,
            y,
            x + size,
            y + size,
            fill=color,
            outline="",
        )
        canvas.create_text(
            x + size / 2,
            y + size / 2,
            text=letter,
            fill="#FFFFFF",
            font=("Microsoft YaHei UI", 10, "bold"),
        )

    def _draw_app_icon(
        self,
        canvas: tk.Canvas,
        x: float,
        y: float,
        row: sqlite3.Row | None,
        size: int = 30,
    ) -> None:
        if row is None:
            self._draw_icon_badge(canvas, x, y, "其他应用", size=size)
            return
        app_key = str(row["app_key"])
        app_name = str(row["app_name"])
        icon_path = str(row["icon_path"] or "")
        photo = self.icon_manager.get_photo(
            app_key,
            app_name,
            icon_path,
            size=size,
        )
        if photo is not None:
            canvas.create_image(
                x + size / 2,
                y + size / 2,
                image=photo,
            )
        else:
            self._draw_icon_badge(canvas, x, y, app_name, size=size)

    def _draw_horizontal_bars(
        self,
        canvas: tk.Canvas,
        rows: list[sqlite3.Row],
        color: str,
        empty_text: str,
    ) -> None:
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        viewport_height = max(canvas.winfo_height(), 1)
        if not rows:
            canvas.configure(scrollregion=(0, 0, width, viewport_height))
            self._draw_empty(canvas, width, viewport_height, empty_text)
            return
        left, right, top, bottom = 16, 18, 14, 18
        row_height = 56
        content_height = max(
            viewport_height,
            top + bottom + row_height * len(rows),
        )
        canvas.configure(scrollregion=(0, 0, width, content_height))
        maximum = max(1, max(int(row["seconds"]) for row in rows))
        for index, row in enumerate(rows):
            y = top + row_height * index
            app_name = str(row["app_name"])
            name = app_name
            if len(name) > 24:
                name = name[:23] + "…"
            seconds = int(row["seconds"])
            self._draw_app_icon(canvas, left, y + 1, row, size=34)
            canvas.create_text(
                left + 36,
                y + 11,
                text=name,
                anchor="w",
                fill=COLORS["text"],
                font=("Microsoft YaHei UI", 10),
            )
            canvas.create_text(
                width - right,
                y + 11,
                text=format_short_duration(seconds),
                anchor="e",
                fill=COLORS["muted"],
                font=("Microsoft YaHei UI", 9),
            )
            track_y = y + 31
            track_left = left + 36
            track_width = width - track_left - right
            canvas.create_rectangle(
                track_left,
                track_y,
                track_left + track_width,
                track_y + 8,
                fill=COLORS["grid"],
                outline="",
            )
            canvas.create_rectangle(
                track_left,
                track_y,
                track_left + track_width * seconds / maximum,
                track_y + 8,
                fill=color,
                outline="",
            )

    def _draw_pie(
        self,
        canvas: tk.Canvas,
        rows: list[sqlite3.Row],
        empty_text: str,
    ) -> None:
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        if not rows:
            self._draw_empty(canvas, width, height, empty_text)
            return

        visible_rows = rows[:6]
        slices = [
            (str(row["app_name"]), int(row["seconds"]), row)
            for row in visible_rows
        ]
        other_seconds = sum(int(row["seconds"]) for row in rows[6:])
        if other_seconds:
            slices.append(("其他应用", other_seconds, None))
        total_seconds = max(
            1,
            sum(seconds for _name, seconds, _row in slices),
        )

        center_x = width * 0.32
        center_y = height * 0.50
        radius = min(
            max(28, width * 0.32),
            max(28, (height - 28) * 0.46),
            132,
        )
        box = (
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
        )
        start_angle = 90
        for index, (_name, seconds, _row) in enumerate(slices):
            extent = seconds / total_seconds * 360
            canvas.create_arc(
                *box,
                start=start_angle,
                extent=extent,
                style=tk.PIESLICE,
                fill=PIE_COLORS[index % len(PIE_COLORS)],
                outline=COLORS["surface"],
                width=2,
            )
            start_angle += extent

        inner_radius = radius * 0.58
        canvas.create_oval(
            center_x - inner_radius,
            center_y - inner_radius,
            center_x + inner_radius,
            center_y + inner_radius,
            fill=COLORS["surface"],
            outline="",
        )
        canvas.create_text(
            center_x,
            center_y - 8,
            text="总时长",
            fill=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        canvas.create_text(
            center_x,
            center_y + 12,
            text=format_short_duration(total_seconds),
            fill=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )

        legend_x = width * 0.60
        legend_y = max(20, height * 0.15)
        row_gap = min(32, max(22, (height - 36) / len(slices)))
        for index, (name, seconds, row) in enumerate(slices):
            y = legend_y + index * row_gap
            percent = seconds / total_seconds * 100
            display_name = name if len(name) <= 12 else name[:11] + "…"
            self._draw_app_icon(
                canvas,
                legend_x,
                y - 11,
                row,
                size=22,
            )
            canvas.create_text(
                legend_x + 30,
                y,
                text=display_name,
                anchor="w",
                fill=COLORS["text"],
                font=("Microsoft YaHei UI", 9),
            )
            canvas.create_text(
                width - 14,
                y,
                text=f"{percent:.0f}%",
                anchor="e",
                fill=COLORS["muted"],
                font=("Microsoft YaHei UI", 9),
            )

    @staticmethod
    def _draw_empty(canvas: tk.Canvas, width: int, height: int, text: str) -> None:
        canvas.create_text(
            width / 2,
            height / 2,
            text=text,
            fill=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        )

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.database.close()
        self.destroy()


from desktop_ui import DashboardMixin


class ScreenTimeTrackerApp(DashboardMixin, LegacyScreenTimeTrackerApp):
    """Dark, DPI-aware dashboard; statistics and collector remain compatible."""


def seed_demo_data(data_dir: Path) -> None:
    """给界面测试准备一组本地演示数据，不会联网。"""
    database = UsageDatabase(data_dir)
    today = date.today()
    demo_apps = [
        ("chrome.exe", "Google Chrome", 1.0),
        ("code.exe", "Visual Studio Code", 0.65),
        ("wechat.exe", "微信", 0.35),
        ("winword.exe", "Microsoft Word", 0.22),
    ]
    for day_offset in range(10):
        current_day = today - timedelta(days=day_offset)
        day_factor = max(0.45, 1.0 - day_offset * 0.04)
        for index, (key, name, weight) in enumerate(demo_apps):
            base_seconds = int(3600 * weight * day_factor)
            sample_time = datetime.combine(
                current_day,
                datetime.min.time(),
            ).replace(hour=8 + index * 2, minute=10)
            database.add_sample(sample_time, key, name, base_seconds)
    database.close()


def run_self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="screen-time-test-") as folder:
        database = UsageDatabase(Path(folder))
        sample_time = datetime(2026, 8, 24, 10, 30)
        database.add_sample(sample_time, "chrome.exe", "Google Chrome", 60)
        database.add_sample(sample_time, "chrome.exe", "Google Chrome", 30)
        database.add_sample(sample_time, "code.exe", "Visual Studio Code", 45)
        database.add_sample(sample_time, "pid-1234", "进程 1234", 20)
        database.add_sample(
            sample_time,
            "screentimetracker.exe",
            "屏幕使用时长",
            600,
        )
        database.close()
        database = UsageDatabase(Path(folder))
        assert database.total_for_day("2026-08-24") == 155
        apps = database.apps_for_day("2026-08-24")
        assert apps[0]["app_name"] == "Google Chrome"
        assert apps[0]["seconds"] == 90
        assert all(
            not str(row["app_key"]).startswith("pid-")
            and not str(row["app_name"]).startswith("进程 ")
            for row in apps
        )
        timeline = database.timeline_for_day("2026-08-24")
        assert sum(timeline.values()) == 155
        weekly = database.apps_for_days(
            [date(2026, 8, 24), date(2026, 8, 23)]
        )
        assert weekly[0]["seconds"] == 90
        database.close()
    website = WindowsActivity.website_identity(
        "msedge.exe",
        "动态首页-哔哩哔哩 和另外 1 个页面 - 个人 - Microsoft\u200b Edge",
    )
    assert website == ("website:B站", "B站（网页）")
    native_icons = NativeIconManager()
    for executable in ("msedge.exe", "chrome.exe"):
        icon_path = native_icons.resolve_icon_path(executable)
        if icon_path:
            assert native_icons._render_icon(icon_path) is not None
    print("自检通过：数据库写入、日统计、时间分布和周统计均正常。")


def main() -> None:
    args = sys.argv[1:]
    if "--self-test" in args:
        run_self_test()
        return

    if "--preview" in args:
        with tempfile.TemporaryDirectory(prefix="screen-time-preview-") as folder:
            preview_dir = Path(folder)
            seed_demo_data(preview_dir)
            app = ScreenTimeTrackerApp(preview_dir, start_collector=False, preview=True)
            app.mainloop()
        return

    data_dir = None
    if "--data-dir" in args:
        index = args.index("--data-dir")
        if index + 1 >= len(args):
            raise SystemExit("--data-dir 后面需要填写文件夹路径")
        data_dir = Path(args[index + 1])
    if "--collector" in args:
        run_collector(data_dir or default_data_dir())
        return
    if "--demo" in args:
        seed_demo_data(data_dir or default_data_dir())

    app = ScreenTimeTrackerApp(data_dir)
    app.mainloop()


if __name__ == "__main__":
    main()
