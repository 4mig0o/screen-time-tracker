"""Generate a local comparison of old and new icon extraction at fixed pixel sizes."""
import json
from pathlib import Path
import tempfile
from PIL import Image, ImageDraw, ImageFont
import screen_time_tracker as core
from desktop_ui import fit_icon, icon_pixels, shell_icon


def run():
    output = Path("qa-output")
    output.mkdir(exist_ok=True)
    report = []
    sheet = Image.new("RGB", (1040, 460), "#111318")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
    small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 14)
    draw.text((24,18), "图标提取对比（每组左：旧链路；右：高清链路）", font=font, fill="white")
    draw.text((24,48), "200% 列表尺寸为 68px；下方大图仅以最近邻放大展示像素差异。", font=small, fill="#A8B0BF")
    with tempfile.TemporaryDirectory() as directory:
        manager = core.NativeIconManager(Path(directory))
        for index, key in enumerate(("chrome.exe", "msedge.exe")):
            path = manager.resolve_icon_path(key)
            before = manager._render_icon(path)
            after = shell_icon(path, 128)
            assert before is not None and after is not None
            physical = icon_pixels(34, 2)
            # Old path produced a 34px Tk image; simulate OS bitmap enlargement.
            old = before.resize((34,34), Image.Resampling.LANCZOS).resize((physical,physical), Image.Resampling.BILINEAR)
            new = fit_icon(after, physical)
            report.append({"app":key,"native_source":list(after.size),"physical_target":physical,"old_chain_bitmap":34})
            x = 24 + index * 510
            draw.text((x,92),key, font=font, fill="#F3F4F6")
            sheet.paste(old, (x,132), old)
            sheet.paste(new, (x+100,132), new)
            for shift, image in ((0,old),(228,new)):
                enlarged = image.resize((204,204), Image.Resampling.NEAREST)
                sheet.paste(enlarged, (x+shift,228), enlarged)
    sheet.save(output / "icons-before-after.png")
    (output / "icons-results.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    run()
