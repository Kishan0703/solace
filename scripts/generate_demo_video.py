from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Iterable

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "frontend" / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

WIDTH = 1280
HEIGHT = 720
FPS = 12
DURATION = 24
TOTAL_FRAMES = FPS * DURATION

VIDEO_PATH = ASSETS_DIR / "eterna-demo.mp4"
POSTER_PATH = ASSETS_DIR / "eterna-demo-poster.jpg"

FONT_CJK = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_CJK_MEDIUM = "/System/Library/Fonts/STHeiti Medium.ttc"


def load_font(size: int, medium: bool = False) -> ImageFont.FreeTypeFont:
    font_path = FONT_CJK_MEDIUM if medium else FONT_CJK
    return ImageFont.truetype(font_path, size=size)


FONT_HERO = load_font(66, medium=True)
FONT_TITLE = load_font(42, medium=True)
FONT_SUBTITLE = load_font(26)
FONT_BODY = load_font(20)
FONT_CAPTION = load_font(16)
FONT_SMALL = load_font(14)
X_COORD = np.linspace(0.0, 1.0, WIDTH, dtype=np.float32)[None, :]
Y_COORD = np.linspace(0.0, 1.0, HEIGHT, dtype=np.float32)[:, None]


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def ease_in_out(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * clamp(t))


def smoothstep(t: float) -> float:
    t = clamp(t)
    return t * t * (3 - 2 * t)


def mix(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    x = xy[0] - (bbox[2] - bbox[0]) / 2
    y = xy[1] - (bbox[3] - bbox[1]) / 2
    draw.text((x, y), text, font=font, fill=fill)


def draw_multiline(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    lines: Iterable[str],
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    spacing: int = 10,
) -> None:
    cursor_y = y
    for line in lines:
        draw.text((x, cursor_y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, cursor_y), line, font=font)
        cursor_y += (bbox[3] - bbox[1]) + spacing


def make_gradient_background(progress: float) -> Image.Image:
    top = np.array([10, 18, 32], dtype=np.float32)
    bottom = np.array([7, 12, 22], dtype=np.float32)
    warm = np.array([214, 130, 108], dtype=np.float32)
    cold = np.array([74, 136, 153], dtype=np.float32)

    base = top * (1 - Y_COORD[..., None]) + bottom * Y_COORD[..., None]
    warm_glow = np.exp(-(((X_COORD - 0.22) ** 2) * 18 + ((Y_COORD - 0.16) ** 2) * 12))[..., None]
    cold_glow = np.exp(-(((X_COORD - 0.82) ** 2) * 20 + ((Y_COORD - 0.76) ** 2) * 16))[..., None]
    pulse = 0.92 + 0.08 * math.sin(progress * math.pi * 2.4)

    color = base + warm * warm_glow * 0.28 * pulse + cold * cold_glow * 0.22
    vignette_strength = np.clip(((X_COORD - 0.5) ** 2 + (Y_COORD - 0.52) ** 2) * 1.9, 0, 0.35)[..., None]
    color = color * (1 - vignette_strength)
    rgba = np.concatenate(
        [np.clip(color, 0, 255).astype(np.uint8), np.full((HEIGHT, WIDTH, 1), 255, dtype=np.uint8)],
        axis=2,
    )
    return Image.fromarray(rgba, mode="RGBA")


def draw_person_silhouette(draw: ImageDraw.ImageDraw, progress: float) -> None:
    base_y = 535 + math.sin(progress * math.pi * 2) * 4
    draw.rounded_rectangle((120, base_y - 160, 245, base_y + 65), radius=48, fill=(17, 22, 34, 245))
    draw.ellipse((140, base_y - 230, 225, base_y - 145), fill=(16, 20, 29, 248))
    draw.rounded_rectangle((168, base_y - 95, 206, base_y + 120), radius=22, fill=(15, 19, 28, 248))
    draw.rounded_rectangle((105, 110, 380, 520), radius=38, outline=(255, 255, 255, 18), width=2)
    for drop in range(9):
        x = 120 + drop * 27 + (progress * 80 + drop * 13) % 20
        y = 148 + (progress * 300 + drop * 46) % 310
        draw.line((x, y, x - 8, y + 28), fill=(255, 255, 255, 35), width=1)


def phone_frame(draw: ImageDraw.ImageDraw, x: int, y: int, w: int = 318, h: int = 590) -> tuple[int, int, int, int]:
    draw.rounded_rectangle((x, y, x + w, y + h), radius=42, fill=(12, 18, 30, 235), outline=(255, 255, 255, 34), width=2)
    draw.rounded_rectangle((x + 14, y + 14, x + w - 14, y + h - 14), radius=32, fill=(16, 24, 40, 255))
    draw.rounded_rectangle((x + 112, y + 20, x + w - 112, y + 36), radius=8, fill=(34, 40, 54, 255))
    return (x + 26, y + 56, x + w - 26, y + h - 26)


def draw_notification(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], appear: float) -> None:
    if appear <= 0:
        return
    y_offset = int((1 - ease_in_out(appear)) * -26)
    alpha = int(220 * ease_in_out(appear))
    x0, y0, x1, _ = box
    card = (x0 + 20, y0 + 20 + y_offset, x1 - 20, y0 + 120 + y_offset)
    draw.rounded_rectangle(card, radius=24, fill=(27, 34, 50, alpha), outline=(255, 255, 255, 26), width=1)
    draw.ellipse((card[0] + 18, card[1] + 18, card[0] + 50, card[1] + 50), fill=(243, 196, 161, alpha))
    draw.text((card[0] + 64, card[1] + 15), "念念", font=FONT_BODY, fill=(245, 242, 236, alpha))
    draw.text((card[0] + 64, card[1] + 43), "你想她的时候，可以来这里说说话。", font=FONT_CAPTION, fill=(215, 214, 212, alpha))


