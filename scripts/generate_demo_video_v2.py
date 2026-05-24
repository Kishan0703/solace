from __future__ import annotations

import json
import math
import subprocess
import wave
from pathlib import Path
from typing import Iterable

import imageio.v2 as imageio
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "frontend" / "assets"
CREATIVE_DIR = ROOT / "creative"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

WIDTH = 1280
HEIGHT = 720
FPS = 24
DURATION = 24.0
TOTAL_FRAMES = int(FPS * DURATION)

VIDEO_PATH = ASSETS_DIR / "eterna-demo-v2.mp4"
VIDEO_SILENT_PATH = ASSETS_DIR / "eterna-demo-v2-silent.mp4"
POSTER_PATH = ASSETS_DIR / "eterna-demo-v2-poster.jpg"
MUSIC_PATH = ASSETS_DIR / "eterna-demo-v2-music.wav"
VTT_PATH = ASSETS_DIR / "eterna-demo-v2-voiceover.vtt"
MUSIC_MAP_PATH = CREATIVE_DIR / "eterna-brand-film-music.json"

FONT_CJK = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_CJK_MEDIUM = "/System/Library/Fonts/STHeiti Medium.ttc"

SAMPLE_RATE = 44100
MASTER_GAIN = 0.54


def load_font(size: int, medium: bool = False) -> ImageFont.FreeTypeFont:
    font_path = FONT_CJK_MEDIUM if medium else FONT_CJK
    return ImageFont.truetype(font_path, size=size)


FONT_HERO = load_font(68, medium=True)
FONT_TITLE = load_font(44, medium=True)
FONT_SUBTITLE = load_font(27)
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


def beat_pulse(t: float, strength: float = 1.0) -> float:
    beat_length = 0.75
    phase = (t % beat_length) / beat_length
    return math.exp(-phase * 6.5) * strength


def create_background(progress: float, t: float) -> Image.Image:
    top = np.array([9, 17, 31], dtype=np.float32)
    bottom = np.array([6, 11, 20], dtype=np.float32)
    warm = np.array([218, 132, 112], dtype=np.float32)
    cold = np.array([88, 150, 165], dtype=np.float32)
    center = np.array([243, 196, 161], dtype=np.float32)

    base = top * (1 - Y_COORD[..., None]) + bottom * Y_COORD[..., None]
    warm_glow = np.exp(-(((X_COORD - 0.2) ** 2) * 18 + ((Y_COORD - 0.18) ** 2) * 14))[..., None]
    cold_glow = np.exp(-(((X_COORD - 0.83) ** 2) * 22 + ((Y_COORD - 0.74) ** 2) * 18))[..., None]
    center_glow = np.exp(-(((X_COORD - 0.5) ** 2) * 16 + ((Y_COORD - 0.52) ** 2) * 12))[..., None]
    pulse = 0.86 + beat_pulse(t, 0.22) + 0.06 * math.sin(progress * math.pi * 2.8)

    color = (
        base
        + warm * warm_glow * (0.24 * pulse)
        + cold * cold_glow * 0.18
        + center * center_glow * (0.03 + beat_pulse(t, 0.08))
    )

    vignette = np.clip(((X_COORD - 0.5) ** 2 + (Y_COORD - 0.55) ** 2) * 1.95, 0, 0.42)[..., None]
    color *= (1 - vignette)
    rgba = np.concatenate(
        [np.clip(color, 0, 255).astype(np.uint8), np.full((HEIGHT, WIDTH, 1), 255, dtype=np.uint8)],
        axis=2,
    )
    return Image.fromarray(rgba)


def draw_text_block(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    lines: Iterable[str],
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    spacing: int = 10,
) -> None:
    cursor = y
    for line in lines:
        draw.text((x, cursor), line, font=font, fill=fill)
        bbox = draw.textbbox((x, cursor), line, font=font)
        cursor += (bbox[3] - bbox[1]) + spacing


