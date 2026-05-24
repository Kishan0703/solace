from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "frontend" / "assets"
OUTPUT_DIR = ROOT / "output" / "video"
WORK_DIR = ROOT / "tmp" / "video_package"

SOURCE_VIDEO = ASSETS_DIR / "eterna-demo-v2.mp4"
POSTER = ASSETS_DIR / "eterna-demo-v2-poster.jpg"

INTRO_IMAGE = WORK_DIR / "intro.png"
OUTRO_IMAGE = WORK_DIR / "outro.png"
INTRO_VIDEO = WORK_DIR / "intro.mp4"
OUTRO_VIDEO = WORK_DIR / "outro.mp4"
CONCAT_LIST = WORK_DIR / "concat.txt"

FINAL_VIDEO = OUTPUT_DIR / "eterna-demo-final.mp4"
FINAL_POSTER = OUTPUT_DIR / "eterna-demo-final-poster.jpg"

WIDTH = 1280
HEIGHT = 720
FPS = 24
INTRO_SECONDS = 3
OUTRO_SECONDS = 3

BG = "#08111F"
PANEL = "#132239"
TEXT = "#F6F4F1"
MUTED = "#B6C0CC"
PRIMARY = "#F3C4A1"
TEAL = "#8ED6D1"
LINE = "#28425F"

FONT_REGULAR = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_BOLD = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_REGULAR, size=size)


def load_bold_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD, size=size)


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def rounded_panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str, outline: str | None = None) -> None:
    draw.rounded_rectangle(box, radius=28, fill=fill, outline=outline, width=2 if outline else 0)


def draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: str,
    box: tuple[int, int, int, int],
    line_spacing: int = 10,
) -> None:
    x0, y0, x1, y1 = box
    max_width = x1 - x0
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)

    cursor_y = y0
    for line in lines:
        if cursor_y > y1:
            break
        draw.text((x0, cursor_y), line, font=font, fill=fill)
        bbox = draw.textbbox((0, 0), line, font=font)
        cursor_y += (bbox[3] - bbox[1]) + line_spacing


def make_background() -> Image.Image:
    base = Image.new("RGB", (WIDTH, HEIGHT), BG)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.ellipse((-120, -140, 360, 260), fill=(243, 196, 161, 38))
    odraw.ellipse((980, 480, 1480, 900), fill=(142, 214, 209, 34))
    odraw.ellipse((430, 110, 980, 640), outline=(243, 196, 161, 96), width=2)
    blur = overlay.filter(ImageFilter.GaussianBlur(18))
    return Image.alpha_composite(base.convert("RGBA"), blur).convert("RGB")


def create_intro_image() -> None:
    bg = make_background()
    draw = ImageDraw.Draw(bg)

    title_font = load_bold_font(62)
    subtitle_font = load_font(26)
    body_font = load_font(22)
    chip_font = load_font(20)

    poster = Image.open(POSTER).convert("RGB")
    poster.thumbnail((560, 420))
    px = WIDTH - poster.width - 90
    py = 120

    rounded_panel(draw, (72, 208, 602, 512), PANEL, LINE)
    bg.paste(poster, (px, py))
    draw.rounded_rectangle((px, py, px + poster.width, py + poster.height), radius=24, outline=PRIMARY, width=2)

    draw.text((80, 66), "念念 Eterna", font=title_font, fill=TEXT)
    draw.text((82, 140), "AI 家庭纪念与数字陪伴 Demo", font=subtitle_font, fill=PRIMARY)
    draw_wrapped_text(
        draw,
        "把亲人的声音、回忆、面容和熟悉的关心方式，沉淀为一个可长期保存、可持续互动、可温和主动联系的数字亲人空间。",
        body_font,
        MUTED,
        (94, 238, 560, 420),
        line_spacing=14,
    )

    chips = ["记忆归档", "多模态互动", "主动联系", "长期陪伴"]
    chip_x = 92
    chip_y = 544
    for label in chips:
        bbox = draw.textbbox((0, 0), label, font=chip_font)
        chip_w = bbox[2] - bbox[0] + 34
        draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_w, chip_y + 38), radius=18, fill="#1B2D45")
        draw.text((chip_x + 17, chip_y + 8), label, font=chip_font, fill=TEXT)
        chip_x += chip_w + 14

    draw.text((82, 642), "Demo Preview · 2026-04", font=load_font(18), fill=TEAL)
    bg.save(INTRO_IMAGE, quality=95)


def create_outro_image() -> None:
    bg = make_background()
    draw = ImageDraw.Draw(bg)

    title_font = load_bold_font(54)
    body_font = load_font(24)
    small_font = load_font(20)

    rounded_panel(draw, (86, 146, 1188, 564), PANEL, LINE)

    draw.text((112, 92), "为什么这段 Demo 重要", font=title_font, fill=TEXT)
    bullets = [
        "不是单点生成工具，而是“建档 - 记忆 - 互动 - 主动联系”的完整闭环。",
        "支持文字、语音、视频等多种方式，适合纪念、陪伴与家庭传承场景。",
        "当前项目已具备产品、支付和部署底座，可进入小规模试运营验证。",
    ]

    y = 182
    for bullet in bullets:
        draw.ellipse((120, y + 12, 136, y + 28), fill=PRIMARY)
        draw_wrapped_text(draw, bullet, body_font, TEXT, (156, y, 1120, y + 86), line_spacing=12)
        y += 102

    draw.text((112, 596), "输出文件：output/video/eterna-demo-final.mp4", font=small_font, fill=MUTED)
    draw.text((112, 628), "适用场景：路演发送、赛事报名、合作介绍、产品预览", font=small_font, fill=MUTED)
    bg.save(OUTRO_IMAGE, quality=95)


def image_to_video(image_path: Path, output_path: Path, duration: int) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-t",
            str(duration),
            "-i",
            str(image_path),
            "-f",
            "lavfi",
            "-t",
            str(duration),
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ]
    )


def concat_video() -> None:
    CONCAT_LIST.write_text(
        "\n".join(
            [
                f"file '{INTRO_VIDEO}'",
                f"file '{SOURCE_VIDEO}'",
                f"file '{OUTRO_VIDEO}'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(CONCAT_LIST),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(FINAL_VIDEO),
        ]
    )


def export_poster() -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "00:00:03",
            "-i",
            str(FINAL_VIDEO),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(FINAL_POSTER),
        ]
    )


def main() -> None:
    ensure_dirs()
    create_intro_image()
    create_outro_image()
    image_to_video(INTRO_IMAGE, INTRO_VIDEO, INTRO_SECONDS)
    image_to_video(OUTRO_IMAGE, OUTRO_VIDEO, OUTRO_SECONDS)
    concat_video()
    export_poster()
    print(FINAL_VIDEO)
    print(FINAL_POSTER)


if __name__ == "__main__":
    main()
