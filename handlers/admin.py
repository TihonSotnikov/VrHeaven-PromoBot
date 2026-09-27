"""Хендлеры админа: заказы, партнёры, сводка, выплаты, экспорт.

Интерфейс работает по принципу «одного окна»: каждый экран — редактирование
единственного сообщения бота, ввод администратора удаляется из чата.
"""

import csv
import io
import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message, TelegramObject

import keyboards as kb
from config import Config
from db import Database
from reports import admin_summary_text
from utils import (
    esc, fmt_dt, fmt_money, fmt_percent, gen_password, hash_password,
    inline_code, parse_amount,
)
from window import delete_user_message, render, render_cb

log = logging.getLogger(__name__)

PROMO_RE = re.compile(r"[a-z0-9_-]{2,32}")


class AdminFilter(Filter):
    async def __call__(self, event: TelegramObject, config: Config) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and user.id in config.admin_ids


router = Router(name="admin")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


class AddOrderSG(StatesGroup):
    amount = State()
    confirm = State()


class AddPartnerSG(StatesGroup):
    code = State()
    name = State()
    contact = State()


class EditPercentSG(StatesGroup):
    value = State()


MENU_TEXT = (
    "*VR Heaven · Партнёрская программа*\n"
    "Панель администратора\n\n"
    "Выберите раздел"
)


async def _push_partner_chats(bot: Bot, db: Database, partner, text: str) -> tuple[int, int]:
    """Пуш во все чаты кабинета партнёра. Возвращает (доставлено, всего).

    Сбой доставки в один чат не мешает остальным и не прерывает
    бизнес-операцию — админ видит итог доставки на экране результата.
    """
    chats = await db.chats_for_partner(partner["id"])
    delivered = 0
    for chat_id in chats:
        try:
            await render(bot, db, chat_id, text, kb.to_partner_menu_kb(),
                         new_message=True)
            delivered += 1
        except TelegramAPIError as e:
            log.warning("Не удалось уведомить чат %s партнёра %s: %s",
                        chat_id, partner["promo_code"], e)
    return delivered, len(chats)


def _delivery_note(delivered: int, total: int) -> str:
    """Строка о доставке уведомления для экрана результата админа."""
    if delivered == 0:
        return ""
    if total == 1:
        return "\n\nПартнёр получил уведомление"
    return f"\n\nУведомление доставлено: {delivered} из {total} устройств"


# ------------------------------------------------------------ Меню и отмена

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await delete_user_message(message)
    await render(bot, db, message.chat.id, MENU_TEXT, kb.admin_menu_kb(), new_message=True)


