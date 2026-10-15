"""Music: pick a track (Jamendo CC-licensed or local folder) and find its beat grid."""
from __future__ import annotations

import logging
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import Config

log = logging.getLogger("storybot.music")

SR, NFFT, HOP = 22050, 1024, 256


@dataclass
class Track:
    path: str
    title: str
    artist: str
    license: str
    url: str
    duration: float
    bpm: float
    first_beat: float   # seconds from track start

    @property
    def credit(self) -> str:
        return f"{self.title} - {self.artist} ({self.license}) {self.url}".strip()


def detect_beats(path: str, max_sec: float = 90.0) -> tuple[float, float]:
    """Return (bpm, first_beat_seconds). numpy-only onset + comb-filter estimate."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-t", str(max_sec), "-vn", "-ac", "1",
         "-ar", str(SR), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    if len(x) < NFFT * 8:
        raise ValueError("track too short for beat detection")
    win = np.hanning(NFFT).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(x, NFFT)[::HOP] * win
    mag = np.abs(np.fft.rfft(frames, axis=1))
    logmag = np.log1p(10 * mag)
    flux = np.maximum(0, np.diff(logmag, axis=0)).sum(axis=1)
    fps = SR / HOP
    k = int(0.4 * fps)
    flux = np.maximum(0, flux - np.convolve(flux, np.ones(k) / k, mode="same"))
    n = len(flux)

    # tempo from autocorrelation with a soft prior around 115 bpm
    f = np.fft.rfft(flux, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    lags = np.arange(n)
    with np.errstate(divide="ignore"):
        bpm_of = 60 * fps / lags
    ok = (bpm_of >= 70) & (bpm_of <= 180)
    prior = np.exp(-0.5 * (np.log2(np.where(ok, bpm_of, 1) / 115) / 0.6) ** 2)
    best = int(np.argmax(np.where(ok, ac * prior, -1)))
    p0 = float(best)
    if 0 < best < n - 1:  # parabolic refinement
        a, b, c = ac[best - 1], ac[best], ac[best + 1]
        d = a - 2 * b + c
        if d != 0:
            p0 += 0.5 * (a - c) / d

    # refine period and phase with a comb over the whole envelope
    best_s, best_p, best_ph = -1.0, p0, 0.0
    for p in p0 * (1 + np.linspace(-0.02, 0.02, 41)):
        for ph in np.arange(0, p, 0.5):
            idx = (ph + np.arange(int((n - ph) / p)) * p).astype(int)
            s = float(flux[idx].sum()) / len(idx)
            if s > best_s:
                best_s, best_p, best_ph = s, float(p), float(ph)
    bpm = 60 * fps / best_p
    first = (best_ph * HOP + NFFT / 2) / SR
    beat_len = 60 / bpm
    if first > beat_len:  # keep first beat inside the first beat period
        first -= int(first / beat_len) * beat_len
    return bpm, first


def _duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def _make_track(path: str, title: str, artist: str, lic: str, url: str) -> Track:
    bpm, first = detect_beats(path)
    return Track(path, title, artist, lic, url, _duration(path), bpm, first)


def _local(cfg: Config) -> Track | None:
    d = Path(cfg.music_dir)
    files = [p for p in d.glob("*") if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".flac", ".ogg")] if d.is_dir() else []
    if not files:
        return None
    p = random.choice(files)
    return _make_track(str(p), p.stem, "local file", "own library", "")


def _jamendo(cfg: Config) -> Track | None:
    import requests

    if not cfg.jamendo_client_id:
        return None
    base = {"client_id": cfg.jamendo_client_id, "format": "json", "limit": 50,
            "audioformat": "mp32", "order": "popularity_week",
            "vocalinstrumental": "instrumental", "speed": "high+veryhigh"}
    results = []
    for tags in (cfg.music_tags, ""):
        params = dict(base)
        if tags:
            params["fuzzytags"] = tags
        r = requests.get("https://api.jamendo.com/v3.0/tracks/", params=params, timeout=30)
        r.raise_for_status()
        j = r.json()
        if j["headers"].get("status") != "success":
            raise RuntimeError(f"Jamendo: {j['headers'].get('error_message')}")
        results = [t for t in j["results"]
                   if t.get("audiodownload_allowed") and t.get("audiodownload")
                   and "-nc" not in t.get("license_ccurl", "")   # commercial use ok
                   and "-nd" not in t.get("license_ccurl", "")]  # may be synced to video
        if results:
            break
    if not results:
        return None
    t = random.choice(results)
    cache = Path(cfg.cache_dir) / "music"
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"jamendo_{t['id']}.mp3"
    if not dest.exists():
        with requests.get(t["audiodownload"], stream=True, timeout=60) as r:
            r.raise_for_status()
            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    fh.write(chunk)
            tmp.rename(dest)
    lic = t.get("license_ccurl", "").replace("http://creativecommons.org/licenses/", "CC ").rstrip("/")
    return _make_track(str(dest), t["name"], t["artist_name"], lic or "Jamendo",
                       t.get("shareurl", f"https://www.jamendo.com/track/{t['id']}"))


def pick_track(cfg: Config) -> Track | None:
    src = cfg.music_source
    if src == "none":
        return None
    order = {"auto": ("jamendo", "local"), "jamendo": ("jamendo",), "local": ("local",)}[src]
    for name in order:
        try:
            tr = _jamendo(cfg) if name == "jamendo" else _local(cfg)
        except Exception as e:  # network, bad key, undecodable file...
            log.warning("music source %s failed: %s", name, e)
            continue
        if tr:
            log.info("music: %s | %.1f bpm", tr.credit, tr.bpm)
            return tr
    log.warning("no music available - stories will have original sound only")
    return None
