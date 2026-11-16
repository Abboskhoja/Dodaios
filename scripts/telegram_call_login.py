#!/usr/bin/env python
"""Sign the assistant's own Telegram account in, once, by the owner's hand.

Run it in a terminal window (it asks for hidden input, so not through a tool):

    python scripts/telegram_call_login.py            # sign in
    python scripts/telegram_call_login.py --logout   # forget the stored session

It asks for ``api_id`` / ``api_hash`` (my.telegram.org -> API development
tools), the phone number of the account the ASSISTANT will use (not yours),
the login code Telegram sends and, if set, the two-step password. The code and
password are read without echo and are never written anywhere; the resulting
session goes to the credential store only. Nothing is printed except whether
it worked.
"""

# ruff: noqa: ASYNC250  (an interactive one-off: nothing runs concurrently with the prompts)
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from dodaios.core import config as cfg_mod
from dodaios.telegram_call.caller import SECRET_API_HASH, SECRET_SESSION
from dodaios.telegram_call.runtime import owner_id_of


async def login() -> int:  # noqa: ASYNC250 - interactive one-off, no concurrent work
    try:
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError
        from telethon.sessions import StringSession
    except ImportError:
        print("Telethon is missing: pip install 'dodaios[telegram-call]'")
        return 2

    cfg = cfg_mod.load_config()
    owner = owner_id_of(cfg)
    if not owner:
        print("No owner is paired yet: connect the Telegram bot and send it /start first.")
        return 2
    print(f"The assistant's account will be allowed to call ONLY Telegram user id {owner}.")
    if input("Type that id to confirm: ").strip() != str(owner):
        print("Not confirmed; nothing was done.")
        return 1

    api_id = int(cfg.integrations.telegram_call.api_id or 0) or int(input("api_id: ").strip())
    api_hash = getpass.getpass("api_hash (hidden): ").strip()
    phone = input("Phone number of the ASSISTANT's account (+...): ").strip()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        await client.send_code_request(phone)
        code = getpass.getpass("Login code Telegram sent (hidden): ").strip()
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass("Two-step password (hidden): "))
        me = await client.get_me()
        if getattr(me, "id", None) == owner:
            print("That is the owner's own account; the assistant needs a different one.")
            return 1
        ok = cfg_mod.set_secret(SECRET_API_HASH, api_hash) and cfg_mod.set_secret(
            SECRET_SESSION, client.session.save()
        )
    finally:
        await client.disconnect()
    if not ok:
        print("The credential store refused the session; nothing was kept.")
        return 1
    print(f"Signed in. Set [integrations.telegram_call] enabled = true and api_id = {api_id}.")
    return 0


def logout() -> int:
    cfg_mod.set_secret(SECRET_SESSION, "")
    cfg_mod.set_secret(SECRET_API_HASH, "")
    print("The stored Telegram call session and api_hash were cleared.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--logout", action="store_true")
    args = parser.parse_args()
    return logout() if args.logout else asyncio.run(login())


if __name__ == "__main__":
    sys.exit(main())