def draw_center_text(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    x = cx - (bbox[2] - bbox[0]) / 2
    y = cy - (bbox[3] - bbox[1]) / 2
    draw.text((x, y), text, font=font, fill=fill)


def draw_person_silhouette(draw: ImageDraw.ImageDraw, progress: float, t: float) -> None:
    base_y = 530 + math.sin(progress * math.pi * 2.0) * 4
    breathing = math.sin(t * math.pi * 0.8) * 3
    draw.rounded_rectangle((120, base_y - 160 + breathing, 250, base_y + 70), radius=50, fill=(16, 21, 33, 244))
    draw.ellipse((144, base_y - 232 + breathing, 228, base_y - 146 + breathing), fill=(15, 19, 28, 248))
    draw.rounded_rectangle((170, base_y - 98 + breathing, 208, base_y + 126), radius=22, fill=(15, 19, 28, 248))
    draw.rounded_rectangle((100, 108, 384, 522), radius=40, outline=(255, 255, 255, 18), width=2)
    for drop in range(10):
        x = 116 + drop * 27 + (progress * 90 + drop * 17) % 22
        y = 150 + (progress * 260 + drop * 43) % 310
        draw.line((x, y, x - 7, y + 30), fill=(255, 255, 255, 34), width=1)


def phone_frame(draw: ImageDraw.ImageDraw, x: int, y: int, beat: float, w: int = 320, h: int = 592) -> tuple[int, int, int, int]:
    glow = int(22 + beat * 36)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=42, fill=(12, 18, 30, 236), outline=(255, 255, 255, 34 + glow // 2), width=2)
    draw.rounded_rectangle((x + 14, y + 14, x + w - 14, y + h - 14), radius=32, fill=(15, 24, 40, 255))
    draw.rounded_rectangle((x + 112, y + 20, x + w - 112, y + 36), radius=8, fill=(34, 40, 54, 255))
    return (x + 26, y + 56, x + w - 26, y + h - 26)


def draw_notification(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], appear: float) -> None:
    if appear <= 0:
        return
    eased = ease_in_out(appear)
    y_offset = int((1 - eased) * -30)
    alpha = int(220 * eased)
    x0, y0, x1, _ = box
    card = (x0 + 20, y0 + 20 + y_offset, x1 - 20, y0 + 126 + y_offset)
    draw.rounded_rectangle(card, radius=24, fill=(28, 34, 52, alpha), outline=(255, 255, 255, 24), width=1)
    draw.ellipse((card[0] + 18, card[1] + 18, card[0] + 50, card[1] + 50), fill=(243, 196, 161, alpha))
    draw.text((card[0] + 64, card[1] + 16), "念念", font=FONT_BODY, fill=(245, 242, 236, alpha))
    draw.text((card[0] + 64, card[1] + 45), "你想她的时候，可以来这里说说话。", font=FONT_CAPTION, fill=(216, 214, 212, alpha))


def draw_form_scene(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], t: float, beat: float) -> None:
    x0, y0, x1, _ = box
    draw.text((x0, y0), "创建亲人的数字生命", font=FONT_SUBTITLE, fill=(245, 244, 240, 255))
    labels = [
        ("亲人的名字", "妈妈"),
        ("与你的关系", "母亲"),
        ("ta 的口头禅", "早点休息，别熬夜。"),
    ]
    cursor = y0 + 56
    for idx, (label, value) in enumerate(labels):
        reveal = smoothstep((t - idx * 0.12) / 0.34)
        y_shift = int((1 - reveal) * 20)
        alpha = int(255 * reveal)
        draw.text((x0, cursor + y_shift), label, font=FONT_SMALL, fill=(190, 196, 206, alpha))
        draw.rounded_rectangle(
            (x0, cursor + 26 + y_shift, x1, cursor + 84 + y_shift),
            radius=18,
            fill=(23, 33, 52, alpha),
            outline=(255, 255, 255, 18 + int(beat * 10)),
            width=1,
        )
        draw.text((x0 + 18, cursor + 44 + y_shift), value, font=FONT_BODY, fill=(247, 245, 241, alpha))
        cursor += 100

    trait_alpha = int(255 * smoothstep((t - 0.3) / 0.38))
    draw.text((x0, cursor), "ta 的性格特点", font=FONT_SMALL, fill=(190, 196, 206, trait_alpha))
    draw.rounded_rectangle((x0, cursor + 26, x1, cursor + 144), radius=18, fill=(23, 33, 52, trait_alpha), outline=(255, 255, 255, 18), width=1)
    draw_text_block(
        draw,
        x0 + 18,
        cursor + 44,
        ["温柔、细腻，", "总会先问你今天累不累。"],
        FONT_BODY,
        (247, 245, 241, trait_alpha),
        spacing=8,
    )

    chip_reveal = smoothstep((t - 0.46) / 0.34)
    chips = [
        ("语音克隆", (x0 + 8, y0 + 360)),
        ("回忆对话", (x0 + 124, y0 + 392)),
        ("生日提醒", (x0 + 34, y0 + 438)),
    ]
    for idx, (label, (cx, cy)) in enumerate(chips):
        alpha = int(235 * clamp(chip_reveal - idx * 0.08, 0, 1))
        if alpha <= 0:
            continue
        draw.rounded_rectangle((cx, cy, cx + 110, cy + 36), radius=18, fill=(243, 196, 161, alpha))
        draw.text((cx + 16, cy + 10), label, font=FONT_SMALL, fill=(24, 17, 11, alpha))


