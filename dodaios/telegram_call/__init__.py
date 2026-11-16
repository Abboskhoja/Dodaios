"""The assistant's own Telegram account places a voice call to its owner.

The Bot API cannot call anyone, so this package drives a separate Telegram
*user* account over MTProto (Telethon) with NTgCalls/PyTgCalls for the media
transport. It is opt-in, owner-only and inert until the owner has signed that
account in themselves (``scripts/telegram_call_login.py``). See
``docs/telegram-call.md``.
"""
