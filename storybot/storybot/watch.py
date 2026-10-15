"""Watch the inbox folder (a Google Drive / Dropbox / rclone sync target)."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from .config import Config
from .pipeline import make_stories

log = logging.getLogger("storybot")


def _key(p: Path) -> str:
    st = p.stat()
    return f"{p.name}:{st.st_size}:{int(st.st_mtime)}"


def _load(path: Path) -> set[str]:
    if path.exists():
        return set(json.loads(path.read_text()))
    return set()


def _save(path: Path, keys: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(keys)))


def watch(cfg: Config) -> None:
    inbox, out_root = Path(cfg.inbox), Path(cfg.out)
    inbox.mkdir(parents=True, exist_ok=True)
    state = Path(cfg.state_file)
    done = _load(state)
    sizes: dict[str, int] = {}
    first_seen: dict[str, float] = {}
    log.info("watching %s (Ctrl+C to stop)", inbox.resolve())

    while True:
        time.sleep(cfg.poll_sec)
        cands = [p for p in sorted(inbox.iterdir())
                 if p.is_file() and p.suffix.lower() in cfg.video_exts
                 and not p.name.startswith(".") and _key(p) not in done]
        if not cands:
            continue
        now = time.time()
        stable = True
        for p in cands:
            k, sz = str(p), p.stat().st_size
            if sizes.get(k) != sz or now - p.stat().st_mtime < 5:
                stable = False
            sizes[k] = sz
            first_seen.setdefault(k, now)
        newest = max(first_seen[str(p)] for p in cands)
        if not stable or now - newest < cfg.batch_quiet_sec:
            continue

        cands.sort(key=lambda p: (p.stat().st_mtime, p.name))
        batches = [cands] if cfg.merge_clips else [[p] for p in cands]
        for batch in batches:
            name = (datetime.now().strftime("%Y-%m-%d_%H-%M-%S") if cfg.merge_clips
                    else batch[0].stem)
            try:
                outs = make_stories([str(p) for p in batch], out_root / name, cfg)
                log.info("done: %d stories in %s", len(outs), out_root / name)
            except Exception:
                log.exception("failed on %s", [p.name for p in batch])
            for p in batch:   # also mark failures, never loop on a bad file
                done.add(_key(p))
            _save(state, done)
