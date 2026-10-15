from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class Config:
    # folders
    inbox: str = "inbox"
    out: str = "out"
    music_dir: str = "music"
    cache_dir: str = "cache"
    state_file: str = "state/processed.json"
    # music: auto | jamendo | local | none
    music_source: str = "auto"
    jamendo_client_id: str = ""
    music_tags: str = "energetic+upbeat"
    music_volume: float = 0.85       # music level when nobody talks
    music_volume_speech: float = 0.22  # music level under speech (also ducked)
    # story structure, seconds
    story_min: float = 15.0
    story_target: float = 30.0
    story_max: float = 60.0
    # shot selection, seconds
    shot_min: float = 1.5
    shot_max: float = 6.0
    speech_shot_max: float = 15.0
    keep_ratio: float = 0.65         # share of "valid" footage to keep
    # picture: crop (fill 9:16) | blur (whole frame on blurred background)
    fit: str = "crop"
    # watcher
    merge_clips: bool = True         # one batch of new clips -> one timeline
    batch_quiet_sec: int = 45
    poll_sec: int = 5
    # encoding
    preset: str = "medium"
    crf: int = 20
    video_exts: tuple = (".mp4", ".mov", ".m4v")
    # telegram bot
    telegram_token: str = ""
    telegram_api_url: str = ""       # local Bot API server, e.g. http://localhost:8081 (lifts 20 MB limit)
    admin_ids: tuple = ()            # Telegram user ids allowed to manage users
    data_dir: str = "data"           # users, settings, job files
    tg_batch_quiet_sec: int = 15     # wait this long for more videos before editing
    keep_hours: int = 24             # delete job files after this long


def _load_dotenv(path: Path = Path(".env")) -> None:
    """KEY=VALUE lines from ./.env; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            os.environ.setdefault(k.strip(), v)


def load_config(path: str | None = None) -> Config:
    _load_dotenv()
    cfg = Config()
    p = Path(path) if path else Path("config.json")
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        names = {f.name for f in fields(Config)}
        unknown = set(data) - names
        if unknown:
            raise SystemExit(f"config: unknown keys {sorted(unknown)}")
        for k, v in data.items():
            setattr(cfg, k, tuple(v) if k in ("video_exts", "admin_ids") else v)
    for name, key in (("JAMENDO_CLIENT_ID", "jamendo_client_id"),
                      ("TELEGRAM_BOT_TOKEN", "telegram_token"),
                      ("TELEGRAM_API_URL", "telegram_api_url")):
        if os.environ.get(name):
            setattr(cfg, key, os.environ[name])
    if os.environ.get("ADMIN_IDS"):
        cfg.admin_ids = tuple(int(x) for x in os.environ["ADMIN_IDS"].replace(",", " ").split())
    return cfg
