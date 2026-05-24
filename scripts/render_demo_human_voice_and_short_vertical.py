from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import render_demo_voiceover_variants as base  # noqa: E402


OUTPUT_DIR = ROOT / "output" / "video"
WORK_DIR = ROOT / "tmp" / "human_voice_variants"
SOURCE_VIDEO = ROOT / "frontend" / "assets" / "eterna-demo-v2.mp4"

VOICE_NAME = "Tingting"
VOICE_RATE = "158"

LONG_HORIZONTAL = OUTPUT_DIR / "eterna-demo-cn-human-subtitled.mp4"
LONG_VERTICAL = OUTPUT_DIR / "eterna-demo-cn-human-subtitled-vertical.mp4"
LONG_HORIZONTAL_POSTER = OUTPUT_DIR / "eterna-demo-cn-human-subtitled-poster.jpg"
LONG_VERTICAL_POSTER = OUTPUT_DIR / "eterna-demo-cn-human-subtitled-vertical-poster.jpg"
LONG_SRT = OUTPUT_DIR / "eterna-demo-cn-human-subtitles.srt"
LONG_AUDIO = WORK_DIR / "eterna-demo-cn-human-voiceover.m4a"
LONG_HORIZONTAL_SILENT = WORK_DIR / "eterna-demo-cn-human-subtitled-silent.mp4"
LONG_VERTICAL_SILENT = WORK_DIR / "eterna-demo-cn-human-subtitled-vertical-silent.mp4"

SHORT_SOURCE = WORK_DIR / "eterna-demo-vertical-15s-source.mp4"
SHORT_VERTICAL = OUTPUT_DIR / "eterna-demo-cn-human-subtitled-vertical-15s.mp4"
SHORT_VERTICAL_POSTER = OUTPUT_DIR / "eterna-demo-cn-human-subtitled-vertical-15s-poster.jpg"
SHORT_SRT = OUTPUT_DIR / "eterna-demo-cn-human-subtitles-vertical-15s.srt"
SHORT_AUDIO = WORK_DIR / "eterna-demo-cn-human-voiceover-15s.m4a"
SHORT_VERTICAL_SILENT = WORK_DIR / "eterna-demo-cn-human-subtitled-vertical-15s-silent.mp4"


@dataclass
class HumanCue:
    start: float
    end: float
    subtitle_text: str
    voice_text: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def build_human_voice_segments(cues: list[HumanCue], prefix: str) -> list[Path]:
    outputs: list[Path] = []
    for idx, cue in enumerate(cues):
        raw_path = WORK_DIR / f"{prefix}_cue_{idx:02d}_raw.aiff"
        out_path = WORK_DIR / f"{prefix}_cue_{idx:02d}.wav"
        subprocess.run(
            [
                "say",
                "-v",
                VOICE_NAME,
                "-r",
                VOICE_RATE,
                "-o",
                str(raw_path),
                cue.voice_text,
            ],
            check=True,
        )
        raw_duration = base.probe_duration(raw_path)
        target_duration = max(0.28, cue.duration - 0.05)
        tempo = raw_duration / target_duration
        fade_out_start = max(0.08, target_duration - 0.09)
        audio_filter = ",".join(
            [
                base.atempo_chain(tempo),
                f"apad=pad_dur={target_duration:.3f}",
                f"atrim=0:{target_duration:.3f}",
                "volume=1.62",
                "afade=t=in:st=0:d=0.03",
                f"afade=t=out:st={fade_out_start:.3f}:d=0.09",
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
        outputs.append(out_path)
    return outputs


def mix_voiceover(source_video: Path, cues: list[HumanCue], segments: list[Path], out_audio: Path, bg_volume: float) -> None:
    cmd = ["ffmpeg", "-y", "-i", str(source_video)]
    for seg in segments:
        cmd += ["-i", str(seg)]

    filters = [f"[0:a]volume={bg_volume:.2f}[bg]"]
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
        str(out_audio),
    ]
    run(cmd)


def mux(video_path: Path, audio_path: Path, out_path: Path) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
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
            str(out_path),
        ]
    )


def build_short_source() -> None:
    segments = [
        (0.0, 2.3),
        (7.9, 10.7),
        (11.9, 14.5),
        (15.7, 18.2),
        (18.9, 20.8),
        (21.0, 23.9),
    ]
    parts = []
    concat_inputs: list[str] = []
    for idx, (start, end) in enumerate(segments):
        parts.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{idx}]")
        parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{idx}]")
        concat_inputs.extend([f"[v{idx}]", f"[a{idx}]"])
    filter_complex = ";".join(parts + [f"{''.join(concat_inputs)}concat=n={len(segments)}:v=1:a=1[v][a]"])
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(SOURCE_VIDEO),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(SHORT_SOURCE),
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


