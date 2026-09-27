"""Маршрутизация состояний FSM в методе «одного окна»."""

from helpers import FakeBot, fake_cb, fake_msg, make_state

from handlers.admin import (
    AddOrderSG,
    AddPartnerSG,
    cb_cancel,
    order_amount,
    order_confirm,
    order_pick_partner,
    partner_add_contact,
    payout_confirm,
    stale_callback,
)
from handlers.partner import partner_menu
from utils import hash_password

ADMIN_CHAT = 1
PARTNER_CHAT = 777


async def test_add_order_flow_keeps_single_window(db):
    bot = FakeBot()
    state = make_state(ADMIN_CHAT)
    pid = await db.create_partner("vr101", "Club X", "", hash_password("x"))
    await db.bind_chat(pid, PARTNER_CHAT)
    await db.set_window(ADMIN_CHAT, 50)            # окно админа уже открыто

    # выбор партнёра -> шаг ввода суммы
    await order_pick_partner(fake_cb(f"ao:p:{pid}", message_id=50), state, db, bot)
    assert await state.get_state() == AddOrderSG.amount.state

    # некорректная сумма: ввод удалён, шаг не меняется
    bad = fake_msg("ноль", chat_id=ADMIN_CHAT)
    await order_amount(bad, state, db, bot)
    bad.delete.assert_awaited()
    assert await state.get_state() == AddOrderSG.amount.state

    # корректная сумма -> шаг подтверждения
    good = fake_msg("1 500,50", chat_id=ADMIN_CHAT)
    await order_amount(good, state, db, bot)
    good.delete.assert_awaited()
    assert await state.get_state() == AddOrderSG.confirm.state

    # подтверждение: заказ создан, состояние сброшено
    await order_confirm(fake_cb("ao:ok", message_id=50), state, db, bot)
    assert await state.get_state() is None
    orders = await db.last_orders()
    assert len(orders) == 1
    assert orders[0]["amount"] == 1500.5
    assert orders[0]["commission"] == 150.05

    # инвариант «одного окна»: в чате админа не появилось новых сообщений,
    # все экраны редактировали одно и то же окно
    assert [m for m in bot.sent if m[0] == ADMIN_CHAT] == []
    assert {(chat, mid) for chat, mid, _ in bot.edited if chat == ADMIN_CHAT} == {(ADMIN_CHAT, 50)}
    assert await db.get_window(ADMIN_CHAT) == 50

    # пуш партнёру пришёл новым сообщением и стал его окном
    partner_pushes = [m for m in bot.sent if m[0] == PARTNER_CHAT]
    assert len(partner_pushes) == 1
    assert await db.get_window(PARTNER_CHAT) == partner_pushes[0][1]


async def test_cancel_resets_state_and_returns_to_menu(db):
    bot = FakeBot()
    state = make_state(ADMIN_CHAT)
    await db.set_window(ADMIN_CHAT, 50)
    await state.set_state(AddOrderSG.amount)
    await state.update_data(partner_id=1, promo_code="vr101")

    await cb_cancel(fake_cb("cancel", message_id=50), state, db, bot)
    assert await state.get_state() is None
    assert "Панель администратора" in bot.edited[-1][2]


async def test_stale_callback_recovers_to_menu(db):
    """Кнопка с экрана, пережившего перезапуск: состояние сброшено, окно — меню."""
    bot = FakeBot()
    state = make_state(ADMIN_CHAT)
    await db.set_window(ADMIN_CHAT, 50)

    await stale_callback(fake_cb("ao:ok", message_id=50), state, db, bot)
    assert await state.get_state() is None
    assert "Панель администратора" in bot.edited[-1][2]


async def test_deleted_partner_interface_is_blocked_and_reset(db):
    """Удалённый партнёр: любая кнопка блокируется, окно — экран авторизации."""
    bot = FakeBot()
    state = make_state(PARTNER_CHAT)
    pid = await db.create_partner("vr101", "Club X", "", hash_password("x"))
    await db.bind_chat(pid, PARTNER_CHAT)
    await db.set_window(PARTNER_CHAT, 60)
    await db.delete_partner(pid)

    cb = fake_cb("pm", chat_id=PARTNER_CHAT, message_id=60)
    await partner_menu(cb, state, db, bot)

    cb.answer.assert_awaited_with("Доступ закрыт")
    assert await state.get_state() is None
    assert "Доступ к кабинету закрыт" in bot.edited[-1][2]
    # на экране есть путь к повторной авторизации — тупика нет
    buttons = [btn.text for row in bot.last_markup.inline_keyboard for btn in row]
    assert "Войти" in buttons


async def test_order_confirm_blocked_for_deleted_partner(db):
    """Партнёра удалили между шагами оформления: заказ не создаётся."""
    bot = FakeBot()
    state = make_state(ADMIN_CHAT)
    pid = await db.create_partner("vr101", "Club X", "", hash_password("x"))
    await db.set_window(ADMIN_CHAT, 50)
    await state.set_state(AddOrderSG.confirm)
    await state.update_data(partner_id=pid, promo_code="vr101", amount=100.0, commission=10.0)
    await db.delete_partner(pid)

    await order_confirm(fake_cb("ao:ok", message_id=50), state, db, bot)
    assert await db.last_orders() == []
    assert await state.get_state() is None
    assert "заказ не оформлен" in bot.edited[-1][2]


async def test_payout_blocked_for_deleted_partner(db):
    """Устаревшая кнопка выплаты не проводит выплату удалённому партнёру."""
    bot = FakeBot()
    pid = await db.create_partner("vr101", "Club X", "", hash_password("x"))
    await db.add_order(pid, 1000, 100)
    await db.delete_partner(pid)

    cb = fake_cb(f"po:ok:{pid}", message_id=50)
    await payout_confirm(cb, db, bot)
    cb.answer.assert_awaited_with("Партнёр не найден", show_alert=True)
    assert await db.payouts_for_partner(pid) == []


async def test_promo_rechecked_before_partner_creation(db):
    """Промокод заняли, пока шёл пошаговый ввод: дубль не создаётся."""
    bot = FakeBot()
    state = make_state(ADMIN_CHAT)
    await db.set_window(ADMIN_CHAT, 50)
    await state.set_state(AddPartnerSG.contact)
    await state.update_data(code="vr101", name="Club X")
    await db.create_partner("vr101", "Club Y", "", hash_password("x"))  # конкурент успел раньше

    msg = fake_msg("-", chat_id=ADMIN_CHAT)
    await partner_add_contact(msg, state, db, bot)
    assert len(await db.list_partners()) == 1
    assert await state.get_state() is None
    assert "Подключение отменено" in bot.edited[-1][2]
