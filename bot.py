"""Telegram-бот для закрепления «Арабский перед тобой», том 1, части 1 и 2.

Запуск: python bot.py   (токен берётся из переменной BOT_TOKEN или файла .env)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from db import DB
from engine import Engine, Resp

log = logging.getLogger("arabic_bot")
router = Router()
db = DB(config.DB_PATH)
engine = Engine(db, tz_name=str(config.TZ), today_fn=lambda: datetime.now(config.TZ).date())


def keyboard(rows) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    b = InlineKeyboardBuilder()
    for row in rows:
        for text, data in row:
            b.button(text=text, callback_data=data)
    b.adjust(*[len(r) for r in rows])
    return b.as_markup()


async def send(message: Message, resp: Resp) -> None:
    """Отправить ответ новым сообщением (для команд)."""
    for i, chunk in enumerate(resp.chunks):
        last = i == len(resp.chunks) - 1
        await message.answer(chunk, reply_markup=keyboard(resp.rows) if last else None)


async def edit(cb: CallbackQuery, resp: Resp) -> None:
    """Обновить сообщение с кнопкой; длинный текст добавляет следующими сообщениями."""
    if not resp.chunks:
        await cb.answer(resp.toast or "", show_alert=False)
        return
    first, rest = resp.chunks[0], resp.chunks[1:]
    markup = keyboard(resp.rows) if not rest else None
    try:
        await cb.message.edit_text(first, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            log.warning("edit failed (%s), sending new message", e)
            await cb.message.answer(first, reply_markup=markup)
    for i, chunk in enumerate(rest):
        last = i == len(rest) - 1
        await cb.message.answer(chunk, reply_markup=keyboard(resp.rows) if last else None)
    await cb.answer()


class AccessMiddleware(BaseMiddleware):
    """Если задан ALLOWED_USERS, бот отвечает только перечисленным пользователям."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if config.ALLOWED_USERS and (user is None or user.id not in config.ALLOWED_USERS):
            return None
        return await handler(event, data)


@router.message(CommandStart())
@router.message(Command("part"))
async def cmd_start(message: Message) -> None:
    """/start и /part: выбор части книги."""
    await send(message, engine.start_text(message.from_user.id))


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await send(message, engine.menu_text(message.from_user.id))


@router.message(Command("remind"))
async def cmd_remind(message: Message, command: CommandObject) -> None:
    await send(message, engine.set_reminder_from_text(message.from_user.id, command.args or ""))


@router.callback_query(F.data)
async def on_callback(cb: CallbackQuery) -> None:
    await edit(cb, engine.handle(cb.from_user.id, cb.data))


@router.message()
async def fallback(message: Message) -> None:
    await send(message, engine.start_text(message.from_user.id))


async def reminder_loop(bot: Bot) -> None:
    while True:
        try:
            now = datetime.now(config.TZ)
            for uid in engine.due_reminders(now):
                db.mark_sent(uid, now.date().isoformat())  # сначала отметка, чтобы не слать дважды
                resp = engine.reminder_payload(uid)
                try:
                    await bot.send_message(uid, resp.chunks[0], reply_markup=keyboard(resp.rows))
                except Exception as e:  # пользователь заблокировал бота и т. п.
                    log.warning("reminder to %s failed: %s", uid, e)
        except Exception:
            log.exception("reminder loop error")
        await asyncio.sleep(30)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN. Скопируй .env.example в .env и вставь токен от @BotFather.")
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="part", description="Выбрать часть книги (1 или 2)"),
        BotCommand(command="menu", description="Главное меню выбранной части"),
        BotCommand(command="remind", description="Напоминание: /remind 19:30"),
    ])
    asyncio.create_task(reminder_loop(bot))
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
