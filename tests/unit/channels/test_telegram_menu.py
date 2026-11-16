"""The Telegram menu: five buttons, an HTTPS-only call page, owner-only answers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from dodaios.channels import telegram_menu as menu
from dodaios.channels.telegram import TelegramChannel
from dodaios.core.config import TelegramConfig


def test_five_buttons_in_the_requested_order():
    labels = [b.label for b in menu.menu_buttons("https://x.example")]
    assert [label.split()[0] for label in labels] == ["📞", "🎙", "💬", "🧠", "🔐"]


def test_call_button_opens_the_page_only_over_https():
    assert menu.menu_buttons("https://x.example/")[0].web_app_url == "https://x.example/tg/"
    for bad in ("", "http://x.example", "https://", "ftp://x"):
        first = menu.menu_buttons(bad)[0]
        assert first.web_app_url == "" and first.callback_data == "jv:call"


def test_callback_actions_are_whitelisted():
    assert menu.callback_action("jv:status") == "status"
    assert menu.callback_action("jv:evil") is None
    assert menu.callback_action("apr:1") is None


def test_status_reply_names_the_stack():
    text = menu.reply_for(
        "status", stt="faster-whisper", tts="edge-tts", voice_mode="pipeline", miniapp_ready=True
    )
    assert "faster-whisper" in text and "edge-tts" in text and "tayyor" in text


def _channel(owner: int = 1) -> tuple[TelegramChannel, list[str]]:
    sent: list[str] = []
    ch = TelegramChannel.__new__(TelegramChannel)
    ch._cfg = TelegramConfig(allowed_user_ids=[owner], miniapp_url="")

    async def fake_send(chat_id, text, *, language):
        sent.append(text)

    ch._send_text = fake_send  # type: ignore[method-assign]
    return ch, sent


def _query(data: str, user_id: int):
    async def answer(*_a, **_k):
        return None

    return SimpleNamespace(
        callback_query=SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(chat_id=9),
            answer=answer,
        )
    )


def test_menu_callback_answers_the_owner_only():
    ch, sent = _channel(owner=1)
    asyncio.run(ch._on_menu_callback(_query("jv:security", 1), None))
    asyncio.run(ch._on_menu_callback(_query("jv:security", 2), None))
    assert len(sent) == 1 and "Xavfsizlik" in sent[0]