def draw_chat_scene(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], t: float, beat: float) -> None:
    x0, y0, x1, _ = box
    draw.rounded_rectangle((x0, y0, x1, y0 + 78), radius=24, fill=(22, 31, 50, 255))
    draw.ellipse((x0 + 16, y0 + 16, x0 + 62, y0 + 62), fill=(243, 196, 161, 255))
    draw.text((x0 + 78, y0 + 18), "妈妈", font=FONT_SUBTITLE, fill=(246, 244, 241, 255))
    draw.text((x0 + 78, y0 + 46), "AI 克隆会在这里保留熟悉的口气", font=FONT_SMALL, fill=(182, 191, 204, 255))

    user_reveal = smoothstep(t / 0.22)
    if user_reveal > 0:
        alpha = int(255 * user_reveal)
        draw.rounded_rectangle((x0 + 16, y0 + 108, x0 + 256, y0 + 164), radius=22, fill=(238, 167, 123, alpha))
        draw.text((x0 + 28, y0 + 127), "今天有点想你，也有点累。", font=FONT_BODY, fill=(28, 19, 14, alpha))

    typing_reveal = smoothstep((t - 0.22) / 0.18)
    if 0 < typing_reveal < 1:
        alpha = int(230 * typing_reveal)
        draw.rounded_rectangle((x0 + 116, y0 + 196, x1 - 22, y0 + 254), radius=24, fill=(35, 45, 66, alpha))
        active = int((t * 16) % 3)
        for idx in range(3):
            cx = x0 + 150 + idx * 18
            radius = 4 + (1 if idx == active else 0)
            draw.ellipse((cx, y0 + 220, cx + radius * 2, y0 + 220 + radius * 2), fill=(243, 196, 161, alpha))

    reply_reveal = smoothstep((t - 0.38) / 0.28)
    if reply_reveal > 0:
        alpha = int(255 * reply_reveal)
        draw.rounded_rectangle(
            (x0 + 96, y0 + 196, x1 - 20, y0 + 348),
            radius=24,
            fill=(35, 45, 66, alpha),
            outline=(243, 196, 161, min(90, 28 + int(beat * 65))),
            width=1,
        )
        draw_text_block(
            draw,
            x0 + 114,
            y0 + 216,
            [
                "早点休息，别熬夜。",
                "我知道你是想我了。",
                "你已经做得很好了。"
            ],
            FONT_BODY,
            (246, 244, 241, alpha),
            spacing=8,
        )

    waveform = smoothstep((t - 0.64) / 0.24)
    if waveform > 0:
        left = x0 + 18
        top = y0 + 384
        right = x1 - 18
        bottom = y0 + 476
        draw.rounded_rectangle((left, top, right, bottom), radius=20, fill=(22, 31, 50, 255), outline=(255, 255, 255, 20), width=1)
        draw.text((left + 14, top + 12), "声音纪念", font=FONT_SMALL, fill=(193, 199, 207, 255))
        center_y = top + 56
        usable = int((right - left - 44) * waveform)
        for idx in range(32):
            x = left + 22 + idx * 8
            if x > left + 22 + usable:
                break
            bar_h = 10 + abs(math.sin(idx * 0.64 + t * math.pi * 2.6)) * (16 + beat * 12)
            draw.rounded_rectangle((x, center_y - bar_h / 2, x + 5, center_y + bar_h / 2), radius=3, fill=(142, 214, 209, 255))


