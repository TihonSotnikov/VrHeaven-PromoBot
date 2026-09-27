"""Хендлеры партнёра: авторизация по промокоду и паролю, кабинет.

Роутер подключается после админского: сюда попадают все, кто не админ.
Интерфейс — «одно окно»: экраны рендерятся в единственное сообщение,
ввод пользователя (включая пароль) удаляется из чата.
"""

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import keyboards as kb
from config import Config
from db import Database
from reports import partner_period_text, payout_history_text
from utils import esc, verify_password
from window import delete_user_message, render, render_cb

log = logging.getLogger(__name__)

router = Router(name="partner")

MAX_LOGIN_ATTEMPTS = 5


class LoginSG(StatesGroup):
    code = State()
    password = State()


WELCOME_TEXT = (
    "*VR Heaven · Партнёрская программа*\n\n"
    "Личный кабинет партнёра: учёт заказов по вашему промокоду, "
    "прозрачные начисления, выплаты 1\\-го и 15\\-го числа\n\n"
    "Для входа используйте данные, предоставленные менеджером VR Heaven"
)

ACCESS_CLOSED_TEXT = (
    "*VR Heaven · Партнёрская программа*\n\n"
    "Доступ к кабинету закрыт\n"
    "По вопросам сотрудничества обращайтесь в поддержку: @VrHeaven"
)

CODE_PROMPT = "*Вход в кабинет*\n\nВведите промокод — он является вашим логином"
PASSWORD_PROMPT = "*Вход в кабинет*\n\nВведите пароль"

NEW_DEVICE_TEXT = (
    "*Вход с нового устройства*\n\n"
    "В кабинет партнёра выполнен вход с нового устройства\\.\n"
    "Если это не вы — обратитесь в поддержку: @VrHeaven"
)


def _menu_text(partner, note: str | None = None) -> str:
    head = (
        "*VR Heaven · Кабинет партнёра*\n"
        f"{esc(partner['promo_code'])} · {esc(partner['venue_name'])}\n\n"
    )
    if note:
        head += note + "\n\n"
    return head + "Выберите раздел"


# ------------------------------------------------------------- Авторизация

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await delete_user_message(message)
    partner = await db.get_partner_by_chat(message.chat.id)
    if partner:
        await render(bot, db, message.chat.id, _menu_text(partner),
                     kb.partner_menu_kb(), new_message=True)
    else:
        await render(bot, db, message.chat.id, WELCOME_TEXT,
                     kb.welcome_kb(), new_message=True)


