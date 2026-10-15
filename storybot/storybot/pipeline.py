"""Clips in -> finished vertical stories out."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from .analyze import analyze
from .config import Config
from .music import pick_track
from .plan import choose_unit, group_stories, select_shots, snap_to_beats
from .render import render_story

log = logging.getLogger("storybot")


def make_stories(files: list[str], out_dir: Path, cfg: Config) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    analyses = []
    for f in files:
        log.info("analysing %s", f)
        analyses.append(analyze(f))
    infos = [a.info for a in analyses]

    track = pick_track(cfg)

    shots = []
    for i, an in enumerate(analyses):
        shots += select_shots(an, i, cfg)
    if not shots:
        log.warning("nothing usable in %s", files)
        return []
    if track:
        unit = choose_unit(track.bpm)
        shots = snap_to_beats(shots, unit, [i.duration for i in infos])
    stories = group_stories(shots, cfg)

    outs: list[Path] = []
    cum = 0.0
    for n, story in enumerate(stories, 1):
        dur = sum(s.dur for s in story)
        out = out_dir / f"story_{n:02d}.mp4"
        offset = 0.0
        if track:   # music runs on across stories, always on the beat grid
            offset = (track.first_beat + cum) % track.duration
        log.info("rendering %s: %d shots, %.1f s", out.name, len(story), dur)
        render_story(story, infos, track, offset, out, cfg)
        outs.append(out)
        cum += dur

    (out_dir / "plan.json").write_text(json.dumps({
        "sources": files,
        "music": track.credit if track else None,
        "bpm": track.bpm if track else None,
        "stories": [[asdict(s) for s in st] for st in stories],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    if track and track.url:
        (out_dir / "credits.txt").write_text(
            f"Music: {track.credit}\n", encoding="utf-8")
    return outs