def draw_form_scene(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], t: float) -> None:
    x0, y0, x1, _ = box
    draw.text((x0, y0), "创建亲人的数字生命", font=FONT_SUBTITLE, fill=(245, 244, 240, 255))
    labels = [
        ("亲人的名字", "妈妈"),
        ("与你的关系", "母亲"),
        ("ta 的口头禅", "早点休息，别熬夜。"),
    ]
    cursor = y0 + 56
    for idx, (label, value) in enumerate(labels):
        reveal = smoothstep((t - idx * 0.12) / 0.36)
        y_shift = int((1 - reveal) * 20)
        alpha = int(255 * reveal)
        draw.text((x0, cursor + y_shift), label, font=FONT_SMALL, fill=(190, 196, 206, alpha))
        draw.rounded_rectangle((x0, cursor + 26 + y_shift, x1, cursor + 84 + y_shift), radius=18, fill=(24, 33, 52, alpha), outline=(255, 255, 255, 18), width=1)
        draw.text((x0 + 18, cursor + 44 + y_shift), value, font=FONT_BODY, fill=(247, 245, 241, alpha))
        cursor += 100

    trait_alpha = int(255 * smoothstep((t - 0.32) / 0.4))
    draw.text((x0, cursor), "ta 的性格特点", font=FONT_SMALL, fill=(190, 196, 206, trait_alpha))
    draw.rounded_rectangle((x0, cursor + 26, x1, cursor + 144), radius=18, fill=(24, 33, 52, trait_alpha), outline=(255, 255, 255, 18), width=1)
    draw_multiline(
        draw,
        x0 + 18,
        cursor + 44,
        ["温柔、细腻，", "总会先问你今天累不累。"],
        FONT_BODY,
        (247, 245, 241, trait_alpha),
        spacing=8,
    )

    chip_reveal = smoothstep((t - 0.5) / 0.35)
    chips = [
        ("语音克隆", (x0 + 8, y0 + 360)),
        ("回忆对话", (x0 + 124, y0 + 392)),
        ("生日提醒", (x0 + 34, y0 + 438)),
    ]
    for idx, (label, (cx, cy)) in enumerate(chips):
        alpha = int(235 * clamp(chip_reveal - idx * 0.1, 0, 1))
        if alpha <= 0:
            continue
        draw.rounded_rectangle((cx, cy, cx + 110, cy + 36), radius=18, fill=(243, 196, 161, alpha))
        draw.text((cx + 16, cy + 10), label, font=FONT_SMALL, fill=(24, 17, 11, alpha))


