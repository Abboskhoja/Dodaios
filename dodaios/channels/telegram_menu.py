"""The assistant's Telegram menu: five buttons under ``/start`` and ``/menu``.

Pure data and text, no Telegram import, so it is testable on its own.  The
call button opens the live-voice Mini App (``docs/telegram-miniapp.md``); the
Bot API cannot place or receive a plain voice call, so that button is the way
to a spoken conversation inside Telegram.  The labels are user-facing Uzbek.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "MENU_TITLE",
    "MenuButton",
    "callback_action",
    "menu_buttons",
    "miniapp_page_url",
    "reply_for",
]

MENU_TITLE = "JARVIS menyusi. Tanlang:"

CALLBACK_PREFIX = "jv:"


@dataclass(frozen=True, slots=True)
class MenuButton:
    label: str
    #: ``callback`` buttons answer in the chat; the ``web_app`` button opens the page.
    callback_data: str = ""
    web_app_url: str = ""


def miniapp_page_url(base: str) -> str:
    """The HTTPS page Telegram may open, or ``""`` (Mini Apps need HTTPS)."""
    base = (base or "").strip().rstrip("/")
    if not base.startswith("https://") or len(base) <= len("https://"):
        return ""
    return f"{base}/tg/"


def menu_buttons(miniapp_url: str) -> list[MenuButton]:
    """Call, voice message, text chat, AI status, security: one per row."""
    page = miniapp_page_url(miniapp_url)
    call = (
        MenuButton("📞 JARVIS bilan gaplashish", web_app_url=page)
        if page
        else MenuButton("📞 JARVIS bilan gaplashish", callback_data=f"{CALLBACK_PREFIX}call")
    )
    return [
        call,
        MenuButton("🎙 Ovozli xabar", callback_data=f"{CALLBACK_PREFIX}voice"),
        MenuButton("💬 Matnli suhbat", callback_data=f"{CALLBACK_PREFIX}text"),
        MenuButton("🧠 AI holati", callback_data=f"{CALLBACK_PREFIX}status"),
        MenuButton("🔐 Xavfsizlik", callback_data=f"{CALLBACK_PREFIX}security"),
    ]


def callback_action(data: str) -> str | None:
    """The action of a menu callback (``call``, ``voice``, ...), else ``None``."""
    if not data.startswith(CALLBACK_PREFIX):
        return None
    action = data[len(CALLBACK_PREFIX) :]
    return action if action in {"call", "voice", "text", "status", "security"} else None


def reply_for(action: str, *, stt: str, tts: str, voice_mode: str, miniapp_ready: bool) -> str:
    """The chat answer for one menu button."""
    if action == "call":
        return (
            "Jonli qo'ng'iroq hali sozlanmagan: Mini App uchun HTTPS manzil kerak. "
            "Sozlamalarda [integrations.telegram] miniapp_url ni to'ldiring "
            "(docs/telegram-miniapp.md)."
        )
    if action == "voice":
        return "Ovozli xabar yuboring, men uni eshitib, javob yozaman."
    if action == "text":
        return "Shunchaki yozing, men javob beraman. Bu ilovadagi suhbatning o'zi."
    if action == "status":
        return (
            "🧠 AI holati\n"
            f"• Ovozni matnga: {stt or 'noma`lum'}\n"
            f"• Matnni ovozga: {tts or 'noma`lum'}\n"
            f"• Ovoz rejimi: {voice_mode or 'noma`lum'}\n"
            f"• Jonli qo'ng'iroq: {'tayyor' if miniapp_ready else 'sozlanmagan'}"
        )
    if action == "security":
        return (
            "🔐 Xavfsizlik\n"
            "• Bot faqat egasiga javob beradi.\n"
            "• Qo'ng'iroq sahifasi Telegram imzosini serverda tekshiradi.\n"
            "• Har ulanish uchun bir martalik, 60 soniyalik bilet beriladi.\n"
            "• Bot tokeni faqat xavfsiz saqlashda turadi, hech qayerda ko'rsatilmaydi."
        )
    return ""
