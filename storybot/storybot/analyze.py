"""Per-second 'interest' score of a clip from picture and sound."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

VW, VH, VFPS = 144, 256, 4   # analysis resolution / fps
ASR = 16000                  # analysis audio rate


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    has_audio: bool

    @property
    def aspect(self) -> float:
        return self.width / self.height


@dataclass
class Analysis:
    info: MediaInfo
    score: np.ndarray    # 0..1 per second
    speech: np.ndarray   # bool per second
    valid: np.ndarray    # bool per second (False = dark / lens covered)


def probe(path: str | Path) -> MediaInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise ValueError(f"{path}: no video stream")
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = int(v.get("tags", {}).get("rotate", rot))
    if abs(rot) % 180 == 90:
        w, h = h, w
    dur = float(data["format"].get("duration") or v.get("duration") or 0)
    has_audio = any(s["codec_type"] == "audio" for s in data["streams"])
    return MediaInfo(str(path), dur, w, h, has_audio)


def _per_sec(x: np.ndarray, k: int, nsec: int) -> np.ndarray:
    need = nsec * k
    if len(x) < need:
        pad = x[-1] if len(x) else 0.0
        x = np.concatenate([x, np.full(need - len(x), pad)])
    return x[:need].reshape(nsec, k).mean(axis=1)


def _visual(path: str, nsec: int):
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-vf", f"fps={VFPS},scale={VW}:{VH},format=gray",
           "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = VW * VH
    sharp, bright, motion = [], [], []
    prev = None
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        f = np.frombuffer(buf, np.uint8).reshape(VH, VW).astype(np.float32)
        c = f[1:-1, 1:-1]
        lap = 4 * c - f[:-2, 1:-1] - f[2:, 1:-1] - f[1:-1, :-2] - f[1:-1, 2:]
        sharp.append(float(lap.var()))
        bright.append(float(f.mean()))
        motion.append(0.0 if prev is None else float(np.abs(f - prev).mean()))
        prev = f
    proc.wait()
    if len(motion) > 1:
        motion[0] = motion[1]
    return (_per_sec(np.array(sharp), VFPS, nsec),
            _per_sec(np.array(bright), VFPS, nsec),
            _per_sec(np.array(motion), VFPS, nsec))


def _audio(path: str, nsec: int, has_audio: bool):
    db = np.full(nsec, -90.0)
    ratio = np.zeros(nsec)
    if not has_audio:
        return db, ratio
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(ASR),
         "-f", "s16le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    for s in range(nsec):
        seg = x[s * ASR:(s + 1) * ASR]
        if len(seg) < ASR // 2:
            continue
        db[s] = 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-6)
        p = np.abs(np.fft.rfft(seg)) ** 2
        freq = np.fft.rfftfreq(len(seg), 1 / ASR)
        band = (freq >= 300) & (freq <= 3400)
        ratio[s] = p[band].sum() / (p.sum() + 1e-12)
    return db, ratio


def _norm(x: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(x, 5), np.percentile(x, 95)
    if hi - lo < 1e-9:
        return np.full(len(x), 0.5)
    return np.clip((x - lo) / (hi - lo), 0, 1)


def analyze(path: str | Path) -> Analysis:
    info = probe(path)
    nsec = max(1, int(np.ceil(info.duration)))
    sharp, bright, motion = _visual(str(path), nsec)
    db, ratio = _audio(str(path), nsec, info.has_audio)

    floor = np.percentile(db, 10)
    active = (db > floor + 10) & (db > -45)
    speech = active & (ratio > 0.5)

    audio_i = 0.5 * _norm(db) + 0.5 * speech
    score = (0.4 * _norm(sharp) + 0.3 * audio_i + 0.3 * _norm(np.log1p(motion)))
    med = np.median(sharp)
    score = np.where(sharp < 0.3 * med, score * 0.5, score)              # blurry
    dead = (~active) & (_norm(np.log1p(motion)) < 0.2)
    score = np.where(dead, score * 0.6, score)                            # static + silent
    valid = bright >= 25                                                  # lens covered / pocket
    score = np.where(valid, score, 0.0)
    return Analysis(info, score, speech, valid)
