"""Оконная модель: в чате всегда ровно одно интерфейсное сообщение."""

from helpers import FakeBot, fake_cb

from window import render, render_cb, send_table

CHAT = 1


async def test_first_render_sends_and_stores_window(db):
    bot = FakeBot()
    await render(bot, db, CHAT, "screen")
    assert len(bot.sent) == 1
    assert await db.get_window(CHAT) == bot.sent[0][1]


async def test_repeated_render_edits_in_place(db):
    bot = FakeBot()
    await render(bot, db, CHAT, "first")
    window_id = await db.get_window(CHAT)

    await render(bot, db, CHAT, "second")
    assert len(bot.sent) == 1                      # новых сообщений нет
    assert bot.edited == [(CHAT, window_id, "second")]
    assert await db.get_window(CHAT) == window_id


async def test_new_message_replaces_old_window(db):
    bot = FakeBot()
    await render(bot, db, CHAT, "first")
    old_id = await db.get_window(CHAT)

    await render(bot, db, CHAT, "push", new_message=True)
    assert (CHAT, old_id) in bot.deleted           # прежнее окно удалено
    new_id = await db.get_window(CHAT)
    assert new_id != old_id
    assert bot.sent[-1] == (CHAT, new_id, "push")


async def test_lost_window_replaced_with_new(db):
    bot = FakeBot()
    await render(bot, db, CHAT, "first")
    old_id = await db.get_window(CHAT)

    bot.edit_error = "Bad Request: message to edit not found"
    await render(bot, db, CHAT, "second")
    assert (CHAT, old_id) in bot.deleted
    assert bot.sent[-1][2] == "second"
    assert await db.get_window(CHAT) == bot.sent[-1][1]


async def test_not_modified_is_noop(db):
    bot = FakeBot()
    await render(bot, db, CHAT, "same")
    window_id = await db.get_window(CHAT)

    bot.edit_error = "Bad Request: message is not modified"
    await render(bot, db, CHAT, "same")
    assert len(bot.sent) == 1
    assert bot.deleted == []
    assert await db.get_window(CHAT) == window_id


async def test_render_cb_adopts_clicked_message_and_removes_old_window(db):
    bot = FakeBot()
    await db.set_window(CHAT, 500)                 # текущее окно
    cb = fake_cb("any", chat_id=CHAT, message_id=42)  # клик по устаревшему сообщению

    await render_cb(bot, db, cb, "screen")
    assert (CHAT, 500) in bot.deleted              # старое окно удалено
    assert (CHAT, 42, "screen") in bot.edited      # нажатое стало окном
    assert await db.get_window(CHAT) == 42


async def test_render_cb_uneditable_message_replaced(db):
    """Нажатие кнопки под документом: документ заменяется текстовым окном."""
    bot = FakeBot()
    await db.set_window(CHAT, 42)
    cb = fake_cb("any", chat_id=CHAT, message_id=42)

    bot.edit_error = "Bad Request: there is no text in the message to edit"
    await render_cb(bot, db, cb, "screen")
    assert (CHAT, 42) in bot.deleted
    assert bot.sent[-1][2] == "screen"
    assert await db.get_window(CHAT) == bot.sent[-1][1]


async def test_send_table_builds_classic_html_table(db):
    """send_table собирает классический <table> и шлёт через sendRichMessage."""
    bot = FakeBot()
    message = await send_table(bot, CHAT, ["Код", "Сумма"], [["vr101", "1 500"]])

    assert len(bot.rich_sent) == 1
    chat_id, message_id, html = bot.rich_sent[0]
    assert chat_id == CHAT and message.message_id == message_id
    assert html == ("<table><thead><tr><th>Код</th><th>Сумма</th></tr></thead>"
                    "<tbody><tr><td>vr101</td><td>1 500</td></tr></tbody></table>")


async def test_send_table_escapes_cell_content(db):
    bot = FakeBot()
    await send_table(bot, CHAT, ["Заведение"], [["Club <X> & Co"]])
    assert "<td>Club &lt;X&gt; &amp; Co</td>" in bot.rich_sent[0][2]


async def test_rich_render_edits_window_via_rich_message(db):
    """Обновление таблицы в окне идёт параметром rich_message в editMessageText."""
    bot = FakeBot()
    await render(bot, db, CHAT, "plain")                      # окно создано
    window_id = await db.get_window(CHAT)

    await render(bot, db, CHAT, "<p><b>Сводка</b></p><table></table>", rich=True)
    assert bot.rich_sent == []                                # нового сообщения нет
    assert bot.edited[-1] == (CHAT, window_id, None)          # текст ушёл rich-параметром
    assert bot.last_rich.html == "<p><b>Сводка</b></p><table></table>"
    assert await db.get_window(CHAT) == window_id


async def test_rich_push_replaces_window_with_rich_message(db):
    """Rich-рассылка (new_message) заменяет окно нативной таблицей."""
    bot = FakeBot()
    await render(bot, db, CHAT, "plain")
    old_id = await db.get_window(CHAT)

    await render(bot, db, CHAT, "<table></table>", new_message=True, rich=True)
    assert (CHAT, old_id) in bot.deleted
    assert bot.rich_sent[-1][0] == CHAT
    assert await db.get_window(CHAT) == bot.rich_sent[-1][1]