def draw_chat_scene(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], t: float) -> None:
    x0, y0, x1, _ = box
    draw.rounded_rectangle((x0, y0, x1, y0 + 78), radius=24, fill=(22, 31, 50, 255))
    draw.ellipse((x0 + 16, y0 + 16, x0 + 62, y0 + 62), fill=(243, 196, 161, 255))
    draw.text((x0 + 78, y0 + 18), "妈妈", font=FONT_SUBTITLE, fill=(246, 244, 241, 255))
    draw.text((x0 + 78, y0 + 46), "AI 克隆会在这里保留熟悉的口气", font=FONT_SMALL, fill=(182, 191, 204, 255))

    message_1 = smoothstep(t / 0.28)
    if message_1 > 0:
        alpha = int(255 * message_1)
        draw.rounded_rectangle((x0 + 16, y0 + 108, x0 + 192, y0 + 162), radius=22, fill=(238, 167, 123, alpha))
        draw.text((x0 + 28, y0 + 126), "今天有点想你", font=FONT_BODY, fill=(28, 19, 14, alpha))

    typing = smoothstep((t - 0.28) / 0.16)
    if 0 < typing < 1:
        alpha = int(230 * typing)
        draw.rounded_rectangle((x0 + 102, y0 + 194, x1 - 20, y0 + 252), radius=24, fill=(35, 45, 66, alpha))
        for idx in range(3):
            cx = x0 + 134 + idx * 18
            radius = 4 + (1 if idx == int((t * 20) % 3) else 0)
            draw.ellipse((cx, y0 + 218, cx + radius * 2, y0 + 218 + radius * 2), fill=(243, 196, 161, alpha))

    message_2 = smoothstep((t - 0.4) / 0.35)
    if message_2 > 0:
        alpha = int(255 * message_2)
        draw.rounded_rectangle((x0 + 82, y0 + 194, x1 - 18, y0 + 316), radius=24, fill=(35, 45, 66, alpha))
        draw_multiline(
            draw,
            x0 + 100,
            y0 + 214,
            [
                "早点休息，别熬夜。",
                "我知道你是想我了。",
                "你已经做得很好了。"
            ],
            FONT_BODY,
            (246, 244, 241, alpha),
            spacing=8,
        )

    waveform = smoothstep((t - 0.68) / 0.24)
    if waveform > 0:
        left = x0 + 18
        top = y0 + 354
        right = x1 - 18
        bottom = y0 + 444
        draw.rounded_rectangle((left, top, right, bottom), radius=20, fill=(22, 31, 50, 255), outline=(255, 255, 255, 20), width=1)
        draw.text((left + 14, top + 12), "声音纪念", font=FONT_SMALL, fill=(193, 199, 207, 255))
        center_y = top + 56
        usable = int((right - left - 44) * waveform)
        for idx in range(28):
            x = left + 22 + idx * 10
            if x > left + 22 + usable:
                break
            bar_h = 12 + abs(math.sin(idx * 0.55 + t * math.pi * 3)) * 24
            draw.rounded_rectangle((x, center_y - bar_h / 2, x + 6, center_y + bar_h / 2), radius=3, fill=(142, 214, 209, 255))


def draw_end_scene(draw: ImageDraw.ImageDraw, progress: float) -> None:
    overlay_alpha = int(180 * smoothstep(progress))
    draw.rounded_rectangle((80, 120, WIDTH - 80, HEIGHT - 120), radius=42, fill=(10, 16, 28, overlay_alpha))
    draw_centered_text(draw, (WIDTH / 2, 250), "念念 Eterna", FONT_HERO, (248, 243, 238, int(255 * smoothstep(progress))))
    draw_centered_text(draw, (WIDTH / 2, 336), "不是替代，是陪伴。不是虚拟，是延续。", FONT_TITLE, (243, 196, 161, int(255 * smoothstep(progress))))
    draw_centered_text(draw, (WIDTH / 2, 412), "把声音、记忆、问候和一段关系，留在你想回来的地方。", FONT_SUBTITLE, (220, 222, 227, int(245 * smoothstep(progress))))
    cta_alpha = int(255 * smoothstep((progress - 0.22) / 0.55))
    draw.rounded_rectangle((WIDTH / 2 - 170, 494, WIDTH / 2 + 170, 554), radius=30, fill=(243, 196, 161, cta_alpha))
    draw_centered_text(draw, (WIDTH / 2, 524), "创建亲人的数字生命", FONT_SUBTITLE, (22, 14, 10, cta_alpha))


