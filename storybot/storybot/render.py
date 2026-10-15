"""Render one story with ffmpeg: cut, fit to 9:16, concat, mix music, normalise."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .analyze import MediaInfo
from .config import Config
from .music import Track
from .plan import Shot

W, H, FPS = 1080, 1920, 30


def _fit(i: int, info: MediaInfo, fit: str) -> str:
    base = f"[{i}:v]fps={FPS},"
    if fit == "crop" or abs(info.aspect - W / H) < 0.02:
        return (f"{base}scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1[v{i}]")
    return (f"{base}split[bg{i}][fg{i}];"
            f"[bg{i}]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=25:3[bb{i}];"
            f"[fg{i}]scale={W}:{H}:force_original_aspect_ratio=decrease[fs{i}];"
            f"[bb{i}][fs{i}]overlay=(W-w)/2:(H-h)/2,setsar=1[v{i}]")


def build_command(story: list[Shot], infos: list[MediaInfo], track: Track | None,
                  music_offset: float, out: Path, cfg: Config) -> list[str]:
    cmd = ["ffmpeg", "-y", "-nostdin", "-v", "error"]
    parts: list[str] = []
    total = sum(s.dur for s in story)
    for i, sh in enumerate(story):
        cmd += ["-ss", f"{sh.start:.3f}", "-t", f"{sh.dur:.3f}", "-i", infos[sh.src].path]
        parts.append(_fit(i, infos[sh.src], cfg.fit))
        d = sh.dur
        if infos[sh.src].has_audio:
            parts.append(
                f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"apad=whole_dur={d:.3f},atrim=0:{d:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:d=0.02,afade=t=out:st={d - 0.02:.3f}:d=0.02[a{i}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{d:.3f},asetpts=PTS-STARTPTS[a{i}]")
    n = len(story)
    cat_in = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{cat_in}concat=n={n}:v=1:a=1[vc][ac]")
    parts.append("[vc]eq=contrast=1.04:saturation=1.12,format=yuv420p[vout]")

    speech = sum(s.speech * s.dur for s in story) / total
    if track is None:
        parts.append("[ac]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
    else:
        cmd += ["-stream_loop", "-1", "-ss", f"{music_offset:.3f}", "-i", track.path]
        m = n
        talk = speech > 0.2
        vol = cfg.music_volume_speech if talk else cfg.music_volume
        parts.append(
            f"[{m}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},"
            f"afade=t=in:d=0.3,atrim=0:{total:.3f},"
            f"afade=t=out:st={max(0.0, total - 1.0):.3f}:d=1.0[mus]")
        if talk:
            parts.append("[ac]asplit=2[o1][o2]")
            parts.append("[mus][o2]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[md]")
            parts.append("[o1][md]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            parts.append("[ac]volume=0.3[o1]")
            parts.append("[o1][mus]amix=inputs=2:duration=first:normalize=0[mix]")
        parts.append("[mix]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")

    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]",
            "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
            "-r", str(FPS), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-movflags", "+faststart", str(out)]
    return cmd


def render_story(story, infos, track, music_offset, out: Path, cfg: Config) -> None:
    cmd = build_command(story, infos, track, music_offset, out, cfg)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {out.name}:\n{r.stderr[-2000:]}")
