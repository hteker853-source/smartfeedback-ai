"""Mock end-to-end test runner.

Simulates a successful CALL-E call with a Turkish transcript for a given phone
number and runs the full pipeline (DB -> analysis -> Telegram summary) without
making a real phone call. The call is explicitly marked as simulated.

Usage:
    python -m app.mock_call --phone 05445974126
    python -m app.mock_call --phone 05445974126 --transcript "Ayran gelmedi."
"""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from . import notify
from .config import get_settings
from .db import init_db
from .pipeline import Pipeline
from .repository import Repository

DEFAULT_TRANSCRIPT = (
    "Yemek çok güzeldi, çok beğendim ama patatesler soğuk geldi. "
    "Ayrıca ayran sipariş etmiştik ama o gelmedi."
)


def _json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return []
    return []


def _telegram_sender(settings, token: str, chat_id: str):
    async def send(text: str) -> None:
        if not (token and chat_id):
            print("[telegram] admin kanalı yok — mesaj gönderilmedi")
            return
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                )
                ok = resp.json().get("ok", False)
                print(f"[telegram] sendMessage -> {'OK' if ok else 'FAILED'}")
        except Exception as exc:  # noqa: BLE001
            print(f"[telegram] hata: {exc}")

    return send


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock CALL-E call + full pipeline")
    parser.add_argument("--phone", default="05445974126")
    parser.add_argument("--transcript", default=None)
    parser.add_argument("--locale", default="tr")
    args = parser.parse_args()

    settings = get_settings()
    init_db(settings.database_file)
    repo = Repository(settings.database_file)
    pipeline = Pipeline(repo, settings)

    transcript = args.transcript or DEFAULT_TRANSCRIPT
    token = settings.telegram_bot_token
    chat_id = settings.telegram_admin_chat_id

    notify.set_sender(_telegram_sender(settings, token, chat_id))

    result = asyncio.run(
        pipeline.simulate_call(args.phone, transcript, args.locale, notify_status=True)
    )

    print("\n=== MOCK CALL SONUCU ===")
    print(f"Numara:        {result['phone']}")
    print(f"Call ID:       {result['call_id']} (CALL-E: {result['calle_call_id']})")
    print(f"Order ID:      {result['order_id']}")
    processed = result.get("processed")
    if processed:
        fb = processed["feedback"]
        d = processed["decision"]
        missing = _json_list(fb.get("missing_products"))
        print(f"Memnuniyet:    {fb.get('satisfaction')}/5")
        print(f"Duygu:         {fb.get('sentiment')}")
        print(f"Öncelik:       {fb.get('priority')}")
        print(f"Eksik ürün:    {[m['name'] for m in missing] if missing else 'yok'}")
        print(f"Karar:         {d.get('insight_title')}")
        print(f"Açıklama:      {d.get('insight_description')}")
        print(f"Telegram alarm: {'EVET' if d.get('alert') else 'hayır'}")
        if d.get("alert"):
            print(f"Alarm mesajı:  {d.get('alert_message')}")
    print("=== BİTİŞ ===\n")


if __name__ == "__main__":
    main()
