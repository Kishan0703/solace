from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "frontend" / "assets"
OUTPUT_DIR = ROOT / "output" / "video"
WORK_DIR = ROOT / "tmp" / "voiceover_variants"

SOURCE_VIDEO = ASSETS_DIR / "eterna-demo-v2.mp4"
SOURCE_VTT = ASSETS_DIR / "eterna-demo-v2-voiceover.vtt"

VOICE_NAME = "Flo (中文（中国大陆）)"
VOICE_RATE = "175"
FPS = 24

HORIZONTAL_SILENT = WORK_DIR / "eterna-demo-cn-subtitled-silent.mp4"
VERTICAL_SILENT = WORK_DIR / "eterna-demo-cn-subtitled-vertical-silent.mp4"
MIXED_AUDIO = WORK_DIR / "eterna-demo-cn-voiceover.m4a"
OUTPUT_HORIZONTAL = OUTPUT_DIR / "eterna-demo-cn-subtitled.mp4"
OUTPUT_VERTICAL = OUTPUT_DIR / "eterna-demo-cn-subtitled-vertical.mp4"
OUTPUT_HORIZONTAL_POSTER = OUTPUT_DIR / "eterna-demo-cn-subtitled-poster.jpg"
OUTPUT_VERTICAL_POSTER = OUTPUT_DIR / "eterna-demo-cn-subtitled-vertical-poster.jpg"
OUTPUT_SRT = OUTPUT_DIR / "eterna-demo-cn-subtitles.srt"

FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_PATH_BOLD = "/System/Library/Fonts/Hiragino Sans GB.ttc"

COLOR_TEXT = "#F6F4F1"
COLOR_MUTED = "#C6CFDA"
COLOR_PRIMARY = "#F3C4A1"
COLOR_PANEL = "#122136"
COLOR_PANEL_DARK = "#0A1422"
COLOR_BORDER = "#294561"
COLOR_BG = "#08111F"


@dataclass
class Cue:
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    ).strip()
    return float(out)


def parse_timecode(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError(f"Unsupported timecode: {value}")


def parse_vtt(path: Path) -> list[Cue]:
    cues: list[Cue] = []
    blocks = path.read_text(encoding="utf-8").strip().split("\n\n")
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines or lines[0] == "WEBVTT":
            continue
        if "-->" not in lines[0]:
            continue
        start_raw, end_raw = [item.strip() for item in lines[0].split("-->")]
        text = " ".join(lines[1:]).strip()
        cues.append(Cue(parse_timecode(start_raw), parse_timecode(end_raw), text))
    return cues


def to_srt(cues: Iterable[Cue]) -> str:
    def srt_time(seconds: float) -> str:
        ms_total = int(round(seconds * 1000))
        hours, rem = divmod(ms_total, 3600_000)
        minutes, rem = divmod(rem, 60_000)
        secs, millis = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    blocks: list[str] = []
    for idx, cue in enumerate(cues, start=1):
        blocks.append(
            "\n".join(
                [
                    str(idx),
                    f"{srt_time(cue.start)} --> {srt_time(cue.end)}",
                    cue.text,
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def atempo_chain(tempo: float) -> str:
    tempo = max(0.5, min(tempo, 100.0))
    parts: list[str] = []
    while tempo > 2.0:
        parts.append("atempo=2.0")
        tempo /= 2.0
    while tempo < 0.5:
        parts.append("atempo=0.5")
        tempo /= 0.5
    parts.append(f"atempo={tempo:.5f}")
    return ",".join(parts)


def generate_voice_segments(cues: list[Cue]) -> list[Path]:
    segments: list[Path] = []
    for idx, cue in enumerate(cues):
        raw_path = WORK_DIR / f"cue_{idx:02d}_raw.aiff"
        out_path = WORK_DIR / f"cue_{idx:02d}.wav"
        subprocess.run(
            [
                "say",
                "-v",
                VOICE_NAME,
                "-r",
                VOICE_RATE,
                "-o",
                str(raw_path),
                cue.text,
            ],
            check=True,
        )
        raw_duration = probe_duration(raw_path)
        target_duration = max(0.35, cue.duration - 0.06)
        tempo = raw_duration / target_duration
        fade_out_start = max(0.1, target_duration - 0.08)
        audio_filter = ",".join(
            [
                atempo_chain(tempo),
                f"apad=pad_dur={target_duration:.3f}",
                f"atrim=0:{target_duration:.3f}",
                "volume=1.55",
                "afade=t=in:st=0:d=0.03",
                f"afade=t=out:st={fade_out_start:.3f}:d=0.08",
            ]
        )
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(raw_path),
                "-filter:a",
                audio_filter,
                "-ar",
                "44100",
                "-ac",
                "1",
                str(out_path),
            ]
        )
        segments.append(out_path)
    return segments


def build_mixed_audio(cues: list[Cue], segments: list[Path]) -> None:
    cmd = ["ffmpeg", "-y", "-i", str(SOURCE_VIDEO)]
    for path in segments:
        cmd += ["-i", str(path)]

    filters = ["[0:a]volume=0.17[bg]"]
    mix_inputs = ["[bg]"]
    for idx, cue in enumerate(cues, start=1):
        delay_ms = int(round(cue.start * 1000))
        filters.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms}[v{idx}]")
        mix_inputs.append(f"[v{idx}]")
    filters.append("".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:normalize=0[aout]")

    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(MIXED_AUDIO),
    ]
    run(cmd)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH_BOLD if bold else FONT_PATH, size=size)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
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
    return lines


def current_cue(cues: list[Cue], t: float) -> Cue | None:
    for cue in cues:
        if cue.start <= t < cue.end:
            return cue
    return None


