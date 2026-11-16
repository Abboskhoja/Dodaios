"""``call-owner`` tool: the assistant rings its owner on Telegram.

Owner only, by construction: the schema has no target. Risk tier ``ask``, so
the approval workflow asks the owner to confirm each call before it goes out;
execution therefore passes ``confirmed=True``. The guard still enforces one
call at a time, a pause after every attempt, a daily cap and a long block after
a declined call. Without the owner's own Telegram login nothing is dialed.
"""

from __future__ import annotations

import logging
from typing import Any

from dodaios.core.protocols import ToolResult
from dodaios.telegram_call.guard import CallRefused

log = logging.getLogger(__name__)

_REFUSALS = {
    "not_confirmed": "The owner has not confirmed this call.",
    "busy": "A call is already in progress.",
    "blocked": "Not calling again yet: the last call ended recently or was declined.",
    "daily_limit": "The daily call limit is reached.",
    "no_owner": "No owner Telegram account is paired, so there is nobody to call.",
    "not_logged_in": (
        "The assistant's Telegram account is not signed in. The owner runs "
        "`python scripts/telegram_call_login.py` once (docs/telegram-call.md)."
    ),
    "not_authorized": "The stored Telegram login is no longer valid; sign in again.",
    "owner_unreachable": "The owner's Telegram account could not be resolved for a call.",
}


class CallOwnerTool:
    name: str = "call-owner"
    description: str = (
        "Ring the owner on Telegram with a real voice call from the assistant's own "
        "Telegram account and talk to them live. Only the owner can be called; "
        "use it when the owner asks to be called. Optional greeting is spoken first."
    )
    risk_tier: str = "ask"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "greeting": {
                "type": "string",
                "description": "Optional first sentence when the owner picks up.",
            }
        },
    }
    input_examples: list[dict[str, Any]] = [{}, {"greeting": "Salom! Eshitaman."}]

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        from dodaios.core.config import load_config
        from dodaios.telegram_call.runtime import get_caller

        cfg = load_config()
        if not cfg.integrations.telegram_call.enabled:
            return ToolResult(
                success=False,
                output="",
                error="Telegram calling is off. Enable [integrations.telegram_call] first.",
            )
        greeting = str(args.get("greeting") or "").strip()[:200] or None
        try:
            result = await get_caller(cfg).call_owner(confirmed=True, greeting=greeting)
        except CallRefused as exc:
            return ToolResult(success=False, output="", error=_REFUSALS.get(exc.code, exc.code))
        except Exception as exc:  # noqa: BLE001 - never crash the turn
            log.warning("call-owner failed: %s", type(exc).__name__)
            return ToolResult(success=False, output="", error="The call could not be placed.")
        if result.outcome == "answered":
            return ToolResult(
                success=True, output=f"The owner answered; the call lasted {int(result.seconds)} s."
            )
        return ToolResult(
            success=False,
            output="",
            error=f"The owner did not take the call ({result.outcome}); not ringing again.",
        )
