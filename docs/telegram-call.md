# The assistant rings its owner on Telegram

The assistant's **own Telegram user account** places a real voice call to the
owner's Telegram. The owner's phone shows Telegram's normal incoming-call
screen; the owner answers and talks to the assistant. Opt-in, owner only,
inert until the owner signs that account in with their own hand.

This is separate from the Mini App ([telegram-miniapp.md](telegram-miniapp.md)):
a microphone inside a web page is not a Telegram call, and this feature does
not need a tunnel or a public server.

## How it works

```
call-owner tool (approval asks the owner first)
  -> CallGuard (confirmed? one at a time? pause? daily cap? declined block?)
  -> Telethon StringSession (session lives in the credential store only)
  -> PyTgCalls p2p call: phone.requestCall -> acceptCall/confirmCall (DH key
     exchange) -> NTgCalls native media transport
  incoming frames (s16le 48 kHz) -> CallAudioBridge -> BrowserVoiceSession
     (VAD -> Faster-Whisper -> brain -> TTS, barge-in) -> 24 kHz PCM
  -> resample -> paced 20 ms frames -> NTgCalls -> the owner's phone
```

| Piece | File |
|---|---|
| Rules: confirmation, one call, pause, cap, declined block | `dodaios/telegram_call/guard.py` |
| Audio bridge: framing, pacing, barge-in flush, idle timeout | `dodaios/telegram_call/bridge.py` |
| One call, all externals injected | `dodaios/telegram_call/caller.py` |
| Config + credential + speech-stack wiring | `dodaios/telegram_call/runtime.py` |
| `call-owner` tool (risk tier `ask`, no target argument) | `dodaios/plugins/tool/call_owner.py` |
| Owner's one-time sign-in of the assistant's account | `scripts/telegram_call_login.py` |

## Feasibility, checked against the code (not the README)

Inspected `py-tgcalls 3.0.1` and `ntgcalls 3.0.7` (Telethon 1.45.0 bridge):

- `PyTgCalls.play(user_id, ...)` with a positive id runs the **private** call
  flow: `request_call`, then on the callee's answer `exchange_keys` and
  `confirm_call`; `discard_call` on timeout. Declined/busy/unanswered calls
  raise `CallDeclined` / `CallBusy` / `TimedOutAnswer`.
- Live audio both ways without ffmpeg: `ExternalMedia.AUDIO` streams,
  `send_frame(..., Device.MICROPHONE, pcm)` out, and
  `stream_frame(Direction.INCOMING, Device.SPEAKER)` updates in.
- `ntgcalls` ships a macOS arm64 wheel for CPython 3.12 (`macosx_14_0_arm64`);
  it imports and answers `ping` on this Mac, and the call types the caller uses
  construct against the real package.

**What is not proven:** a call over the real Telegram network. That needs the
owner's signed-in second account and a live phone, so the two-way audio
transport has only been exercised against fakes (`tests/unit/telegram_call`).
Telegram can still refuse or throttle a fresh account.

## Safety

- Without the owner's own sign-in (`telegram_call_session` and
  `telegram_call_api_hash` in the credential store, `api_id` in config) every
  call is refused with `not_logged_in` and **no network is touched**.
- Only `[integrations.telegram_call].owner_user_id` (default: the paired
  Telegram owner) can be called; the tool schema has no target field.
- Each call needs the owner's approval (risk tier `ask`), runs one at a time,
  pauses 5 minutes afterwards, stops at 3 attempts a day, and a declined or
  hung-up call blocks any ring for 30 minutes. No automatic redial.
- The login script reads the code and password without echo and writes the
  session only to the credential store. It refuses the owner's own account.
- Calling third parties is not wired and must not be added without a new
  decision.
- Automating a user account can breach Telegram's terms; use a dedicated
  account and expect that Telegram may limit it.

## Setup (needs you)

1. Install the extra: `pip install 'dodaios[telegram-call]'` (pinned versions;
   `py-tgcalls` is LGPL-3).
2. A **second Telegram account** (own phone number) for the assistant. Get its
   `api_id` / `api_hash` at my.telegram.org -> API development tools.
3. In a Terminal window (hidden prompts): `python scripts/telegram_call_login.py`.
4. In `dodaios.toml`:
   ```toml
   [integrations.telegram_call]
   enabled = true
   api_id = <your api_id>
   ```
5. From the owner's phone, open the assistant account and press Start / save it
   as a contact (so Telegram lets it ring you).
6. Ask the assistant to call you; approve the confirmation.
