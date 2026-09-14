"""Telegram bot — business panel + critical alerts.

First `/start` becomes the admin (chat id persisted to `.env`). `/setup` walks
through missing API keys (CALL-E + AWS) one at a time, writing each answer to
`.env` without echoing it. Sending a phone number starts the two-option order
flow. Any other text is treated as a natural-language query over stored data.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import notify, secrets
from .actions import execute_approved_action
from .config import Settings
from .pipeline import Pipeline
from .repository import Repository

logger = logging.getLogger(__name__)

PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")

# (env key, settings attribute, human label)
REQUIRED_KEYS = [
    ("CALLE_API_KEY", "calle_api_key", "CALL-E API anahtarı"),
    ("AWS_ACCESS_KEY_ID", "aws_access_key_id", "AWS Access Key ID"),
    ("AWS_SECRET_ACCESS_KEY", "aws_secret_access_key", "AWS Secret Access Key"),
]

DEMO_TRANSCRIPT = (
    "Yemek çok güzeldi, çok beğendim ama patatesler soğuk geldi. "
    "Ayrıca ayran da sipariş etmiştik ama o gelmedi."
)

CONFIRMATION = "✅ Tüm API anahtarları tanımlandı, sistem tam kapasite çalışıyor"


class TelegramBot:
    def __init__(self, settings: Settings, pipeline: Pipeline, repo: Repository) -> None:
        self.settings = settings
        self.pipeline = pipeline
        self.repo = repo
        # chat_id -> ordered list of env keys still to collect
        self._setup: dict[int, list[str]] = {}
        # chat_id -> action_id awaiting a custom percentage value
        self._pending_action_pct: dict[int, int] = {}
        self._app: Application | None = None

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if not self.settings.telegram_bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN not set; Telegram disabled")
            return
        self._app = (
            Application.builder().token(self.settings.telegram_bot_token).build()
        )
        self._app.add_handler(CommandHandler("start", self.cmd_start))
        self._app.add_handler(CommandHandler("setup", self.cmd_setup))
        self._app.add_handler(CommandHandler("health", self.cmd_health))
        self._app.add_handler(CommandHandler("demo", self.cmd_demo))
        self._app.add_handler(CommandHandler("call", self.cmd_call))
        self._app.add_handler(CallbackQueryHandler(self.on_callback))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text)
        )
        notify.set_sender(self.send_admin)
        notify.set_approval_sender(self.send_approval)
        notify.set_simple_approval_sender(self.send_reengagement_approval)
        try:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            logger.info("Telegram bot polling started")
        except Exception:  # noqa: BLE001
            logger.exception("Telegram bot failed to start (invalid token?); disabled")
            self._app = None

    async def stop(self) -> None:
        if self._app is not None:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:  # noqa: BLE001
                logger.exception("Error during Telegram shutdown")

    async def send_admin(self, text: str) -> None:
        chat_id = self._admin_chat_id()
        if not chat_id or self._app is None:
            logger.info("Alert (no admin channel): %s", text[:120])
            return
        try:
            await self._app.bot.send_message(chat_id=chat_id, text=text)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send Telegram message")

    async def send_approval(self, text: str, action_id: int) -> None:
        chat_id = self._admin_chat_id()
        if not chat_id or self._app is None:
            logger.info("Approval (no admin channel): %s", text[:120])
            return
        keyboard = [
            [
                InlineKeyboardButton("25%", callback_data=f"approve:{action_id}:25"),
                InlineKeyboardButton("50%", callback_data=f"approve:{action_id}:50"),
                InlineKeyboardButton("75%", callback_data=f"approve:{action_id}:75"),
                InlineKeyboardButton("Özel", callback_data=f"approve:{action_id}:custom"),
            ],
            [
                InlineKeyboardButton("Reddet", callback_data=f"reject:{action_id}"),
            ],
        ]
        try:
            await self._app.bot.send_message(
                chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send approval message")

    async def send_reengagement_approval(self, text: str, action_id: int) -> None:
        """Simple approve/reject prompt (no percentage buttons) — used for
        re-engagement outreach proposals (GÖREV 2)."""
        chat_id = self._admin_chat_id()
        if not chat_id or self._app is None:
            logger.info("Reengagement approval (no admin channel): %s", text[:120])
            return
        keyboard = [
            [
                InlineKeyboardButton("✅ Ara", callback_data=f"reengage_approve:{action_id}"),
                InlineKeyboardButton("❌ Reddet", callback_data=f"reject:{action_id}"),
            ],
        ]
        try:
            await self._app.bot.send_message(
                chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send reengagement approval message")

    # -- helpers ---------------------------------------------------------
    def _admin_chat_id(self) -> str | int | None:
        cid = self.settings.telegram_admin_chat_id
        if not cid:
            return None
        try:
            return int(cid)
        except ValueError:
            return cid

    def _is_admin(self, chat_id: int) -> bool:
        return str(chat_id) == str(self.settings.telegram_admin_chat_id)

    def _make_admin(self, chat_id: int) -> None:
        self.settings.telegram_admin_chat_id = str(chat_id)
        secrets.set_env_var("TELEGRAM_ADMIN_CHAT_ID", str(chat_id))

    def _missing_keys(self) -> list[str]:
        missing: list[str] = []
        for env_key, attr, _label in REQUIRED_KEYS:
            if not getattr(self.settings, attr):
                missing.append(env_key)
        return missing

    def _label_for(self, env_key: str) -> str:
        for key, _attr, label in REQUIRED_KEYS:
            if key == env_key:
                return label
        return env_key

    def _all_complete(self) -> bool:
        return self.settings.has_calle and self.settings.has_aws

    def _apply_secret(self, env_key: str, value: str) -> None:
        for key, attr, _label in REQUIRED_KEYS:
            if key == env_key:
                setattr(self.settings, attr, value.strip())
        secrets.set_env_var(env_key, value.strip())
        self.pipeline.refresh()

    async def _start_setup(self, update: Update) -> None:
        chat_id = update.effective_chat.id
        missing = self._missing_keys()
        if not missing:
            await update.message.reply_text(CONFIRMATION)
            return
        self._setup[chat_id] = missing
        await self._prompt_key(update.message, missing[0])

    async def _prompt_key(self, message: Any, env_key: str) -> None:
        label = self._label_for(env_key)
        await message.reply_text(
            f"🔐 Kurulum — {label}\n\n"
            f"Lütfen {label} değerini gönderin. Değer .env dosyasına kaydedilecek "
            f"ve bu sohbette gizlenecektir.\n\n/cancel ile kurulumu iptal edebilirsiniz."
        )

    # -- commands --------------------------------------------------------
    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not self.settings.telegram_admin_chat_id:
            self._make_admin(chat_id)
            await update.message.reply_text(
                "SmartFeedback AI yöneticisi olarak kaydedildiniz."
            )
        else:
            await update.message.reply_text("SmartFeedback AI çalışıyor.")

        if self._missing_keys():
            await update.message.reply_text(
                "Eksik API anahtarları var. Kuruluma başlıyoruz."
            )
            await self._start_setup(update)
        else:
            await update.message.reply_text(
                "Kullanım:\n"
                "- Müşteri telefon numarası gönderin\n"
                "- /setup → API anahtarlarını yönet\n"
                "- /health → sistem durumu\n"
                "- /demo → örnek analiz\n"
                "- Soru sorun (örn. 'bu hafta en çok hangi sorun?')"
            )

    async def cmd_setup(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_chat.id):
            await update.message.reply_text("Bu komut yalnızca yönetici içindir.")
            return
        if self._missing_keys():
            await self._start_setup(update)
        else:
            await update.message.reply_text(CONFIRMATION)

    async def cmd_health(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_chat.id):
            await update.message.reply_text("Bu komut yalnızca yönetici içindir.")
            return
        await update.message.reply_text(self._health_text())

    async def cmd_demo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_chat.id):
            await update.message.reply_text("Bu komut yalnızca yönetici içindir.")
            return
        result = await self._run_demo()
        await update.message.reply_text(result)

    async def cmd_call(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_chat.id):
            await update.message.reply_text("Bu komut yalnızca yönetici içindir.")
            return
        if not ctx.args:
            await update.message.reply_text("Kullanım: /call <telefon numarası>")
            return
        phone = ctx.args[0]
        result = await self.pipeline.trigger_immediate_call(phone)
        await update.message.reply_text(
            f"📞 {result.get('phone')} için arama tetiklendi.\n"
            f"Durum: {result.get('status')}"
        )

    def _health_text(self) -> str:
        s = self.settings
        lines = [
            "SmartFeedback AI — Sağlık",
            f"LLM (Bedrock): {'✓' if s.has_aws else '✗'}",
            f"DeepSeek: {'✓' if s.has_deepseek else '✗'}",
            f"CALL-E: {'✓' if s.has_calle else '✗'}",
            f"Telegram admin: {'✓' if s.telegram_admin_chat_id else '✗'}",
        ]
        return "\n".join(lines)

    def _action_expired(self, action: dict[str, Any]) -> bool:
        exp = action.get("expires_at")
        if not exp:
            return False
        try:
            ts = datetime.fromisoformat(exp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) > ts
        except (ValueError, TypeError):
            return False

    def _execute_after_approval(self, action_id: int) -> str:
        """Run the deterministic downstream executor after a human approval.
        Returns a short human-readable status (never logs the webhook URL)."""
        action = self.repo.get_action(action_id) or {}
        _ok, detail = execute_approved_action(self.settings, action, None)
        self.repo.record_action_execution(action_id, detail)
        if detail == "off":
            return "dış aksiyon kapalı"
        if detail.startswith("sent:"):
            return "dış aksiyon yürütüldü"
        return f"dış aksiyon durumu: {detail}"

    async def _trigger_offer_call(self, action_id: int) -> str:
        """After a compensation-offer approval, place the (separate, GÖREV 3)
        CALL-E call that actually informs the customer. Never raises: any
        failure is reported back as text, the approval itself already stands."""
        action = self.repo.get_action(action_id)
        if action is None or action.get("action_type") != "compensation_offer":
            return ""
        try:
            result = await self.pipeline._dispatch_offer_call(action)
        except Exception:  # noqa: BLE001
            logger.exception("offer call dispatch failed")
            return "\n⚠️ Müşteri araması tetiklenirken bir hata oluştu."
        if result is None:
            return ""
        status = result.get("status")
        if status == "failed":
            return f"\n⚠️ Müşteri araması başlatılamadı ({result.get('failure_code') or 'bilinmiyor'})."
        return "\n📞 Müşteriye teklif bildirim araması tetiklendi."

    # -- callbacks -------------------------------------------------------
    async def on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        # Inline buttons (approve/reject/order status) are business actions;
        # only the admin may drive them.
        chat_id = update.effective_chat.id
        if not self._is_admin(chat_id):
            return

        data = query.data or ""

        if data.startswith("approve:"):
            await self._handle_approval(query, data)
            return
        if data.startswith("reengage_approve:"):
            await self._handle_reengagement_approval(query, data)
            return
        if data.startswith("reject:"):
            action_id = int(data.split(":", 1)[1])
            action = self.repo.get_action(action_id)
            if action is None:
                await query.edit_message_text("Onay kaydı bulunamadı.")
                return
            if self._action_expired(action):
                await query.edit_message_text("⏰ Bu onay isteğinin süresi doldu.")
                return
            if not self.repo.resolve_action(action_id, "declined"):
                await query.edit_message_text("Bu istek zaten işlendi.")
                return
            await query.edit_message_text("✅ Teklif reddedildi. Hiçbir işlem uygulanmadı.")
            return

        if data.startswith("created:"):
            await query.edit_message_text("✅ Sipariş oluşturuldu olarak kaydedildi.")
            return
        if data.startswith("sent:"):
            order_id = int(data.split(":", 1)[1])
            order = self.repo.get_order(order_id)
            if order is None:
                await query.edit_message_text("Sipariş bulunamadı.")
                return
            self.repo.mark_order_sent(
                order_id, self.settings.post_delivery_delay_minutes
            )
            # Optional, one-time delay — pressing nothing leaves the default
            # POST_DELIVERY_DELAY_MINUTES schedule exactly as it was.
            keyboard = [
                [
                    InlineKeyboardButton("⏳ +30 dakika", callback_data=f"delay:{order_id}:30"),
                    InlineKeyboardButton("⏳ +2 saat", callback_data=f"delay:{order_id}:120"),
                    InlineKeyboardButton("⏳ Yarın", callback_data=f"delay:{order_id}:1440"),
                ]
            ]
            await query.edit_message_text(
                f"🚚 Sipariş gönderildi.\nGeri bildirim araması ~"
                f"{self.settings.post_delivery_delay_minutes} dk içinde planlanacak.\n"
                "İsterseniz aramayı erteleyebilirsiniz (yalnızca bir kez kullanılabilir):",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return
        if data.startswith("delay:"):
            _prefix, order_id_s, minutes_s = data.split(":", 2)
            order_id = int(order_id_s)
            minutes = int(minutes_s)
            updated = self.repo.delay_feedback_call(order_id, minutes)
            if updated is None:
                await query.edit_message_text(
                    "⚠️ Erteleme uygulanamadı (sipariş bulunamadı ya da zaten bir kez ertelendi)."
                )
                return
            await query.edit_message_text(
                f"⏳ Geri bildirim araması ertelendi.\n"
                f"Yeni planlanan zaman: {updated['feedback_scheduled_at']}"
            )
            return

    async def _handle_approval(self, query: Any, data: str) -> None:
        """Record a human approval decision. Never applies any commercial action."""
        _prefix, action_id_s, value = data.split(":", 2)
        action_id = int(action_id_s)
        action = self.repo.get_action(action_id)
        if action is None:
            await query.edit_message_text("Onay kaydı bulunamadı.")
            return
        if self._action_expired(action):
            await query.edit_message_text("⏰ Bu onay isteğinin süresi doldu.")
            return
        if action["status"] != "proposed":
            await query.edit_message_text("Bu istek zaten işlendi.")
            return
        if value == "custom":
            self._pending_action_pct[query.message.chat_id] = action_id
            await query.edit_message_text(
                "Özel indirim oranını yüzde olarak gönderin (örn. 40). "
                "Bu yalnızca bir öneri kaydıdır; otomatik uygulanmaz."
            )
            return
        pct = int(value)
        if not self.repo.resolve_action(action_id, f"approved:{pct}%"):
            await query.edit_message_text("Bu istek zaten işlendi.")
            return
        exec_note = self._execute_after_approval(action_id)
        call_note = await self._trigger_offer_call(action_id)
        await query.edit_message_text(
            f"✅ %{pct} telafi teklifi onaylandı ve kaydedildi. "
            f"(Uygulama işletme tarafından yapılır; sistem otomatik işlem yapmaz.)\n{exec_note}{call_note}"
        )

    async def _handle_reengagement_approval(self, query: Any, data: str) -> None:
        """Approve a re-engagement outreach proposal (GÖREV 2). A plain
        approve/reject decision, no percentage — approving here immediately
        places the (separate, GÖREV 2) CALL-E call, since there is no
        commercial/monetary decision left to make beyond "yes, call them"."""
        action_id = int(data.split(":", 1)[1])
        action = self.repo.get_action(action_id)
        if action is None:
            await query.edit_message_text("Onay kaydı bulunamadı.")
            return
        if self._action_expired(action):
            await query.edit_message_text("⏰ Bu onay isteğinin süresi doldu.")
            return
        if not self.repo.resolve_action(action_id, "approved"):
            await query.edit_message_text("Bu istek zaten işlendi.")
            return
        try:
            result = await self.pipeline._dispatch_reengagement_call(action)
        except Exception:  # noqa: BLE001
            logger.exception("reengagement call dispatch failed")
            await query.edit_message_text(
                "✅ Onaylandı, ancak müşteri araması tetiklenirken bir hata oluştu."
            )
            return
        status = (result or {}).get("status", "bilinmiyor")
        if status == "failed":
            await query.edit_message_text(
                f"✅ Onaylandı, ancak arama başlatılamadı "
                f"({(result or {}).get('failure_code') or 'bilinmiyor'})."
            )
            return
        await query.edit_message_text(f"✅ Onaylandı. Müşteri aranıyor (durum: {status}).")

    # -- text ------------------------------------------------------------
    async def on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        text = update.message.text.strip()

        # Only the admin may submit phone numbers / questions / approvals.
        if not self._is_admin(chat_id):
            await update.message.reply_text("Bu panel yalnızca yönetici içindir.")
            return

        if chat_id in self._setup:
            await self._handle_setup_value(update, text)
            return

        if chat_id in self._pending_action_pct:
            action_id = self._pending_action_pct.pop(chat_id)
            action = self.repo.get_action(action_id)
            if action is None:
                await update.message.reply_text("Onay kaydı bulunamadı.")
                return
            if self._action_expired(action):
                await update.message.reply_text("⏰ Bu onay isteğinin süresi doldu.")
                return
            if action["status"] != "proposed":
                await update.message.reply_text("Bu istek zaten işlendi.")
                return
            if text.isdigit() and 1 <= int(text) <= 100:
                self.repo.resolve_action(action_id, f"approved:{text}%")
                exec_note = self._execute_after_approval(action_id)
                call_note = await self._trigger_offer_call(action_id)
                await update.message.reply_text(
                    f"✅ %{text} telafi teklifi onaylandı ve kaydedildi. "
                    f"(Uygulama işletme tarafından yapılır; sistem otomatik işlem yapmaz.)\n{exec_note}{call_note}"
                )
            else:
                await update.message.reply_text(
                    "Geçersiz oran. 1-100 arasında bir sayı gönderin."
                )
            return

        if PHONE_RE.match(text):
            await self._handle_phone(update, text)
            return

        await update.message.chat.send_action("typing")
        answer = await self.pipeline.answer_query(text)
        await update.message.reply_text(answer or "Sonuç bulunamadı.")

    async def _handle_setup_value(self, update: Update, text: str) -> None:
        chat_id = update.effective_chat.id

        if text in ("/cancel", "iptal", "İptal"):
            self._setup.pop(chat_id, None)
            await update.message.reply_text("Kurulum iptal edildi.")
            return

        missing = self._missing_keys()
        env_key = missing[0]
        self._apply_secret(env_key, text)
        try:
            await update.message.delete()
        except Exception:  # noqa: BLE001
            pass
        await update.message.reply_text(f"✓ {self._label_for(env_key)} kaydedildi.")

        if self._all_complete():
            self._setup.pop(chat_id, None)
            await update.message.reply_text(CONFIRMATION)
            return
        await self._prompt_key(update.message, self._missing_keys()[0])

    async def _handle_phone(self, update: Update, phone: str) -> None:
        result = self.pipeline.handle_order(phone, status="created")
        if result.get("status") == "invalid_phone":
            await update.message.reply_text(
                f"⚠️ {result.get('error', 'Geçersiz numara')}"
            )
            return
        if result.get("status") == "do_not_call":
            await update.message.reply_text(
                "Bu müşteri 'beni aramayın' listesinde; bir daha aranmayacak."
            )
            return
        order = result["order"]
        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Sipariş oluşturuldu", callback_data=f"created:{order['id']}"
                ),
                InlineKeyboardButton(
                    "🚚 Sipariş gönderildi", callback_data=f"sent:{order['id']}"
                ),
            ]
        ]
        await update.message.reply_text(
            f"Müşteri: {phone}\nSipariş durumunu seçin:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # -- demo ------------------------------------------------------------
    async def _run_demo(self) -> str:
        phone = "+905551112233"
        result = self.pipeline.handle_order(phone, status="sent")
        if result.get("status") == "do_not_call":
            return "Demo müşterisi do_not_call listesinde."
        order = result["order"]
        call = self.repo.create_call(
            order["id"], result["customer_id"], status="calling",
            scheduled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        processed = await self.pipeline.process_call_result(
            call["id"], DEMO_TRANSCRIPT, simulated=True
        )
        if processed is None:
            return "Demo analiz edilemedi."
        feedback = processed["feedback"]
        decision = processed["decision"]
        return (
            "🧪 Demo analiz (simüle edilmiş çağrı):\n\n"
            f"Memnuniyet: {feedback['satisfaction']}/5\n"
            f"Duygu: {feedback['sentiment']}\n"
            f"Öncelik: {feedback['priority']}\n"
            f"Eksik ürün: {[m['name'] for m in feedback['missing_products']] or 'yok'}\n\n"
            f"Karar: {decision['insight_title']}\n{decision['insight_description']}"
        )
