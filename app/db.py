"""SQLite data layer.

Single data access layer for the whole application. Uses WAL mode, foreign
keys and a busy timeout so multiple async consumers (API, Telegram, scheduler)
can safely share one database file.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    close_hour INTEGER NOT NULL DEFAULT 2,
    morning_summary_hour INTEGER NOT NULL DEFAULT 8,
    post_delivery_delay_minutes INTEGER NOT NULL DEFAULT 30,
    default_locale TEXT NOT NULL DEFAULT 'tr',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    phone TEXT NOT NULL,
    order_count INTEGER NOT NULL DEFAULT 0,
    first_order_at TEXT,
    last_order_at TEXT,
    loyalty_status TEXT NOT NULL DEFAULT 'new',
    churn_risk TEXT NOT NULL DEFAULT 'none',
    do_not_call INTEGER NOT NULL DEFAULT 0,
    preferred_language TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (business_id, phone)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_type TEXT NOT NULL DEFAULT 'service',
    status TEXT NOT NULL DEFAULT 'created',
    locale TEXT NOT NULL DEFAULT 'tr',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at TEXT,
    completed_at TEXT,
    feedback_scheduled_at TEXT,
    correlation_id TEXT,
    delay_used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL DEFAULT 'scheduled',
    calle_call_id TEXT,
    scheduled_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    outcome TEXT,
    transcript TEXT,
    result_json TEXT,
    decision_json TEXT,
    failure_code TEXT,
    is_simulated INTEGER NOT NULL DEFAULT 0,
    notify_completion INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS feedbacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL REFERENCES calls(id),
    order_id INTEGER NOT NULL REFERENCES orders(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    transcript TEXT,
    satisfaction INTEGER,
    sentiment TEXT,
    positives TEXT,
    category TEXT,
    priority TEXT,
    urgency TEXT,
    missing_products TEXT,
    recommended_action TEXT,
    language TEXT NOT NULL DEFAULT 'tr',
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS canonical_problems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    name TEXT NOT NULL,
    category TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    vector TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS feedback_problems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_id INTEGER NOT NULL REFERENCES feedbacks(id),
    canonical_problem_id INTEGER NOT NULL REFERENCES canonical_problems(id),
    problem_text TEXT,
    severity TEXT,
    UNIQUE (feedback_id, canonical_problem_id)
);

CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    insight_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT,
    recommended_action TEXT,
    period_start TEXT,
    period_end TEXT,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    insight_id INTEGER REFERENCES insights(id),
    customer_id INTEGER REFERENCES customers(id),
    action_type TEXT NOT NULL,
    proposed_by TEXT NOT NULL DEFAULT 'agent',
    status TEXT NOT NULL DEFAULT 'proposed',
    decision TEXT,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    applied_at TEXT,
    outcome TEXT,
    expires_at TEXT,
    execution_status TEXT
);

-- Calls dispatched as a result of an approved/proposed human-review action
-- (compensation-offer notification, re-engagement outreach). Deliberately a
-- SEPARATE table from `calls` (the post-delivery feedback call pipeline):
-- keyed by action_id instead of order_id, so this new, less-tested flow can
-- never collide with or destabilize the core feedback pipeline's schema.
CREATE TABLE IF NOT EXISTS outreach_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id INTEGER NOT NULL UNIQUE REFERENCES actions(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    calle_call_id TEXT,
    status TEXT NOT NULL DEFAULT 'calling',
    failure_code TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS webhook_events (
    event_id TEXT PRIMARY KEY,
    calle_call_id TEXT,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL REFERENCES calls(id),
    step TEXT NOT NULL,
    detail TEXT,
    reasoning_summary TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    canonical_problem_id INTEGER REFERENCES canonical_problems(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'opened',
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    affected_customer_count INTEGER NOT NULL DEFAULT 1,
    severity TEXT NOT NULL DEFAULT 'medium',
    confidence REAL NOT NULL DEFAULT 0.0,
    impact_score REAL NOT NULL DEFAULT 0.0,
    trend_direction TEXT,
    evidence_json TEXT,
    root_cause_hypothesis TEXT,
    recommended_action TEXT,
    decision TEXT,
    dissent_json TEXT,
    last_decision_action TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    outcome TEXT,
    UNIQUE (business_id, canonical_problem_id)
);

CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER,
    provider TEXT,
    model TEXT,
    step TEXT,
    fallback_occurred INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL,
    correlation_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER REFERENCES calls(id),
    action_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'sent',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def init_db(db_path: Path | str) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created before a column was added."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(calls)")}
    if "notify_completion" not in cols:
        conn.execute(
            "ALTER TABLE calls ADD COLUMN notify_completion INTEGER NOT NULL DEFAULT 0"
        )
    if "decision_json" not in cols:
        conn.execute("ALTER TABLE calls ADD COLUMN decision_json TEXT")

    # Unique constraints / indexes added after initial schema (idempotent).
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_feedbacks_call ON feedbacks(call_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_problems_biz_name "
        "ON canonical_problems(business_id, name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedbacks_created ON feedbacks(created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedbacks_business ON feedbacks(business_id)"
    )

    order_cols = {row["name"] for row in conn.execute("PRAGMA table_info(orders)")}
    if "delay_used" not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN delay_used INTEGER NOT NULL DEFAULT 0")

    action_cols = {row["name"] for row in conn.execute("PRAGMA table_info(actions)")}
    if "reason" not in action_cols:
        conn.execute("ALTER TABLE actions ADD COLUMN reason TEXT")
    if "expires_at" not in action_cols:
        conn.execute("ALTER TABLE actions ADD COLUMN expires_at TEXT")
    if "execution_status" not in action_cols:
        conn.execute("ALTER TABLE actions ADD COLUMN execution_status TEXT")
    if "customer_id" not in action_cols:
        conn.execute("ALTER TABLE actions ADD COLUMN customer_id INTEGER REFERENCES customers(id)")

    case_cols = {row["name"] for row in conn.execute("PRAGMA table_info(cases)")}
    if "impact_score" not in case_cols:
        conn.execute("ALTER TABLE cases ADD COLUMN impact_score REAL NOT NULL DEFAULT 0.0")
    if "trend_direction" not in case_cols:
        conn.execute("ALTER TABLE cases ADD COLUMN trend_direction TEXT")

    customer_cols = {row["name"] for row in conn.execute("PRAGMA table_info(customers)")}
    if "preferred_language" not in customer_cols:
        conn.execute("ALTER TABLE customers ADD COLUMN preferred_language TEXT")

    trace_cols = {row["name"] for row in conn.execute("PRAGMA table_info(agent_traces)")}
    if "reasoning_summary" not in trace_cols:
        conn.execute("ALTER TABLE agent_traces ADD COLUMN reasoning_summary TEXT")

    call_cols = {row["name"] for row in conn.execute("PRAGMA table_info(calls)")}
    if "retry_count" not in call_cols:
        conn.execute("ALTER TABLE calls ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
    if "retry_at" not in call_cols:
        conn.execute("ALTER TABLE calls ADD COLUMN retry_at TEXT")


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def _json_dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _json_load(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
