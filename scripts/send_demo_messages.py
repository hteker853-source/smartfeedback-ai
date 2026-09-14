"""ONE-OFF DEMO SCRIPT — sends all 22 Telegram message templates (sample
data, English) to the admin chat for visual review.

SAFETY: this script NEVER touches the database. It does not import
Repository, Pipeline, or app.db, and never calls notify.py (which would
require a live Pipeline/TelegramBot). It talks to the Telegram Bot API
directly via httpx, exactly like the existing app/mock_call.py pattern,
using only settings read from .env.

All approval-style messages (B1/B2/B3) use inline buttons whose
callback_data is the harmless placeholder "demo_ignore" — never a real
action/record id — so pressing one in Telegram triggers the bot's normal
"not found" handling and can never approve/reject/call anything real.

Usage:
    python -m scripts.send_demo_messages
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

SLEEP_SECONDS = 1.5
DEMO_CALLBACK = "demo_ignore"


def _buttons(*rows: list[str]) -> dict[str, Any]:
    """Build a Telegram inline_keyboard with every button wired to the
    harmless DEMO_CALLBACK placeholder (never a real action id)."""
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": DEMO_CALLBACK} for label in row]
            for row in rows
        ]
    }


MESSAGES: list[tuple[str, str, dict[str, Any] | None]] = [
    (
        "A1",
        "SmartFeedback — Daily Digest\n"
        "Last 24 hours.\n"
        "- cold fries: active (increasing, 4 this week vs 1 last week, 3 different customers)\n"
        "Overall satisfaction: 3.2/5\n"
        "Foreign-language customer satisfaction: improving (+12.0%)",
        None,
    ),
    (
        "B1",
        "Customer +1-555-0100 shows high loyalty and left negative feedback.\n"
        "A compensation offer may be worth considering.\n"
        "Choose a rate (suggestion only, not auto-applied):",
        _buttons(["25%", "50%", "75%", "Custom"], ["Reject"]),
    ),
    (
        "B2",
        "Customer +1-555-0101 order frequency has dropped: normal rhythm ~10 days,\n"
        "now 22 days (~2.2x). Past verified complaints: cold fries.\n"
        "Should we offer this customer a discount? Choose a rate (suggestion only):",
        _buttons(["25%", "50%", "75%", "Custom"], ["Reject"]),
    ),
    (
        "B3",
        "Customer +1-555-0102 hasn't ordered in 15+ days and had a previously\n"
        "verified issue (cold fries).\n"
        "Suggested call message: 'Hi, we missed you! Did we resolve the cold fries\n"
        "issue from your last order?'\n"
        "Approve calling this customer with this message?",
        _buttons(["Call", "Reject"]),
    ),
    (
        "C1",
        "Urgent customer issue\n"
        "Missing item: drink\n"
        "Reported in customer feedback call and confirmed by evidence.",
        None,
    ),
    (
        "C2",
        "Recurring operational issue\n"
        "Issue: cold fries\n"
        "Evidence confirmed; high business impact and rising recurrence detected.",
        None,
    ),
    (
        "D1",
        "Database backup could not be verified: [demo result placeholder]",
        None,
    ),
    (
        "D2",
        "Database backup failed. Check disk/permission status.",
        None,
    ),
    (
        "D3",
        "SmartFeedback cannot reach the database. Check the database path.",
        None,
    ),
    (
        "D4",
        "CALL-E API key missing. Enter it via /setup or add it to .env.",
        None,
    ),
    (
        "D5",
        "AWS/Bedrock credentials missing. Bedrock analysis is currently unavailable;\n"
        "deterministic fallback is active. Enter credentials via /setup or .env.",
        None,
    ),
    (
        "D6",
        "Telegram configuration missing. Notifications cannot be sent.",
        None,
    ),
    (
        "E1",
        "Test call started.\nNumber: +1-555-0103\nProvider: CALL-E",
        None,
    ),
    (
        "E2",
        "Call dispatched to +1-555-0103.",
        None,
    ),
    (
        "E3",
        "[MOCK] Test call completed. Sample transcript generated.",
        None,
    ),
    (
        "E4",
        "Call analysis complete.\nSatisfaction: 2/5 | Sentiment: negative | Issue: cold fries",
        None,
    ),
    (
        "E5",
        "Call completed but no transcript was captured.",
        None,
    ),
    (
        "E6",
        "Call failed (call_failed).",
        None,
    ),
    (
        "E7",
        "Call went unanswered (voicemail).",
        None,
    ),
    (
        "E8",
        "Call attempt failed (failed).",
        None,
    ),
    (
        "E9",
        "Call went unanswered.",
        None,
    ),
    (
        "E10",
        "Call completed (transcript unavailable).",
        None,
    ),
]


def _redact(token: str) -> str:
    if len(token) <= 8:
        return "****"
    return f"{token[:4]}...{token[-4:]}"


def main() -> None:
    settings = get_settings()
    token = settings.telegram_bot_token
    chat_id = settings.telegram_admin_chat_id

    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_ADMIN_CHAT_ID missing from .env — aborting.")
        sys.exit(1)

    print(f"Token loaded: {_redact(token)}")
    print(f"Admin chat id: {chat_id}")
    print(f"About to send {len(MESSAGES)} demo messages, {SLEEP_SECONDS}s apart.")
    print("This script does NOT touch the database (no Repository/Pipeline import).\n")

    base_url = f"https://api.telegram.org/bot{token}/sendMessage"
    sent = 0
    failed: list[str] = []

    with httpx.Client(timeout=15) as client:
        for i, (code, text, reply_markup) in enumerate(MESSAGES, start=1):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            try:
                resp = client.post(base_url, json=payload)
                ok = resp.json().get("ok", False)
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"  -> transport error: {exc}")

            if ok:
                sent += 1
                print(f"Sent: {code}")
            else:
                failed.append(code)
                print(f"FAILED: {code} -> {resp.text[:200] if 'resp' in dir() else 'no response'}")

            if i < len(MESSAGES):
                time.sleep(SLEEP_SECONDS)

    print(f"\n=== DONE: {sent}/{len(MESSAGES)} messages sent successfully. ===")
    if failed:
        print(f"Failed codes: {', '.join(failed)}")


if __name__ == "__main__":
    main()
