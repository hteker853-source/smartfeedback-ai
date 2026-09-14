"""Background scheduler.

- Dispatches feedback calls whose scheduled time has arrived.
- Polls CALL-E for in-flight calls (webhook is the primary path; polling is a
  safe fallback).
- Runs the nightly synthesis after business close and sends the morning summary
  before opening.
- Runs the raw-transcript retention job.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import notify
from .calle import classify_outcome, extract_customer_text
from .pipeline import Pipeline
from .repository import Repository

logger = logging.getLogger(__name__)

DUE_INTERVAL = 15
POLL_INTERVAL = 20
SYNTH_INTERVAL = 300
RETENTION_INTERVAL = 3600


class Scheduler:
    def __init__(self, pipeline: Pipeline, repo: Repository) -> None:
        self.pipeline = pipeline
        self.repo = repo
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        logger.info("Scheduler started")
        try:
            await asyncio.gather(
                self._due_loop(),
                self._poll_loop(),
                self._synth_loop(),
                self._delivery_loop(),
                self._retention_loop(),
                self._backup_loop(),
                self._reengagement_loop(),
                self._frequency_drop_loop(),
                self._retry_loop(),
            )
        except asyncio.CancelledError:
            pass

    async def _due_loop(self) -> None:
        while not self._stop.is_set():
            try:
                n = await self.pipeline.run_due_calls()
                if n:
                    logger.info("Dispatched %d feedback calls", n)
            except Exception:  # noqa: BLE001
                logger.exception("due-call loop error")
            await asyncio.sleep(DUE_INTERVAL)

    async def _poll_loop(self) -> None:
        if not self.pipeline.calle.enabled:
            return
        while not self._stop.is_set():
            try:
                await self._poll_inflight_calls()
            except Exception:  # noqa: BLE001
                logger.exception("poll loop error")
            await asyncio.sleep(POLL_INTERVAL)

    async def _retry_loop(self) -> None:
        """Fires the single 5-minute no_answer/voicemail retry redial
        (see `Pipeline.redial_due_calls`)."""
        if not self.pipeline.calle.enabled:
            return
        while not self._stop.is_set():
            try:
                n = await self.pipeline.redial_due_calls()
                if n:
                    logger.info("Redialed %d retry-scheduled calls", n)
            except Exception:  # noqa: BLE001
                logger.exception("retry loop error")
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_inflight_calls(self) -> None:
        from .db import connect as _connect

        with _connect(self.repo.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM calls WHERE status = 'calling'
                   AND calle_call_id IS NOT NULL"""
            ).fetchall()
        for row in rows:
            call = dict(row)
            try:
                result = self.pipeline.calle.fetch_call(call["calle_call_id"])
            except Exception:  # noqa: BLE001
                continue
            outcome = classify_outcome(result)
            if outcome == "in_progress":
                continue
            if outcome in ("failed", "canceled"):
                self.repo.update_call(
                    call["id"], status="failed", failure_code="call_failed",
                    completed_at=_now(),
                )
                if call.get("notify_completion"):
                    await notify.send_admin("❌ Arama başarısız oldu (call_failed).")
                continue
            if outcome in ("no_answer", "voicemail"):
                if (call.get("retry_count") or 0) == 0:
                    retry_at = (
                        datetime.now(timezone.utc) + timedelta(minutes=5)
                    ).isoformat(timespec="seconds")
                    self.repo.update_call(
                        call["id"], status="retry_scheduled", outcome=outcome,
                        retry_count=1, retry_at=retry_at,
                    )
                    if call.get("notify_completion"):
                        await notify.send_admin(
                            f"ℹ️ Arama cevapsız kaldı ({outcome}). "
                            "5 dakika sonra tekrar denenecek."
                        )
                    continue
                self.repo.update_call(
                    call["id"], status="no_answer", outcome=outcome, completed_at=_now()
                )
                if call.get("notify_completion"):
                    await notify.send_admin(f"ℹ️ Arama cevapsız kaldı ({outcome}).")
                continue
            transcript = extract_customer_text(result)
            await self.pipeline.process_call_result(
                call["id"], transcript,
                notify_status=bool(call.get("notify_completion")),
            )

    async def _synth_loop(self) -> None:
        """Computation only — timing UNCHANGED (close_hour + 1). Saves the
        daily_summary insight with `notified=0` ("pending delivery") but does
        NOT send it; delivery is a separate concern (see `_delivery_loop`)."""
        last_run_date: str | None = None
        while not self._stop.is_set():
            local_now = self.pipeline._business_local_now()
            close_hour = self.pipeline._business["close_hour"]
            synth_hour = (close_hour + 1) % 24
            today = local_now.strftime("%Y-%m-%d")
            if local_now.hour == synth_hour and last_run_date != today:
                last_run_date = today
                try:
                    await self.pipeline.nightly_synthesis()
                except Exception:  # noqa: BLE001
                    logger.exception("synthesis error")
            await asyncio.sleep(SYNTH_INTERVAL)

    async def _delivery_loop(self) -> None:
        """Once a day, at `businesses.morning_summary_hour`, deliver any
        pending (not-yet-notified) daily_summary insight(s) to Telegram.
        Separated from `_synth_loop` on purpose: computation timing stays
        tied to business close, delivery timing is independently
        configurable (MORNING_SUMMARY_HOUR — previously defined but unused).
        """
        last_run_date: str | None = None
        while not self._stop.is_set():
            local_now = self.pipeline._business_local_now()
            deliver_hour = self.pipeline._business["morning_summary_hour"]
            today = local_now.strftime("%Y-%m-%d")
            if local_now.hour == deliver_hour and last_run_date != today:
                last_run_date = today
                try:
                    await self.pipeline.deliver_pending_synthesis()
                except Exception:  # noqa: BLE001
                    logger.exception("synthesis delivery error")
            await asyncio.sleep(SYNTH_INTERVAL)

    async def _retention_loop(self) -> None:
        if self.pipeline.settings.raw_transcript_retention_days <= 0:
            return
        last_run_date: str | None = None
        while not self._stop.is_set():
            local_now = self.pipeline._business_local_now()
            today = local_now.strftime("%Y-%m-%d")
            if last_run_date != today:
                last_run_date = today
                try:
                    cutoff = (
                        datetime.now(timezone.utc)
                        - timedelta(days=self.pipeline.settings.raw_transcript_retention_days)
                    ).isoformat(timespec="seconds")
                    n = self.repo.purge_raw_transcripts(cutoff)
                    if n:
                        logger.info("Retention: cleared %d raw transcripts", n)
                except Exception:  # noqa: BLE001
                    logger.exception("retention error")
            await asyncio.sleep(RETENTION_INTERVAL)

    async def _backup_loop(self) -> None:
        if not self.pipeline.settings.backup_dir:
            return
        last_run_date: str | None = None
        while not self._stop.is_set():
            local_now = self.pipeline._business_local_now()
            today = local_now.strftime("%Y-%m-%d")
            if last_run_date != today:
                last_run_date = today
                try:
                    from pathlib import Path

                    from .backup import backup_database, verify_backup

                    dest = backup_database(
                        self.pipeline.settings.database_file,
                        Path(self.pipeline.settings.backup_dir),
                        keep=self.pipeline.settings.backup_keep,
                    )
                    ok, result = verify_backup(dest)
                    if not ok:
                        await notify.send_admin(
                            f"⚠️ Veritabanı yedeği doğrulanamadı: {result}"
                        )
                    else:
                        logger.info("Backup verified: %s", dest)
                except Exception:  # noqa: BLE001
                    logger.exception("backup error")
                    await notify.send_admin(
                        "⚠️ Veritabanı yedeği alınamadı. Disk/izin durumunu kontrol edin."
                    )
            await asyncio.sleep(RETENTION_INTERVAL)

    async def _reengagement_loop(self) -> None:
        """Once a day, look for dormant customers with a past verified
        complaint and PROPOSE (never send) a personalized re-engagement call
        for human approval. See Pipeline.propose_reengagement_outreach."""
        last_run_date: str | None = None
        while not self._stop.is_set():
            local_now = self.pipeline._business_local_now()
            today = local_now.strftime("%Y-%m-%d")
            if last_run_date != today:
                last_run_date = today
                try:
                    n = await self.pipeline.propose_reengagement_outreach()
                    if n:
                        logger.info("Proposed %d re-engagement outreach(es)", n)
                except Exception:  # noqa: BLE001
                    logger.exception("reengagement loop error")
            await asyncio.sleep(RETENTION_INTERVAL)

    async def _frequency_drop_loop(self) -> None:
        """Once a day, independently of `_reengagement_loop`, scan for
        customers whose ordering frequency has dropped relative to their OWN
        historical rhythm and PROPOSE (never send) a compensation offer for
        human approval. See Pipeline.detect_frequency_drop_risk."""
        last_run_date: str | None = None
        while not self._stop.is_set():
            local_now = self.pipeline._business_local_now()
            today = local_now.strftime("%Y-%m-%d")
            if last_run_date != today:
                last_run_date = today
                try:
                    n = await self.pipeline.detect_frequency_drop_risk()
                    if n:
                        logger.info("Proposed %d frequency-drop compensation offer(s)", n)
                except Exception:  # noqa: BLE001
                    logger.exception("frequency-drop loop error")
            await asyncio.sleep(RETENTION_INTERVAL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
