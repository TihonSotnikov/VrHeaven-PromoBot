"""Inline-клавиатуры админа и партнёра."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils import fmt_dt, fmt_money

# --------------------------------------------------------------------- Общие

CANCEL_CB = "cancel"


def cancel_kb(cb: str = CANCEL_CB) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена", callback_data=cb)
    return kb.as_markup()


def confirm_kb(yes_cb: str, cancel_cb: str = CANCEL_CB) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Подтвердить", callback_data=yes_cb)
    kb.button(text="Отмена", callback_data=cancel_cb)
    kb.adjust(1)
    return kb.as_markup()


# --------------------------------------------------------------------- Админ

def admin_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Добавить заказ", callback_data="ao:add")
    kb.button(text="Отменить заказ", callback_data="ac:list")
    kb.button(text="Партнёры", callback_data="ap:menu")
    kb.button(text="Сводка", callback_data="sum")
    kb.button(text="Выплата", callback_data="po:list")
    kb.button(text="Экспорт данных", callback_data="ex")
    kb.adjust(1)
    return kb.as_markup()


def to_admin_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="В меню", callback_data="am")
    return kb.as_markup()


def order_partner_pick_kb(partners) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in partners:
        kb.button(
            text=f"{p['promo_code']} · {p['venue_name']}",
            callback_data=f"ao:p:{p['id']}",
        )
    kb.button(text="Отмена", callback_data=CANCEL_CB)
    kb.adjust(1)
    return kb.as_markup()


def orders_pick_kb(orders, tz) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for o in orders:
        paid = " · выплачен" if o["payout_id"] is not None else ""
        kb.button(
            text=f"№{o['id']} {o['promo_code']} · {fmt_money(o['amount'])}"
                 f" · {fmt_dt(o['created_at'], tz, '%d.%m')}{paid}",
            callback_data=f"ac:o:{o['id']}",
        )
    kb.button(text="Отмена", callback_data=CANCEL_CB)
    kb.adjust(1)
    return kb.as_markup()


def partners_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Добавить партнёра", callback_data="ap:add")
    kb.button(text="Список партнёров", callback_data="ap:list")
    kb.button(text="В меню", callback_data="am")
    kb.adjust(1)
    return kb.as_markup()


def partners_list_kb(partners) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in partners:
        status = "" if p["is_active"] else " · приостановлен"
        kb.button(
            text=f"{p['promo_code']} · {p['venue_name']}{status}",
            callback_data=f"ap:card:{p['id']}",
        )
    kb.button(text="Назад", callback_data="ap:menu")
    kb.adjust(1)
    return kb.as_markup()


def partner_card_kb(partner) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if partner["is_active"]:
        kb.button(text="Приостановить", callback_data=f"ap:toggle:{partner['id']}")
    else:
        kb.button(text="Активировать", callback_data=f"ap:toggle:{partner['id']}")
    kb.button(text="Новый пароль", callback_data=f"ap:pwd:{partner['id']}")
    kb.button(text="Изменить процент", callback_data=f"ap:pct:{partner['id']}")
    kb.button(text="Удалить партнёра", callback_data=f"ap:del:{partner['id']}")
    kb.button(text="К списку", callback_data="ap:list")
    kb.adjust(1)
    return kb.as_markup()


def payout_partner_pick_kb(rows) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in rows:
        kb.button(
            text=f"{r['promo_code']} · {fmt_money(r['commission_sum'])}",
            callback_data=f"po:p:{r['id']}",
        )
    kb.button(text="Отмена", callback_data=CANCEL_CB)
    kb.adjust(1)
    return kb.as_markup()


# ------------------------------------------------------------------- Партнёр

def partner_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Текущий период", callback_data="pp")
    kb.button(text="История выплат", callback_data="ph")
    kb.adjust(1)
    return kb.as_markup()


def to_partner_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="В меню", callback_data="pm")
    return kb.as_markup()


def welcome_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Войти", callback_data="plogin")
    return kb.as_markup()


def login_cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена", callback_data="pcancel")
    return kb.as_markup()
