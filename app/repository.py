"""Repository — the single data access layer.

All reads/writes go through these functions. No other module talks to SQLite
directly (except `db.init_db`).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import db
from .db import _json_dump, _json_load


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    # -- businesses -------------------------------------------------------
    def get_or_create_business(self, name: str, default_locale: str = "tr") -> dict[str, Any]:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM businesses ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO businesses (name, default_locale) VALUES (?, ?)",
                    (name, default_locale),
                )
                row = conn.execute(
                    "SELECT * FROM businesses WHERE id = ?", (cur.lastrowid,)
                ).fetchone()
            return dict(row)

    def update_business_settings(
        self,
        business_id: int,
        *,
        close_hour: int | None = None,
        morning_summary_hour: int | None = None,
        post_delivery_delay_minutes: int | None = None,
    ) -> None:
        with db.connect(self.db_path) as conn:
            for col, val in (
                ("close_hour", close_hour),
                ("morning_summary_hour", morning_summary_hour),
                ("post_delivery_delay_minutes", post_delivery_delay_minutes),
            ):
                if val is not None:
                    conn.execute(
                        f"UPDATE businesses SET {col} = ? WHERE id = ?", (val, business_id)
                    )

    # -- customers --------------------------------------------------------
    def get_customer(self, business_id: int, phone: str) -> dict[str, Any] | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE business_id = ? AND phone = ?",
                (business_id, phone),
            ).fetchone()
            return dict(row) if row else None

    def upsert_customer(self, business_id: int, phone: str) -> dict[str, Any]:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE business_id = ? AND phone = ?",
                (business_id, phone),
            ).fetchone()
            if row:
                return dict(row)
            cur = conn.execute(
                "INSERT INTO customers (business_id, phone) VALUES (?, ?)",
                (business_id, phone),
            )
            row = conn.execute(
                "SELECT * FROM customers WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def set_do_not_call(self, customer_id: int) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE customers SET do_not_call = 1 WHERE id = ?", (customer_id,)
            )

    def set_preferred_language(self, customer_id: int, language: str) -> bool:
        """Persist the customer's detected spoken language, once. Never
        overwrites an already-set value (guarded at the SQL level too, so a
        concurrent detection race cannot flip it back and forth)."""
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE customers SET preferred_language = ? "
                "WHERE id = ? AND preferred_language IS NULL",
                (language, customer_id),
            )
            return cur.rowcount > 0

    def is_do_not_call(self, customer_id: int) -> bool:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT do_not_call FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
            return bool(row and row["do_not_call"])

    def update_customer_stats(self, customer_id: int) -> None:
        with db.connect(self.db_path) as conn:
            agg = conn.execute(
                """SELECT COUNT(*) AS cnt, MIN(created_at) AS first_at,
                          MAX(created_at) AS last_at
                   FROM orders WHERE customer_id = ? AND status != 'cancelled'""",
                (customer_id,),
            ).fetchone()
            order_count = agg["cnt"] or 0
            first_at = agg["first_at"]
            last_at = agg["last_at"]
            loyalty = "new"
            if order_count >= 20:
                loyalty = "vip"
            elif order_count >= 10:
                loyalty = "loyal"
            elif order_count >= 3:
                loyalty = "regular"
            conn.execute(
                """UPDATE customers SET order_count = ?, first_order_at = ?,
                   last_order_at = ?, loyalty_status = ? WHERE id = ?""",
                (order_count, first_at, last_at, loyalty, customer_id),
            )

    def list_customers(self, business_id: int) -> list[dict[str, Any]]:
        """All non-do-not-call customers for a business — used by GÖREV F's
        frequency-drop scan (independent of GÖREV 2's dormant-customer list,
        which is pre-filtered by a last-order-age cutoff)."""
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM customers WHERE business_id = ? AND do_not_call = 0",
                (business_id,),
            ).fetchall()
            return db.rows_to_dicts(rows)

    def customer_order_dates(self, customer_id: int, limit: int = 10) -> list[str]:
        """Most recent `limit` order `created_at` timestamps for a customer
        (any order, cancelled excluded) — used to compute their "normal
        ordering rhythm" (GÖREV F, see trends.compute_customer_rhythm)."""
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT created_at FROM orders WHERE customer_id = ?
                   AND status != 'cancelled' ORDER BY created_at DESC LIMIT ?""",
                (customer_id, limit),
            ).fetchall()
            return [r["created_at"] for r in rows]

    # -- orders -----------------------------------------------------------
    def create_order(
        self, business_id: int, customer_id: int, status: str = "created",
        order_type: str = "service", locale: str = "tr",
    ) -> dict[str, Any]:
        correlation_id = uuid.uuid4().hex
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO orders
                   (business_id, customer_id, order_type, status, locale, correlation_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (business_id, customer_id, order_type, status, locale, correlation_id),
            )
            row = conn.execute(
                "SELECT * FROM orders WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_open_order_for_customer(self, customer_id: int) -> dict[str, Any] | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM orders WHERE customer_id = ?
                   AND status IN ('created', 'sent')
                   ORDER BY id DESC LIMIT 1""",
                (customer_id,),
            ).fetchone()
            return dict(row) if row else None

    def mark_order_sent(
        self, order_id: int, delay_minutes: int
    ) -> dict[str, Any]:
        now = _now()
        schedule_at = datetime.now(timezone.utc).timestamp() + delay_minutes * 60
        from datetime import datetime as _dt

        schedule_iso = _dt.fromtimestamp(schedule_at, timezone.utc).isoformat(timespec="seconds")
        with db.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE orders SET status = 'sent', sent_at = ?,
                   feedback_scheduled_at = ? WHERE id = ?""",
                (now, schedule_iso, order_id),
            )
            row = conn.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            return dict(row)

    def delay_feedback_call(self, order_id: int, minutes: int) -> dict[str, Any] | None:
        """Push `feedback_scheduled_at` forward by `minutes`, exactly ONCE per
        order (guarded by `delay_used`, checked both before and atomically in
        the UPDATE's WHERE clause so a concurrent double-press can't apply it
        twice). Returns the updated order, or None if the order doesn't exist,
        has no scheduled time yet, or was already delayed once."""
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT feedback_scheduled_at, delay_used FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()
            if row is None or row["feedback_scheduled_at"] is None or row["delay_used"]:
                return None
            current = datetime.fromisoformat(row["feedback_scheduled_at"])
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            new_time = (current + timedelta(minutes=minutes)).isoformat(timespec="seconds")
            cur = conn.execute(
                """UPDATE orders SET feedback_scheduled_at = ?, delay_used = 1
                   WHERE id = ? AND delay_used = 0""",
                (new_time, order_id),
            )
            if cur.rowcount == 0:
                return None
            row2 = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            return dict(row2)

    def list_orders_due_for_feedback(self) -> list[dict[str, Any]]:
        now = _now()
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM orders WHERE status = 'sent'
                   AND feedback_scheduled_at <= ?
                   AND id NOT IN (SELECT order_id FROM calls)""",
                (now,),
            ).fetchall()
            return db.rows_to_dicts(rows)

    # -- calls ------------------------------------------------------------
    def create_call(
        self,
        order_id: int,
        customer_id: int,
        *,
        scheduled_at: str | None = None,
        status: str = "scheduled",
        calle_call_id: str | None = None,
        notify_completion: int = 0,
    ) -> dict[str, Any]:
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO calls
                   (order_id, customer_id, status, scheduled_at, calle_call_id,
                    notify_completion)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (order_id, customer_id, status, scheduled_at, calle_call_id,
                 notify_completion),
            )
            row = conn.execute(
                "SELECT * FROM calls WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def get_call(self, call_id: int) -> dict[str, Any] | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_call_by_calle_id(self, calle_call_id: str) -> dict[str, Any] | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM calls WHERE calle_call_id = ?", (calle_call_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_call(self, call_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with db.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE calls SET {cols} WHERE id = ?",
                (*fields.values(), call_id),
            )

    # -- feedbacks --------------------------------------------------------
    def save_feedback(
        self,
        *,
        call_id: int,
        order_id: int,
        customer_id: int,
        business_id: int,
        feedback: Any,
        transcript: str,
    ) -> dict[str, Any]:
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO feedbacks
                   (call_id, order_id, customer_id, business_id, transcript,
                    satisfaction, sentiment, positives, category, priority, urgency,
                    missing_products, recommended_action, language, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    call_id,
                    order_id,
                    customer_id,
                    business_id,
                    transcript,
                    feedback.satisfaction,
                    feedback.sentiment,
                    _json_dump(feedback.positives),
                    feedback.category,
                    feedback.priority,
                    feedback.urgency,
                    _json_dump([m.model_dump() for m in feedback.missing_products]),
                    feedback.recommended_action,
                    feedback.language,
                    _json_dump(feedback.model_dump()),
                ),
            )
            row = conn.execute(
                "SELECT * FROM feedbacks WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def feedback_exists(self, call_id: int) -> bool:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM feedbacks WHERE call_id = ? LIMIT 1", (call_id,)
            ).fetchone()
            return row is not None

    def get_feedback_for_call(self, call_id: int) -> dict[str, Any] | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM feedbacks WHERE call_id = ? LIMIT 1", (call_id,)
            ).fetchone()
            return dict(row) if row else None

    # -- agent traces ----------------------------------------------------
    def record_agent_trace(
        self, call_id: int, step: str, detail: str, reasoning_summary: str | None = None
    ) -> None:
        """`reasoning_summary` (GÖREV D) is the agent's OWN one-sentence
        account of what it found/decided (from its structured LLM output,
        never templated here) — purely a visibility/reporting addition,
        never read by policy.py or any decision path."""
        with db.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO agent_traces (call_id, step, detail, reasoning_summary)
                   VALUES (?, ?, ?, ?)""",
                (call_id, step, detail, reasoning_summary),
            )

    def list_agent_traces(self, call_id: int) -> list[dict[str, Any]]:
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM agent_traces WHERE call_id = ? ORDER BY id", (call_id,)
            ).fetchall()
            return db.rows_to_dicts(rows)

    def list_feedbacks(self, business_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM feedbacks WHERE business_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (business_id, limit),
            ).fetchall()
            return db.rows_to_dicts(rows)

    # -- canonical problems ----------------------------------------------
    def list_canonical_problems(self, business_id: int) -> list[dict[str, Any]]:
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM canonical_problems WHERE business_id = ?
                   ORDER BY count DESC""",
                (business_id,),
            ).fetchall()
            return db.rows_to_dicts(rows)

    def get_canonical_problem(self, business_id: int, name: str) -> dict[str, Any] | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM canonical_problems
                   WHERE business_id = ? AND name = ?""",
                (business_id, name),
            ).fetchone()
            return dict(row) if row else None

    def get_canonical_problem_by_id(self, canonical_problem_id: int) -> dict[str, Any] | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM canonical_problems WHERE id = ?",
                (canonical_problem_id,),
            ).fetchone()
            return dict(row) if row else None

    def upsert_canonical_problem(
        self,
        business_id: int,
        name: str,
        *,
        category: str | None,
        vector: list[float] | None,
        first_seen_at: str,
        last_seen_at: str,
        count_delta: int,
    ) -> dict[str, Any]:
        with db.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO canonical_problems
                   (business_id, name, category, first_seen_at, last_seen_at, count, vector)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(business_id, name) DO UPDATE SET
                       last_seen_at = excluded.last_seen_at,
                       count = canonical_problems.count + excluded.count,
                       category = COALESCE(excluded.category, canonical_problems.category),
                       vector = COALESCE(excluded.vector, canonical_problems.vector)""",
                (
                    business_id,
                    name,
                    category,
                    first_seen_at,
                    last_seen_at,
                    max(count_delta, 1),
                    _json_dump(vector),
                ),
            )
            row = conn.execute(
                """SELECT * FROM canonical_problems
                   WHERE business_id = ? AND name = ?""",
                (business_id, name),
            ).fetchone()
            return dict(row)

    def update_canonical_status(self, problem_id: int, status: str) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE canonical_problems SET status = ? WHERE id = ?",
                (status, problem_id),
            )

    def link_feedback_problem(
        self, feedback_id: int, canonical_problem_id: int, problem_text: str, severity: str
    ) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO feedback_problems
                   (feedback_id, canonical_problem_id, problem_text, severity)
                   VALUES (?, ?, ?, ?)""",
                (feedback_id, canonical_problem_id, problem_text, severity),
            )

    def count_problem_mentions_since(
        self, business_id: int, canonical_problem_id: int, since: str
    ) -> int:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS cnt FROM feedback_problems fp
                   JOIN feedbacks f ON f.id = fp.feedback_id
                   WHERE fp.canonical_problem_id = ? AND f.business_id = ?
                     AND f.created_at >= ?""",
                (canonical_problem_id, business_id, since),
            ).fetchone()
            return row["cnt"] if row else 0

    # -- insights ---------------------------------------------------------
    def save_insight(
        self,
        business_id: int,
        *,
        insight_type: str,
        title: str,
        description: str,
        priority: str,
        recommended_action: str,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> dict[str, Any]:
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO insights
                   (business_id, insight_type, title, description, priority,
                    recommended_action, period_start, period_end)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (business_id, insight_type, title, description, priority,
                 recommended_action, period_start, period_end),
            )
            row = conn.execute(
                "SELECT * FROM insights WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def list_insights(self, business_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM insights WHERE business_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (business_id, limit),
            ).fetchall()
            return db.rows_to_dicts(rows)

    def mark_insight_notified(self, insight_id: int) -> bool:
        """Mark an insight as delivered. Returns True only on the transition
        (0 -> 1), so a duplicate delivery-loop tick can't double-send."""
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE insights SET notified = 1 WHERE id = ? AND notified = 0",
                (insight_id,),
            )
            return cur.rowcount > 0

    def list_undelivered_insights(
        self, business_id: int, insight_type: str
    ) -> list[dict[str, Any]]:
        """Insights computed but not yet delivered (`notified = 0`), oldest
        first — used to separate WHEN an insight is computed from WHEN it is
        actually sent to the business owner (see Pipeline.nightly_synthesis /
        deliver_pending_synthesis)."""
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM insights WHERE business_id = ?
                   AND insight_type = ? AND notified = 0
                   ORDER BY id ASC""",
                (business_id, insight_type),
            ).fetchall()
            return db.rows_to_dicts(rows)

    def daily_summary_exists(self, business_id: int, period_start: str) -> bool:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT 1 FROM insights WHERE business_id = ?
                   AND insight_type = 'daily_summary' AND period_start = ?
                   LIMIT 1""",
                (business_id, period_start),
            ).fetchone()
            return row is not None

    # -- webhook deduplication ------------------------------------------
    def record_webhook_event(self, event_id: str, calle_call_id: str | None) -> bool:
        """Return True if the event is new (was recorded now); False if already seen."""
        with db.connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO webhook_events (event_id, calle_call_id) VALUES (?, ?)",
                    (event_id, calle_call_id),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    # -- retention -------------------------------------------------------
    def purge_raw_transcripts(self, cutoff: str) -> int:
        """Null raw transcript fields older than `cutoff`, keeping structured
        analysis and aggregated stats. Returns the number of records touched."""
        import json as _json

        total = 0
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE feedbacks SET transcript = NULL "
                "WHERE created_at < ? AND transcript IS NOT NULL",
                (cutoff,),
            )
            total += cur.rowcount
            cur = conn.execute(
                "UPDATE calls SET transcript = NULL "
                "WHERE created_at < ? AND transcript IS NOT NULL",
                (cutoff,),
            )
            total += cur.rowcount

            # Strip the truncated transcript excerpt from structured JSON.
            rows = conn.execute(
                "SELECT id, raw_json FROM feedbacks WHERE created_at < ? "
                "AND raw_json IS NOT NULL",
                (cutoff,),
            ).fetchall()
            for row in rows:
                data = _json_load(row["raw_json"], {})
                if isinstance(data, dict) and "summary" in data:
                    data["summary"] = ""
                    conn.execute(
                        "UPDATE feedbacks SET raw_json = ? WHERE id = ?",
                        (_json_dump(data), row["id"]),
                    )
                    total += 1
        return total

    # -- actions ----------------------------------------------------------
    def create_action(
        self,
        business_id: int,
        action_type: str,
        *,
        insight_id: int | None = None,
        proposed_by: str = "agent",
        reason: str | None = None,
        expires_at: str | None = None,
        customer_id: int | None = None,
    ) -> dict[str, Any]:
        if expires_at is None:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(hours=24)
            ).isoformat(timespec="seconds")
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO actions
                   (business_id, insight_id, action_type, proposed_by, reason,
                    expires_at, customer_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (business_id, insight_id, action_type, proposed_by, reason,
                 expires_at, customer_id),
            )
            row = conn.execute(
                "SELECT * FROM actions WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def get_action(self, action_id: int) -> dict[str, Any] | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM actions WHERE id = ?", (action_id,)
            ).fetchone()
            return dict(row) if row else None

    def resolve_action(self, action_id: int, decision: str, outcome: str | None = None) -> bool:
        """Resolve an action only if it is still 'proposed'. Returns True if the
        transition happened (idempotent: duplicate callbacks are ignored)."""
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                """UPDATE actions SET status = 'resolved', decision = ?,
                   applied_at = ?, outcome = ? WHERE id = ? AND status = 'proposed'""",
                (decision, _now(), outcome, action_id),
            )
            return cur.rowcount > 0

    def list_dormant_customers(
        self, business_id: int, cutoff_iso: str
    ) -> list[dict[str, Any]]:
        """Customers (not on the do-not-call list) whose most recent order
        predates `cutoff_iso`. Candidates for re-engagement outreach; callers
        must separately confirm a verified complaint via
        `customer_verified_problems` before proposing anything."""
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM customers
                   WHERE business_id = ? AND do_not_call = 0
                     AND last_order_at IS NOT NULL AND last_order_at < ?
                   ORDER BY last_order_at ASC""",
                (business_id, cutoff_iso),
            ).fetchall()
            return db.rows_to_dicts(rows)

    def has_action_since(
        self, customer_id: int, action_type: str, since_iso: str | None
    ) -> bool:
        """True if an action of this type already exists for this customer at
        or after `since_iso` (or ever, when `since_iso` is None). Used to
        propose outreach at most once per dormancy episode instead of every
        time the scheduler runs."""
        with db.connect(self.db_path) as conn:
            if since_iso:
                row = conn.execute(
                    """SELECT 1 FROM actions WHERE customer_id = ?
                       AND action_type = ? AND created_at >= ? LIMIT 1""",
                    (customer_id, action_type, since_iso),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT 1 FROM actions WHERE customer_id = ?
                       AND action_type = ? LIMIT 1""",
                    (customer_id, action_type),
                ).fetchone()
            return row is not None

    # -- outreach calls (action-triggered, NOT the core feedback-call flow) -
    def create_outreach_call(
        self,
        action_id: int,
        customer_id: int,
        *,
        status: str = "calling",
        calle_call_id: str | None = None,
    ) -> dict[str, Any]:
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO outreach_calls
                   (action_id, customer_id, status, calle_call_id)
                   VALUES (?, ?, ?, ?)""",
                (action_id, customer_id, status, calle_call_id),
            )
            row = conn.execute(
                "SELECT * FROM outreach_calls WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def get_outreach_call_by_action(self, action_id: int) -> dict[str, Any] | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM outreach_calls WHERE action_id = ?", (action_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_outreach_call(self, outreach_call_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with db.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE outreach_calls SET {cols} WHERE id = ?",
                (*fields.values(), outreach_call_id),
            )

    def record_action_execution(self, action_id: int, status: str) -> bool:
        """Record the deterministic downstream-execution result. Returns True only
        when this action had not been executed yet (idempotent)."""
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE actions SET execution_status = ? WHERE id = ? AND execution_status IS NULL",
                (status, action_id),
            )
            return cur.rowcount > 0

    # -- cases (persistent case state) -----------------------------------
    def upsert_case(
        self,
        business_id: int,
        canonical_problem_id: int | None,
        title: str,
        *,
        severity: str,
        confidence: float,
        evidence_json: str | None,
        decision: str | None,
        dissent_json: str | None,
        last_decision_action: str | None,
        recommended_action: str | None = None,
        root_cause_hypothesis: str | None = None,
        affected_customer_count: int = 1,
        impact_score: float = 0.0,
        trend_direction: str | None = None,
    ) -> dict[str, Any]:
        """Open a new case or fold new evidence into the existing one.

        Recurrence updates the same case (same canonical problem) rather than
        creating a new row, so cross-call recurrence is a first-class concept.
        """
        now = _now()
        with db.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO cases
                   (business_id, canonical_problem_id, title, status, occurrence_count,
                    affected_customer_count, severity, confidence, impact_score,
                    trend_direction, evidence_json, recommended_action, decision,
                    dissent_json, last_decision_action, root_cause_hypothesis,
                    created_at, updated_at)
                   VALUES (?, ?, ?, 'evidence_collected', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(business_id, canonical_problem_id) DO UPDATE SET
                       occurrence_count = cases.occurrence_count + 1,
                       affected_customer_count = excluded.affected_customer_count,
                       severity = CASE
                           WHEN CASE cases.severity
                                WHEN 'critical' THEN 4 WHEN 'high' THEN 3
                                WHEN 'medium' THEN 2 ELSE 1 END
                              >= CASE excluded.severity
                                WHEN 'critical' THEN 4 WHEN 'high' THEN 3
                                WHEN 'medium' THEN 2 ELSE 1 END
                           THEN cases.severity ELSE excluded.severity END,
                       confidence = excluded.confidence,
                       impact_score = excluded.impact_score,
                       trend_direction = COALESCE(excluded.trend_direction, cases.trend_direction),
                       evidence_json = excluded.evidence_json,
                       recommended_action = COALESCE(excluded.recommended_action, cases.recommended_action),
                       decision = COALESCE(excluded.decision, cases.decision),
                       dissent_json = COALESCE(excluded.dissent_json, cases.dissent_json),
                       last_decision_action = COALESCE(excluded.last_decision_action, cases.last_decision_action),
                       root_cause_hypothesis = COALESCE(excluded.root_cause_hypothesis, cases.root_cause_hypothesis),
                       status = CASE WHEN cases.status = 'resolved' THEN 'opened'
                                     ELSE 'evidence_collected' END,
                       updated_at = excluded.updated_at""",
                (
                    business_id,
                    canonical_problem_id,
                    title,
                    affected_customer_count,
                    severity,
                    confidence,
                    impact_score,
                    trend_direction,
                    evidence_json,
                    recommended_action,
                    decision,
                    dissent_json,
                    last_decision_action,
                    root_cause_hypothesis,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """SELECT * FROM cases WHERE business_id = ? AND canonical_problem_id = ?""",
                (business_id, canonical_problem_id),
            ).fetchone()
            return dict(row)

    def update_case_status(self, case_id: int, status: str, *, resolved: bool = False) -> None:
        with db.connect(self.db_path) as conn:
            if resolved:
                conn.execute(
                    "UPDATE cases SET status = ?, resolved_at = ?, updated_at = ? WHERE id = ?",
                    (status, _now(), _now(), case_id),
                )
            else:
                conn.execute(
                    "UPDATE cases SET status = ?, updated_at = ? WHERE id = ?",
                    (status, _now(), case_id),
                )

    def record_case_outcome(self, case_id: int, outcome: str) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE cases SET outcome = ?, resolved_at = ?, status = 'resolved', updated_at = ? WHERE id = ?",
                (outcome, _now(), _now(), case_id),
            )

    def list_cases(self, business_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM cases WHERE business_id = ?
                   ORDER BY confidence DESC, occurrence_count DESC LIMIT ?""",
                (business_id, limit),
            ).fetchall()
            return db.rows_to_dicts(rows)

    def get_case(self, case_id: int) -> dict[str, Any] | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM cases WHERE id = ?", (case_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_case_trace(self, case_id: int) -> dict[str, Any] | None:
        """Assemble a replayable evidence chain for one case.

        Answers "why did this alert fire?" from the system's own data: the case,
        its canonical problem, the linked feedbacks (with transcripts), the
        evidence spans, dissent and impact/trend/confidence.
        """
        case = self.get_case(case_id)
        if case is None:
            return None
        canon = (
            self.get_canonical_problem_by_id(case["canonical_problem_id"])
            if case.get("canonical_problem_id")
            else None
        )
        feedbacks: list[dict[str, Any]] = []
        if case.get("canonical_problem_id"):
            with db.connect(self.db_path) as conn:
                rows = conn.execute(
                    """SELECT f.id, f.call_id, f.satisfaction, f.sentiment,
                              f.priority, f.transcript, f.created_at
                       FROM feedbacks f
                       JOIN feedback_problems fp ON fp.feedback_id = f.id
                       WHERE fp.canonical_problem_id = ?
                       ORDER BY f.id DESC""",
                    (case["canonical_problem_id"],),
                ).fetchall()
            feedbacks = db.rows_to_dicts(rows)
        return {
            "case": case,
            "canonical_problem": canon,
            "feedbacks": feedbacks,
            "evidence": _json_load(case.get("evidence_json"), []),
            "dissent": _json_load(case.get("dissent_json"), []),
        }

    def count_affected_customers(self, canonical_problem_id: int) -> int:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT COUNT(DISTINCT f.customer_id) AS cnt
                   FROM feedback_problems fp
                   JOIN feedbacks f ON f.id = fp.feedback_id
                   WHERE fp.canonical_problem_id = ?""",
                (canonical_problem_id,),
            ).fetchone()
            return row["cnt"] if row else 0

    def customer_complaint_history_by_phone(
        self, business_id: int, phone: str
    ) -> list[dict[str, Any]]:
        """GÖREV E: ALL of a customer's past VERIFIED complaints (canonical
        problem names), looked up directly by phone number — no confidence
        threshold, no item limit.

        "Verified" means the canonical problem has a persistent `cases` row:
        cases are only ever created from claims that passed
        `evidence.verify_claims` (see `Pipeline._upsert_cases`), never from a
        merely-mentioned, unverified transcript claim.

        Distinct from `customer_verified_problems` below, which is
        confidence-gated and capped (by design, for safe pre-call
        contextualization) — this one is the general-purpose "give me this
        customer's full verified complaint history" query.
        """
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT DISTINCT cp.id AS canonical_problem_id, cp.name AS name,
                          cp.category AS category, cs.severity AS severity,
                          cs.confidence AS confidence, cs.status AS case_status,
                          cs.occurrence_count AS occurrence_count
                   FROM customers c
                   JOIN feedbacks f ON f.customer_id = c.id
                   JOIN feedback_problems fp ON fp.feedback_id = f.id
                   JOIN canonical_problems cp ON cp.id = fp.canonical_problem_id
                   JOIN cases cs ON cs.canonical_problem_id = cp.id
                                AND cs.business_id = c.business_id
                   WHERE c.business_id = ? AND c.phone = ?
                   ORDER BY cs.confidence DESC""",
                (business_id, phone),
            ).fetchall()
            return db.rows_to_dicts(rows)

    def customer_verified_problems(
        self,
        business_id: int,
        customer_id: int,
        *,
        min_confidence: float = 0.6,
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        """Structured, verified case history for one customer — used to build a
        safe pre-call context. Returns only canonical problem names (no raw
        transcripts, no PII) for cases whose confidence meets the threshold."""
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT cp.name AS name, cs.confidence AS confidence,
                          cs.status AS status, f.id AS feedback_id
                   FROM feedbacks f
                   JOIN feedback_problems fp ON fp.feedback_id = f.id
                   JOIN canonical_problems cp ON cp.id = fp.canonical_problem_id
                   LEFT JOIN cases cs ON cs.canonical_problem_id = cp.id
                                     AND cs.business_id = f.business_id
                   WHERE f.customer_id = ? AND f.business_id = ?
                   ORDER BY f.id DESC""",
                (customer_id, business_id),
            ).fetchall()
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            conf = d.get("confidence")
            if conf is None or conf < min_confidence:
                continue
            name = d.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            out.append({"name": name, "confidence": conf, "status": d.get("status")})
            if len(out) >= limit:
                break
        return out

    # -- notifications (idempotent outbound actions) --------------------
    def record_notification(self, call_id: int | None, action_type: str, idempotency_key: str) -> bool:
        """Record an outbound notification attempt; returns False if already sent
        (dedup by idempotency key)."""
        with db.connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO notification_events (call_id, action_type, idempotency_key) VALUES (?, ?, ?)",
                    (call_id, action_type, idempotency_key),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def update_notification_status(self, idempotency_key: str, status: str) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE notification_events SET status = ? WHERE idempotency_key = ?",
                (status, idempotency_key),
            )

    # -- model provenance ------------------------------------------------
    def record_model_run(
        self,
        *,
        call_id: int | None,
        provider: str,
        model: str,
        step: str,
        fallback_occurred: bool,
        latency_ms: float | None,
        correlation_id: str,
    ) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO model_runs
                   (call_id, provider, model, step, fallback_occurred, latency_ms, correlation_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (call_id, provider, model, step, 1 if fallback_occurred else 0,
                 latency_ms, correlation_id),
            )

    def list_model_runs(self, call_id: int) -> list[dict[str, Any]]:
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM model_runs WHERE call_id = ? ORDER BY id", (call_id,)
            ).fetchall()
            return db.rows_to_dicts(rows)

    def foreign_customer_satisfaction_since(
        self, business_id: int, default_locale: str, since: str, until: str | None = None
    ) -> dict[str, Any]:
        """Average satisfaction + sample size for feedbacks from customers
        whose `preferred_language` (GÖREV 1) is known and differs from
        `default_locale`, within [since, until). Used for the "foreign-
        language customer satisfaction trend" (GÖREV C, see
        Pipeline.compute_foreign_customer_trend) — a real, data-derived
        metric, never a fabricated one."""
        query = (
            "SELECT AVG(f.satisfaction) AS avg_sat, COUNT(*) AS n, "
            "SUM(CASE WHEN f.sentiment = 'positive' THEN 1 ELSE 0 END) AS positive_n "
            "FROM feedbacks f JOIN customers c ON c.id = f.customer_id "
            "WHERE f.business_id = ? AND c.preferred_language IS NOT NULL "
            "AND c.preferred_language != ? AND f.satisfaction IS NOT NULL "
            "AND f.created_at >= ?"
        )
        params: list[Any] = [business_id, default_locale, since]
        if until:
            query += " AND f.created_at < ?"
            params.append(until)
        with db.connect(self.db_path) as conn:
            row = conn.execute(query, params).fetchone()
        n = row["n"] or 0
        return {
            "avg_satisfaction": row["avg_sat"],
            "n": n,
            "positive_n": row["positive_n"] or 0,
        }

    # -- stats ------------------------------------------------------------
    def average_satisfaction_since(self, business_id: int, since: str) -> float | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT AVG(satisfaction) AS avg_sat FROM feedbacks
                   WHERE business_id = ? AND satisfaction IS NOT NULL
                     AND created_at >= ?""",
                (business_id, since),
            ).fetchone()
            return row["avg_sat"] if row and row["avg_sat"] is not None else None
