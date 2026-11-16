# Telegram Mini App: a live voice call with the assistant

The Telegram Bot API cannot place or receive a voice call. What Telegram does
offer is a **Mini App**: a web page that opens inside Telegram, with a
microphone and a speaker. This feature puts the assistant's browser voice call
(`/ws/audio`: microphone, speech-to-text, brain, text-to-speech, speaker) on
that page. The conversation is the same one the app and the Telegram chat
continue; nothing here is a second conversation.

```
iPhone Telegram -> bot menu "📞" (inline web_app button) -> https://<host>/tg/
  page: POST /api/telegram/miniapp/session {initData}  -> single-use WS ticket
  page: wss://<host>/ws/audio?ticket=...   (raw PCM up, 24 kHz PCM down)
```

## What is in the box

| Piece | File |
|---|---|
| The call page (one static file, no build) | `dodaios/ui/web/miniapp/index.html` |
| `initData` verification, token stores, rate limiter | `dodaios/channels/telegram_miniapp.py` |
| `GET /tg/`, `POST /api/telegram/miniapp/session`, `POST /api/telegram/miniapp/handoff` | `dodaios/ui/web/telegram_miniapp_routes.py` |
| The bot's five-button menu (`/start`, `/menu`) | `dodaios/channels/telegram_menu.py` |

The page shows Start/End call, mute, listening / thinking / speaking, a live
transcript, reconnects with backoff when the socket drops, and names every
failure (microphone refused, bad signature, expired session, server busy).
Speaking over the assistant stops its playback and sends `barge_in`.

## Setup (needs you)

1. **Create the bot** in Telegram with `@BotFather` (`/newbot`). Do not paste
   the token anywhere but the credential store: run `python -m dodaios --wizard`
   or save it under Settings. It is read from the credential store
   (`telegram_bot_token`) and never printed, logged or returned.
2. Send `/start` to the bot from your own account. The first private sender is
   paired as the owner (`[integrations.telegram] allowed_user_ids`), and set
   `enabled = true`.
3. **An HTTPS address.** Telegram opens Mini Apps over HTTPS only. Put a tunnel
   or a domain in front of this app (for example a Cloudflare quick tunnel to
   `127.0.0.1:<port>`), then set
   `[integrations.telegram] miniapp_url = "https://<host>"`. That host is added
   to the trusted hosts automatically. **Turn on "Ask for the key in the
   browser" (`ui.require_browser_login`) before exposing the port**: the
   tunnel forwards the whole app, and only the `/tg/` page and the two
   `/api/telegram/miniapp/*` routes are meant to be reachable without a key.
4. Restart. `/start` or `/menu` now shows 📞 JARVIS bilan gaplashish as a
   button that opens the page.

For the spoken language, `[stt]`, `[tts]` and `[voice].mode` decide the stack.
With `voice.mode = "realtime"` a duplex provider is used when one is ready and
the classic pipeline otherwise; for a fixed Uzbek stack (Faster-Whisper in,
Edge TTS out) use `voice.mode = "pipeline"`.

## Security model

- `initData` is checked on the server: HMAC-SHA256 with the key
  `HMAC("WebAppData", bot_token)`, compared in constant time; `auth_date` must
  be at most one hour old (and not in the future); the Telegram user id must be
  in `allowed_user_ids`. Nothing the page claims about itself is trusted.
- The result is a **single-use, 60-second WebSocket ticket** for `/ws/audio`
  (the same mechanism the desktop UI uses), plus a page-session token (4 hours,
  memory only) so a reconnect does not depend on `initData` age.
- 20 attempts a minute, and 5 failures per 5 minutes before every attempt is
  refused with 429.
- The page is served with `no-store`, a strict Content-Security-Policy and
  `frame-ancestors` limited to Telegram's web clients.
- The Control API, settings and every other route keep the normal boundary;
  nothing on localhost is opened.
- Unpaired bot (no token or no owner) answers 503 and never mints a ticket.

## iPhone: microphone inside Telegram

Telegram's in-app browser may not offer the microphone on every iOS version.
The page then shows a message and a **"Safari'da ochish"** button: it asks
`POST /api/telegram/miniapp/handoff` for a one-use code (valid 60 seconds) and
opens `https://<host>/tg/#h=<code>` in Safari, where the microphone prompt is
standard. The code travels in the URL fragment, so it never reaches a log.

## Phase 2 (research only): the assistant calling you

The Bot API has no call method, so a call that *starts from the assistant*
needs a separate Telegram **user** account driven over MTProto (a userbot
library with a call stack such as py-tgcalls on Pyrogram or Telethon). Before
anyone builds it, know that:

- The account needs its own phone number, and a stored session is full access
  to that account: it must live in the credential store, never in a file.
- It needs an `api_id` / `api_hash` from my.telegram.org.
- Automating a user account can violate Telegram's terms and get the account
  limited or banned; calls to a person who has not agreed are spam.
- Nothing in this repository logs in, creates a session or calls anyone, and
  nothing should until the owner approves each of those steps.
