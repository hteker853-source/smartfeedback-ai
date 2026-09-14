"""Outbound notification bridge.

Keeps the pipeline decoupled from the Telegram transport. The Telegram bot
registers async senders at startup:
- send_admin(text)      -> plain message to the admin
- send_approval(..., action_id) -> message with human-approval buttons
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

Sender = Callable[[str], Awaitable[Any]]
ApprovalSender = Callable[[str, int], Awaitable[Any]]
SimpleApprovalSender = Callable[[str, int], Awaitable[Any]]

_sender: Sender | None = None
_approval_sender: ApprovalSender | None = None
_simple_approval_sender: SimpleApprovalSender | None = None


def set_sender(sender: Sender) -> None:
    global _sender
    _sender = sender


def set_approval_sender(sender: ApprovalSender) -> None:
    global _approval_sender
    _approval_sender = sender


def set_simple_approval_sender(sender: SimpleApprovalSender) -> None:
    """Register the sender for a plain approve/reject decision (no
    percentage buttons) — used by re-engagement outreach proposals."""
    global _simple_approval_sender
    _simple_approval_sender = sender


async def send_admin(text: str) -> None:
    if _sender is not None:
        await _sender(text)


async def send_approval(text: str, action_id: int) -> None:
    if _approval_sender is not None:
        await _approval_sender(text, action_id)
    elif _sender is not None:
        await _sender(text)


async def send_simple_approval(text: str, action_id: int) -> None:
    if _simple_approval_sender is not None:
        await _simple_approval_sender(text, action_id)
    elif _sender is not None:
        await _sender(text)
