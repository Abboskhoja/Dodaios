"""TelegramCaller end to end with a fake Telethon and a fake PyTgCalls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from dodaios.telegram_call.caller import SECRET_API_HASH, SECRET_SESSION, TelegramCaller
from dodaios.telegram_call.guard import CallGuard, CallOutcome, CallRefused

OWNER = 777
SECRETS = {SECRET_API_HASH: "hash-x", SECRET_SESSION: "session-x"}


class CallDeclined(Exception):  # noqa: N818 - mirrors pytgcalls' class name
    pass


class TimedOutAnswer(Exception):  # noqa: N818
    pass


class FakeClient:
    def __init__(self, *, authorized=True, resolves=True) -> None:
        self.authorized, self.resolves = authorized, resolves
        self.connected = self.disconnected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return self.authorized

    async def get_input_entity(self, user_id):
        if not self.resolves:
            raise ValueError("no such user")
        return user_id


class FakeCalls:
    def __init__(self, *, play_error=None) -> None:
        self.handlers: dict[str, object] = {}
        self.sent: list[bytes] = []
        self.left = False
        self.play_error = play_error
        self.recorded = False

    def on_update(self, flt):
        def deco(fn):
            self.handlers[flt] = fn
            return fn

        return deco

    async def start(self):
        pass

    async def play(self, chat_id, stream, config):
        if self.play_error:
            raise self.play_error

    async def record(self, chat_id, stream):
        self.recorded = True

    async def send_frame(self, chat_id, device, frame):
        self.sent.append(frame)

    async def leave_call(self, chat_id):
        self.left = True


FAKE_TYPES = SimpleNamespace(
    mic="mic",
    left_call=1,
    frames_filter="frames",
    chat_filter="chat",
    call_config=lambda t: ("cfg", t),
    stream=lambda r: ("stream", r),
    record_stream=lambda r: ("rec", r),
)


class FakeSession:
    def __init__(self, **sinks) -> None:
        self.sinks, self.audio, self.said, self.ended = sinks, [], [], ""

    async def handle_audio_frame(self, d):
        self.audio.append(d)

    async def handle_control(self, m):
        pass

    async def say(self, t):
        self.said.append(t)
        await self.sinks["send_json"](
            {"type": "transcript", "text": t, "is_final": True, "role": "assistant"}
        )

    async def end(self, reason=""):
        self.ended = reason


def build(*, secrets=SECRETS, client=None, calls=None, guard=None, owner=OWNER):
    client = client or FakeClient()
    calls = calls or FakeCalls()
    made: list[FakeSession] = []

    def session_factory(**sinks):
        made.append(FakeSession(**sinks))
        return made[-1]

    caller = TelegramCaller(
        cfg=SimpleNamespace(
            api_id=12345,
            ring_timeout_s=5,
            max_call_s=5,
            silence_timeout_s=60.0,
            greeting="Salom! Eshitaman.",
        ),
        owner_user_id=owner,
        guard=guard or CallGuard(clock=lambda: 0.0),
        session_factory=session_factory,
        read_secret=lambda k: secrets.get(k),
        telethon_factory=lambda *a: client,
        calls_factory=lambda c: calls,
        types_factory=lambda: FAKE_TYPES,
    )
    return caller, client, calls, made


async def refusal(caller, **kw) -> str:
    with pytest.raises(CallRefused) as err:
        await caller.call_owner(**kw)
    return err.value.code


@pytest.mark.asyncio
async def test_no_confirmation_means_no_network():
    caller, client, *_ = build()
    assert await refusal(caller, confirmed=False) == "not_confirmed"
    assert not client.connected


@pytest.mark.asyncio
async def test_without_the_owners_own_login_nothing_is_touched():
    for missing in (SECRET_API_HASH, SECRET_SESSION):
        caller, client, *_ = build(secrets={k: v for k, v in SECRETS.items() if k != missing})
        assert await refusal(caller, confirmed=True) == "not_logged_in"
        assert not client.connected


@pytest.mark.asyncio
async def test_unauthorized_session_and_unreachable_owner_are_refused():
    caller, client, calls, _ = build(client=FakeClient(authorized=False))
    assert await refusal(caller, confirmed=True) == "not_authorized"
    assert client.disconnected and not calls.recorded
    caller, *_ = build(client=FakeClient(resolves=False))
    assert await refusal(caller, confirmed=True) == "owner_unreachable"


@pytest.mark.asyncio
async def test_no_owner_configured_refuses():
    caller, *_ = build(owner=0)
    assert await refusal(caller, confirmed=True) == "no_owner"


@pytest.mark.asyncio
async def test_declined_call_blocks_the_next_ring():
    guard = CallGuard(clock=lambda: 0.0)
    caller, *_ = build(calls=FakeCalls(play_error=CallDeclined()), guard=guard)
    result = await caller.call_owner(confirmed=True)
    assert result.outcome == CallOutcome.DECLINED
    assert await refusal(caller, confirmed=True) == "blocked"


@pytest.mark.asyncio
async def test_unanswered_call_is_missed():
    caller, *_ = build(calls=FakeCalls(play_error=TimedOutAnswer()))
    assert (await caller.call_owner(confirmed=True)).outcome == CallOutcome.MISSED


@pytest.mark.asyncio
async def test_answered_call_greets_hears_speaks_and_ends_on_hangup():
    caller, client, calls, made = build()
    task = asyncio.create_task(caller.call_owner(confirmed=True))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if made and calls.recorded and made[0].said:
            break
    assert made[0].said == ["Salom! Eshitaman."]
    frame = SimpleNamespace(frame=b"\x01\x00" * 960)
    await calls.handlers["frames"](None, SimpleNamespace(chat_id=OWNER, frames=[frame]))
    await calls.handlers["frames"](
        None, SimpleNamespace(chat_id=999, frames=[frame])
    )  # another chat: ignored
    assert made[0].audio == [b"\x01\x00" * 960]
    await made[0].sinks["send_binary"](b"\x02\x00" * 2400)  # assistant speaks (100 ms)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if calls.sent:
            break
    assert calls.sent and calls.sent[0][:2] != b""
    await calls.handlers["chat"](None, SimpleNamespace(chat_id=OWNER, status=1))  # owner hangs up
    result = await asyncio.wait_for(task, 2)
    assert result.outcome == CallOutcome.ANSWERED
    assert ("assistant", "Salom! Eshitaman.") in result.transcript
    assert calls.left and client.disconnected and made[0].ended == "hangup"


@pytest.mark.asyncio
async def test_a_second_request_during_a_call_is_busy():
    caller, _, calls, made = build()
    task = asyncio.create_task(caller.call_owner(confirmed=True))
    await asyncio.sleep(0.05)
    assert await refusal(caller, confirmed=True) == "busy"
    await calls.handlers["chat"](None, SimpleNamespace(chat_id=OWNER, status=1))
    await asyncio.wait_for(task, 2)
