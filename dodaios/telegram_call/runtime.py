"""Wires the real config, credential store and speech stack into a caller."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from dodaios.core import config as cfg_mod
from dodaios.telegram_call.caller import TelegramCaller
from dodaios.telegram_call.guard import CallGuard

log = logging.getLogger("dodaios.telegram_call")

_caller: TelegramCaller | None = None
_guard: CallGuard | None = None


def owner_id_of(cfg: Any) -> int:
    """The one user the assistant may call: the configured id, else the paired owner."""
    call_cfg = cfg.integrations.telegram_call
    if int(call_cfg.owner_user_id or 0):
        return int(call_cfg.owner_user_id)
    owners = cfg.integrations.telegram.allowed_user_ids
    return int(owners[0]) if owners else 0


def _session_factory(cfg: Any):
    def factory(*, send_binary: Any, send_json: Any) -> Any:
        from dodaios.browser_voice.route import _build_classic_browser_session

        session = _build_classic_browser_session(
            state=SimpleNamespace(),
            cfg=cfg,
            bus=None,
            session_id=f"tgcall-{uuid4().hex[:8]}",
            send_binary=send_binary,
            send_json=send_json,
        )
        if session is None:
            raise RuntimeError("The speech stack (STT, brain, TTS) could not be built.")
        return session

    return factory


def get_caller(cfg: Any | None = None) -> TelegramCaller:
    """The process-wide caller; its guard persists so pauses and caps hold."""
    global _caller, _guard  # noqa: PLW0603
    cfg = cfg or cfg_mod.load_config()
    call_cfg = cfg.integrations.telegram_call
    if _guard is None:
        _guard = CallGuard(
            max_per_day=int(call_cfg.max_calls_per_day),
            pause_s=float(call_cfg.pause_between_calls_s),
            declined_block_s=float(call_cfg.declined_block_s),
        )
    if _caller is None:
        _caller = TelegramCaller(
            cfg=call_cfg,
            owner_user_id=owner_id_of(cfg),
            guard=_guard,
            session_factory=_session_factory(cfg),
            read_secret=lambda key: cfg_mod.get_secret(key, None),
        )
    return _caller


def reset_for_tests() -> None:
    global _caller, _guard  # noqa: PLW0603
    _caller = _guard = None