@router.callback_query(F.data == "plogin")
async def login_start(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await state.set_state(LoginSG.code)
    await render_cb(bot, db, cb, CODE_PROMPT, kb.login_cancel_kb())
    await cb.answer()


@router.callback_query(F.data == "pcancel")
async def login_cancel(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await render_cb(bot, db, cb, WELCOME_TEXT, kb.welcome_kb())
    await cb.answer()


@router.message(LoginSG.code, F.text)
async def login_code(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_message(message)
    code = message.text.strip().lower()
    partner = await db.get_partner_by_code(code)
    if not partner:
        await render(
            bot, db, message.chat.id,
            "*Вход в кабинет*\n\nПромокод не найден\\. "
            "Проверьте данные и введите код ещё раз",
            kb.login_cancel_kb(),
        )
        return
    await state.update_data(partner_id=partner["id"], attempts=0)
    await state.set_state(LoginSG.password)
    await render(bot, db, message.chat.id, PASSWORD_PROMPT, kb.login_cancel_kb())


@router.message(LoginSG.password, F.text)
async def login_password(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_message(message)
    data = await state.get_data()
    partner = await db.get_partner(data["partner_id"])
    if not partner or partner["deleted_at"] is not None or not partner["is_active"]:
        # партнёра отключили в процессе входа — блокируем без раскрытия деталей
        await state.clear()
        await render(bot, db, message.chat.id, ACCESS_CLOSED_TEXT, kb.welcome_kb())
        return
    if verify_password(message.text.strip(), partner["password_hash"]):
        await state.clear()
        chats_before = await db.chats_for_partner(partner["id"])
        is_new_device = message.chat.id not in chats_before
        await db.bind_chat(partner["id"], message.chat.id)
        await render(
            bot, db, message.chat.id,
            _menu_text(partner, "Вход выполнен\\. Уведомления о заказах "
                                "будут приходить в этот чат"),
            kb.partner_menu_kb(),
        )
        # прежние устройства предупреждаются о входе с нового —
        # единственная сигнализация при общем пароле совладельцев
        if is_new_device:
            for chat_id in chats_before:
                try:
                    await render(bot, db, chat_id, NEW_DEVICE_TEXT,
                                 kb.to_partner_menu_kb(), new_message=True)
                except TelegramAPIError as e:
                    log.warning("Не удалось уведомить чат %s партнёра %s"
                                " о новом устройстве: %s",
                                chat_id, partner["promo_code"], e)
        return
    attempts = data.get("attempts", 0) + 1
    if attempts >= MAX_LOGIN_ATTEMPTS:
        await state.clear()
        await render(
            bot, db, message.chat.id,
            WELCOME_TEXT + "\n\nПревышено число попыток входа\\. "
            "Попробуйте позднее или обратитесь в поддержку: @VrHeaven",
            kb.welcome_kb(),
        )
        return
    await state.update_data(attempts=attempts)
    await render(
        bot, db, message.chat.id,
        "*Вход в кабинет*\n\nПароль не подходит\\. Попробуйте ещё раз",
        kb.login_cancel_kb(),
    )


# ------------------------------------------------------------------ Кабинет

async def _require_partner(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot):
    """Партнёр по chat_id. Если партнёр удалён или привязка потеряна —
    блокирует интерфейс и сбрасывает состояние до экрана авторизации."""
    partner = await db.get_partner_by_chat(cb.message.chat.id)
    if partner:
        return partner
    await state.clear()
    await render_cb(bot, db, cb, ACCESS_CLOSED_TEXT, kb.welcome_kb())
    await cb.answer("Доступ закрыт")
    return None


@router.callback_query(F.data == "pm")
async def partner_menu(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    partner = await _require_partner(cb, state, db, bot)
    if not partner:
        return
    await render_cb(bot, db, cb, _menu_text(partner), kb.partner_menu_kb())
    await cb.answer()


@router.callback_query(F.data == "pp")
async def current_period(
    cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot, config: Config
) -> None:
    partner = await _require_partner(cb, state, db, bot)
    if not partner:
        return
    text = await partner_period_text(db, partner, config.tz)
    await render_cb(bot, db, cb, text, kb.to_partner_menu_kb(), rich=True)
    await cb.answer()


@router.callback_query(F.data == "ph")
async def payout_history(
    cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot, config: Config
) -> None:
    partner = await _require_partner(cb, state, db, bot)
    if not partner:
        return
    payouts = await db.payouts_for_partner(partner["id"])
    text = payout_history_text(payouts, config.tz, partner["promo_code"])
    await render_cb(bot, db, cb, text, kb.to_partner_menu_kb(), rich=True)
    await cb.answer()


# ---------------------------------------- Сообщения и кнопки вне сценариев

@router.message()
async def cleanup_message(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    """Ввод вне сценария удаляется; вне FSM окно приводится к актуальному экрану."""
    await delete_user_message(message)
    if await state.get_state() is not None:
        return  # окно уже показывает текущий шаг сценария
    partner = await db.get_partner_by_chat(message.chat.id)
    if partner:
        await render(bot, db, message.chat.id, _menu_text(partner), kb.partner_menu_kb())
    else:
        await render(bot, db, message.chat.id, WELCOME_TEXT, kb.welcome_kb())


@router.callback_query()
async def stale_callback(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    """Кнопка устаревшего экрана: окно приводится к актуальному состоянию."""
    await state.clear()
    partner = await db.get_partner_by_chat(cb.message.chat.id)
    if partner:
        await render_cb(bot, db, cb, _menu_text(partner), kb.partner_menu_kb())
    else:
        await render_cb(bot, db, cb, WELCOME_TEXT, kb.welcome_kb())
    await cb.answer()