@router.callback_query(F.data == "am")
async def cb_menu(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await render_cb(bot, db, cb, MENU_TEXT, kb.admin_menu_kb())
    await cb.answer()


@router.callback_query(F.data == kb.CANCEL_CB)
async def cb_cancel(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await render_cb(bot, db, cb, MENU_TEXT, kb.admin_menu_kb())
    await cb.answer("Действие отменено")


# ------------------------------------------------------------ Добавить заказ

@router.callback_query(F.data == "ao:add")
async def order_add_start(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    partners = await db.list_partners(active_only=True)
    if not partners:
        await render_cb(
            bot, db, cb,
            "*Новый заказ*\n\nДействующих партнёров нет\\. "
            "Подключите партнёра в разделе «Партнёры»",
            kb.to_admin_menu_kb(),
        )
        await cb.answer()
        return
    await render_cb(
        bot, db, cb,
        "*Новый заказ*\n\nВыберите промокод партнёра",
        kb.order_partner_pick_kb(partners),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ao:p:"))
async def order_pick_partner(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    partner = await db.get_partner(int(cb.data.rsplit(":", 1)[1]))
    if not partner:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    await state.set_state(AddOrderSG.amount)
    await state.update_data(partner_id=partner["id"], promo_code=partner["promo_code"])
    await render_cb(
        bot, db, cb,
        f"*Новый заказ · {esc(partner['promo_code'])}*\n\n"
        "Укажите сумму заказа в рублях",
        kb.cancel_kb(),
    )
    await cb.answer()


@router.message(AddOrderSG.amount, F.text)
async def order_amount(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_message(message)
    data = await state.get_data()
    amount = parse_amount(message.text)
    if amount is None:
        await render(
            bot, db, message.chat.id,
            f"*Новый заказ · {esc(data['promo_code'])}*\n\n"
            "Сумма — положительное число, например 1500 или 1500,50\n"
            "Укажите сумму ещё раз",
            kb.cancel_kb(),
        )
        return
    partner = await db.get_partner(data["partner_id"])
    commission = round(amount * partner["commission_percent"] / 100, 2)
    await state.set_state(AddOrderSG.confirm)
    await state.update_data(amount=amount, commission=commission)
    await render(
        bot, db, message.chat.id,
        "*Подтверждение заказа*\n\n"
        f"Партнёр: {esc(partner['promo_code'])} · {esc(partner['venue_name'])}\n"
        f"Сумма: {fmt_money(amount)}\n"
        f"Комиссия {fmt_percent(partner['commission_percent'])}: {fmt_money(commission)}",
        kb.confirm_kb("ao:ok"),
    )


@router.callback_query(AddOrderSG.confirm, F.data == "ao:ok")
async def order_confirm(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    partner = await db.get_partner(data["partner_id"])
    if not partner or partner["deleted_at"] is not None or not partner["is_active"]:
        await render_cb(
            bot, db, cb,
            "*Новый заказ*\n\nПартнёр недоступен — заказ не оформлен",
            kb.to_admin_menu_kb(),
        )
        await cb.answer()
        return
    order_id = await db.add_order(partner["id"], data["amount"], data["commission"])
    total = await db.unpaid_total(partner["id"])
    delivered, chats_total = await _push_partner_chats(
        bot, db, partner,
        "*Новый заказ по вашему промокоду*\n\n"
        f"Сумма заказа: {fmt_money(data['amount'])}\n"
        f"Ваша комиссия: {fmt_money(data['commission'])}\n"
        f"Накоплено к выплате: {fmt_money(total['commission_sum'])}\n\n"
        "Выплаты проводятся 1\\-го и 15\\-го числа",
    )
    await render_cb(
        bot, db, cb,
        f"*Заказ №{order_id} оформлен*\n\n"
        f"Партнёр: {esc(partner['promo_code'])} · {esc(partner['venue_name'])}\n"
        f"Сумма: {fmt_money(data['amount'])}\n"
        f"Комиссия: {fmt_money(data['commission'])}{_delivery_note(delivered, chats_total)}",
        kb.to_admin_menu_kb(),
    )
    await cb.answer("Заказ оформлен")


# ------------------------------------------------------------ Отменить заказ

@router.callback_query(F.data == "ac:list")
async def order_cancel_list(
    cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot, config: Config
) -> None:
    await state.clear()
    orders = await db.last_orders(10)
    if not orders:
        await render_cb(bot, db, cb, "*Отмена заказа*\n\nЗаказов пока нет",
                        kb.to_admin_menu_kb())
        await cb.answer()
        return
    await render_cb(
        bot, db, cb,
        "*Отмена заказа*\n\nПоследние 10 заказов\\. Выберите заказ для отмены",
        kb.orders_pick_kb(orders, config.tz),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ac:o:"))
async def order_cancel_pick(cb: CallbackQuery, db: Database, bot: Bot, config: Config) -> None:
    order = await db.get_order(int(cb.data.rsplit(":", 1)[1]))
    if not order or order["cancelled_at"] is not None:
        await cb.answer("Заказ уже отменён", show_alert=True)
        return
    warn = ("\nВнимание: заказ уже включён в проведённую выплату"
            if order["payout_id"] is not None else "")
    await render_cb(
        bot, db, cb,
        f"*Отмена заказа №{order['id']}*\n\n"
        f"Партнёр: {esc(order['promo_code'])} · {esc(order['venue_name'])}\n"
        f"Сумма: {fmt_money(order['amount'])}\n"
        f"Дата: {esc(fmt_dt(order['created_at'], config.tz))}\n\n"
        f"Заказ будет исключён из расчёта комиссии{warn}",
        kb.confirm_kb(f"ac:ok:{order['id']}"),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ac:ok:"))
async def order_cancel_confirm(cb: CallbackQuery, db: Database, bot: Bot, config: Config) -> None:
    order_id = int(cb.data.rsplit(":", 1)[1])
    order = await db.get_order(order_id)
    # cancel_order условный: параллельная отмена вторым админом даёт False
    if not order or not await db.cancel_order(order_id):
        await render_cb(bot, db, cb, "Заказ уже был отменён", kb.to_admin_menu_kb())
        await cb.answer()
        return
    partner = await db.get_partner(order["partner_id"])
    delivered, chats_total = 0, 0
    if partner and partner["deleted_at"] is None:
        total = await db.unpaid_total(partner["id"])
        delivered, chats_total = await _push_partner_chats(
            bot, db, partner,
            "*Заказ отменён*\n\n"
            f"Заказ от {esc(fmt_dt(order['created_at'], config.tz, '%d.%m.%Y'))} "
            f"на сумму {fmt_money(order['amount'])} исключён из расчёта комиссии\n"
            f"Накоплено к выплате: {fmt_money(total['commission_sum'])}\n\n"
            "Подробности — служба поддержки @VrHeaven",
        )
    await render_cb(
        bot, db, cb,
        f"*Заказ №{order_id} отменён*{_delivery_note(delivered, chats_total)}",
        kb.to_admin_menu_kb(),
    )
    await cb.answer("Заказ отменён")


# ---------------------------------------------------------------- Партнёры

def _partner_card(partner, total, chats_count: int) -> str:
    status = "действующий" if partner["is_active"] else "приостановлен"
    cabinet = f"подключён · устройств: {chats_count}" if chats_count else "не подключён"
    return (
        f"*{esc(partner['promo_code'])}*\n"
        f"Заведение: {esc(partner['venue_name'])}\n"
        f"Контакт: {esc(partner['contact'] or '—')}\n"
        f"Ставка комиссии: {fmt_percent(partner['commission_percent'])}\n"
        f"Статус: {status}\n"
        f"Кабинет: {cabinet}\n"
        f"К выплате: {fmt_money(total['commission_sum'])} · заказов: {total['orders_count']}"
    )


async def _partner_card_text(db: Database, partner) -> str:
    total = await db.unpaid_total(partner["id"])
    chats = await db.chats_for_partner(partner["id"])
    return _partner_card(partner, total, len(chats))


@router.callback_query(F.data == "ap:menu")
async def partners_menu(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await render_cb(
        bot, db, cb,
        "*Партнёры*\n\nУсловия сотрудничества и доступ партнёров",
        kb.partners_menu_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "ap:list")
async def partners_list(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    partners = await db.list_partners()
    if not partners:
        await render_cb(bot, db, cb, "*Партнёры*\n\nПартнёры пока не подключены",
                        kb.partners_menu_kb())
        await cb.answer()
        return
    await render_cb(bot, db, cb, "*Партнёры*\n\nВыберите партнёра",
                    kb.partners_list_kb(partners))
    await cb.answer()


@router.callback_query(F.data.startswith("ap:card:"))
async def partner_card(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    partner = await db.get_partner(int(cb.data.rsplit(":", 1)[1]))
    if not partner or partner["deleted_at"] is not None:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    await render_cb(bot, db, cb, await _partner_card_text(db, partner),
                    kb.partner_card_kb(partner))
    await cb.answer()


@router.callback_query(F.data.startswith("ap:toggle:"))
async def partner_toggle(cb: CallbackQuery, db: Database, bot: Bot) -> None:
    partner = await db.get_partner(int(cb.data.rsplit(":", 1)[1]))
    if not partner or partner["deleted_at"] is not None:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    if not partner["is_active"]:
        # активация возможна, только если промокод не занят другим действующим партнёром
        if not await db.promo_code_available(partner["promo_code"], exclude_id=partner["id"]):
            await cb.answer(
                "Промокод закреплён за другим действующим партнёром. Активация невозможна",
                show_alert=True,
            )
            return
    await db.set_partner_active(partner["id"], not partner["is_active"])
    partner = await db.get_partner(partner["id"])
    await render_cb(bot, db, cb, await _partner_card_text(db, partner),
                    kb.partner_card_kb(partner))
    await cb.answer("Статус обновлён")


@router.callback_query(F.data.startswith("ap:pwd:"))
async def partner_pwd_ask(cb: CallbackQuery, db: Database, bot: Bot) -> None:
    partner = await db.get_partner(int(cb.data.rsplit(":", 1)[1]))
    if not partner or partner["deleted_at"] is not None:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    await render_cb(
        bot, db, cb,
        f"*Смена пароля · {esc(partner['promo_code'])}*\n\n"
        "Текущий пароль перестанет действовать, все подключённые устройства "
        "будут отключены от кабинета\\. Продолжить?",
        kb.confirm_kb(f"ap:pwdok:{partner['id']}", f"ap:card:{partner['id']}"),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ap:pwdok:"))
async def partner_pwd_regen(cb: CallbackQuery, db: Database, bot: Bot) -> None:
    partner = await db.get_partner(int(cb.data.rsplit(":", 1)[1]))
    if not partner or partner["deleted_at"] is not None:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    password = gen_password()
    await db.set_partner_password(partner["id"], hash_password(password))
    # смена пароля = отзыв доступа: старые устройства должны войти заново
    await db.unbind_partner_chats(partner["id"])
    await render_cb(
        bot, db, cb,
        f"*Пароль обновлён · {esc(partner['promo_code'])}*\n\n"
        f"Новый пароль: {inline_code(password)} \\(нажмите, чтобы скопировать\\)\n"
        "Передайте пароль партнёру — он отображается только один раз\\.\n"
        "Все устройства отключены от кабинета — вход по новому паролю",
        kb.partner_card_kb(partner),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ap:pct:"))
async def partner_pct_ask(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    partner = await db.get_partner(int(cb.data.rsplit(":", 1)[1]))
    if not partner or partner["deleted_at"] is not None:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    await state.set_state(EditPercentSG.value)
    await state.update_data(partner_id=partner["id"], promo_code=partner["promo_code"])
    await render_cb(
        bot, db, cb,
        f"*Ставка комиссии · {esc(partner['promo_code'])}*\n\n"
        f"Текущая ставка: {fmt_percent(partner['commission_percent'])}\n"
        "Введите новую ставку в процентах, например 10 или 12,5\n"
        "Изменение распространяется только на новые заказы",
        kb.cancel_kb(),
    )
    await cb.answer()


@router.message(EditPercentSG.value, F.text)
async def partner_pct_set(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_message(message)
    data = await state.get_data()
    percent = parse_amount(message.text)
    if percent is None or percent > 100:
        await render(
            bot, db, message.chat.id,
            f"*Ставка комиссии · {esc(data['promo_code'])}*\n\n"
            "Значение — число больше 0 и не больше 100, например 10 или 12,5\n"
            "Введите ставку ещё раз",
            kb.cancel_kb(),
        )
        return
    await state.clear()
    await db.set_partner_percent(data["partner_id"], percent)
    partner = await db.get_partner(data["partner_id"])
    await render(
        bot, db, message.chat.id,
        "*Ставка обновлена*\n\n" + await _partner_card_text(db, partner),
        kb.partner_card_kb(partner),
    )


@router.callback_query(F.data.startswith("ap:del:"))
async def partner_delete_ask(cb: CallbackQuery, db: Database, bot: Bot) -> None:
    partner = await db.get_partner(int(cb.data.rsplit(":", 1)[1]))
    if not partner or partner["deleted_at"] is not None:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    total = await db.unpaid_total(partner["id"])
    warn = ""
    if total["orders_count"] > 0:
        warn = (f"\n\nВнимание: к выплате числится {fmt_money(total['commission_sum'])} — "
                "после удаления сумма не будет выплачена")
    await render_cb(
        bot, db, cb,
        f"*Удаление партнёра · {esc(partner['promo_code'])}*\n\n"
        "Доступ в кабинет будет закрыт, промокод освободится для нового партнёра\\. "
        f"История заказов и выплат сохранится в отчётах{warn}",
        kb.confirm_kb(f"ap:delok:{partner['id']}", f"ap:card:{partner['id']}"),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ap:delok:"))
async def partner_delete_confirm(cb: CallbackQuery, db: Database, bot: Bot) -> None:
    partner = await db.get_partner(int(cb.data.rsplit(":", 1)[1]))
    if not partner or partner["deleted_at"] is not None:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    await db.delete_partner(partner["id"])
    await render_cb(
        bot, db, cb,
        f"*Партнёр {esc(partner['promo_code'])} удалён*\n\n"
        "Промокод доступен для повторного подключения",
        kb.partners_menu_kb(),
    )
    await cb.answer("Партнёр удалён")


@router.callback_query(F.data == "ap:add")
async def partner_add_start(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await state.set_state(AddPartnerSG.code)
    await render_cb(
        bot, db, cb,
        "*Новый партнёр*\n\nВведите промокод — латинские буквы, цифры, дефис "
        "или подчёркивание",
        kb.cancel_kb(),
    )
    await cb.answer()


@router.message(AddPartnerSG.code, F.text)
async def partner_add_code(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_message(message)
    code = message.text.strip().lower()
    if not PROMO_RE.fullmatch(code):
        await render(
            bot, db, message.chat.id,
            "*Новый партнёр*\n\nФормат промокода: латинские буквы, цифры, дефис, "
            "подчёркивание, от 2 до 32 символов\nВведите промокод ещё раз",
            kb.cancel_kb(),
        )
        return
    if not await db.promo_code_available(code):
        await render(
            bot, db, message.chat.id,
            "*Новый партнёр*\n\nПромокод закреплён за действующим партнёром\n"
            "Укажите другой промокод",
            kb.cancel_kb(),
        )
        return
    await state.update_data(code=code)
    await state.set_state(AddPartnerSG.name)
    await render(
        bot, db, message.chat.id,
        f"*Новый партнёр · {esc(code)}*\n\nВведите название заведения",
        kb.cancel_kb(),
    )


@router.message(AddPartnerSG.name, F.text)
async def partner_add_name(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_message(message)
    data = await state.get_data()
    name = message.text.strip()
    if not name:
        await render(
            bot, db, message.chat.id,
            f"*Новый партнёр · {esc(data['code'])}*\n\n"
            "Название не может быть пустым\nВведите название ещё раз",
            kb.cancel_kb(),
        )
        return
    await state.update_data(name=name)
    await state.set_state(AddPartnerSG.contact)
    await render(
        bot, db, message.chat.id,
        f"*Новый партнёр · {esc(data['code'])}*\n\n"
        "Укажите контакт представителя\n"
        "Если контакт не требуется — отправьте прочерк",
        kb.cancel_kb(),
    )


@router.message(AddPartnerSG.contact, F.text)
async def partner_add_contact(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_message(message)
    contact = message.text.strip()
    if contact in {"-", "—", "–"}:
        contact = ""
    data = await state.get_data()
    await state.clear()
    # промокод мог быть занят, пока шёл ввод — проверяем перед созданием
    if not await db.promo_code_available(data["code"]):
        await render(
            bot, db, message.chat.id,
            "*Новый партнёр*\n\nПромокод уже закреплён за действующим партнёром\\. "
            "Подключение отменено",
            kb.partners_menu_kb(),
        )
        return
    password = gen_password()
    partner_id = await db.create_partner(
        data["code"], data["name"], contact, hash_password(password)
    )
    partner = await db.get_partner(partner_id)
    await render(
        bot, db, message.chat.id,
        "*Партнёр подключён*\n\n"
        + await _partner_card_text(db, partner)
        + f"\n\nПароль: {inline_code(password)} \\(нажмите, чтобы скопировать\\)\n"
        "Передайте партнёру промокод и пароль\\. Пароль отображается только один раз",
        kb.partner_card_kb(partner),
    )


# ------------------------------------------------------------------- Сводка

@router.callback_query(F.data == "sum")
async def summary(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await render_cb(bot, db, cb, await admin_summary_text(db), kb.to_admin_menu_kb(),
                    rich=True)
    await cb.answer()


# ------------------------------------------------------------------ Выплата

@router.callback_query(F.data == "po:list")
async def payout_list(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    rows = [r for r in await db.unpaid_summary() if r["orders_count"] > 0]
    if not rows:
        await render_cb(bot, db, cb, "*Выплата*\n\nНевыплаченных заказов нет",
                        kb.to_admin_menu_kb())
        await cb.answer()
        return
    await render_cb(bot, db, cb, "*Выплата*\n\nВыберите партнёра для проведения выплаты",
                    kb.payout_partner_pick_kb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("po:p:"))
async def payout_pick(cb: CallbackQuery, db: Database, bot: Bot) -> None:
    partner = await db.get_partner(int(cb.data.rsplit(":", 1)[1]))
    if not partner or partner["deleted_at"] is not None:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    total = await db.unpaid_total(partner["id"])
    if total["orders_count"] == 0:
        await cb.answer("У партнёра нет заказов к выплате", show_alert=True)
        return
    await render_cb(
        bot, db, cb,
        "*Подтверждение выплаты*\n\n"
        f"Партнёр: {esc(partner['promo_code'])} · {esc(partner['venue_name'])}\n"
        f"Заказов: {total['orders_count']}\n"
        f"Оборот: {fmt_money(total['turnover'])}\n"
        f"*К выплате: {fmt_money(total['commission_sum'])}*\n\n"
        "После подтверждения заказы периода будут помечены выплаченными",
        kb.confirm_kb(f"po:ok:{partner['id']}"),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("po:ok:"))
async def payout_confirm(cb: CallbackQuery, db: Database, bot: Bot) -> None:
    partner = await db.get_partner(int(cb.data.rsplit(":", 1)[1]))
    if not partner or partner["deleted_at"] is not None:
        await cb.answer("Партнёр не найден", show_alert=True)
        return
    result = await db.create_payout(partner["id"])
    if result is None:
        await render_cb(bot, db, cb, "У партнёра уже нет заказов к выплате",
                        kb.to_admin_menu_kb())
        await cb.answer()
        return
    _, amount, orders_count = result
    delivered, chats_total = await _push_partner_chats(
        bot, db, partner,
        "*Выплата проведена*\n\n"
        f"Сумма: {fmt_money(amount)}\n"
        f"Заказов в периоде: {orders_count}\n\n"
        "Открыт новый учётный период\\. Благодарим за сотрудничество",
    )
    await render_cb(
        bot, db, cb,
        "*Выплата проведена*\n\n"
        f"Партнёр: {esc(partner['promo_code'])} · {esc(partner['venue_name'])}\n"
        f"Сумма: {fmt_money(amount)}\n"
        f"Заказов закрыто: {orders_count}{_delivery_note(delivered, chats_total)}",
        kb.to_admin_menu_kb(),
    )
    await cb.answer("Выплата проведена")


# ------------------------------------------------------------------ Экспорт

def _csv_bytes(headers: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


@router.callback_query(F.data == "ex")
async def export_csv(
    cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot, config: Config
) -> None:
    await state.clear()
    await cb.answer("Формирую выгрузку")
    tz = config.tz

    partners = await db.export_partners()
    partners_csv = _csv_bytes(
        ["id", "промокод", "заведение", "контакт", "комиссия_%", "статус",
         "устройств_кабинета", "создан"],
        [[p["id"], p["promo_code"], p["venue_name"], p["contact"],
          p["commission_percent"],
          "удалён" if p["deleted_at"] else ("действующий" if p["is_active"] else "приостановлен"),
          p["chats_count"],
          fmt_dt(p["created_at"], tz)]
         for p in partners],
    )

    orders = await db.export_orders()
    orders_csv = _csv_bytes(
        ["id", "промокод", "сумма", "комиссия", "выплачен", "id_выплаты",
         "отменён", "дата"],
        [[o["id"], o["promo_code"], o["amount"], o["commission"],
          "да" if o["payout_id"] is not None else "нет",
          o["payout_id"] or "",
          fmt_dt(o["cancelled_at"], tz) if o["cancelled_at"] else "",
          fmt_dt(o["created_at"], tz)]
         for o in orders],
    )

    payouts = await db.export_payouts()
    payouts_csv = _csv_bytes(
        ["id", "промокод", "сумма", "заказов", "дата"],
        [[p["id"], p["promo_code"], p["amount"], p["orders_count"],
          fmt_dt(p["created_at"], tz)]
         for p in payouts],
    )

    for name, data, caption in [
        ("partners.csv", partners_csv, "Партнёры"),
        ("orders.csv", orders_csv, "Заказы"),
        ("payouts.csv", payouts_csv, "Выплаты"),
    ]:
        await bot.send_document(
            cb.message.chat.id, BufferedInputFile(data, filename=name), caption=caption
        )
    await render(
        bot, db, cb.message.chat.id,
        "*Экспорт данных*\n\nВыгрузка сформирована: партнёры, заказы, выплаты",
        kb.to_admin_menu_kb(),
        new_message=True,
    )


# ---------------------------------------- Сообщения и кнопки вне сценариев

@router.message()
async def cleanup_message(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    """Ввод вне сценария удаляется; вне FSM окно возвращается в меню."""
    await delete_user_message(message)
    if await state.get_state() is None:
        await render(bot, db, message.chat.id, MENU_TEXT, kb.admin_menu_kb())


@router.callback_query()
async def stale_callback(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    """Кнопка устаревшего экрана (например, после перезапуска)."""
    await state.clear()
    await render_cb(bot, db, cb, MENU_TEXT, kb.admin_menu_kb())
    await cb.answer("Сессия обновлена")
