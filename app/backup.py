"""Safe SQLite backup / restore.

Uses `sqlite3.Connection.backup()` (an online, consistent snapshot) and verifies
the copy with `PRAGMA integrity_check`. Backups are pruned to a configurable
retention count.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup_database(src: Path, dest_dir: Path, *, keep: int = 7) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    dest = dest_dir / f"smartfeedback-{ts}.db"

    src_conn = sqlite3.connect(str(src))
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()

    _prune(dest_dir, keep)
    return dest


def verify_backup(path: Path) -> tuple[bool, str]:
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            result = row[0] if row else "no result"
            return result == "ok", str(result)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, str(exc)


def count_records(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(path))
    try:
        counts: dict[str, int] = {}
        for (table,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ):
            counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        return counts
    finally:
        conn.close()


def _prune(dest_dir: Path, keep: int) -> None:
    files = sorted(dest_dir.glob("smartfeedback-*.db"))
    for f in files[:-keep] if keep > 0 else []:
        f.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup and verify the database")
    parser.add_argument("--src", default=None)
    parser.add_argument("--dest-dir", default="backups")
    parser.add_argument("--keep", type=int, default=7)
    parser.add_argument("--verify", default=None, help="verify an existing backup file")
    args = parser.parse_args()

    if args.verify:
        ok, result = verify_backup(Path(args.verify))
        print(f"integrity_check: {result} -> {'OK' if ok else 'FAIL'}")
        if ok:
            print("records:", count_records(Path(args.verify)))
        return

    from .config import get_settings

    settings = get_settings()
    src = Path(args.src) if args.src else settings.database_file
    dest = backup_database(src, Path(args.dest_dir), keep=args.keep)
    ok, result = verify_backup(dest)
    print(f"backup: {dest}")
    print(f"integrity_check: {result} -> {'OK' if ok else 'FAIL'}")
    if ok:
        print("records:", count_records(dest))


if __name__ == "__main__":
    main()
