"""Telegram front-end: friends send clips, the bot sends back finished stories."""
from __future__ import annotations

import asyncio
import functools
import logging
import re
from pathlib import Path

from telegram import BotCommand, Update
from telegram.error import BadRequest
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Config
from .jobs import Batcher, Job, JobQueue, cleanup
from .users import DEFAULTS, MOODS, UserStore

log = logging.getLogger("storybot.telegram")

HELP = (
    "Пришлите видео с очков (можно несколько подряд), и через пару минут я пришлю "
    "готовые сторис 9:16 с музыкой.\n\n"
    "/settings: ваши настройки\n"
    "/fit crop|blur: на весь экран или целиком на размытом фоне\n"
    "/length 15-60: длина одной сторис в секундах\n"
    "/mood energetic|chill|travel|party|calm: настроение музыки\n"
    "/music on|off: музыка вкл/выкл\n"
    "/go: не ждать, начать монтаж сейчас\n"
    "/myid: ваш Telegram ID"
)
ADMIN_HELP = "\n\nАдмин:\n/adduser ID, /removeuser ID, /users"


class TgMessenger:
    def __init__(self, bot):
        self.bot = bot

    async def send_text(self, chat_id: int, text: str) -> None:
        await self.bot.send_message(chat_id, text)

    async def send_video(self, chat_id: int, path: Path, caption: str) -> None:
        with open(path, "rb") as f:
            await self.bot.send_video(chat_id, f, caption=caption, supports_streaming=True,
                                      width=1080, height=1920, write_timeout=600, read_timeout=600)