def draw_subtitle_block(
    image: Image.Image,
    cue: Cue | None,
    *,
    box: tuple[int, int, int, int],
    font_size: int,
    align_center: bool = True,
) -> None:
    if cue is None:
        return

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = load_font(font_size, bold=True)
    x0, y0, x1, y1 = box
    lines = wrap_text(draw, cue.text, font, x1 - x0 - 52)
    line_height = draw.textbbox((0, 0), "你", font=font)[3] + 14
    block_height = max(92, len(lines) * line_height + 34)
    panel_y0 = y1 - block_height

    draw.rounded_rectangle(
        (x0, panel_y0, x1, y1),
        radius=28,
        fill=(10, 20, 34, 208),
        outline=(41, 69, 97, 220),
        width=2,
    )

    cursor_y = panel_y0 + 18
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        if align_center:
            tx = x0 + (x1 - x0 - (bbox[2] - bbox[0])) / 2
        else:
            tx = x0 + 24
        draw.text((tx, cursor_y), line, font=font, fill=COLOR_TEXT)
        cursor_y += line_height

    image.alpha_composite(overlay)


def render_horizontal_frame(frame: np.ndarray, cue: Cue | None) -> np.ndarray:
    image = Image.fromarray(frame).convert("RGBA")
    draw_subtitle_block(image, cue, box=(90, 572, 1190, 694), font_size=36)
    return np.array(image.convert("RGB"))


def render_vertical_frame(frame: np.ndarray, cue: Cue | None) -> np.ndarray:
    base = Image.new("RGBA", (1080, 1920), COLOR_BG)
    frame_img = Image.fromarray(frame).convert("RGBA")

    bg = frame_img.resize((1080, 1920), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(24))
    dark = Image.new("RGBA", (1080, 1920), (6, 12, 20, 138))
    base = Image.alpha_composite(bg, dark)

    overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.ellipse((-120, -160, 400, 320), fill=(243, 196, 161, 28))
    odraw.ellipse((760, 1460, 1220, 1940), fill=(142, 214, 209, 24))
    base.alpha_composite(overlay)

    header_draw = ImageDraw.Draw(base)
    header_draw.text((86, 112), "念念 Eterna", font=load_font(56, bold=True), fill=COLOR_TEXT)
    header_draw.text((88, 184), "AI 家庭纪念与数字陪伴 Demo", font=load_font(30), fill=COLOR_PRIMARY)

    card_w = 972
    card_h = int(card_w * 9 / 16)
    card_x = (1080 - card_w) // 2
    card_y = 430

    card_shadow = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(card_shadow)
    sdraw.rounded_rectangle((card_x + 8, card_y + 16, card_x + card_w + 8, card_y + card_h + 16), radius=42, fill=(0, 0, 0, 70))
    card_shadow = card_shadow.filter(ImageFilter.GaussianBlur(18))
    base.alpha_composite(card_shadow)

    frame_resized = frame_img.resize((card_w, card_h), Image.Resampling.LANCZOS)
    mask = Image.new("L", (card_w, card_h), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle((0, 0, card_w, card_h), radius=40, fill=255)
    base.paste(frame_resized, (card_x, card_y), mask)

    border = ImageDraw.Draw(base)
    border.rounded_rectangle((card_x, card_y, card_x + card_w, card_y + card_h), radius=40, outline=COLOR_PRIMARY, width=3)

    draw_subtitle_block(base, cue, box=(70, 1130, 1010, 1460), font_size=48)

    footer_draw = ImageDraw.Draw(base)
    footer_draw.text((86, 1546), "把记忆留在一个可以回来的入口里", font=load_font(34, bold=True), fill=COLOR_TEXT)
    footer_draw.text((86, 1620), "记忆归档 · 多模态互动 · 主动联系", font=load_font(28), fill=COLOR_MUTED)
    footer_draw.text((86, 1752), "适合微信视频号 / 抖音 / 路演转发", font=load_font(24), fill=COLOR_PRIMARY)

    return np.array(base.convert("RGB"))


def render_variant(output_path: Path, cues: list[Cue], renderer) -> None:
    reader = imageio.get_reader(str(SOURCE_VIDEO))
    writer = imageio.get_writer(
        str(output_path),
        fps=FPS,
        codec="libx264",
        macro_block_size=None,
        quality=8,
    )
    for idx, frame in enumerate(reader):
        t = idx / FPS
        cue = current_cue(cues, t)
        writer.append_data(renderer(frame, cue))
    writer.close()
    reader.close()


def mux_audio(video_path: Path, output_path: Path) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(MIXED_AUDIO),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            str(output_path),
        ]
    )


def export_poster(video_path: Path, out_path: Path, timestamp: str) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            timestamp,
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(out_path),
        ]
    )


def main() -> None:
    ensure_dirs()
    cues = parse_vtt(SOURCE_VTT)
    OUTPUT_SRT.write_text(to_srt(cues), encoding="utf-8")
    segments = generate_voice_segments(cues)
    build_mixed_audio(cues, segments)
    render_variant(HORIZONTAL_SILENT, cues, render_horizontal_frame)
    render_variant(VERTICAL_SILENT, cues, render_vertical_frame)
    mux_audio(HORIZONTAL_SILENT, OUTPUT_HORIZONTAL)
    mux_audio(VERTICAL_SILENT, OUTPUT_VERTICAL)
    export_poster(OUTPUT_HORIZONTAL, OUTPUT_HORIZONTAL_POSTER, "00:00:12")
    export_poster(OUTPUT_VERTICAL, OUTPUT_VERTICAL_POSTER, "00:00:12")
    print(OUTPUT_HORIZONTAL)
    print(OUTPUT_VERTICAL)
    print(OUTPUT_SRT)


if __name__ == "__main__":
    main()
