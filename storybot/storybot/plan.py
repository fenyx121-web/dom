"""Choose shots, snap them to the beat grid and group them into stories."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .analyze import Analysis
from .config import Config


@dataclass
class Shot:
    src: int
    start: float
    end: float
    score: float
    speech: float   # share of seconds with speech

    @property
    def dur(self) -> float:
        return self.end - self.start


def _smooth(x: np.ndarray, w: int = 3) -> np.ndarray:
    k = np.ones(w)
    return np.convolve(x, k, "same") / np.convolve(np.ones_like(x), k, "same")


def select_shots(an: Analysis, src: int, cfg: Config) -> list[Shot]:
    n = len(an.score)
    valid = an.valid
    if not valid.any():
        return []
    s = _smooth(an.score)
    thr = np.quantile(s[valid], 1 - cfg.keep_ratio)
    keep = valid & (s >= thr)
    for i in range(1, n - 1):  # bridge one-second holes
        if not keep[i] and keep[i - 1] and keep[i + 1] and valid[i]:
            keep[i] = True

    runs: list[list[int]] = []
    i = 0
    while i < n:
        if keep[i]:
            j = i
            while j < n and keep[j]:
                j += 1
            runs.append([i, j])
            i = j
        else:
            i += 1

    for r in runs:  # don't cut a sentence in half
        ext = 0
        while r[1] < n and ext < 2 and an.speech[r[1] - 1] and an.speech[r[1]] and valid[r[1]]:
            r[1] += 1
            ext += 1
    merged: list[list[int]] = []
    for r in runs:
        if merged and r[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], r[1])
        else:
            merged.append(r)

    shots: list[Shot] = []
    dur = an.info.duration
    for a, b in merged:
        b = min(float(b), dur)
        if b - a < cfg.shot_min:
            continue
        sp = float(an.speech[a:int(math.ceil(b))].mean())
        lim = cfg.speech_shot_max if sp > 0.5 else cfg.shot_max
        parts = math.ceil((b - a) / lim)
        step = (b - a) / parts
        for k in range(parts):
            pa, pb = a + k * step, a + (k + 1) * step
            shots.append(Shot(src, pa, pb, float(s[int(pa):max(int(pa) + 1, int(math.ceil(pb)))].mean()), sp))
    return shots


def choose_unit(bpm: float, target: float = 2.0) -> float:
    """Cut length = 1/2/4/8 beats, whichever is closest to ~2 s."""
    beat = 60.0 / bpm
    return min((beat * k for k in (1, 2, 4, 8)), key=lambda u: abs(u - target))


def snap_to_beats(shots: list[Shot], unit: float, src_durs: list[float]) -> list[Shot]:
    """Make every shot a whole number of cut units, so cuts land on the beat.

    Talking shots are rounded up (never truncate a sentence), others to the nearest.
    A shot may grow only into footage that no other shot uses.
    """
    out: list[Shot] = []
    prev_end: dict[int, float] = {}
    for i, sh in enumerate(shots):
        if sh.speech > 0.5:
            n = math.ceil(sh.dur / unit - 1e-6)
        else:
            n = math.floor(sh.dur / unit + 0.5)
        n = max(1, n)
        want = n * unit
        nxt = shots[i + 1] if i + 1 < len(shots) else None
        limit = nxt.start if nxt and nxt.src == sh.src else src_durs[sh.src]
        floor_ = prev_end.get(sh.src, 0.0)
        end = min(sh.start + want, limit)
        # only talking shots may reach back (the lead-in before a sentence is fine)
        start = max(floor_, end - want) if sh.speech > 0.5 else sh.start
        if end - start < want - 1e-6:   # no room: drop whole units
            n = int((end - start) / unit + 1e-6)
            if n < 1:
                continue
            end = start + n * unit
        out.append(Shot(sh.src, start, end, sh.score, sh.speech))
        prev_end[sh.src] = end
    return out


def group_stories(shots: list[Shot], cfg: Config) -> list[list[Shot]]:
    stories: list[list[Shot]] = []
    cur: list[Shot] = []
    total = 0.0
    for sh in shots:
        if cur and total + sh.dur > cfg.story_max:
            stories.append(cur)
            cur, total = [], 0.0
        cur.append(sh)
        total += sh.dur
        if total >= cfg.story_target:
            stories.append(cur)
            cur, total = [], 0.0
    if cur:
        if total < cfg.story_min and stories:
            prev = sum(s.dur for s in stories[-1])
            if prev + total <= cfg.story_max:
                stories[-1].extend(cur)
                return stories
        if total >= 5 or not stories:
            stories.append(cur)
    return stories
