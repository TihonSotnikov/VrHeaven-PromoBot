"""Фейковые объекты Telegram для тестов оконной модели и FSM."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage


class FakeBot:
    """Записывает вызовы Bot API; edit_error имитирует ошибку редактирования."""

    def __init__(self):
        self.sent = []      # (chat_id, message_id, text)
        self.edited = []    # (chat_id, message_id, text); text=None для rich-правок
        self.deleted = []   # (chat_id, message_id)
        self.documents = []
        self.rich_sent = []  # (chat_id, message_id, html) — вызовы sendRichMessage
        self.edit_error: str | None = None
        self.last_markup = None
        self.last_rich = None  # последний InputRichMessage (send или edit)
        self._next_id = 1000

    async def __call__(self, method):
        """Вызов метода API как bot(SendRichMessage(...))."""
        self._next_id += 1
        self.rich_sent.append((method.chat_id, self._next_id, method.rich_message.html))
        self.last_markup = method.reply_markup
        self.last_rich = method.rich_message
        return SimpleNamespace(message_id=self._next_id)

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self._next_id += 1
        self.sent.append((chat_id, self._next_id, text))
        self.last_markup = reply_markup
        return SimpleNamespace(message_id=self._next_id)

    async def edit_message_text(self, text=None, chat_id=None, message_id=None,
                                reply_markup=None, rich_message=None, **kwargs):
        if self.edit_error:
            raise TelegramBadRequest(method=None, message=self.edit_error)
        self.edited.append((chat_id, message_id, text))
        self.last_markup = reply_markup
        if rich_message is not None:
            self.last_rich = rich_message

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    async def send_document(self, chat_id, document, caption=None,
                            reply_markup=None, **kwargs):
        self._next_id += 1
        self.documents.append((chat_id, self._next_id, caption))
        return SimpleNamespace(message_id=self._next_id)


def make_state(chat_id: int = 1) -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=9, chat_id=chat_id, user_id=chat_id),
    )


def fake_cb(data: str, chat_id: int = 1, message_id: int = 50):
    return SimpleNamespace(
        data=data,
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id), message_id=message_id
        ),
        from_user=SimpleNamespace(id=chat_id),
        answer=AsyncMock(),
    )


def fake_msg(text: str, chat_id: int = 1):
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=chat_id),
        delete=AsyncMock(),
    )