def draw_services_scene(draw: ImageDraw.ImageDraw, t: float, beat: float) -> None:
    orbit_progress = smoothstep(t / 3.0)
    draw_center_text(draw, WIDTH // 2, 130, "不只是对话，还要把纪念继续做下去。", FONT_TITLE, (247, 244, 239, 255))
    cards = [
        ("生日祝福", "在她生日那天，替她先说一句“今天要开心”。", -0.82),
        ("声音相册", "把熟悉的叮嘱留成一段可以反复点开的声音。", 0.04),
        ("回忆影像", "把老照片、短视频和故事，整理成可回看的纪念片段。", 0.9),
    ]
    for idx, (label, desc, angle) in enumerate(cards):
        reveal = clamp(orbit_progress - idx * 0.1, 0, 1)
        if reveal <= 0:
            continue
        cx = WIDTH / 2 + math.cos(angle + orbit_progress * 0.22) * 292
        cy = 396 + math.sin(angle + orbit_progress * 0.22) * 160
        w = 270
        h = 124
        alpha = int(225 * reveal)
        border = min(90, 22 + int(beat * 65))
        draw.rounded_rectangle((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), radius=24, fill=(18, 28, 47, alpha), outline=(255, 255, 255, border), width=1)
        draw.text((cx - 104, cy - 34), label, font=FONT_SUBTITLE, fill=(243, 196, 161, alpha))
        draw_text_block(draw, int(cx - 104), int(cy + 2), [desc], FONT_CAPTION, (214, 216, 222, alpha), spacing=6)
    glow = int(28 + beat * 80)
    draw.ellipse((WIDTH / 2 - 105, 305, WIDTH / 2 + 105, 515), fill=(16, 24, 40, 242), outline=(243, 196, 161, glow), width=2)
    draw_center_text(draw, WIDTH // 2, 384, "念念", FONT_TITLE, (248, 244, 239, 255))
    draw_center_text(draw, WIDTH // 2, 430, "把思念留在日常里", FONT_SUBTITLE, (220, 222, 227, 242))


def draw_end_scene(draw: ImageDraw.ImageDraw, progress: float, beat: float) -> None:
    overlay_alpha = int(186 * smoothstep(progress))
    draw.rounded_rectangle((80, 120, WIDTH - 80, HEIGHT - 120), radius=42, fill=(10, 16, 28, overlay_alpha))
    draw_center_text(draw, WIDTH // 2, 248, "念念 Eterna", FONT_HERO, (248, 243, 238, int(255 * smoothstep(progress))))
    draw_center_text(draw, WIDTH // 2, 336, "不是替代，是陪伴。不是虚拟，是延续。", FONT_TITLE, (243, 196, 161, int(255 * smoothstep(progress))))
    draw_center_text(draw, WIDTH // 2, 412, "把声音、记忆、问候和一段关系，留在你想回来的地方。", FONT_SUBTITLE, (220, 222, 227, int(246 * smoothstep(progress))))
    cta_alpha = int(255 * smoothstep((progress - 0.18) / 0.52))
    border_alpha = min(100, 22 + int(beat * 78))
    draw.rounded_rectangle((WIDTH / 2 - 170, 494, WIDTH / 2 + 170, 554), radius=30, fill=(243, 196, 161, cta_alpha), outline=(255, 255, 255, border_alpha), width=1)
    draw_center_text(draw, WIDTH // 2, 524, "创建亲人的数字生命", FONT_SUBTITLE, (22, 14, 10, cta_alpha))


def render_frame(frame_index: int) -> np.ndarray:
    t = frame_index / FPS
    progress = frame_index / max(1, TOTAL_FRAMES - 1)
    beat = beat_pulse(t, 1.0)

    base = create_background(progress, t)
    draw = ImageDraw.Draw(base, "RGBA")

    title_alpha = int(255 * smoothstep((t - 0.25) / 0.8))
    subtitle_alpha = int(228 * smoothstep((t - 0.72) / 0.92))

    if t < 4:
        draw_person_silhouette(draw, progress, t)
        draw.text((470, 182), "离开之后，最难的", font=FONT_HERO, fill=(247, 244, 240, title_alpha))
        draw.text((470, 266), "是那些没人回应的日常。", font=FONT_HERO, fill=(247, 244, 240, int(title_alpha * 0.92)))
        draw.text((470, 372), "夜深的时候，很多话，只能在心里来回讲给自己听。", font=FONT_SUBTITLE, fill=(214, 216, 222, subtitle_alpha))

    elif t < 8:
        draw_person_silhouette(draw, progress, t)
        inner = phone_frame(draw, 822, 82, beat)
        draw_notification(draw, inner, (t - 4.05) / 1.15)
        draw.text((110, 188), "直到有一天，", font=FONT_TITLE, fill=(247, 244, 239, 255))
        draw_text_block(
            draw,
            110,
            254,
            [
                "你在一个安静的夜里收到提醒：",
                "“想她的时候，可以来这里说说话。”"
            ],
            FONT_SUBTITLE,
            (218, 219, 223, 242),
            spacing=12,
        )

    elif t < 12:
        inner = phone_frame(draw, 826, 64, beat)
        draw_form_scene(draw, inner, (t - 8) / 4, beat)
        draw.text((102, 154), "你先留下名字、关系和口头禅，", font=FONT_TITLE, fill=(247, 244, 239, 255))
        draw.text((102, 214), "把她最像她的地方，留在一个入口里。", font=FONT_TITLE, fill=(247, 244, 239, 255))
        draw_text_block(
            draw,
            102,
            314,
            [
                "不需要一开始就很完整，",
                "只要先留下那句你一想到她就会想起的话。"
            ],
            FONT_SUBTITLE,
            (214, 216, 222, 244),
            spacing=12,
        )

    elif t < 18:
        inner = phone_frame(draw, 796, 54, beat)
        draw_chat_scene(draw, inner, (t - 12) / 6, beat)
        draw.text((94, 138), "当你说：", font=FONT_SUBTITLE, fill=(201, 206, 214, 255))
        draw.text((94, 188), "“今天有点想你，也有点累。”", font=FONT_HERO, fill=(248, 244, 240, 255))
        draw_text_block(
            draw,
            94,
            316,
            [
                "念念会把她的口气、回忆和关心，",
                "重新带回到那一刻。"
            ],
            FONT_TITLE,
            (243, 196, 161, 252),
            spacing=14,
        )
        chips = ["语音克隆", "智能对话", "回忆触发", "节日提醒"]
        for idx, label in enumerate(chips):
            x = 94 + (idx % 2) * 134
            y = 504 + (idx // 2) * 50
            border = min(80, 20 + int(beat * 54))
            draw.rounded_rectangle((x, y, x + 116, y + 34), radius=17, fill=(255, 255, 255, 18), outline=(255, 255, 255, border), width=1)
            draw.text((x + 16, y + 9), label, font=FONT_SMALL, fill=(225, 226, 229, 255))

    elif t < 21:
        draw_services_scene(draw, t - 18, beat)

    else:
        draw_end_scene(draw, (t - 21) / 3, beat)

    flare_alpha = int(beat * 34)
    if flare_alpha > 0:
        draw.ellipse((WIDTH // 2 - 260, HEIGHT // 2 - 260, WIDTH // 2 + 260, HEIGHT // 2 + 260), outline=(243, 196, 161, flare_alpha), width=2)

    return np.array(base.convert("RGB"))


def note_frequency(note: str) -> float:
    notes = {
        "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
        "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
        "A#": 10, "Bb": 10, "B": 11
    }
    if len(note) == 3:
        pitch, octave = note[:2], int(note[2])
    else:
        pitch, octave = note[0], int(note[1])
    midi = (octave + 1) * 12 + notes[pitch]
    return 440.0 * (2 ** ((midi - 69) / 12))


def adsr(length: int, attack: float, decay: float, sustain: float, release: float) -> np.ndarray:
    attack_len = max(1, int(length * attack))
    decay_len = max(1, int(length * decay))
    release_len = max(1, int(length * release))
    sustain_len = max(1, length - attack_len - decay_len - release_len)

    envelope = np.concatenate([
        np.linspace(0.0, 1.0, attack_len, endpoint=False),
        np.linspace(1.0, sustain, decay_len, endpoint=False),
        np.full(sustain_len, sustain),
        np.linspace(sustain, 0.0, release_len, endpoint=True),
    ])
    if envelope.size < length:
        envelope = np.pad(envelope, (0, length - envelope.size))
    return envelope[:length]


def synth_tone(freq: float, duration: float, volume: float = 1.0, harmonic_mix: tuple[float, ...] = (1.0, 0.3, 0.12)) -> np.ndarray:
    length = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, length, endpoint=False)
    signal = np.zeros_like(t)
    for idx, amp in enumerate(harmonic_mix, start=1):
        signal += amp * np.sin(2 * math.pi * freq * idx * t)
    signal /= max(1.0, sum(abs(v) for v in harmonic_mix))
    signal *= adsr(length, attack=0.04, decay=0.18, sustain=0.55, release=0.24)
    return signal * volume


def synth_pad(freqs: Iterable[float], duration: float, volume: float = 0.38) -> np.ndarray:
    freqs = list(freqs)
    length = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, length, endpoint=False)
    signal = np.zeros_like(t)
    for idx, freq in enumerate(freqs):
        detune = 1.0 + (idx - 1) * 0.003
        wave = np.sin(2 * math.pi * freq * detune * t)
        wave += 0.4 * np.sin(2 * math.pi * freq * 0.5 * t)
        signal += wave
    signal /= max(1, len(freqs))
    signal *= adsr(length, attack=0.12, decay=0.18, sustain=0.72, release=0.25)
    return signal * volume


def add_clip(track: np.ndarray, clip: np.ndarray, start_seconds: float) -> None:
    start = int(start_seconds * SAMPLE_RATE)
    end = min(track.size, start + clip.size)
    track[start:end] += clip[: end - start]


def build_music_track() -> np.ndarray:
    total_samples = int(SAMPLE_RATE * DURATION)
    track = np.zeros(total_samples, dtype=np.float32)

    chords = [
        ["A3", "C4", "E4"],
        ["F3", "A3", "C4", "E4"],
        ["C3", "E3", "G3", "B3"],
        ["G3", "A3", "D4"],
        ["A3", "C4", "E4"],
        ["F3", "A3", "C4", "E4"],
        ["C3", "E3", "G3", "B3"],
        ["G3", "B3", "D4"],
    ]
    beat_interval = 0.75
    bar_duration = 3.0

    # Pads per bar.
    for idx, chord in enumerate(chords):
        freqs = [note_frequency(note) for note in chord]
        pad = synth_pad(freqs, bar_duration, volume=0.24 if idx < 2 else 0.31)
        add_clip(track, pad, idx * bar_duration)

    # Piano pulses per beat.
    melody = [
        "A4", "C5", "E5", "C5",
        "F4", "A4", "C5", "A4",
        "C4", "E4", "G4", "E4",
        "D4", "G4", "A4", "D5",
        "A4", "C5", "E5", "G5",
        "F4", "A4", "C5", "E5",
        "C4", "G4", "B4", "E5",
        "G4", "B4", "D5", "G5",
    ]
    for beat_idx, note in enumerate(melody):
        freq = note_frequency(note)
        pulse = synth_tone(freq, 0.58, volume=0.22 if beat_idx < 8 else 0.27)
        add_clip(track, pulse, beat_idx * beat_interval)

    # Warm bass on strong beats from scene 4 onward.
    bass_notes = ["A2", "F2", "C2", "G2", "A2", "F2", "C2", "G2"]
    for idx, note in enumerate(bass_notes):
        start = idx * bar_duration
        vol = 0.0 if idx < 2 else (0.14 if idx < 4 else 0.2)
        if vol <= 0:
            continue
        bass = synth_tone(note_frequency(note), 0.9, volume=vol, harmonic_mix=(1.0, 0.18, 0.05))
        add_clip(track, bass, start)

    # Chime accents following the beat map cues.
    chime_points = [4.5, 6.0, 6.75, 7.5, 9.0, 12.0, 13.5, 15.0, 16.5, 18.0, 18.75, 19.5, 21.0, 22.5]
    chime_notes = ["E5", "A5", "C6", "E6", "A5", "C6", "E6", "G5", "E5", "A5", "C6", "E6", "G5", "A5"]
    for timepoint, note in zip(chime_points, chime_notes):
        chime = synth_tone(note_frequency(note), 0.34, volume=0.16, harmonic_mix=(1.0, 0.58, 0.22, 0.08))
        add_clip(track, chime, timepoint)

    # Noise swell for scene transitions.
    noise = np.random.default_rng(42).normal(0, 1, total_samples).astype(np.float32)
    noise = np.convolve(noise, np.ones(400) / 400, mode="same")
    envelope = np.zeros(total_samples, dtype=np.float32)
    for point in [3.6, 7.7, 11.6, 17.7, 20.8]:
        center = int(point * SAMPLE_RATE)
        width = int(0.65 * SAMPLE_RATE)
        start = max(0, center - width // 2)
        end = min(total_samples, center + width // 2)
        env_t = np.linspace(0, 1, end - start)
        envelope[start:end] += np.sin(env_t * math.pi) * 0.03
    track += noise * envelope

    # Master shaping.
    fade = np.ones(total_samples, dtype=np.float32)
    fade_in = int(0.8 * SAMPLE_RATE)
    fade_out = int(1.2 * SAMPLE_RATE)
    fade[:fade_in] = np.linspace(0.0, 1.0, fade_in)
    fade[-fade_out:] = np.linspace(1.0, 0.0, fade_out)
    track *= fade

    peak = np.max(np.abs(track)) or 1.0
    track = np.tanh(track / peak * 1.45) * MASTER_GAIN
    return track


def save_wav(path: Path, audio: np.ndarray) -> None:
    audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(audio_int16.tobytes())


def mux_audio_video(video_path: Path, audio_path: Path, output_path: Path) -> None:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def create_vtt() -> None:
    content = """WEBVTT

00:00.000 --> 00:03.900
离开之后，最难的，是那些没人回应的日常。

00:03.900 --> 00:07.900
夜深的时候，很多话，只能在心里来回讲给自己听。

00:07.900 --> 00:11.900
直到有一天，念念把她最像她的地方，留在一个你可以回来的入口里。

00:11.900 --> 00:15.700
你说，今天有点想你，也有点累。

00:15.700 --> 00:18.900
念念会把她的口气、回忆和关心，重新带回到那一刻。

00:18.900 --> 00:21.000
生日祝福，声音相册，回忆影像，继续把纪念留在日常里。

00:21.000 --> 00:24.000
念念 Eterna。不是替代，是陪伴。不是虚拟，是延续。
"""
    VTT_PATH.write_text(content, encoding="utf-8")


def main() -> None:
    CREATIVE_DIR.mkdir(parents=True, exist_ok=True)
    poster_frame = None
    with imageio.get_writer(
        VIDEO_SILENT_PATH,
        fps=FPS,
        codec="libx264",
        ffmpeg_params=["-pix_fmt", "yuv420p", "-crf", "22"],
    ) as writer:
        for idx in range(TOTAL_FRAMES):
            frame = render_frame(idx)
            writer.append_data(frame)
            if idx == int(TOTAL_FRAMES * 0.56):
                poster_frame = frame

    if poster_frame is not None:
        Image.fromarray(poster_frame).save(POSTER_PATH, quality=92)

    audio = build_music_track()
    save_wav(MUSIC_PATH, audio)
    mux_audio_video(VIDEO_SILENT_PATH, MUSIC_PATH, VIDEO_PATH)
    create_vtt()
    VIDEO_SILENT_PATH.unlink(missing_ok=True)

    if MUSIC_MAP_PATH.exists():
        payload = json.loads(MUSIC_MAP_PATH.read_text(encoding="utf-8"))
        payload["generated_music_asset"] = str(MUSIC_PATH.name)
        payload["generated_video_asset"] = str(VIDEO_PATH.name)
        MUSIC_MAP_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Generated {VIDEO_PATH}")
    print(f"Generated {POSTER_PATH}")
    print(f"Generated {MUSIC_PATH}")
    print(f"Generated {VTT_PATH}")


if __name__ == "__main__":
    main()
