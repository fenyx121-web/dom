"""Who may use the bot, and each person's own settings. Stored as small JSON files."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from .config import Config

MOODS = {
    "energetic": "energetic+upbeat",
    "chill": "chill+lounge",
    "travel": "travel+summer",
    "party": "party+dance",
    "calm": "ambient+calm",
}
DEFAULTS = {"fit": "crop", "length": 30, "mood": "energetic", "music": True}


class UserStore:
    def __init__(self, cfg: Config):
        self.dir = Path(cfg.data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.admins = set(cfg.admin_ids)
        self._users_f = self.dir / "users.json"
        self._set_f = self.dir / "settings.json"
        self.users: set[int] = set(self._read(self._users_f, []))
        self.settings: dict[str, dict] = self._read(self._set_f, {})

    @staticmethod
    def _read(path: Path, default):
        return json.loads(path.read_text()) if path.exists() else default

    # access
    def is_allowed(self, uid: int) -> bool:
        return uid in self.admins or uid in self.users

    def is_admin(self, uid: int) -> bool:
        return uid in self.admins

    def add(self, uid: int) -> None:
        self.users.add(uid)
        self._users_f.write_text(json.dumps(sorted(self.users)))

    def remove(self, uid: int) -> bool:
        if uid not in self.users:
            return False
        self.users.discard(uid)
        self._users_f.write_text(json.dumps(sorted(self.users)))
        return True

    # settings
    def get(self, uid: int) -> dict:
        return {**DEFAULTS, **self.settings.get(str(uid), {})}

    def set(self, uid: int, key: str, value) -> None:
        self.settings.setdefault(str(uid), {})[key] = value
        self._set_f.write_text(json.dumps(self.settings, ensure_ascii=False))

    def config_for(self, uid: int, base: Config) -> Config:
        """The pipeline config this person's videos are edited with."""
        s = self.get(uid)
        target = float(min(max(s["length"], 15), base.story_max))
        return replace(
            base,
            fit=s["fit"],
            story_target=target,
            story_min=min(base.story_min, target),
            music_tags=MOODS.get(s["mood"], base.music_tags),
            music_source=base.music_source if s["music"] else "none",
        )