def allowed(fn):
    @functools.wraps(fn)
    async def wrap(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        users: UserStore = ctx.application.bot_data["users"]
        u = update.effective_user
        if not u or not update.message:
            return
        if not users.is_allowed(u.id):
            await update.message.reply_text(
                f"Нет доступа. Передайте администратору ваш ID: {u.id}")
            return
        await fn(update, ctx)
    return wrap


def admin_only(fn):
    @functools.wraps(fn)
    async def wrap(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        users: UserStore = ctx.application.bot_data["users"]
        if not users.is_admin(update.effective_user.id):
            await update.message.reply_text("Только для администратора.")
            return
        await fn(update, ctx)
    return wrap


def _fmt_settings(s: dict) -> str:
    return (f"Режим кадра: {s['fit']}\nДлина сторис: {s['length']} с\n"
            f"Музыка: {'вкл, ' + s['mood'] if s['music'] else 'выкл'}")


async def cmd_start(update: Update, ctx):
    users: UserStore = ctx.application.bot_data["users"]
    uid = update.effective_user.id
    if not users.is_allowed(uid):
        await update.message.reply_text(f"Нет доступа. Передайте администратору ваш ID: {uid}")
        return
    await update.message.reply_text(HELP + (ADMIN_HELP if users.is_admin(uid) else ""))


async def cmd_myid(update: Update, ctx):
    await update.message.reply_text(f"Ваш ID: {update.effective_user.id}")


@allowed
async def cmd_settings(update: Update, ctx):
    users: UserStore = ctx.application.bot_data["users"]
    await update.message.reply_text(_fmt_settings(users.get(update.effective_user.id)))


def _arg(ctx) -> str:
    return (ctx.args[0].lower() if ctx.args else "")


@allowed
async def cmd_fit(update: Update, ctx):
    users: UserStore = ctx.application.bot_data["users"]
    v = _arg(ctx)
    if v not in ("crop", "blur"):
        await update.message.reply_text("Напишите /fit crop (на весь экран) или /fit blur (целиком, фон размыт).")
        return
    users.set(update.effective_user.id, "fit", v)
    await update.message.reply_text(f"Режим кадра: {v}")


@allowed
async def cmd_length(update: Update, ctx):
    users: UserStore = ctx.application.bot_data["users"]
    v = _arg(ctx)
    if not v.isdigit() or not 15 <= int(v) <= 60:
        await update.message.reply_text("Напишите число от 15 до 60, например /length 30.")
        return
    users.set(update.effective_user.id, "length", int(v))
    await update.message.reply_text(f"Длина сторис: {int(v)} с")


@allowed
async def cmd_mood(update: Update, ctx):
    users: UserStore = ctx.application.bot_data["users"]
    v = _arg(ctx)
    if v not in MOODS:
        await update.message.reply_text("Доступно: " + ", ".join(MOODS) + ". Например: /mood chill")
        return
    users.set(update.effective_user.id, "mood", v)
    users.set(update.effective_user.id, "music", True)
    await update.message.reply_text(f"Настроение музыки: {v}")


@allowed
async def cmd_music(update: Update, ctx):
    users: UserStore = ctx.application.bot_data["users"]
    v = _arg(ctx)
    if v not in ("on", "off"):
        await update.message.reply_text("Напишите /music on или /music off.")
        return
    users.set(update.effective_user.id, "music", v == "on")
    await update.message.reply_text("Музыка включена." if v == "on" else "Музыка выключена.")


@admin_only
async def cmd_adduser(update: Update, ctx):
    users: UserStore = ctx.application.bot_data["users"]
    if not ctx.args or not re.fullmatch(r"\d+", ctx.args[0]):
        await update.message.reply_text("Напишите /adduser 123456789 (ID человека; он узнаёт его командой /myid).")
        return
    users.add(int(ctx.args[0]))
    await update.message.reply_text(f"Добавлен {ctx.args[0]}.")


@admin_only
async def cmd_removeuser(update: Update, ctx):
    users: UserStore = ctx.application.bot_data["users"]
    ok = bool(ctx.args) and ctx.args[0].isdigit() and users.remove(int(ctx.args[0]))
    await update.message.reply_text("Удалён." if ok else "Такого пользователя нет в списке.")


@admin_only
async def cmd_users(update: Update, ctx):
    users: UserStore = ctx.application.bot_data["users"]
    ids = sorted(users.admins | users.users)
    await update.message.reply_text("Доступ есть у: " + (", ".join(map(str, ids)) or "никого"))


@allowed
async def cmd_go(update: Update, ctx):
    batcher: Batcher = ctx.application.bot_data["batcher"]
    if batcher.count(update.effective_user.id) == 0:
        await update.message.reply_text("Пока нет видео. Пришлите их, потом /go.")
        return
    await batcher.flush(update.effective_user.id)


@allowed
async def on_video(update: Update, ctx):
    app = ctx.application
    batcher: Batcher = app.bot_data["batcher"]
    jq: JobQueue = app.bot_data["jobs"]
    m, uid = update.message, update.effective_user.id
    att = m.video
    if att is None and m.document and (m.document.mime_type or "").startswith("video/"):
        att = m.document
    if att is None:
        await m.reply_text("Пришлите видео (файлом или обычным видео).")
        return

    work = batcher.open(uid, jq.new_work_dir(uid))
    try:
        f = await att.get_file()
        name = re.sub(r"[^\w.\-]", "_", getattr(att, "file_name", None) or f"{att.file_unique_id}.mp4")
        dest = Path(work) / f"{m.message_id}_{name}"
        await f.download_to_drive(dest)
    except BadRequest as e:
        batcher.discard_if_empty(uid)
        if "too big" in str(e).lower():
            await m.reply_text("Файл больше 20 МБ, а у этого бота лимит Telegram 20 МБ. "
                               "Админу нужно подключить локальный сервер Telegram (см. README).")
        else:
            await m.reply_text("Не удалось скачать видео.")
        return
    except Exception:
        log.exception("download failed")
        batcher.discard_if_empty(uid)
        await m.reply_text("Не удалось скачать видео.")
        return

    n = batcher.add(uid, (m.message_id, str(dest), m.chat_id))
    if n == 1:
        await m.reply_text(f"Получил. Жду ещё видео {app.bot_data['cfg'].tg_batch_quiet_sec} с, "
                           "или напишите /go, чтобы начать сразу.")


async def on_text(update: Update, ctx):
    users: UserStore = ctx.application.bot_data["users"]
    u = update.effective_user
    if not u or not update.message:
        return
    if users.is_allowed(u.id):
        await update.message.reply_text("Пришлите видео, и я смонтирую сторис. Справка: /start")
    else:
        await update.message.reply_text(f"Нет доступа. Передайте администратору ваш ID: {u.id}")


async def _on_batch_ready(app: Application, uid: int, items: list, work: Path) -> None:
    items.sort()   # by message id = order of sending
    jq: JobQueue = app.bot_data["jobs"]
    await jq.submit(Job(uid, items[0][2], [i[1] for i in items], Path(work)))


async def _post_init(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    users = UserStore(cfg)
    jq = JobQueue(cfg, users, TgMessenger(app.bot))
    jq.start()
    app.bot_data.update(users=users, jobs=jq)
    app.bot_data["batcher"] = Batcher(
        cfg.tg_batch_quiet_sec, lambda uid, items, work: _on_batch_ready(app, uid, items, work))
    await app.bot.set_my_commands([
        BotCommand("settings", "мои настройки"), BotCommand("fit", "crop или blur"),
        BotCommand("length", "длина сторис 15-60"), BotCommand("mood", "настроение музыки"),
        BotCommand("music", "музыка вкл/выкл"), BotCommand("go", "начать монтаж сейчас"),
        BotCommand("myid", "мой ID"), BotCommand("start", "помощь")])

    async def janitor():
        while True:
            n = await asyncio.to_thread(cleanup, cfg)
            if n:
                log.info("cleaned %d old job folders", n)
            await asyncio.sleep(3600)
    app.bot_data["janitor"] = asyncio.create_task(janitor())


def build_app(cfg: Config) -> Application:
    b = (ApplicationBuilder().token(cfg.telegram_token).concurrent_updates(True)
         .connect_timeout(30).read_timeout(60).write_timeout(600).post_init(_post_init))
    if cfg.telegram_api_url:
        base = cfg.telegram_api_url.rstrip("/")
        b = b.base_url(f"{base}/bot").base_file_url(f"{base}/file/bot").local_mode(True)
    app = b.build()
    app.bot_data["cfg"] = cfg
    for name, fn in (("start", cmd_start), ("help", cmd_start), ("myid", cmd_myid),
                     ("settings", cmd_settings), ("fit", cmd_fit), ("length", cmd_length),
                     ("mood", cmd_mood), ("music", cmd_music), ("go", cmd_go),
                     ("adduser", cmd_adduser), ("removeuser", cmd_removeuser), ("users", cmd_users)):
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, on_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def run(cfg: Config) -> None:
    if not cfg.telegram_token:
        raise SystemExit("Нет токена: задайте TELEGRAM_BOT_TOKEN или telegram_token в config.json "
                         "(токен выдаёт @BotFather).")
    if not cfg.admin_ids:
        log.warning("Администратор не задан: бот никого не пустит. Напишите боту /myid "
                    "и впишите свой ID в ADMIN_IDS, затем перезапустите.")
    build_app(cfg).run_polling()