def render_with_base(source_video: Path, cues: list[HumanCue], renderer, output_silent: Path) -> None:
    old_source = base.SOURCE_VIDEO
    try:
        base.SOURCE_VIDEO = source_video
        base.render_variant(output_silent, [base.Cue(c.start, c.end, c.subtitle_text) for c in cues], renderer)
    finally:
        base.SOURCE_VIDEO = old_source


def main() -> None:
    ensure_dirs()

    long_cues = [
        HumanCue(0.0, 3.9, "离开之后，最难的，是那些没人回应的日常。", "离开之后……最难的，是那些没人回应的日常。"),
        HumanCue(3.9, 7.9, "夜深的时候，很多话，只能在心里来回讲给自己听。", "夜深的时候，很多话，只能在心里，来回讲给自己听。"),
        HumanCue(7.9, 11.9, "直到有一天，念念把她最像她的地方，留在一个你可以回来的入口里。", "直到有一天，念念把她最像她的地方，留在一个你可以回来的入口里。"),
        HumanCue(11.9, 15.7, "你说，今天有点想你，也有点累。", "你说……今天有点想你，也有点累。"),
        HumanCue(15.7, 18.9, "念念会把她的口气、回忆和关心，重新带回到那一刻。", "念念会把她的口气、回忆和关心，重新带回到那一刻。"),
        HumanCue(18.9, 21.0, "生日祝福，声音相册，回忆影像，继续把纪念留在日常里。", "生日祝福，声音相册，回忆影像，继续把纪念留在日常里。"),
        HumanCue(21.0, 24.0, "念念 Eterna。不是替代，是陪伴。不是虚拟，是延续。", "念念 Eterna。不是替代，是陪伴。不是虚拟，是延续。"),
    ]

    short_cues = [
        HumanCue(0.0, 2.3, "离开之后，最难的，是那些没人回应的日常。", "离开之后……最难的，是那些没人回应的日常。"),
        HumanCue(2.3, 5.1, "直到有一天，念念把她留在一个你可以回来的入口里。", "直到有一天，念念把她留在一个你可以回来的入口里。"),
        HumanCue(5.1, 7.7, "你说，今天有点想你，也有点累。", "你说……今天有点想你，也有点累。"),
        HumanCue(7.7, 10.2, "念念会把她的口气和关心，重新带回到那一刻。", "念念会把她的口气和关心，重新带回到那一刻。"),
        HumanCue(10.2, 12.1, "生日祝福，声音相册，回忆影像。", "生日祝福，声音相册，回忆影像。"),
        HumanCue(12.1, 15.0, "念念 Eterna。不是替代，是陪伴。不是虚拟，是延续。", "念念 Eterna。不是替代，是陪伴。不是虚拟，是延续。"),
    ]

    LONG_SRT.write_text(base.to_srt([base.Cue(c.start, c.end, c.subtitle_text) for c in long_cues]), encoding="utf-8")
    SHORT_SRT.write_text(base.to_srt([base.Cue(c.start, c.end, c.subtitle_text) for c in short_cues]), encoding="utf-8")

    long_segments = build_human_voice_segments(long_cues, "long_human")
    mix_voiceover(SOURCE_VIDEO, long_cues, long_segments, LONG_AUDIO, bg_volume=0.15)
    render_with_base(SOURCE_VIDEO, long_cues, base.render_horizontal_frame, LONG_HORIZONTAL_SILENT)
    render_with_base(SOURCE_VIDEO, long_cues, base.render_vertical_frame, LONG_VERTICAL_SILENT)
    mux(LONG_HORIZONTAL_SILENT, LONG_AUDIO, LONG_HORIZONTAL)
    mux(LONG_VERTICAL_SILENT, LONG_AUDIO, LONG_VERTICAL)
    export_poster(LONG_HORIZONTAL, LONG_HORIZONTAL_POSTER, "00:00:12")
    export_poster(LONG_VERTICAL, LONG_VERTICAL_POSTER, "00:00:12")

    build_short_source()
    short_segments = build_human_voice_segments(short_cues, "short_human")
    mix_voiceover(SHORT_SOURCE, short_cues, short_segments, SHORT_AUDIO, bg_volume=0.22)
    render_with_base(SHORT_SOURCE, short_cues, base.render_vertical_frame, SHORT_VERTICAL_SILENT)
    mux(SHORT_VERTICAL_SILENT, SHORT_AUDIO, SHORT_VERTICAL)
    export_poster(SHORT_VERTICAL, SHORT_VERTICAL_POSTER, "00:00:07")

    print(LONG_HORIZONTAL)
    print(LONG_VERTICAL)
    print(SHORT_VERTICAL)


if __name__ == "__main__":
    main()
