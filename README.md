# 屏幕使用时长统计程序

一个统计 Windows 电脑使用时长的小工具，可以查看每天用了多久、哪些软件用得最多，以及最近 7 天的使用情况。用 Python 和 SQLite 开发，数据保存在本地。

## 深色圆角仪表盘版本

- 左侧导航、深色圆角卡片与自适应分区，支持日视图和最近 7 天统计。
- 日/周柱状图向上生长，圆环从顶部顺时针展开，约 1 秒完成；启动、实际切换日期及日/周页面时播放，首屏外的图表等进入可视区域再播放。刷新、改尺寸、重复点击当前页面和周视图单日明细选择不触发重播。
- 统一平滑滚动：整页和应用列表按鼠标位置滚动，到列表边界后继续滚动页面。
- 图标按显示器 DPI 提取；优先 Windows Shell 高分辨率图标，保留透明度，低分辨率资源不会被强行放大。
- 软件与网站使用各自图标；界面仅使用本机资源和本地图标缓存，不发起外部图标请求。
- 图标和浏览器历史读取在后台进行，加载期间可继续操作界面。
- `python screen_time_tracker.py --preview` 或 `ScreenTimeTracker.exe --preview` 使用临时演示数据，不记录实际使用。
- `python -B -m unittest test_regressions test_dashboard test_chart_animation -v` 执行回归检查；`python -B ui_acceptance.py` 生成独立演示窗口验收截图和记录。
- `python -B animation_acceptance.py` 用临时数据验证动画，输出日/周图表的真实连续帧及播放时长；该检查会短暂显示自己的演示窗口，不操作真实采集器。
- `python -B animation_acceptance.py --replay-only --output qa-output/chart-replay` 专门验证日期和日/周切换重播、连续切换、空数据及缩放重建不误触发。

新版主源码是根目录的 `screen_time_tracker.py` 与 `desktop_ui.py`；`work` 文件夹保留历史开发记录。

网站识别由标题及浏览器本地历史推断，不能保证每个页面识别准确；软件本身缺少高清图标时会使用原尺寸图标或文字徽标。关闭窗口保留独立后台采集器。

## 主要功能

- **持续记录**：Windows 桌面解锁期间持续记录屏幕使用时长，锁屏时自动暂停。
- **网站识别**：结合浏览器本地历史和标题，尽力识别当前网站（哔哩哔哩、GitHub、ChatGPT、Render、百度、知乎等）。
- **站点专属图标**：直接从 Edge / Chrome 本地提取各网站高清 Favicon 图标并缓存，列表和饼图均显示对应网站真实图标。
- **原生程序图标**：桌面程序优先从 EXE 自身读取 Windows 原生高清图标。
- **整页顺滑滚动**：日视图与周视图支持整页上下滚动与鼠标滚轮，告别内容拥挤与裁切。
- **时长分布完整呈现**：今日使用时长分布图自适应画布高度，00:00 至 24:00 时间横轴清晰可见。
- **后台独立采集**：内置隐藏后台采集器，即使关闭主界面也能在后台持续记录。
- **一键即时刷新**：顶部“刷新”按钮可立即同步后台最新时长数据。
- **纯本地安全隐私**：数据保存在本地 `%LOCALAPPDATA%\ScreenTimeTracker\screen_time.db`，不联网上传个人隐私。

## 运行与使用

- 双击桌面上的“屏幕使用时长”快捷方式即可启动程序。
- 也可以直接运行 `ScreenTimeTracker.exe`。

## 从源码运行

需要 Windows、Python 3.10 或更新版本，以及 Python 自带的 Tkinter。

```powershell
python -m pip install -r requirements.txt
python screen_time_tracker.py
```

正常启动会启用后台采集并使用本机统计数据库。只想查看界面、不记录实际使用时，请运行：

```powershell
python screen_time_tracker.py --preview
```

关闭正常统计窗口不会停止独立后台采集器；演示模式不启动采集器。

## 测试与打包

```powershell
python -B -m unittest test_regressions test_dashboard test_chart_animation -v
python -B screen_time_tracker.py --self-test
```

在 PowerShell 中运行 `./build_exe.ps1` 安装打包依赖并生成 `dist/ScreenTimeTracker.exe`。
`./create_shortcut.ps1` 会同时创建桌面快捷方式和开机启动的后台采集快捷方式；不需要开机采集时不要执行此脚本。

GitHub 源码仓库不包含本地数据库、历史备份、第三方参考目录、生成的验收截图、exe 或压缩包；这些文件由 `.gitignore` 排除。本地交付文件仍保留在项目目录中。
