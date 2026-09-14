import sqlite3

from app.backup import backup_database, count_records, verify_backup
from app.pipeline import Pipeline
import asyncio


def _run(coro):
    return asyncio.run(coro)


def _seed_data(repo, p):
    for i in range(3):
        result = p.handle_order(f"+90555111{i:04d}", status="sent")
        call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
        _run(p.process_call_result(call["id"], "Yemek güzeldi.", simulated=True))


def test_backup_and_verify_roundtrip(repo, settings, tmp_path):
    p = Pipeline(repo, settings)
    _seed_data(repo, p)

    dest_dir = tmp_path / "backups"
    dest = backup_database(settings.database_file, dest_dir, keep=3)
    assert dest.exists()

    ok, result = verify_backup(dest)
    assert ok is True
    assert result == "ok"

    # Records preserved in the backup.
    counts = count_records(dest)
    assert counts["feedbacks"] == 3
    assert counts["orders"] == 3
    assert counts["calls"] == 3


def test_backup_prunes_old(repo, settings, tmp_path):
    p = Pipeline(repo, settings)
    _seed_data(repo, p)
    dest_dir = tmp_path / "backups"
    for _ in range(5):
        backup_database(settings.database_file, dest_dir, keep=2)
    files = list(dest_dir.glob("smartfeedback-*.db"))
    assert len(files) == 2


def test_restore_to_fresh_connection(repo, settings, tmp_path):
    p = Pipeline(repo, settings)
    _seed_data(repo, p)
    dest_dir = tmp_path / "backups"
    dest = backup_database(settings.database_file, dest_dir, keep=2)

    # Simulate restore: open the backup file as a new database and query.
    conn = sqlite3.connect(str(dest))
    try:
        n = conn.execute("SELECT COUNT(*) FROM feedbacks").fetchone()[0]
        assert n == 3
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok"
    finally:
        conn.close()