def render_frame(frame_index: int) -> np.ndarray:
    t = frame_index / FPS
    progress = frame_index / max(1, TOTAL_FRAMES - 1)

    base = make_gradient_background(progress)
    draw = ImageDraw.Draw(base, "RGBA")

    left_title_alpha = int(255 * smoothstep((t - 0.3) / 1.0))
    subtitle_alpha = int(220 * smoothstep((t - 0.8) / 1.0))

    if t < 4:
        draw_person_silhouette(draw, progress)
        draw.text((470, 182), "离开之后，最难的", font=FONT_HERO, fill=(247, 244, 240, left_title_alpha))
        draw.text((470, 266), "是那些没人回应的日常。", font=FONT_HERO, fill=(247, 244, 240, int(left_title_alpha * 0.9)))
        draw.text((470, 372), "夜深的时候，很多话只是在心里来回讲给自己听。", font=FONT_SUBTITLE, fill=(214, 216, 222, subtitle_alpha))

    elif t < 8:
        draw_person_silhouette(draw, progress)
        inner = phone_frame(draw, 822, 82)
        draw_notification(draw, inner, (t - 4.1) / 1.2)
        draw.text((110, 188), "直到有一天，", font=FONT_TITLE, fill=(247, 244, 239, 255))
        draw_multiline(
            draw,
            110,
            254,
            [
                "你在一个安静的夜里收到提醒：",
                "“想她的时候，可以来这里说说话。”"
            ],
            FONT_SUBTITLE,
            (218, 219, 223, 240),
            spacing=12,
        )

    elif t < 12:
        inner = phone_frame(draw, 826, 64)
        draw_form_scene(draw, inner, (t - 8) / 4)
        draw.text((102, 154), "你先留下名字、关系和口头禅，", font=FONT_TITLE, fill=(247, 244, 239, 255))
        draw.text((102, 214), "把她最像她的地方，留在一个入口里。", font=FONT_TITLE, fill=(247, 244, 239, 255))
        draw_multiline(
            draw,
            102,
            314,
            [
                "不需要一开始就很完整，",
                "只要先留下那句你一想到她就会想起的话。"
            ],
            FONT_SUBTITLE,
            (214, 216, 222, 242),
            spacing=12,
        )

    elif t < 18:
        inner = phone_frame(draw, 798, 66)
        draw_chat_scene(draw, inner, (t - 12) / 6)
        draw.text((94, 138), "当你说：", font=FONT_SUBTITLE, fill=(201, 206, 214, 255))
        draw.text((94, 188), "“今天有点想你，也有点累。”", font=FONT_HERO, fill=(248, 244, 240, 255))
        draw_multiline(
            draw,
            94,
            316,
            [
                "念念会把她的口气、回忆和关心，",
                "重新带回到那一刻。"
            ],
            FONT_TITLE,
            (243, 196, 161, 250),
            spacing=14,
        )
        chips = ["语音克隆", "智能对话", "回忆触发", "节日提醒"]
        for idx, label in enumerate(chips):
            x = 94 + (idx % 2) * 134
            y = 474 + (idx // 2) * 50
            draw.rounded_rectangle((x, y, x + 116, y + 34), radius=17, fill=(255, 255, 255, 18), outline=(255, 255, 255, 22), width=1)
            draw.text((x + 16, y + 9), label, font=FONT_SMALL, fill=(225, 226, 229, 255))

    elif t < 21:
        orbit_progress = smoothstep((t - 18) / 3)
        draw_centered_text(draw, (WIDTH / 2, 130), "不只是对话，还能把纪念继续做下去。", FONT_TITLE, (247, 244, 239, 255))
        for idx, (label, desc, angle) in enumerate([
            ("生日祝福", "在她生日那天，替她先说一句“今天要开心”。", -0.8),
            ("声音相册", "把熟悉的叮嘱留成一段可以反复点开的声音。", 0.0),
            ("回忆影像", "把老照片、短视频和故事，整理成可回看的纪念片段。", 0.9),
        ]):
            cx = WIDTH / 2 + math.cos(angle + orbit_progress * 0.25) * 290
            cy = 390 + math.sin(angle + orbit_progress * 0.25) * 160
            w = 264
            h = 120
            draw.rounded_rectangle((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), radius=24, fill=(18, 28, 47, 218), outline=(255, 255, 255, 22), width=1)
            draw.text((cx - 104, cy - 34), label, font=FONT_SUBTITLE, fill=(243, 196, 161, 255))
            draw_multiline(draw, cx - 104, cy + 2, [desc], FONT_CAPTION, (214, 216, 222, 235), spacing=6)
        draw.ellipse((WIDTH / 2 - 105, 305, WIDTH / 2 + 105, 515), fill=(16, 24, 40, 240), outline=(243, 196, 161, 48), width=2)
        draw_centered_text(draw, (WIDTH / 2, 384), "念念", FONT_TITLE, (248, 244, 239, 255))
        draw_centered_text(draw, (WIDTH / 2, 430), "把思念留在日常里", FONT_SUBTITLE, (220, 222, 227, 240))

    else:
        draw_end_scene(draw, (t - 21) / 3)

    return np.array(base.convert("RGB"))


def main() -> None:
    poster_frame = None
    with imageio.get_writer(
        VIDEO_PATH,
        fps=FPS,
        codec="libx264",
        ffmpeg_params=["-pix_fmt", "yuv420p", "-crf", "24"],
    ) as writer:
        for idx in range(TOTAL_FRAMES):
            frame = render_frame(idx)
            writer.append_data(frame)
            if idx == TOTAL_FRAMES // 2:
                poster_frame = frame

    if poster_frame is not None:
        Image.fromarray(poster_frame).save(POSTER_PATH, quality=92)

    print(f"Generated {VIDEO_PATH}")
    print(f"Generated {POSTER_PATH}")


if __name__ == "__main__":
    main()
