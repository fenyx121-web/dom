"""One-at-a-time editing queue, independent of Telegram (so it can be tested)."""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from .config import Config
from .pipeline import make_stories
from .users import UserStore

log = logging.getLogger("storybot.jobs")


class Messenger(Protocol):
    async def send_text(self, chat_id: int, text: str) -> None: ...
    async def send_video(self, chat_id: int, path: Path, caption: str) -> None: ...


@dataclass
class Job:
    user_id: int
    chat_id: int
    files: list[str]
    work_dir: Path
    id: int = 0


class JobQueue:
    def __init__(self, cfg: Config, users: UserStore, messenger: Messenger,
                 maker: Callable = make_stories):
        self.cfg, self.users, self.msg, self.maker = cfg, users, messenger, maker
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self.running: Job | None = None
        self._n = 0
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        self._worker = asyncio.create_task(self._run())

    def new_work_dir(self, user_id: int) -> Path:
        self._n += 1
        d = Path(self.cfg.data_dir) / "jobs" / str(user_id) / f"{int(time.time())}_{self._n}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def waiting(self) -> int:
        return self.queue.qsize() + (1 if self.running else 0)

    async def submit(self, job: Job) -> None:
        ahead = self.waiting
        await self.queue.put(job)
        if ahead:
            await self.msg.send_text(job.chat_id, f"В очереди: перед вами {ahead}. Начну, как освобожусь.")
        else:
            await self.msg.send_text(job.chat_id, f"Монтирую {len(job.files)} видео...")

    async def _run(self) -> None:
        while True:
            job = await self.queue.get()
            self.running = job
            try:
                await self._process(job)
            except Exception:
                log.exception("job failed")
                await self._safe_text(job.chat_id, "Не получилось смонтировать. Попробуйте другое видео.")
            finally:
                self.running = None
                self.queue.task_done()

    async def _process(self, job: Job) -> None:
        cfg = self.users.config_for(job.user_id, self.cfg)
        out_dir = job.work_dir / "out"
        outs = await asyncio.to_thread(self.maker, job.files, out_dir, cfg)
        if not outs:
            await self.msg.send_text(job.chat_id, "В этих видео не нашёл ничего пригодного (слишком тёмно?).")
            return
        credit = ""
        cf = out_dir / "credits.txt"
        if cf.exists():
            credit = cf.read_text(encoding="utf-8").strip()
        await self.msg.send_text(job.chat_id, f"Готово: {len(outs)} сторис.")
        for i, o in enumerate(outs, 1):
            cap = f"Сторис {i}/{len(outs)}"
            if credit:
                cap += f"\n{credit}"
            await self.msg.send_video(job.chat_id, o, cap)

    async def _safe_text(self, chat_id: int, text: str) -> None:
        try:
            await self.msg.send_text(chat_id, text)
        except Exception:
            log.exception("could not notify user")


def cleanup(cfg: Config, now: float | None = None) -> int:
    """Delete job folders older than keep_hours."""
    now = now or time.time()
    root = Path(cfg.data_dir) / "jobs"
    removed = 0
    if not root.is_dir():
        return 0
    for user_dir in root.iterdir():
        for d in user_dir.iterdir():
            if d.is_dir() and now - d.stat().st_mtime > cfg.keep_hours * 3600:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return removed


class Batcher:
    """Collect items per key; hand the batch over after `quiet` seconds of silence."""

    def __init__(self, quiet: float, on_ready: Callable[[object, list, object], Awaitable[None]]):
        self.quiet, self.on_ready = quiet, on_ready
        self._b: dict = {}

    def open(self, key, ctx):
        """Start (or join) the batch synchronously, so concurrent uploads share one."""
        return self._b.setdefault(key, {"items": [], "ctx": ctx, "task": None})["ctx"]

    def count(self, key) -> int:
        b = self._b.get(key)
        return len(b["items"]) if b else 0

    def add(self, key, item) -> int:
        b = self._b[key]
        b["items"].append(item)
        if b["task"]:
            b["task"].cancel()
        b["task"] = asyncio.create_task(self._wait(key))
        return len(b["items"])

    def discard_if_empty(self, key) -> None:
        b = self._b.get(key)
        if b and not b["items"]:
            del self._b[key]

    async def _wait(self, key) -> None:
        await asyncio.sleep(self.quiet)
        await self.flush(key)

    async def flush(self, key) -> None:
        b = self._b.pop(key, None)
        if not b:
            return
        if b["task"] and b["task"] is not asyncio.current_task():
            b["task"].cancel()
        if b["items"]:
            await self.on_ready(key, b["items"], b["ctx"])
